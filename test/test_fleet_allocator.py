"""Tests for splitting a mission across robots."""

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from fleet.allocator import (
    FleetAllocator,
    _extract_json,
    orchard_summary,
    suggest_partition,
)
from fleet.models import AllocationPlan
from fleet.registry import RobotRecord


def make_tree_points(rows=4, cols=6, axis="row"):
    trees = []
    index = 1
    for row in range(1, rows + 1):
        for col in range(1, cols + 1):
            trees.append(
                {
                    "tree_index": index,
                    "row": row,
                    "col": col,
                    "lat": 37.0 + row / 1000,
                    "lon": -120.0 + col / 1000,
                }
            )
            index += 1
    line_count = rows if axis == "row" else cols
    return {
        "traversal_axis": axis,
        "trees": trees,
        "aisle_entrances": [],
        "aisle_to_entrance_indices": {str(i): [] for i in range(1, line_count)},
    }


def make_robot(robot_id, schema="amiga_btcpp", actions=None):
    return RobotRecord(
        robot_id=robot_id,
        schema_name=schema,
        port=12346,
        host="10.0.0.5",
        actions=actions or [],
    )


class FakeGPT:
    """Stands in for LLMInterface, returning canned responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def ask_gpt(self, prompt, add_context=False):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("ask_gpt called more times than expected")
        return self.responses.pop(0)


class ExplodingGPT:
    def ask_gpt(self, prompt, add_context=False):
        raise AssertionError("the model should not have been consulted")


@pytest.fixture
def logger():
    return logging.getLogger("test-allocator")


# --- geometry -------------------------------------------------------------


def test_orchard_summary(logger):
    summary = orchard_summary(make_tree_points(rows=8, cols=18, axis="column"))
    assert summary["rows"] == 8
    assert summary["cols"] == 18
    assert summary["tree_count"] == 144
    assert summary["line_key"] == "col"
    assert summary["line_count"] == 18
    # Aisles run between lines, so there is one fewer.
    assert summary["aisle_count"] == 17
    assert summary["tree_index_min"] == 1
    assert summary["tree_index_max"] == 144


def test_partition_splits_evenly_in_two(logger):
    partition = suggest_partition(make_tree_points(rows=4, cols=6, axis="row"), 2)
    assert len(partition) == 2
    assert [block["tree_count"] for block in partition] == [12, 12]
    assert partition[0]["line_range"] == [1, 2]
    assert partition[1]["line_range"] == [3, 4]


def test_partition_covers_every_tree_exactly_once():
    tree_points = make_tree_points(rows=8, cols=18, axis="column")
    for robot_count in (1, 2, 3, 4, 5):
        partition = suggest_partition(tree_points, robot_count)
        seen = [i for block in partition for i in block["tree_indices"]]
        assert sorted(seen) == list(range(1, 145)), robot_count
        assert len(seen) == len(set(seen)), robot_count


def test_partition_blocks_never_share_an_aisle():
    partition = suggest_partition(make_tree_points(rows=9, cols=4, axis="row"), 3)
    claimed = [aisle for block in partition for aisle in block["aisles"]]
    assert len(claimed) == len(set(claimed))


def test_partition_keeps_lines_whole():
    """Two robots in the same row would be two robots in the same aisle."""
    partition = suggest_partition(make_tree_points(rows=5, cols=4, axis="row"), 2)
    all_lines = [line for block in partition for line in block["lines"]]
    assert sorted(all_lines) == [1, 2, 3, 4, 5]
    assert len(all_lines) == len(set(all_lines))


def test_partition_handles_more_robots_than_lines():
    partition = suggest_partition(make_tree_points(rows=2, cols=3, axis="row"), 5)
    # Cannot give five robots two lines; produce what is possible, no empties.
    assert 0 < len(partition) <= 2
    assert all(block["tree_count"] > 0 for block in partition)


def test_partition_without_orchard_is_empty():
    assert suggest_partition(None, 2) == []
    assert suggest_partition({"trees": []}, 2) == []


# --- JSON extraction ------------------------------------------------------


def test_extract_json_bare():
    assert _extract_json('{"assignments": []}') == {"assignments": []}


def test_extract_json_fenced():
    text = 'Here you go:\n```json\n{"assignments": []}\n```\n'
    assert _extract_json(text) == {"assignments": []}


def test_extract_json_with_surrounding_prose():
    text = 'Sure! {"assignments": [], "unassigned_reason": null} Hope that helps.'
    assert _extract_json(text)["assignments"] == []


def test_extract_json_rejects_garbage():
    with pytest.raises(ValueError):
        _extract_json("no json here at all")
    with pytest.raises(ValueError):
        _extract_json("")


# --- allocation -----------------------------------------------------------


def test_single_robot_skips_the_model(logger):
    """One robot gets the whole mission without a round trip."""
    allocator = FleetAllocator(logger)
    plan = allocator.allocate(
        ExplodingGPT(), "Sample 100 trees.", [make_robot("amiga-01")], None
    )
    assert len(plan.assignments) == 1
    assert plan.assignments[0].robot_id == "amiga-01"
    assert plan.assignments[0].sub_mission == "Sample 100 trees."


def test_no_robots_returns_a_reason(logger):
    plan = FleetAllocator(logger).allocate(ExplodingGPT(), "Sample trees.", [], None)
    assert plan.assignments == []
    assert "No robots" in plan.unassigned_reason


def test_two_robots_are_allocated_from_model_output(logger):
    response = json.dumps(
        {
            "assignments": [
                {
                    "robot_id": "amiga-01",
                    "sub_mission": "Sample every tree in rows 1-2.",
                    "assigned_aisles": [1],
                    "assigned_tree_indices": list(range(1, 13)),
                },
                {
                    "robot_id": "amiga-02",
                    "sub_mission": "Sample every tree in rows 3-4.",
                    "assigned_aisles": [3],
                    "assigned_tree_indices": list(range(13, 25)),
                },
            ],
            "unassigned_reason": None,
        }
    )
    gpt = FakeGPT([response])
    plan = FleetAllocator(logger).allocate(
        gpt,
        "Sample all 24 trees.",
        [make_robot("amiga-01"), make_robot("amiga-02")],
        make_tree_points(),
    )
    assert [a.robot_id for a in plan.assignments] == ["amiga-01", "amiga-02"]
    # The precomputed split is offered to the model rather than left to it.
    assert "SUGGESTED BALANCED SPLIT" in gpt.prompts[0]
    assert "amiga-01" in gpt.prompts[0] and "amiga-02" in gpt.prompts[0]


def test_unknown_robot_id_is_rejected_then_retried(logger):
    bad = json.dumps(
        {"assignments": [{"robot_id": "ghost-99", "sub_mission": "Do something."}]}
    )
    good = json.dumps(
        {
            "assignments": [
                {"robot_id": "amiga-01", "sub_mission": "Rows 1-2."},
                {"robot_id": "amiga-02", "sub_mission": "Rows 3-4."},
            ]
        }
    )
    gpt = FakeGPT([bad, good])
    plan = FleetAllocator(logger).allocate(
        gpt, "Sample trees.", [make_robot("amiga-01"), make_robot("amiga-02")], None
    )
    assert [a.robot_id for a in plan.assignments] == ["amiga-01", "amiga-02"]
    assert "ghost-99" in gpt.prompts[1]


def test_duplicate_robot_is_rejected(logger):
    allocator = FleetAllocator(logger)
    plan = AllocationPlan.model_validate(
        {
            "assignments": [
                {"robot_id": "amiga-01", "sub_mission": "A"},
                {"robot_id": "amiga-01", "sub_mission": "B"},
            ]
        }
    )
    with pytest.raises(ValueError, match="more than one assignment"):
        allocator._check_plan(plan, {"amiga-01", "amiga-02"})


def test_shared_aisle_is_rejected(logger):
    allocator = FleetAllocator(logger)
    plan = AllocationPlan.model_validate(
        {
            "assignments": [
                {"robot_id": "amiga-01", "sub_mission": "A", "assigned_aisles": [1, 2]},
                {"robot_id": "amiga-02", "sub_mission": "B", "assigned_aisles": [2, 3]},
            ]
        }
    )
    with pytest.raises(ValueError, match="never share an aisle"):
        allocator._check_plan(plan, {"amiga-01", "amiga-02"})


def test_empty_allocation_needs_a_reason(logger):
    allocator = FleetAllocator(logger)
    with pytest.raises(ValueError, match="unassigned_reason"):
        allocator._check_plan(AllocationPlan(), {"amiga-01"})
    # With a reason it is a legitimate outcome.
    allocator._check_plan(
        AllocationPlan(unassigned_reason="No robot has a manipulator."), {"amiga-01"}
    )


def test_gives_up_after_max_retries(logger):
    gpt = FakeGPT(["not json", "still not json"])
    allocator = FleetAllocator(logger, max_retries=2)
    with pytest.raises(RuntimeError, match="usable allocation"):
        allocator.allocate(
            gpt, "Sample trees.", [make_robot("a"), make_robot("b")], None
        )


def test_mixed_schemas_reach_the_prompt(logger):
    response = json.dumps(
        {
            "assignments": [
                {"robot_id": "amiga-01", "sub_mission": "Sample leaves."},
                {"robot_id": "husky-01", "sub_mission": "Take CO2 readings."},
            ]
        }
    )
    gpt = FakeGPT([response])
    robots = [
        make_robot("amiga-01", "amiga_btcpp", ["SampleLeaf"]),
        make_robot("husky-01", "clearpath_husky", ["takeCO2Reading"]),
    ]
    plan = FleetAllocator(logger).allocate(gpt, "Sample and measure.", robots, None)
    assert len(plan.assignments) == 2
    assert "amiga_btcpp" in gpt.prompts[0]
    assert "clearpath_husky" in gpt.prompts[0]
    assert "SampleLeaf" in gpt.prompts[0]
    assert "takeCO2Reading" in gpt.prompts[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
