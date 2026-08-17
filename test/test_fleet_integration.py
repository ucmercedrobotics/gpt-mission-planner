"""End-to-end check of the robot contract.

Runs the real discovery service and the real `scripts/mock_robot.py` as a
subprocess, then dispatches to it. This is the test that would catch a drift
between what the planner expects and what the documented reference robot does.

Skipped automatically if fastapi/uvicorn are unavailable (they are present in
the project container).
"""

import json
import logging
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

import urllib.request

from fleet.dispatcher import FleetDispatcher
from fleet.models import Assignment, RobotPlan
from fleet.registry import RobotRegistry
from fleet.service import start_fleet_service

MOCK_ROBOT = REPO_ROOT / "scripts" / "mock_robot.py"

BEHAVIOR_TREE = """<root BTCPP_format="2" schema_location="schemas/amiga_btcpp.xsd">
  <Mission>Sample every tree in rows 1-2.</Mission>
  <BehaviorTree ID="Main"><Sequence/></BehaviorTree>
</root>
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, timeout=15.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def logger():
    return logging.getLogger("test-integration")


@pytest.fixture
def discovery(logger):
    registry = RobotRegistry(logger, ttl_s=15.0, stale_after_s=8.0, registry_file=None)
    port = free_port()
    start_fleet_service(registry, logger, host="127.0.0.1", port=port)

    def up():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                return True
        except Exception:
            return False

    assert wait_for(up, timeout=10), "discovery service never came up"
    return registry, port


class MockRobot:
    def __init__(self, robot_id, discovery_port, extra_args=()):
        self.robot_id = robot_id
        self.bt_port = free_port()
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK_ROBOT),
                "--robot-id",
                robot_id,
                "--bt-port",
                str(self.bt_port),
                "--planner",
                f"http://127.0.0.1:{discovery_port}",
                "--heartbeat-interval",
                "1",
                *extra_args,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


@pytest.fixture
def robots(discovery):
    started = []

    def _start(robot_id, *extra_args):
        robot = MockRobot(robot_id, discovery[1], extra_args)
        started.append(robot)
        return robot

    yield _start
    for robot in started:
        robot.stop()


def make_plan(robot_id, tmp_path, sub_mission="Sample every tree in rows 1-2."):
    xml_path = tmp_path / f"{robot_id}.xml"
    xml_path.write_text(BEHAVIOR_TREE)
    return RobotPlan(
        robot_id=robot_id,
        xml_path=str(xml_path),
        matched_schema="schemas/amiga_btcpp.xsd",
        assignment=Assignment(
            robot_id=robot_id,
            sub_mission=sub_mission,
            assigned_aisles=[1, 2],
            assigned_tree_indices=[1, 2, 3],
        ),
    )


def test_robot_registers_and_is_eligible(discovery, robots):
    registry, _ = discovery
    robots("amiga-01")

    assert wait_for(lambda: len(registry.eligible()) == 1), "robot never registered"
    record = registry.eligible()[0]
    assert record.robot_id == "amiga-01"
    assert record.schema_name == "amiga_btcpp"
    # Registered with host=null, so the planner resolved it from the request.
    assert record.host == "127.0.0.1"


def test_heartbeat_keeps_robot_online(discovery, robots):
    registry, _ = discovery
    robots("amiga-01")
    assert wait_for(lambda: len(registry.eligible()) == 1)

    time.sleep(3)  # longer than one heartbeat interval
    record, liveness, age = registry.list_all()[0]
    assert liveness == "online"
    assert age is not None and age < 3


def test_dispatch_round_trip(discovery, robots, logger, tmp_path):
    """The whole loop: register, dispatch, decode, ack."""
    registry, _ = discovery
    robots("amiga-01")
    assert wait_for(lambda: len(registry.eligible()) == 1)

    plan = make_plan("amiga-01", tmp_path)
    tree_points = {
        "traversal_axis": "row",
        "trees": [{"tree_index": 1, "row": 1, "col": 1, "lat": 37.0, "lon": -120.0}],
        "aisle_entrances": [],
        "aisle_to_entrance_indices": {},
    }
    results = FleetDispatcher(logger).dispatch(
        [plan], {r.robot_id: r for r in registry.eligible()}, "m-1", tree_points
    )

    assert len(results) == 1
    assert results[0].outcome.value == "dispatched"
    assert results[0].ack["accepted"] is True
    assert results[0].ack["mission_id"] == "m-1"


def test_robot_goes_busy_then_idle(discovery, robots, logger, tmp_path):
    registry, _ = discovery
    robots("amiga-01", "--busy-seconds", "3")
    assert wait_for(lambda: len(registry.eligible()) == 1)

    plan = make_plan("amiga-01", tmp_path)
    FleetDispatcher(logger).dispatch(
        [plan], {r.robot_id: r for r in registry.eligible()}, "m-2", None
    )

    # Busy robots must not be handed a second mission.
    assert wait_for(lambda: registry.get("amiga-01").status == "busy", timeout=5)
    assert registry.eligible() == []
    assert wait_for(lambda: registry.get("amiga-01").status == "idle", timeout=10)
    assert len(registry.eligible()) == 1


def test_rejection_is_reported(discovery, robots, logger, tmp_path):
    registry, _ = discovery
    robots("amiga-01", "--reject", "battery too low")
    assert wait_for(lambda: len(registry.eligible()) == 1)

    plan = make_plan("amiga-01", tmp_path)
    results = FleetDispatcher(logger).dispatch(
        [plan], {r.robot_id: r for r in registry.eligible()}, "m-3", None
    )
    assert results[0].outcome.value == "rejected"
    assert results[0].error == "battery too low"


def test_receiver_without_ack_is_not_a_failure(discovery, robots, logger, tmp_path):
    """A robot predating the ack frame must still be dispatchable."""
    registry, _ = discovery
    robots("amiga-01", "--no-ack")
    assert wait_for(lambda: len(registry.eligible()) == 1)

    plan = make_plan("amiga-01", tmp_path)
    results = FleetDispatcher(logger).dispatch(
        [plan], {r.robot_id: r for r in registry.eligible()}, "m-4", None
    )
    assert results[0].outcome.value == "unacknowledged"
    assert results[0].ok is True


def test_two_robots_get_their_own_trees(discovery, robots, logger, tmp_path):
    """Same schema on both, so routing can only come from robot_id."""
    registry, _ = discovery
    robots("amiga-01")
    robots("amiga-02")
    assert wait_for(
        lambda: len(registry.eligible()) == 2
    ), "both robots never registered"

    by_id = {r.robot_id: r for r in registry.eligible()}
    assert by_id["amiga-01"].port != by_id["amiga-02"].port

    plans = [
        make_plan("amiga-01", tmp_path, "Sample every tree in rows 1-2."),
        make_plan("amiga-02", tmp_path, "Sample every tree in rows 3-4."),
    ]
    results = FleetDispatcher(logger).dispatch(plans, by_id, "m-5", None)

    assert {r.robot_id for r in results} == {"amiga-01", "amiga-02"}
    assert all(r.outcome.value == "dispatched" for r in results)
    # Each ack came from the robot the plan was addressed to.
    for result in results:
        assert result.ack["robot_id"] == result.robot_id
        assert result.port == by_id[result.robot_id].port


def test_unknown_schema_robot_is_listed_but_not_eligible(discovery, robots):
    registry, _ = discovery
    robots("weird-01", "--schema", "not_a_real_platform")

    assert wait_for(lambda: len(registry.list_all()) == 1)
    assert registry.eligible() == []
    assert registry.get("weird-01").schema_known is False


def test_deregister_on_shutdown(discovery, robots):
    registry, _ = discovery
    robot = robots("amiga-01")
    assert wait_for(lambda: len(registry.eligible()) == 1)

    robot.stop()
    assert wait_for(
        lambda: len(registry.list_all()) == 0, timeout=10
    ), "robot did not deregister on shutdown"


def test_roster_endpoint_shape(discovery, robots):
    """The shape the web UI renders."""
    _, port = discovery
    robots("amiga-01")

    def has_robot():
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/robots", timeout=2
        ) as response:
            return len(json.loads(response.read())["robots"]) == 1

    assert wait_for(has_robot)

    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/robots", timeout=2
    ) as response:
        robot = json.loads(response.read())["robots"][0]

    assert robot["robot_id"] == "amiga-01"
    assert robot["schema"] == "amiga_btcpp"  # serialized under its alias
    assert robot["liveness"] == "online"
    assert robot["eligible"] is True
    assert robot["schema_known"] is True
    assert robot["last_seen_s_ago"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
