"""Behaviour tests for structured spec diffs."""

from __future__ import annotations

from typing import Any

from evalharness.core.spec_diff import diff_run_snapshots, spec_diff_data


def _spec(candidate_model: str = "stub-1") -> dict[str, Any]:
    return {
        "name": "spec",
        "seed": 7,
        "replicas": 1,
        "budget_usd": 1.0,
        "source": {
            "adapter": "inline",
            "options": {"cases": [{"case_id": "case-1", "prompt": "q"}]},
        },
        "candidates": [
            {"name": "cand", "adapter": "stub", "model": candidate_model}
        ],
        "metrics": [],
        "judges": [],
        "gates": [],
    }


def _current_prices(input_rate: float = 0.1) -> dict[str, Any]:
    return {
        "version": 2,
        "providers": {
            "p": {"m": {"input": input_rate, "output": 0.2, "tool": 0.0}}
        },
    }


def test_a_candidate_model_change_is_one_structured_change() -> None:
    diff = diff_run_snapshots(_spec("stub-1"), _spec("stub-2"), {}, {})

    assert diff.total == 1
    change = diff.changes[0]
    assert change.category == "candidates"
    assert change.path == "candidates[cand].model"
    assert change.before == "stub-1"
    assert change.after == "stub-2"
    assert "stub-1" in change.summary
    assert "stub-2" in change.summary
    assert diff.counts["candidates"] == 1


def test_identical_and_missing_snapshots_have_no_changes() -> None:
    identical = diff_run_snapshots(_spec(), _spec(), _current_prices(), _current_prices())
    assert identical.changes == ()
    assert identical.total == 0
    assert identical.headline == "No differences between the two snapshots."
    assert identical.counts == {
        "cases": 0,
        "candidates": 0,
        "judges": 0,
        "prices": 0,
        "run": 0,
        "gates": 0,
    }

    empty = diff_run_snapshots({}, {}, {}, {})
    assert empty.changes == ()
    assert empty.total == 0


def test_case_prompts_are_added_removed_and_changed_in_stable_order() -> None:
    a = _spec()
    a["source"]["options"]["cases"] = [
        {"case_id": "case-1", "prompt": "old"},
        {"case_id": "case-2", "prompt": "gone"},
    ]
    b = _spec()
    b["source"]["options"]["cases"] = [
        {"case_id": "case-1", "prompt": "new"},
        {"case_id": "case-3", "prompt": "added"},
    ]

    diff = diff_run_snapshots(a, b, {}, {})

    assert [change.path for change in diff.changes] == [
        "source.options.cases[case-1].prompt",
        "source.options.cases[case-2].prompt",
        "source.options.cases[case-3].prompt",
    ]
    changed, removed, added = diff.changes
    assert (changed.before, changed.after) == ("old", "new")
    assert (removed.before, removed.after) == ("gone", None)
    assert (added.before, added.after) == (None, "added")
    assert diff.counts["cases"] == 3
    assert diff.headline == "3 changes: 3 cases."


def test_candidates_report_adapter_and_model_swaps_plus_adds_and_removes() -> None:
    a = _spec()
    a["candidates"] = [
        {"name": "cand", "adapter": "a1", "model": "m1"},
        {"name": "gone", "adapter": "x", "model": "y"},
    ]
    b = _spec()
    b["candidates"] = [
        {"name": "cand", "adapter": "a2", "model": "m2"},
        {"name": "new", "adapter": "z", "model": "w"},
    ]

    diff = diff_run_snapshots(a, b, {}, {})

    assert [change.path for change in diff.changes] == [
        "candidates[cand].adapter",
        "candidates[cand].model",
        "candidates[gone]",
        "candidates[new]",
    ]
    assert diff.changes[2].before == {"adapter": "x", "model": "y"}
    assert diff.changes[2].after is None
    assert diff.changes[3].before is None
    assert diff.changes[3].after == {"adapter": "z", "model": "w"}


def test_judge_declarations_and_metric_assignments_are_diffed() -> None:
    a = _spec()
    a["judges"] = [
        {"name": "j", "adapter": "a1", "model": "m1", "options": {"temperature": 0.1}},
        {"name": "gone", "adapter": "x", "model": "y", "options": {}},
    ]
    a["metrics"] = [{"key": "m", "adapter": "metric", "options": {"judge": "j"}}]
    b = _spec()
    b["judges"] = [
        {"name": "j", "adapter": "a2", "model": "m2", "options": {"temperature": 0.2}},
        {"name": "new", "adapter": "z", "model": "w", "options": {}},
    ]
    b["metrics"] = [{"key": "m", "adapter": "metric", "options": {}}]

    diff = diff_run_snapshots(a, b, {}, {})

    assert [change.path for change in diff.changes] == [
        "judges[gone]",
        "judges[j].adapter",
        "judges[j].model",
        "judges[j].options",
        "judges[new]",
        "metrics[m].options.judge",
    ]
    assignment = diff.changes[-1]
    assert (assignment.before, assignment.after) == ("j", None)
    assert "none" in assignment.summary


def test_price_rates_are_diffed_in_both_snapshot_shapes() -> None:
    a_prices = {
        "version": 2,
        "providers": {
            "p": {
                "m": {"input": 0.1, "output": 0.2, "tool": 0.0},
                "gone": {"input": 0.3, "output": 0.4},
            }
        },
    }
    b_prices = {
        "version": 2,
        "providers": {
            "p": {
                "m": {"input": 0.2, "output": 0.2},
                "new": {"input": 0.5, "output": 0.6},
            }
        },
    }

    diff = diff_run_snapshots(_spec(), _spec(), a_prices, b_prices)

    assert [change.path for change in diff.changes] == [
        "prices[p/gone]",
        "prices[p/m].input",
        "prices[p/new]",
    ]
    assert "0.1" in diff.changes[1].summary
    assert "0.2" in diff.changes[1].summary

    legacy = diff_run_snapshots(
        _spec(),
        _spec(),
        {"m": {"input": 0.1, "output": 0.2}},
        {"m": {"input": 0.1, "output": 0.3}},
    )
    assert legacy.total == 1
    assert legacy.changes[0].path == "prices[m].output"
    assert legacy.headline == "1 change: 1 price."


def test_run_knobs_and_gates_are_diffed() -> None:
    a = _spec()
    a["seed"] = 7
    a["replicas"] = 1
    a["budget_usd"] = 1.0
    a["gates"] = [
        {"metric": "exact_match", "min": 0.5},
        {"metric": "exact_match", "candidate": "cand", "min": 0.1},
        {"metric": "removed_gate", "min": 0.0},
    ]
    b = _spec()
    b["seed"] = 8
    b["replicas"] = 2
    b["budget_usd"] = None
    b["gates"] = [
        {"metric": "exact_match", "min": 0.9},
        {"metric": "exact_match", "candidate": "cand", "max": 0.2},
        {"metric": "added_gate", "min": 0.3},
    ]

    diff = diff_run_snapshots(a, b, {}, {})

    assert [change.path for change in diff.changes] == [
        "budget_usd",
        "replicas",
        "seed",
        "gates[added_gate]",
        "gates[exact_match@cand].max",
        "gates[exact_match@cand].min",
        "gates[exact_match].min",
        "gates[removed_gate]",
    ]
    assert diff.counts["run"] == 3
    assert diff.counts["gates"] == 5
    assert "no cap" in diff.changes[0].summary
    assert diff.changes[6].before == 0.5
    assert diff.changes[6].after == 0.9


def test_categories_and_paths_have_one_stable_order() -> None:
    a = _spec("m1")
    a["source"]["options"]["cases"] = [{"case_id": "case-1", "prompt": "old"}]
    a["judges"] = [{"name": "j", "adapter": "a", "model": "m1", "options": {}}]
    a["metrics"] = [{"key": "m", "adapter": "metric", "options": {"judge": "j"}}]
    a["gates"] = [{"metric": "m", "min": 0.1}]
    a["seed"] = 1
    b = _spec("m2")
    b["source"]["options"]["cases"] = [{"case_id": "case-1", "prompt": "new"}]
    b["judges"] = [{"name": "j", "adapter": "a", "model": "m2", "options": {}}]
    b["metrics"] = [{"key": "m", "adapter": "metric", "options": {"judge": "j"}}]
    b["gates"] = [{"metric": "m", "min": 0.2}]
    b["seed"] = 2

    diff = diff_run_snapshots(a, b, _current_prices(0.1), _current_prices(0.2))

    assert [change.category for change in diff.changes] == [
        "cases",
        "candidates",
        "judges",
        "prices",
        "run",
        "gates",
    ]
    assert diff.total == 6
    assert diff.counts == {
        "cases": 1,
        "candidates": 1,
        "judges": 1,
        "prices": 1,
        "run": 1,
        "gates": 1,
    }


def test_spec_diff_data_is_json_ready() -> None:
    diff = diff_run_snapshots(_spec("stub-1"), _spec("stub-2"), {}, {})

    assert spec_diff_data(diff) == {
        "headline": "1 change: 1 candidate.",
        "total": 1,
        "counts": {
            "cases": 0,
            "candidates": 1,
            "judges": 0,
            "prices": 0,
            "run": 0,
            "gates": 0,
        },
        "changes": [
            {
                "category": "candidates",
                "path": "candidates[cand].model",
                "before": "stub-1",
                "after": "stub-2",
                "summary": "candidate cand model: stub-1 -> stub-2",
            }
        ],
    }
