"""Reading the `fleet:` block out of the planner YAML.

Shared by both entry points so the CLI and the web app agree on where robots
register and which of them are allowed to take work.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fleet import schema_index
from fleet.registry import RobotRegistry


@dataclass
class FleetConfig:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8003
    heartbeat_ttl_s: float = 15.0
    stale_after_s: float = 8.0
    auth_token: Optional[str] = None
    registry_file: Optional[str] = "logs/robots.json"
    static_robots: list[dict[str, Any]] = field(default_factory=list)
    require_idle: bool = True
    min_battery_pct: float = 0.0
    max_robots_per_mission: Optional[int] = None


def load_fleet_config(config_yaml: dict[str, Any]) -> FleetConfig:
    fleet_cfg = config_yaml.get("fleet") or {}
    discovery = fleet_cfg.get("discovery") or {}
    allocation = fleet_cfg.get("allocation") or {}

    return FleetConfig(
        enabled=bool(fleet_cfg.get("enabled", False)),
        host=str(discovery.get("host", "0.0.0.0")),
        port=int(discovery.get("port", 8003)),
        heartbeat_ttl_s=float(discovery.get("heartbeat_ttl_s", 15.0)),
        stale_after_s=float(discovery.get("stale_after_s", 8.0)),
        auth_token=fleet_cfg.get("auth_token") or None,
        registry_file=fleet_cfg.get("registry_file", "logs/robots.json") or None,
        static_robots=list(fleet_cfg.get("static_robots") or []),
        require_idle=bool(allocation.get("require_idle", True)),
        min_battery_pct=float(allocation.get("min_battery_pct", 0.0)),
        max_robots_per_mission=(
            int(allocation["max_robots_per_mission"])
            if allocation.get("max_robots_per_mission")
            else None
        ),
    )


def build_registry(cfg: FleetConfig, logger: logging.Logger) -> RobotRegistry:
    registry = RobotRegistry(
        logger,
        ttl_s=cfg.heartbeat_ttl_s,
        stale_after_s=cfg.stale_after_s,
        registry_file=cfg.registry_file,
    )
    registry.seed_static(cfg.static_robots)
    # Flags statics and restored records whose schema we do not have, so they
    # show up in the UI as ineligible rather than silently never being chosen.
    registry.mark_schema_support(schema_index.schema_hashes())
    return registry


def eligible_robots(registry: RobotRegistry, cfg: FleetConfig) -> list:
    return registry.eligible(
        require_idle=cfg.require_idle,
        min_battery_pct=cfg.min_battery_pct,
        max_robots=cfg.max_robots_per_mission,
    )
