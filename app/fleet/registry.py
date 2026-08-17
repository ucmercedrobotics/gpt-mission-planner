"""In-memory roster of robots that have reported in.

Liveness is derived lazily from ``last_seen`` on every read, so there is no
reaper thread to supervise. Records survive past their TTL as ``offline`` for a
while because a robot that vanished mid-mission is exactly the thing an
operator needs to see in the UI; they are only forgotten after
``forget_after_s``.

NOTE: state lives in this process. That is safe today because the planner runs
a single uvicorn worker (see the `webapp` target in the Makefile). If workers
are ever scaled past one, each would hold a different roster -- move this to
the JSON file with locking, or to Redis, before adding `--workers`.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_TTL_S = 15.0
DEFAULT_STALE_AFTER_S = 8.0


@dataclass
class RobotRecord:
    robot_id: str
    schema_name: str
    port: int
    host: Optional[str] = None
    name: Optional[str] = None
    protocol: str = "tcp-lenprefix-v1"
    schema_sha256: Optional[str] = None
    actions: list[str] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    status: str = "idle"
    battery_pct: Optional[float] = None
    position: Optional[dict[str, float]] = None
    current_mission_id: Optional[str] = None
    heartbeat_interval_s: float = 5.0
    registered_at: float = 0.0
    last_seen: Optional[float] = None
    # "http" for robots that registered themselves, "static" for entries seeded
    # from config. Static robots cannot heartbeat, so they are always live.
    source: str = "http"
    schema_known: bool = True
    schema_matches: Optional[bool] = None

    def to_json(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "schema": self.schema_name,
            "host": self.host,
            "port": self.port,
            "name": self.name,
            "protocol": self.protocol,
            "schema_sha256": self.schema_sha256,
            "actions": list(self.actions),
            "capabilities": dict(self.capabilities),
            "status": self.status,
            "battery_pct": self.battery_pct,
            "position": self.position,
            "current_mission_id": self.current_mission_id,
            "heartbeat_interval_s": self.heartbeat_interval_s,
            "registered_at": self.registered_at,
            "last_seen": self.last_seen,
            "source": self.source,
            "schema_known": self.schema_known,
            "schema_matches": self.schema_matches,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RobotRecord":
        return cls(
            robot_id=data["robot_id"],
            schema_name=data.get("schema") or data.get("schema_name", ""),
            port=int(data["port"]),
            host=data.get("host"),
            name=data.get("name"),
            protocol=data.get("protocol", "tcp-lenprefix-v1"),
            schema_sha256=data.get("schema_sha256"),
            actions=list(data.get("actions", [])),
            capabilities=dict(data.get("capabilities", {})),
            status=data.get("status", "idle"),
            battery_pct=data.get("battery_pct"),
            position=data.get("position"),
            current_mission_id=data.get("current_mission_id"),
            heartbeat_interval_s=float(data.get("heartbeat_interval_s", 5.0)),
            registered_at=float(data.get("registered_at", 0.0)),
            # Deliberately dropped: a restored record has not been heard from
            # in this process, so it is "unknown" until its next heartbeat.
            last_seen=None,
            source=data.get("source", "http"),
            schema_known=bool(data.get("schema_known", True)),
            schema_matches=data.get("schema_matches"),
        )


class RobotRegistry:
    def __init__(
        self,
        logger: logging.Logger,
        ttl_s: float = DEFAULT_TTL_S,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        registry_file: Optional[str] = None,
        forget_after_s: Optional[float] = None,
        clock=time.time,
    ) -> None:
        self.logger = logger
        self.ttl_s = float(ttl_s)
        self.stale_after_s = float(stale_after_s)
        # Kept visible (as offline) long enough to be useful for debugging a
        # robot that dropped out, then forgotten.
        self.forget_after_s = (
            float(forget_after_s)
            if forget_after_s is not None
            else max(300.0, self.ttl_s * 20)
        )
        self.registry_file = Path(registry_file) if registry_file else None
        # Injectable so tests can drive liveness without sleeping.
        self._clock = clock
        self._lock = threading.Lock()
        self._robots: dict[str, RobotRecord] = {}

        if self.registry_file:
            self._restore()

    # --- liveness ---------------------------------------------------------

    def liveness(self, record: RobotRecord, now: Optional[float] = None) -> str:
        if record.source == "static":
            # Seeded from config; nothing will ever heartbeat on its behalf.
            return "online"
        if record.last_seen is None:
            return "unknown"
        age = (now if now is not None else self._clock()) - record.last_seen
        if age <= self.stale_after_s:
            return "online"
        if age <= self.ttl_s:
            return "stale"
        return "offline"

    def _age(self, record: RobotRecord, now: float) -> Optional[float]:
        if record.last_seen is None:
            return None
        return max(0.0, now - record.last_seen)

    # --- mutation ---------------------------------------------------------

    def register(self, record: RobotRecord) -> RobotRecord:
        """Idempotent: re-registering the same robot_id replaces the record."""
        now = self._clock()
        record.registered_at = now
        record.last_seen = now
        with self._lock:
            previous = self._robots.get(record.robot_id)
            if previous is not None and previous.source == "static":
                # A real robot claiming a statically-configured id wins; the
                # config entry was only ever a stand-in.
                self.logger.info(
                    "Robot %s registered over its static config entry", record.robot_id
                )
            self._robots[record.robot_id] = record
        self.logger.info(
            "Robot registered: %s schema=%s endpoint=%s:%s",
            record.robot_id,
            record.schema_name,
            record.host,
            record.port,
        )
        self._persist()
        return record

    def heartbeat(
        self,
        robot_id: str,
        status: Optional[str] = None,
        battery_pct: Optional[float] = None,
        position: Optional[dict[str, float]] = None,
        current_mission_id: Optional[str] = None,
    ) -> Optional[RobotRecord]:
        """Returns None when the robot is unknown, so the caller can 404 and
        prompt the robot to re-register."""
        with self._lock:
            record = self._robots.get(robot_id)
            if record is None:
                return None
            was = self.liveness(record)
            record.last_seen = self._clock()
            if status is not None:
                record.status = status
            if battery_pct is not None:
                record.battery_pct = battery_pct
            if position is not None:
                record.position = position
            # Explicitly settable back to None when a mission finishes.
            record.current_mission_id = current_mission_id
        if was in ("stale", "offline", "unknown"):
            self.logger.info("Robot %s recovered (was %s)", robot_id, was)
        return record

    def remove(self, robot_id: str) -> bool:
        with self._lock:
            existed = self._robots.pop(robot_id, None) is not None
        if existed:
            self.logger.info("Robot deregistered: %s", robot_id)
            self._persist()
        return existed

    def seed_static(self, entries: list[dict[str, Any]]) -> None:
        """Load `fleet.static_robots` from config as always-live entries."""
        for entry in entries or []:
            try:
                record = RobotRecord(
                    robot_id=str(entry["robot_id"]),
                    schema_name=str(entry["schema"]),
                    host=entry.get("host"),
                    port=int(entry["port"]),
                    name=entry.get("name"),
                    actions=list(entry.get("actions", [])),
                    source="static",
                    registered_at=self._clock(),
                )
            except (KeyError, TypeError, ValueError) as exc:
                self.logger.warning(
                    "Skipping malformed static robot %s: %s", entry, exc
                )
                continue
            with self._lock:
                if record.robot_id in self._robots:
                    continue
                self._robots[record.robot_id] = record
            self.logger.info(
                "Static robot seeded: %s schema=%s endpoint=%s:%s",
                record.robot_id,
                record.schema_name,
                record.host,
                record.port,
            )

    def mark_schema_support(self, known_schemas: dict[str, Optional[str]]) -> None:
        """Flag records whose schema the planner does not have, or whose copy
        of the XSD has drifted from ours.

        `known_schemas` maps schema stem -> sha256 of our copy (or None if we
        have not hashed it).
        """
        with self._lock:
            for record in self._robots.values():
                our_hash = known_schemas.get(record.schema_name)
                record.schema_known = record.schema_name in known_schemas
                if record.schema_sha256 and our_hash:
                    record.schema_matches = record.schema_sha256 == our_hash
                    if not record.schema_matches:
                        self.logger.warning(
                            "Robot %s reports a different %s.xsd than the planner has "
                            "(robot=%s planner=%s). Generated plans may not match what "
                            "it can execute.",
                            record.robot_id,
                            record.schema_name,
                            record.schema_sha256[:12],
                            our_hash[:12],
                        )
                else:
                    record.schema_matches = None

    # --- reads ------------------------------------------------------------

    def get(self, robot_id: str) -> Optional[RobotRecord]:
        with self._lock:
            return self._robots.get(robot_id)

    def list_all(self) -> list[tuple[RobotRecord, str, Optional[float]]]:
        """All known robots as (record, liveness, seconds_since_last_seen)."""
        now = self._clock()
        self._forget_expired(now)
        with self._lock:
            records = list(self._robots.values())
        records.sort(key=lambda r: r.robot_id)
        return [(r, self.liveness(r, now), self._age(r, now)) for r in records]

    def eligible(
        self,
        require_idle: bool = True,
        min_battery_pct: float = 0.0,
        max_robots: Optional[int] = None,
    ) -> list[RobotRecord]:
        """Robots that may be given work: online, schema we recognise, and (by
        default) idle with enough battery."""
        now = self._clock()
        self._forget_expired(now)
        out: list[RobotRecord] = []
        with self._lock:
            records = sorted(self._robots.values(), key=lambda r: r.robot_id)
        for record in records:
            if self.liveness(record, now) != "online":
                continue
            if not record.schema_known:
                continue
            if record.host is None:
                # Never resolved an address; nothing to dispatch to.
                continue
            if require_idle and record.status != "idle":
                continue
            if (
                min_battery_pct > 0.0
                and record.battery_pct is not None
                and record.battery_pct < min_battery_pct
            ):
                continue
            out.append(record)
        if max_robots is not None and max_robots > 0:
            # Prefer the healthiest when we have to cut: known battery first,
            # fullest first, then by id so the choice is stable run to run.
            out.sort(
                key=lambda r: (
                    -(r.battery_pct if r.battery_pct is not None else 1.0),
                    r.robot_id,
                )
            )
            out = out[:max_robots]
            out.sort(key=lambda r: r.robot_id)
        return out

    def unique_schemas(self, records: list[RobotRecord]) -> list[str]:
        """Deduplicated schema stems, in first-seen order.

        Two Amigas contribute one copy of amiga_btcpp.xsd to the prompt; that
        dedupe is where the token saving comes from.
        """
        return list(dict.fromkeys(r.schema_name for r in records))

    # --- persistence ------------------------------------------------------

    def _forget_expired(self, now: float) -> None:
        with self._lock:
            doomed = [
                robot_id
                for robot_id, record in self._robots.items()
                if record.source != "static"
                and record.last_seen is not None
                and (now - record.last_seen) > self.forget_after_s
            ]
            for robot_id in doomed:
                del self._robots[robot_id]
        for robot_id in doomed:
            self.logger.info(
                "Forgetting robot %s (no heartbeat in %.0fs)",
                robot_id,
                self.forget_after_s,
            )
        if doomed:
            self._persist()

    def _persist(self) -> None:
        if not self.registry_file:
            return
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = [
                    r.to_json() for r in self._robots.values() if r.source != "static"
                ]
            tmp = self.registry_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            tmp.replace(self.registry_file)
        except OSError as exc:
            self.logger.warning("Could not persist robot registry: %s", exc)

    def _restore(self) -> None:
        if not self.registry_file or not self.registry_file.exists():
            return
        try:
            with open(self.registry_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("Could not restore robot registry: %s", exc)
            return
        if not isinstance(payload, list):
            return
        for entry in payload:
            try:
                record = RobotRecord.from_json(entry)
            except (KeyError, TypeError, ValueError) as exc:
                self.logger.warning("Skipping malformed registry entry: %s", exc)
                continue
            self._robots[record.robot_id] = record
        if self._robots:
            self.logger.info(
                "Restored %d robot(s) from %s; all marked unknown until they heartbeat",
                len(self._robots),
                self.registry_file,
            )
