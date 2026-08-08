"""Splitting one mission across the robots that are online.

The LLM owns the decision -- it sees the roster, the action pool each robot
supports, and the orchard geometry, and returns a JSON assignment. What it is
*not* asked to do is arithmetic: `suggest_partition` precomputes a balanced
contiguous split and hands it over as a starting point, because "divide 100
trees over 3 robots" is exactly the kind of thing models get subtly wrong.
"""

import json
import logging
import re
from typing import Any, Optional

from pydantic import ValidationError

from context import load_template
from fleet.models import AllocationPlan, Assignment
from fleet.registry import RobotRecord

DEFAULT_ALLOCATION_RETRIES = 3


def orchard_summary(tree_points: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Compact geometry description for the allocation prompt.

    Deliberately excludes the per-tree coordinates -- the full map is already in
    the system context, and repeating ~30KB of it in a user turn would undo the
    schema dedupe savings.
    """
    if not tree_points:
        return None
    trees = tree_points.get("trees") or []
    if not trees:
        return None

    rows = max((int(t.get("row", 0)) for t in trees), default=0)
    cols = max((int(t.get("col", 0)) for t in trees), default=0)
    axis = str(tree_points.get("traversal_axis", "row"))
    # Aisles are the driving lanes *between* tree lines, 1-indexed, so there is
    # one fewer of them than there are lines along the traversal axis.
    lanes = tree_points.get("aisle_to_entrance_indices") or {}

    return {
        "rows": rows,
        "cols": cols,
        "tree_count": len(trees),
        "traversal_axis": axis,
        "line_key": _line_key(axis),
        "line_count": rows if _line_key(axis) == "row" else cols,
        "aisle_count": len(lanes),
        "tree_index_min": min(int(t.get("tree_index", 0)) for t in trees),
        "tree_index_max": max(int(t.get("tree_index", 0)) for t in trees),
    }


def _line_key(traversal_axis: str) -> str:
    """Which tree coordinate the driving lanes run alongside.

    Mirrors `_generate_lane_waypoints`: ROW traversal makes lanes between rows,
    COLUMN traversal makes lanes between columns.
    """
    return "row" if str(traversal_axis).strip().lower() == "row" else "col"


def suggest_partition(
    tree_points: Optional[dict[str, Any]], robot_count: int
) -> list[dict[str, Any]]:
    """Balanced contiguous blocks of whole tree lines, one per robot.

    Whole lines only: splitting a line between two robots would put both of
    them in the same aisle. The aisles reported per block are the block's
    interior lanes, so blocks never claim a shared boundary lane.
    """
    if robot_count <= 0:
        return []
    if not tree_points or not tree_points.get("trees"):
        return []

    trees = tree_points["trees"]
    key = _line_key(str(tree_points.get("traversal_axis", "row")))

    by_line: dict[int, list[int]] = {}
    for tree in trees:
        line = int(tree.get(key, 0))
        by_line.setdefault(line, []).append(int(tree.get("tree_index", 0)))
    lines = sorted(by_line)
    if not lines:
        return []

    total = sum(len(by_line[line]) for line in lines)
    blocks: list[list[int]] = []
    remaining_robots = robot_count
    remaining_trees = total
    current: list[int] = []
    current_count = 0

    for index, line in enumerate(lines):
        current.append(line)
        current_count += len(by_line[line])
        lines_left = len(lines) - index - 1
        # Close the block once it has its fair share, but never leave a later
        # robot with no lines at all.
        if remaining_robots > 1:
            target = remaining_trees / remaining_robots
            if current_count >= target or lines_left < remaining_robots - 1:
                blocks.append(current)
                remaining_trees -= current_count
                remaining_robots -= 1
                current, current_count = [], 0
    if current:
        blocks.append(current)

    partition = []
    for block in blocks:
        tree_indices = sorted(i for line in block for i in by_line[line])
        # Interior lanes only: lane k runs between line k and line k+1.
        aisles = [line for line in block[:-1]] if len(block) > 1 else []
        partition.append(
            {
                "line_key": key,
                "line_range": [block[0], block[-1]],
                "lines": block,
                "aisles": aisles,
                "tree_count": len(tree_indices),
                "tree_index_range": (
                    [tree_indices[0], tree_indices[-1]] if tree_indices else []
                ),
                "tree_indices": tree_indices,
            }
        )
    return partition


def robot_roster(robots: list[RobotRecord]) -> list[dict[str, Any]]:
    """Roster rows for the prompt: identity and capability, not addressing.

    Host/port are deliberately withheld -- the model has no use for them and
    routing keys on robot_id anyway.
    """
    return [
        {
            "robot_id": r.robot_id,
            "name": r.name or r.robot_id,
            "schema": r.schema_name,
            "actions": list(r.actions),
            "status": r.status,
            "battery_pct": r.battery_pct,
            "position": r.position,
        }
        for r in robots
    ]


def _extract_json(text: Optional[str]) -> dict[str, Any]:
    """Pull a JSON object out of a model response, fenced or bare."""
    if not text:
        raise ValueError("Empty response from the allocation model.")

    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        # Fall back to the outermost braces, which survives a model that wrote
        # a sentence before or after the object.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("No JSON object found in the allocation response.")
        parsed = json.loads(candidate[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("Allocation response was not a JSON object.")
    return parsed


class FleetAllocator:
    def __init__(
        self, logger: logging.Logger, max_retries: int = DEFAULT_ALLOCATION_RETRIES
    ) -> None:
        self.logger = logger
        self.max_retries = max_retries

    def allocate(
        self,
        gpt: Any,
        mission_text: str,
        robots: list[RobotRecord],
        tree_points: Optional[dict[str, Any]] = None,
    ) -> AllocationPlan:
        """Ask the model to divide `mission_text` across `robots`.

        `gpt` is the shared LLMInterface, so this runs as a turn on the session
        that already holds the schemas and orchard context.
        """
        if not robots:
            return AllocationPlan(
                assignments=[],
                unassigned_reason="No robots are online and eligible for work.",
            )

        if len(robots) == 1:
            # Nothing to divide. Skipping the round trip is both faster and
            # strictly more reliable than asking the model to agree.
            only = robots[0]
            self.logger.info(
                "Single eligible robot (%s); assigning the whole mission to it.",
                only.robot_id,
            )
            return AllocationPlan(
                assignments=[
                    Assignment(
                        robot_id=only.robot_id,
                        sub_mission=mission_text,
                        rationale="Only one robot is online, so it takes the whole mission.",
                    )
                ]
            )

        roster = robot_roster(robots)
        summary = orchard_summary(tree_points)
        partition = suggest_partition(tree_points, len(robots))
        valid_ids = {r.robot_id for r in robots}

        prompt = load_template(
            "fleet_allocation",
            {
                "mission": mission_text,
                "robots": roster,
                "orchard": summary,
                "suggested_partition": partition,
                "robot_count": len(robots),
            },
        )

        last_error = "Allocation was never attempted."
        for attempt in range(self.max_retries):
            response = gpt.ask_gpt(prompt, True)
            self.logger.debug("Allocation response: %s", response)
            try:
                plan = AllocationPlan.model_validate(_extract_json(response))
                self._check_plan(plan, valid_ids)
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)
                self.logger.warning(
                    "Allocation attempt %d/%d rejected: %s",
                    attempt + 1,
                    self.max_retries,
                    last_error,
                )
                # Same feedback loop the XML path uses: hand the error back and
                # let the model correct itself.
                prompt = (
                    "Your allocation was rejected: "
                    f"{last_error}\n\nReturn only corrected JSON in the required format."
                )
                continue

            for assignment in plan.assignments:
                self.logger.info(
                    "Allocated to %s: %s", assignment.robot_id, assignment.sub_mission
                )
            return plan

        raise RuntimeError(
            f"Could not get a usable allocation after {self.max_retries} attempts: {last_error}"
        )

    def _check_plan(self, plan: AllocationPlan, valid_ids: set[str]) -> None:
        if not plan.assignments:
            if plan.unassigned_reason:
                return
            raise ValueError(
                "No assignments were produced and no unassigned_reason was given."
            )

        seen_robots: set[str] = set()
        claimed_aisles: dict[int, str] = {}
        for assignment in plan.assignments:
            if assignment.robot_id not in valid_ids:
                raise ValueError(
                    f"Unknown robot_id '{assignment.robot_id}'. "
                    f"Valid ids: {', '.join(sorted(valid_ids))}."
                )
            if assignment.robot_id in seen_robots:
                raise ValueError(
                    f"Robot '{assignment.robot_id}' appears in more than one assignment; "
                    "give each robot exactly one."
                )
            seen_robots.add(assignment.robot_id)

            for aisle in assignment.assigned_aisles:
                owner = claimed_aisles.get(aisle)
                if owner is not None:
                    raise ValueError(
                        f"Aisle {aisle} is assigned to both '{owner}' and "
                        f"'{assignment.robot_id}'. Two robots must never share an aisle."
                    )
                claimed_aisles[aisle] = assignment.robot_id
