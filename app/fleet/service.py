"""HTTP endpoints robots call to announce themselves.

Runs on its own port (default 8003) so the answer to "where do I register?" is
the same whether the operator started the web app or the CLI. Both entry points
call `start_fleet_service` against the same in-process registry.
"""

import logging
import threading
from typing import Any, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Request

from fleet import schema_index
from fleet.models import (
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
    RobotView,
)
from fleet.registry import RobotRecord, RobotRegistry


def robot_views(registry: RobotRegistry) -> list[dict[str, Any]]:
    """Serialise the roster for GET /robots. Shared by both HTTP surfaces."""
    eligible_ids = {r.robot_id for r in registry.eligible()}
    views = []
    for record, liveness, age in registry.list_all():
        view = RobotView(
            robot_id=record.robot_id,
            name=record.name,
            schema_name=record.schema_name,
            schema_known=record.schema_known,
            schema_matches=record.schema_matches,
            host=record.host,
            port=record.port,
            protocol=record.protocol,
            status=record.status,  # type: ignore[arg-type]
            liveness=liveness,  # type: ignore[arg-type]
            eligible=record.robot_id in eligible_ids,
            battery_pct=record.battery_pct,
            position=record.position,  # type: ignore[arg-type]
            current_mission_id=record.current_mission_id,
            actions=record.actions,
            last_seen_s_ago=age,
            source=record.source,
        )
        views.append(view.model_dump(by_alias=True, mode="json"))
    return views


def create_fleet_app(
    registry: RobotRegistry,
    logger: logging.Logger,
    auth_token: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="GPT Mission Planner - Fleet Discovery")

    def _check_auth(token: Optional[str]) -> None:
        if auth_token and token != auth_token:
            raise HTTPException(
                status_code=401, detail="Invalid or missing X-Fleet-Token"
            )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "fleet-discovery"}

    @app.post("/robots/register", response_model=RegisterResponse)
    def register(
        request: Request,
        body: RegisterRequest,
        x_fleet_token: Optional[str] = Header(default=None),
    ) -> RegisterResponse:
        _check_auth(x_fleet_token)

        host = body.bt_endpoint.host
        if not host or host in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
            # The robot either did not know its routable address or gave one
            # that is only meaningful on its own machine. The source IP of this
            # request is what the planner can actually reach.
            host = request.client.host if request.client else None
            if host is None:
                raise HTTPException(
                    status_code=400,
                    detail="Could not determine robot address; set bt_endpoint.host explicitly",
                )
            logger.debug("Resolved %s bt_endpoint host to %s", body.robot_id, host)

        known = schema_index.schema_exists(body.schema_name)
        our_hash = schema_index.hash_schema(body.schema_name) if known else None
        matches: Optional[bool] = None
        if known and body.schema_sha256 and our_hash:
            matches = body.schema_sha256 == our_hash

        record = RobotRecord(
            robot_id=body.robot_id,
            schema_name=body.schema_name,
            host=host,
            port=body.bt_endpoint.port,
            name=body.name,
            protocol=body.bt_endpoint.protocol,
            schema_sha256=body.schema_sha256,
            actions=list(body.capabilities.actions),
            capabilities=body.capabilities.model_dump(),
            status=body.status.value,
            battery_pct=body.battery_pct,
            position=body.position.model_dump() if body.position else None,
            heartbeat_interval_s=body.heartbeat_interval_s,
            schema_known=known,
            schema_matches=matches,
        )
        registry.register(record)

        if not known:
            logger.warning(
                "Robot %s reports schema '%s' which the planner does not have. "
                "It will be listed but excluded from planning. Known schemas: %s",
                body.robot_id,
                body.schema_name,
                ", ".join(schema_index.list_schema_stems()) or "(none)",
            )
        elif matches is False:
            logger.warning(
                "Robot %s has a different copy of %s.xsd than the planner",
                body.robot_id,
                body.schema_name,
            )

        return RegisterResponse(
            robot_id=body.robot_id,
            ttl_s=registry.ttl_s,
            heartbeat_url=f"/robots/{body.robot_id}/heartbeat",
            schema_known=known,
            schema_matches=matches,
        )

    @app.post("/robots/{robot_id}/heartbeat", response_model=HeartbeatResponse)
    def heartbeat(
        robot_id: str,
        body: HeartbeatRequest = Body(default_factory=HeartbeatRequest),
        x_fleet_token: Optional[str] = Header(default=None),
    ) -> HeartbeatResponse:
        _check_auth(x_fleet_token)
        record = registry.heartbeat(
            robot_id,
            status=body.status.value,
            battery_pct=body.battery_pct,
            position=body.position.model_dump() if body.position else None,
            current_mission_id=body.current_mission_id,
        )
        if record is None:
            # The planner restarted, or this robot never registered. A 404 is
            # the robot's cue to re-register.
            raise HTTPException(status_code=404, detail="Unknown robot; register first")
        return HeartbeatResponse()

    @app.delete("/robots/{robot_id}")
    def deregister(
        robot_id: str, x_fleet_token: Optional[str] = Header(default=None)
    ) -> dict[str, Any]:
        _check_auth(x_fleet_token)
        return {"ok": registry.remove(robot_id)}

    @app.get("/robots")
    def list_robots() -> dict[str, Any]:
        return {"robots": robot_views(registry)}

    return app


def start_fleet_service(
    registry: RobotRegistry,
    logger: logging.Logger,
    host: str = "0.0.0.0",
    port: int = 8003,
    auth_token: Optional[str] = None,
) -> threading.Thread:
    """Serve the discovery API on a daemon thread and return immediately."""
    import uvicorn

    app = create_fleet_app(registry, logger, auth_token)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    # Signal handlers can only be installed on the main thread.
    server.install_signal_handlers = False

    def _serve() -> None:
        try:
            server.run()
        except Exception as exc:  # pragma: no cover - thread-level guard
            logger.error("Fleet discovery service stopped: %s", exc)

    thread = threading.Thread(target=_serve, name="fleet-discovery", daemon=True)
    thread.start()
    logger.info("Fleet discovery listening on http://%s:%d/robots/register", host, port)
    return thread
