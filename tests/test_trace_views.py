"""The trace view model: one case, every candidate, every check."""

from __future__ import annotations

from typing import Any

import pytest

from evalharness.api.trace_views import build_trace
from evalharness.core.models import CellRecord, Check, RunRecord
from evalharness.read_model import RunRead


def _run(snapshot: dict[str, Any], *, status: str = "ok") -> RunRecord:
    return RunRecord(
        run_id="run-1",
        spec_snapshot=snapshot,
        status=status,
        n_cells=2,
    )


def _cell(
    candidate: str,
    case_id: str = "case-1",
    *,
    replica: int = 0,
    answer: str = "42",
    status: str = "ok",
    variant: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
) -> CellRecord:
    return CellRecord(
        cell_id=f"{case_id}-{candidate}-{replica}",
        run_id="run-1",
        candidate=candidate,
        case_id=case_id,
        replica=replica,
        answer=answer,
        status=status,
        variant=variant or {},
        output=output or {},
        wall_ms=7,
        prompt_tokens=11,
        completion_tokens=3,
        cost_usd=0.000002,
        usage_source="provider",
        provider="stub",
    )


def _read(
    run: RunRecord,
    cells: list[CellRecord],
    checks: dict[str, list[Check]] | None = None,
) -> RunRead:
    return RunRead(
        run=run,
        cells=cells,
        checks_by_cell=checks or {},
        aggregates=[],
        gates=[],
        events=[],
    )


def _full_snapshot() -> dict[str, Any]:
    return {
        "source": {
            "options": {
                "cases": [{"case_id": "case-1", "prompt": "What is 6 times 7?"}]
            }
        },
        "candidates": [
            {"name": "strict", "model": "snapshot-strict"},
            {"name": "explained", "model": "snapshot-explained"},
        ],
    }


def test_trace_keeps_snapshot_order_prompts_models_and_every_check() -> None:
    strict = _cell(
        "strict",
        replica=1,
        answer="42",
        variant={
            "prompt_override": "Answer with digits only.",
            "prompt_variant": "concise",
            "context": [
                {"chunk_id": "c1", "text": "policy", "path": "docs/policy.md"},
                {"chunk_id": "c2", "text": "no path"},
                "not a chunk",
            ],
        },
        output={"model": "observed-strict"},
    )
    strict_zero = _cell("strict", replica=0, answer="42")
    explained = _cell(
        "explained",
        answer="42 because 6 times 7 is 42",
        status="error",
        output={"error": "transport failed"},
    )
    checks = {
        strict.cell_id: [
            Check(metric="exact_match", passed=True, score=1.0, reason="match", evidence={"actual": "42"}),
        ],
        strict_zero.cell_id: [
            Check(metric="exact_match", passed=True, score=1.0, reason="match", evidence={}),
        ],
        explained.cell_id: [
            Check(metric="exact_match", passed=False, score=0.0, reason="expected 42", evidence={}),
        ],
    }
    trace = build_trace(
        _read(_run(_full_snapshot()), [strict, explained, strict_zero], checks)
    )

    assert trace.has_cells is True
    assert trace.cells_recorded == 3
    assert [candidate.candidate for candidate in trace.candidates] == [
        "strict",
        "explained",
    ]
    assert trace.candidates[0].checks_passed == 2
    assert trace.candidates[0].checks_total == 2
    assert trace.candidates[0].errored == 0
    assert trace.candidates[1].checks_passed == 0
    assert trace.candidates[1].checks_total == 1
    assert trace.candidates[1].errored == 1

    case = trace.cases[0]
    assert case.case_id == "case-1"
    assert case.prompt == "What is 6 times 7?"
    assert case.candidate_count == 2
    assert case.answers_match is False
    assert case.diff_left == "strict"
    assert case.diff_right == "explained"
    assert {kind for kind, _ in case.diff_segments} == {"same", "add"}

    strict_column, explained_column = case.columns
    assert [cell.replica for cell in strict_column.cells] == [0, 1]
    strict_cell = strict_column.cells[1]
    assert strict_cell.effective_prompt == "Answer with digits only."
    assert strict_cell.prompt_variant == "concise"
    assert strict_cell.context_name == "inline"
    assert [chunk.chunk_id for chunk in strict_cell.context_chunks] == ["c1", "c2"]
    assert strict_cell.context_chunks[0].path == "docs/policy.md"
    assert strict_cell.context_chunks[1].path is None
    assert strict_cell.model == "observed-strict"
    assert strict_cell.error is None
    assert strict_cell.checks[0].metric == "exact_match"
    assert strict_cell.checks[0].evidence == {"actual": "42"}
    assert strict_cell.artifact_url == "/runs/run-1/cells/case-1-strict-1/artifact"

    explained_cell = explained_column.cells[0]
    assert explained_cell.effective_prompt == "What is 6 times 7?"
    assert explained_cell.context_name == "inline"
    assert explained_cell.context_chunks == ()
    assert explained_cell.model == "snapshot-explained"
    assert explained_cell.error == "transport failed"


def test_identical_chip_requires_an_answer_from_every_candidate() -> None:
    trace = build_trace(
        _read(
            _run(_full_snapshot()),
            [
                _cell("strict", answer="42"),
                _cell("explained", answer="42"),
                _cell("failing", answer="", status="error"),
            ],
        )
    )
    case = trace.cases[0]
    assert case.candidate_count == 3
    assert case.answers_match is False


@pytest.mark.parametrize(
    ("left", "right", "expected_kinds"),
    [
        ("a b", "a", {"same", "del"}),
        ("a", "a b", {"same", "add"}),
        ("a b", "a x", {"same", "del", "add"}),
        ("a b", "a b", {"same"}),
    ],
)
def test_word_diff_covers_every_opcode(
    left: str, right: str, expected_kinds: set[str]
) -> None:
    trace = build_trace(
        _read(
            _run(_full_snapshot()),
            [_cell("strict", answer=left), _cell("explained", answer=right)],
        )
    )
    assert {kind for kind, _ in trace.cases[0].diff_segments} == expected_kinds


def test_trace_falls_back_to_stored_cells_when_the_snapshot_is_empty() -> None:
    trace = build_trace(_read(_run({}), [_cell("only")]))
    assert trace.has_cells is True
    assert trace.cases[0].case_id == "case-1"
    assert trace.cases[0].prompt == ""
    assert trace.cases[0].candidate_count == 1
    assert trace.cases[0].answers_match is True
    assert trace.cases[0].diff_segments == ()
    assert trace.cases[0].diff_left == ""
    assert trace.cases[0].diff_right == ""
    assert trace.candidates[0].candidate == "only"


def test_trace_without_cells_has_nothing_to_show() -> None:
    trace = build_trace(_read(_run({}), []))
    assert trace.has_cells is False
    assert trace.cases == ()
    assert trace.candidates == ()
    assert trace.running is False


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"source": "not a mapping"},
        {"source": {"options": "not a mapping"}},
        {"source": {"options": {"cases": "not a list"}}},
        {
            "source": {
                "options": {
                    "cases": [
                        None,
                        {},
                        {"case_id": ""},
                        {"case_id": "case-1", "prompt": "kept"},
                    ]
                }
            }
        },
        {"candidates": "not a list"},
        {
            "candidates": [
                None,
                {},
                {"name": ""},
                {"name": "no-model"},
                {"name": "bad-model", "model": 3},
            ]
        },
    ],
)
def test_malformed_snapshots_fall_back_without_inventing_values(
    snapshot: dict[str, Any],
) -> None:
    trace = build_trace(_read(_run(snapshot), [_cell("only")]))
    assert trace.cases[0].case_id == "case-1"
    assert trace.cases[0].candidate_count == 1


def test_running_run_is_marked_as_running() -> None:
    trace = build_trace(_read(_run({}, status="running"), []))
    assert trace.running is True
