import logging
import uuid
from pathlib import Path
from typing import Any

import yaml
import click

from mission_planner import MissionPlanner, MissionPlannerConfig, ModelRoutingConfig
from orchards.tree_placement_generator import TreePlacementGenerator
from network_interface import NetworkInterface
from fleet.config import build_registry, eligible_robots, load_fleet_config
from fleet.dispatcher import FleetDispatcher
from fleet.schema_index import schema_path
from fleet.service import start_fleet_service


@click.command()
@click.option(
    "--config",
    default="./app/config/localhost.yaml",
    help="YAML config file",
)
def main(config: str):
    def resolve_config_candidate(candidate: str) -> Path:
        candidate_path = Path(candidate)
        if candidate_path.is_absolute():
            return candidate_path

        cwd_resolved = candidate_path.resolve()
        if cwd_resolved.exists():
            return cwd_resolved

        return (config_path.parent / candidate_path).resolve()

    config_path = Path(config).resolve()
    with open(config_path, "r") as file:
        config_yaml: dict = yaml.safe_load(file)

    mission_cfg: dict[str, Any] = config_yaml["mission"]
    planner_cfg: dict[str, Any] = config_yaml["planner"]
    generation_cfg: dict[str, Any] = planner_cfg["generation"]
    validation_cfg: dict[str, Any] = planner_cfg["validation"]
    verification_cfg: dict[str, Any] = planner_cfg["formal_verification"]
    llm_cfg: dict[str, Any] = config_yaml["llm"]
    routing_cfg: dict[str, Any] = llm_cfg["routing"]
    endpoints_cfg: dict[str, Any] = llm_cfg["endpoints"]
    network_cfg: dict[str, Any] = config_yaml["network"]["tcp"]

    context_files: list[str] = mission_cfg.get("context_files", [])
    context_vars: dict | None = None

    ltl: bool = bool(validation_cfg.get("ltl_enabled", False))
    pml_template_path: str = str(verification_cfg.get("promela_template", ""))
    spin_path: str = str(verification_cfg.get("spin_path", ""))

    try:
        # configure logger
        logging_level = str(config_yaml["logging"]["level"])
        logging.basicConfig(level=logging._nameToLevel[logging_level])
        # OpenAI loggers turned off completely.
        logging.getLogger("openai").setLevel(logging.CRITICAL)
        logging.getLogger("anthropic").setLevel(logging.CRITICAL)
        logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
        logging.getLogger("httpx").setLevel(logging.CRITICAL)
        logging.getLogger("httpcore").setLevel(logging.CRITICAL)
        logger: logging.Logger = logging.getLogger()

        farm_polygon = None
        farm_cfg = mission_cfg.get("farm", {})
        farm_polygon_file = farm_cfg.get("polygon_file")
        if farm_polygon_file:
            polygon_path = resolve_config_candidate(farm_polygon_file)
            try:
                with open(polygon_path, "r") as polygon_handle:
                    farm_polygon = yaml.safe_load(polygon_handle)
                if not isinstance(farm_polygon, dict):
                    logger.warning(
                        "Farm polygon file did not contain a mapping: %s",
                        polygon_path,
                    )
                    farm_polygon = None
            except FileNotFoundError:
                logger.warning("Farm polygon file not found: %s", polygon_path)
            except yaml.YAMLError as exc:
                logger.warning("Improper farm polygon YAML: %s", exc)

        if farm_polygon is None:
            farm_polygon = farm_cfg.get("polygon")

        if farm_polygon:
            tpg = TreePlacementGenerator(
                farm_polygon["points"],
                farm_polygon["dimensions"],
                perimeter_margin_m=farm_polygon.get("perimeter_margin_m", 5.0),
                traversal_axis=farm_polygon.get("traversal_axis", "row"),
            )
            context_vars = {
                "farm_polygon": farm_polygon,
            }
            logger.debug("Farm polygon points defined are: %s", tpg.polygon_coords)
            logger.debug("Farm dimensions defined are: %s", tpg.dimensions)
        else:
            tpg = None
            logger.warning(
                "No farm polygon found. Assuming we're not dealing with an orchard grid..."
            )

        if ltl and not (pml_template_path and spin_path):
            ltl = False
            logger.warning(
                "No spin configuration found. Proceeding without formal verification..."
            )

        routing_mode = str(routing_cfg.get("mode", "online")).lower()
        online_model = str(routing_cfg["online_model"])
        offline_model = routing_cfg.get("offline_model")
        online_api_base = endpoints_cfg.get("online_api_base")
        offline_api_base = endpoints_cfg.get("offline_api_base")

        if routing_mode == "auto":
            selected_model = online_model
            selected_api_base = online_api_base
            auto_toggle = True
            local_model = offline_model
            local_api_base = offline_api_base
        elif routing_mode == "online":
            selected_model = online_model
            selected_api_base = online_api_base
            auto_toggle = False
            local_model = None
            local_api_base = None
        elif routing_mode == "offline":
            if offline_model is None:
                logger.warning(
                    "llm.routing.mode=offline but no offline_model provided. Falling back to online_model."
                )
            selected_model = offline_model or online_model
            selected_api_base = offline_api_base
            auto_toggle = False
            local_model = None
            local_api_base = None
        else:
            raise ValueError(
                "Invalid llm.routing.mode '%s'. Supported values: auto, online, offline"
                % routing_mode
            )

        planner_config = MissionPlannerConfig(
            token_path=config_yaml["auth"]["token_env_file"],
            schema_paths=mission_cfg["schema_paths"],
            lint_xml=bool(validation_cfg.get("lint_xml", True)),
            max_retries=int(planner_cfg["retries"]["max"]),
            max_tokens=int(generation_cfg["max_tokens"]),
            temperature=float(generation_cfg["temperature"]),
            ltl=ltl,
            promela_template_path=pml_template_path,
            spin_path=spin_path,
            log_directory=mission_cfg["log_directory"],
            model_routing=ModelRoutingConfig(
                model=selected_model,
                api_base=selected_api_base,
                auto_model_toggle=auto_toggle,
                local_model=local_model,
                local_api_base=local_api_base,
            ),
        )

        mp: MissionPlanner = MissionPlanner(
            planner_config,
            context_files,
            tpg,
            logger,
            context_vars,
        )
    except yaml.YAMLError as exc:
        logger.error(f"Improper YAML config: {exc}")

    fleet_cfg = load_fleet_config(config_yaml)
    registry = None
    dispatcher = None
    if fleet_cfg.enabled:
        registry = build_registry(fleet_cfg, logger)
        start_fleet_service(
            registry, logger, fleet_cfg.host, fleet_cfg.port, fleet_cfg.auth_token
        )
        dispatcher = FleetDispatcher(logger)

    nic = NetworkInterface(logger, network_cfg["host"], int(network_cfg["port"]))

    while True:
        mp_input = input("Enter the specifications for your mission plan: ")

        robots = eligible_robots(registry, fleet_cfg) if registry else []
        if robots:
            _run_fleet_mission(mp, robots, registry, dispatcher, mp_input, logger)
            continue

        if fleet_cfg.enabled:
            logger.warning(
                "No robots have reported in; falling back to %s:%s from network.tcp",
                network_cfg["host"],
                network_cfg["port"],
            )

        file_xml_out = mp.run(mp_input)

        # Send XML and tree points if available
        tree_points = (
            mp.tree_points if hasattr(mp, "tree_points") and mp.tree_points else None
        )
        try:
            nic.init_socket()
            nic.send_file(file_xml_out, tree_points)
            ack = nic.recv_ack()
            if ack is not None and not ack.get("accepted", False):
                logger.error("Robot rejected the mission: %s", ack.get("error"))
            else:
                logger.info("Mission sent to %s:%s", nic.host, nic.port)
        except OSError as exc:
            # Previously an unreachable robot raised straight out of the REPL.
            logger.error("Could not send mission to %s:%s: %s", nic.host, nic.port, exc)
        finally:
            nic.close_socket()


def _run_fleet_mission(mp, robots, registry, dispatcher, mp_input, logger) -> None:
    """Plan across the discovered robots and send each its own tree."""
    logger.info(
        "Planning across %d robot(s): %s",
        len(robots),
        ", ".join(f"{r.robot_id} ({r.schema_name})" for r in robots),
    )
    # Deduplicated: two robots on the same platform contribute one copy of the XSD.
    mp.set_schema_paths([schema_path(r.schema_name) for r in robots])

    mission_id = uuid.uuid4().hex[:8]
    try:
        result = mp.run_fleet(mp_input, robots, mission_id)
    except Exception as exc:
        logger.error("Fleet planning failed: %s", exc)
        return

    if not result.plans:
        logger.error(
            "No plans were produced. %s",
            result.allocation.unassigned_reason or "See the errors above.",
        )
        return

    for robot_id, error in result.failures.items():
        logger.error("No plan for %s: %s", robot_id, error)

    tree_points = mp.tree_points if getattr(mp, "tree_points", None) else None
    results = dispatcher.dispatch(
        result.plans, {r.robot_id: r for r in robots}, mission_id, tree_points
    )
    for outcome in results:
        logger.info(
            "%s -> %s (%s:%d)%s",
            outcome.robot_id,
            outcome.outcome.value,
            outcome.host,
            outcome.port,
            f" - {outcome.error}" if outcome.error else "",
        )


if __name__ == "__main__":
    main()
