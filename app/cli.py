import logging
from pathlib import Path

import yaml
import click

from mission_planner import MissionPlanner
from orchards.tree_placement_generator import TreePlacementGenerator
from network_interface import NetworkInterface

LTL_KEY: str = "ltl"
PROMELA_TEMPLATE_KEY: str = "promela_template"
SPIN_PATH_KEY: str = "spin_path"


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

    context_files: list[str] = []
    context_vars: dict | None = None

    # don't generate/check LTL by default
    ltl: bool = False
    pml_template_path: str = ""
    spin_path: str = ""

    try:
        # configure logger
        logging.basicConfig(level=logging._nameToLevel[config_yaml["logging"]])
        # OpenAI loggers turned off completely.
        logging.getLogger("openai").setLevel(logging.CRITICAL)
        logging.getLogger("anthropic").setLevel(logging.CRITICAL)
        logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
        logging.getLogger("httpx").setLevel(logging.CRITICAL)
        logging.getLogger("httpcore").setLevel(logging.CRITICAL)
        logger: logging.Logger = logging.getLogger()

        # you don't necessarily need context
        if "context_files" in config_yaml:
            context_files = config_yaml["context_files"]
        else:
            logger.warning("No additional context files found. Proceeding...")
        farm_polygon = None
        farm_polygon_file = config_yaml.get("farm_polygon_file")
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
            farm_polygon = config_yaml.get("farm_polygon")

        if farm_polygon:
            tpg = TreePlacementGenerator(
                farm_polygon["points"],
                farm_polygon["dimensions"],
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

        # if user specifies config key -> optional keys
        ltl = config_yaml.get(LTL_KEY) or False
        pml_template_path = config_yaml.get(PROMELA_TEMPLATE_KEY) or ""
        spin_path = config_yaml.get(SPIN_PATH_KEY) or ""
        if ltl and not (pml_template_path and spin_path):
            ltl = False
            logger.warning(
                "No spin configuration found. Proceeding without formal verification..."
            )

        mp: MissionPlanner = MissionPlanner(
            config_yaml["token"],
            config_yaml["schema"],
            config_yaml.get("lint_xml", True),
            context_files,
            tpg,
            config_yaml["max_retries"],
            config_yaml["max_tokens"],
            config_yaml["temperature"],
            ltl,
            pml_template_path,
            spin_path,
            config_yaml["log_directory"],
            logger,
            context_vars,
        )
    except yaml.YAMLError as exc:
        logger.error(f"Improper YAML config: {exc}")

    nic = NetworkInterface(logger, config_yaml["host"], int(config_yaml["port"]))

    while True:
        mp_input = input("Enter the specifications for your mission plan: ")
        file_xml_out = mp.run(mp_input)

        nic.init_socket()
        # Send XML and tree points if available
        tree_points = (
            mp.tree_points if hasattr(mp, "tree_points") and mp.tree_points else None
        )
        nic.send_file(file_xml_out, tree_points)
        nic.close_socket()


if __name__ == "__main__":
    main()
