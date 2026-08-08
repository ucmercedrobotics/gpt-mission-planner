"""Sending each robot its own behavior tree.

One connection per robot, to that robot's registered BT endpoint. Every failure
is reported per robot rather than swallowed: previously a refused connection
either killed the CLI loop outright or was downgraded to a log warning while
the web UI still reported success.
"""

import copy
import logging
from typing import Any, Optional

from network_interface import NetworkInterface

from fleet.models import DispatchOutcome, DispatchResult, RobotPlan
from fleet.registry import RobotRecord


def scoped_tree_points(
    tree_points: Optional[dict[str, Any]],
    mission_id: str,
    plan: RobotPlan,
    all_plans: list[RobotPlan],
) -> Optional[dict[str, Any]]:
    """Attach this robot's share to the tree payload.

    The full orchard map still goes to every robot -- it needs the geometry
    either way -- but `mission` names what this robot owns and, advisorily,
    what the others own so it can stay out of their aisles. Purely additive, so
    a receiver that ignores the key behaves exactly as before.
    """
    if tree_points is None:
        return None

    payload = copy.deepcopy(tree_points)
    payload["mission"] = {
        "mission_id": mission_id,
        "robot_id": plan.robot_id,
        "assigned_tree_indices": list(plan.assignment.assigned_tree_indices),
        "assigned_aisles": list(plan.assignment.assigned_aisles),
        "fleet": [
            {
                "robot_id": other.robot_id,
                "assigned_aisles": list(other.assignment.assigned_aisles),
                "assigned_tree_indices": list(other.assignment.assigned_tree_indices),
            }
            for other in all_plans
            if other.robot_id != plan.robot_id
        ],
    }
    return payload


class FleetDispatcher:
    def __init__(
        self,
        logger: logging.Logger,
        timeout: float = 5.0,
        ack_timeout: float = 10.0,
    ) -> None:
        self.logger = logger
        self.timeout = timeout
        self.ack_timeout = ack_timeout

    def dispatch_one(
        self,
        robot: RobotRecord,
        plan: RobotPlan,
        mission_id: str,
        tree_points: Optional[dict[str, Any]] = None,
    ) -> DispatchResult:
        host = robot.host or "127.0.0.1"
        nic = NetworkInterface(
            self.logger,
            host,
            robot.port,
            timeout=self.timeout,
            ack_timeout=self.ack_timeout,
        )
        try:
            nic.init_socket()
            nic.send_file(plan.xml_path, tree_points)
            ack = nic.recv_ack()
        except OSError as exc:
            self.logger.error(
                "Could not deliver mission to %s at %s:%d: %s",
                robot.robot_id,
                host,
                robot.port,
                exc,
            )
            return DispatchResult(
                robot_id=robot.robot_id,
                outcome=DispatchOutcome.UNREACHABLE,
                host=host,
                port=robot.port,
                mission_id=mission_id,
                error=str(exc),
            )
        finally:
            nic.close_socket()

        if ack is None:
            self.logger.warning(
                "%s took the mission but did not acknowledge it.", robot.robot_id
            )
            return DispatchResult(
                robot_id=robot.robot_id,
                outcome=DispatchOutcome.UNACKNOWLEDGED,
                host=host,
                port=robot.port,
                mission_id=mission_id,
            )

        if not ack.get("accepted", False):
            error = (
                ack.get("error")
                or "Robot rejected the mission without giving a reason."
            )
            self.logger.error("%s rejected the mission: %s", robot.robot_id, error)
            return DispatchResult(
                robot_id=robot.robot_id,
                outcome=DispatchOutcome.REJECTED,
                host=host,
                port=robot.port,
                mission_id=mission_id,
                error=error,
                ack=ack,
            )

        self.logger.info(
            "%s accepted mission %s (%s:%d)",
            robot.robot_id,
            mission_id,
            host,
            robot.port,
        )
        return DispatchResult(
            robot_id=robot.robot_id,
            outcome=DispatchOutcome.DISPATCHED,
            host=host,
            port=robot.port,
            mission_id=mission_id,
            ack=ack,
        )

    def dispatch(
        self,
        plans: list[RobotPlan],
        robots_by_id: dict[str, RobotRecord],
        mission_id: str,
        tree_points: Optional[dict[str, Any]] = None,
    ) -> list[DispatchResult]:
        results: list[DispatchResult] = []
        for plan in plans:
            robot = robots_by_id.get(plan.robot_id)
            if robot is None:
                results.append(
                    DispatchResult(
                        robot_id=plan.robot_id,
                        outcome=DispatchOutcome.UNREACHABLE,
                        host="",
                        port=0,
                        mission_id=mission_id,
                        error="Robot dropped out of the roster before dispatch.",
                    )
                )
                continue
            payload = scoped_tree_points(tree_points, mission_id, plan, plans)
            results.append(self.dispatch_one(robot, plan, mission_id, payload))
        return results
