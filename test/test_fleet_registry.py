"""Tests for the robot registry's liveness and eligibility rules."""

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from fleet.registry import RobotRecord, RobotRegistry


class FakeClock:
    """Drives TTL expiry without sleeping."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def logger():
    return logging.getLogger("test-registry")


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def registry(logger, clock):
    return RobotRegistry(
        logger, ttl_s=15.0, stale_after_s=8.0, registry_file=None, clock=clock
    )


def make_record(robot_id="amiga-01", schema="amiga_btcpp", port=12346, **kwargs):
    return RobotRecord(
        robot_id=robot_id, schema_name=schema, port=port, host="10.0.0.5", **kwargs
    )


def test_register_then_online(registry):
    registry.register(make_record())
    record, liveness, age = registry.list_all()[0]
    assert record.robot_id == "amiga-01"
    assert liveness == "online"
    assert age == 0.0
    assert [r.robot_id for r in registry.eligible()] == ["amiga-01"]


def test_liveness_degrades_with_silence(registry, clock):
    registry.register(make_record())

    clock.advance(5)
    assert registry.list_all()[0][1] == "online"

    clock.advance(5)  # 10s total, past stale_after_s=8
    assert registry.list_all()[0][1] == "stale"
    assert registry.eligible() == []

    clock.advance(10)  # 20s total, past ttl_s=15
    assert registry.list_all()[0][1] == "offline"
    assert registry.eligible() == []


def test_heartbeat_restores_liveness(registry, clock):
    registry.register(make_record())
    clock.advance(20)
    assert registry.list_all()[0][1] == "offline"

    registry.heartbeat("amiga-01", status="idle", battery_pct=0.5)
    assert registry.list_all()[0][1] == "online"
    assert [r.robot_id for r in registry.eligible()] == ["amiga-01"]


def test_heartbeat_for_unknown_robot_returns_none(registry):
    assert registry.heartbeat("ghost-01") is None


def test_registration_is_idempotent(registry):
    registry.register(make_record(port=12346))
    registry.register(make_record(port=12999))
    assert len(registry.list_all()) == 1
    assert registry.get("amiga-01").port == 12999


def test_busy_robot_is_not_eligible(registry):
    registry.register(make_record(status="busy"))
    assert registry.eligible(require_idle=True) == []
    assert len(registry.eligible(require_idle=False)) == 1


def test_low_battery_excluded(registry):
    registry.register(make_record(robot_id="full", battery_pct=0.9))
    registry.register(make_record(robot_id="flat", battery_pct=0.05))
    eligible = registry.eligible(min_battery_pct=0.2)
    assert [r.robot_id for r in eligible] == ["full"]


def test_unknown_schema_excluded_but_still_listed(registry):
    registry.register(make_record(robot_id="mystery", schema="not_a_real_platform"))
    registry.mark_schema_support({"amiga_btcpp": None})
    assert registry.eligible() == []
    # Still visible, so the operator can see why it is not being used.
    assert len(registry.list_all()) == 1
    assert registry.get("mystery").schema_known is False


def test_robot_without_resolved_host_is_not_dispatchable(registry):
    record = make_record(robot_id="hostless")
    record.host = None
    registry.register(record)
    assert registry.eligible() == []


def test_max_robots_caps_the_fleet(registry):
    for i in range(5):
        registry.register(make_record(robot_id=f"amiga-{i}", battery_pct=0.5 + i / 10))
    eligible = registry.eligible(max_robots=2)
    assert len(eligible) == 2
    # Fullest batteries win, and the result stays sorted by id.
    assert [r.robot_id for r in eligible] == ["amiga-3", "amiga-4"]


def test_unique_schemas_dedupes(registry):
    records = [
        make_record(robot_id="amiga-01", schema="amiga_btcpp"),
        make_record(robot_id="amiga-02", schema="amiga_btcpp"),
        make_record(robot_id="husky-01", schema="clearpath_husky"),
    ]
    for record in records:
        registry.register(record)
    assert registry.unique_schemas(records) == ["amiga_btcpp", "clearpath_husky"]


def test_static_robots_are_always_online(registry, clock):
    registry.seed_static(
        [
            {
                "robot_id": "bench",
                "schema": "amiga_btcpp",
                "host": "172.17.0.1",
                "port": 12346,
            }
        ]
    )
    clock.advance(10_000)
    record, liveness, _ = registry.list_all()[0]
    assert liveness == "online"
    assert record.source == "static"
    assert [r.robot_id for r in registry.eligible()] == ["bench"]


def test_static_entry_yields_to_a_real_registration(registry):
    registry.seed_static(
        [
            {
                "robot_id": "amiga-01",
                "schema": "amiga_btcpp",
                "host": "172.17.0.1",
                "port": 12346,
            }
        ]
    )
    registry.register(make_record(robot_id="amiga-01", port=12999))
    record = registry.get("amiga-01")
    assert record.source == "http"
    assert record.port == 12999


def test_forgotten_after_long_silence(logger, clock):
    registry = RobotRegistry(
        logger,
        ttl_s=15.0,
        stale_after_s=8.0,
        registry_file=None,
        forget_after_s=300.0,
        clock=clock,
    )
    registry.register(make_record())
    clock.advance(301)
    assert registry.list_all() == []


def test_remove(registry):
    registry.register(make_record())
    assert registry.remove("amiga-01") is True
    assert registry.remove("amiga-01") is False
    assert registry.list_all() == []


def test_persistence_round_trip(logger, clock, tmp_path):
    path = tmp_path / "robots.json"
    first = RobotRegistry(logger, registry_file=str(path), clock=clock)
    first.register(make_record(battery_pct=0.42))

    saved = json.loads(path.read_text())
    assert saved[0]["robot_id"] == "amiga-01"

    # A fresh planner process restores the roster but trusts none of it until
    # each robot heartbeats again.
    second = RobotRegistry(logger, registry_file=str(path), clock=clock)
    record, liveness, age = second.list_all()[0]
    assert record.robot_id == "amiga-01"
    assert record.battery_pct == 0.42
    assert liveness == "unknown"
    assert age is None
    assert second.eligible() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
