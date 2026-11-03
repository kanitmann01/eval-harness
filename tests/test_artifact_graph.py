"""Behaviour tests for the artifact graph context selector."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evalharness.adapters.contexts.artifact_graph import build
from evalharness.core.context import RunContext
from evalharness.core.events import EventEmitter
from evalharness.core.models import Case
from evalharness.errors import SpecError

_UNSET = object()


def _context(artifacts: Path | None, run_id: str = "run") -> RunContext:
    return RunContext(
        run_id=run_id,
        seed=0,
        credential=None,
        events=EventEmitter(run_id),
        artifacts_root=artifacts,
    )


def _case(prompt: str = "refund policy") -> Case:
    return Case(case_id="case-1", prompt=prompt)


def _write_run(
    artifacts: Path,
    run_id: str = "run-1",
    status: str = "ok",
    *,
    record_run_id: bool = True,
) -> Path:
    directory = artifacts / run_id
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"status": status}
    if record_run_id:
        payload["run_id"] = run_id
    (directory / "run.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _write_cell(
    artifacts: Path,
    cell_id: str,
    *,
    run_id: str = "run-1",
    answer: object = "ok",
    candidate: object = "alpha",
    cited: tuple[str, ...] = (),
    variant: object = _UNSET,
    cell_fields: dict[str, object] | None = None,
    write_answer_file: bool = True,
) -> Path:
    directory = artifacts / run_id / cell_id
    directory.mkdir(parents=True, exist_ok=True)
    if variant is _UNSET:
        variant = {"context": [{"chunk_id": chunk_id} for chunk_id in cited]}
    cell: dict[str, object] = {
        "cell_id": cell_id,
        "candidate": candidate,
        "case_id": "case-1",
        "replica": 1,
        "answer": answer,
        "output": {},
        "variant": variant,
    }
    cell.update(cell_fields or {})
    payload = {"cell": cell, "checks": []}
    (directory / "cell.json").write_text(json.dumps(payload), encoding="utf-8")
    if write_answer_file:
        text = answer if isinstance(answer, str) else ""
        (directory / "answer.txt").write_text(text, encoding="utf-8")
    return directory


def _select(
    artifacts: Path,
    *,
    prompt: str = "refund policy",
    run: str = "run-1",
    **options: object,
) -> list:
    selector = build("graph", {"run": run, **options})
    case = _case(prompt)
    return selector.select(case, _context(artifacts))


def test_an_empty_closed_run_selects_nothing(tmp_path: Path) -> None:
    _write_run(tmp_path)

    selector = build("graph", {"run": "run-1"})

    assert selector.name == "graph"
    assert selector.select(_case(), _context(tmp_path)) == []


def test_unknown_option_is_actionable() -> None:
    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1", "rank": "pagerank"})

    assert info.value.field == "contexts.graph.options.rank"
    assert "unknown option" in str(info.value)
    assert "max_chunks" in info.value.fix


def test_run_option_is_required() -> None:
    with pytest.raises(SpecError) as info:
        build("graph", {})

    assert info.value.field == "contexts.graph.options.run"
    assert "required" in str(info.value)


@pytest.mark.parametrize(
    "value",
    [None, 7, "", "   ", ".", "..", "a..b", "run/1", "run\\1", "C:run"],
)
def test_run_must_be_one_bare_run_id(value: object) -> None:
    with pytest.raises(SpecError) as info:
        build("graph", {"run": value})

    assert info.value.field == "contexts.graph.options.run"
    assert "bare run id" in str(info.value) or "non-empty" in str(info.value)


@pytest.mark.parametrize("value", [0, 51, -1, True, "6", 3.5])
def test_max_chunks_is_bounded(value: object) -> None:
    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1", "max_chunks": value})

    assert info.value.field == "contexts.graph.options.max_chunks"
    assert "integer from 1 to 50" in str(info.value)


@pytest.mark.parametrize("value", [0, 2_000_001, False, "12000"])
def test_max_bytes_is_bounded(value: object) -> None:
    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1", "max_bytes": value})

    assert info.value.field == "contexts.graph.options.max_bytes"
    assert "integer from 1 to 2000000" in str(info.value)


def test_exclude_candidate_must_be_a_string() -> None:
    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1", "exclude_candidate": 3})

    assert info.value.field == "contexts.graph.options.exclude_candidate"
    assert "must be a string" in str(info.value)


def test_missing_artifacts_root_is_actionable() -> None:
    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1"}).select(_case(), _context(None))

    assert info.value.field == "contexts.graph.artifacts"
    assert "no artifacts root" in str(info.value)


def test_a_run_without_artifacts_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1"}).select(_case(), _context(tmp_path))

    assert info.value.field == "contexts.graph.options.run"
    assert "the run has no artifacts" in str(info.value)
    assert info.value.fix == "name an existing run"


def test_corrupt_run_json_is_actionable(tmp_path: Path) -> None:
    directory = _write_run(tmp_path)
    (directory / "run.json").write_text("{oops", encoding="utf-8")

    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1"}).select(_case(), _context(tmp_path))

    assert info.value.field == "contexts.graph.options.run"
    assert "run artifact is corrupt" in str(info.value)


def test_run_json_must_be_an_object(tmp_path: Path) -> None:
    directory = _write_run(tmp_path)
    (directory / "run.json").write_text("[]", encoding="utf-8")

    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1"}).select(_case(), _context(tmp_path))

    assert "not a JSON object" in str(info.value)


def test_a_running_run_is_refused(tmp_path: Path) -> None:
    _write_run(tmp_path, status="running")

    with pytest.raises(SpecError) as info:
        build("graph", {"run": "run-1"}).select(_case(), _context(tmp_path))

    assert info.value.field == "contexts.graph.options.run"
    assert "the run is not closed" in str(info.value)


def test_selection_returns_answer_chunks_in_score_order(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(
        tmp_path,
        "alpha",
        answer="refund policy within 30 days",
        cited=("p2", "p1"),
    )
    _write_cell(tmp_path, "beta", answer="shipping policy only", cited=("p2",))
    case = _case()
    before = case.model_dump()

    chunks = build("graph", {"run": "run-1"}).select(case, _context(tmp_path))

    assert [chunk.chunk_id for chunk in chunks] == [
        "run:run-1/alpha#answer",
        "run:run-1/beta#answer",
    ]
    first, second = chunks
    assert first.text == "refund policy within 30 days"
    assert first.graph_nodes == ["p1", "p2"]
    assert second.graph_nodes == ["p2"]
    assert first.path is None
    assert first.start_line is None
    assert first.end_line is None
    assert first.blob_sha256 is None
    assert first.score is not None and first.score > (second.score or 0.0)
    assert case.model_dump() == before


def test_selection_is_repeatable(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "alpha", answer="refund policy", cited=("p1",))
    _write_cell(tmp_path, "beta", answer="refund rules", cited=("p1", "p2"))

    selector = build("graph", {"run": "run-1"})
    first = [
        chunk.model_dump() for chunk in selector.select(_case(), _context(tmp_path))
    ]
    second = [
        chunk.model_dump() for chunk in selector.select(_case(), _context(tmp_path))
    ]

    assert first == second
    assert first[0]["chunk_id"] == "run:run-1/alpha#answer"


def test_co_citation_boosts_answers_that_share_citations(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "a", answer="same", cited=("shared",))
    _write_cell(tmp_path, "b", answer="same", cited=("shared",))
    _write_cell(tmp_path, "c", answer="same", cited=())

    chunks = _select(tmp_path, prompt="")

    assert [chunk.chunk_id for chunk in chunks] == [
        "run:run-1/a#answer",
        "run:run-1/b#answer",
        "run:run-1/c#answer",
    ]
    assert chunks[0].score == chunks[1].score
    assert (chunks[0].score or 0.0) > (chunks[2].score or 0.0)
    assert chunks[2].graph_nodes == []


def test_equal_scores_break_the_tie_on_chunk_id(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "b-cell", answer="alpha")
    _write_cell(tmp_path, "a-cell", answer="alpha")

    chunks = _select(tmp_path, prompt="alpha beta")

    assert [chunk.chunk_id for chunk in chunks] == [
        "run:run-1/a-cell#answer",
        "run:run-1/b-cell#answer",
    ]


def test_exclude_candidate_drops_those_answers(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "alpha", answer="refund policy", candidate="alpha")
    _write_cell(tmp_path, "beta", answer="refund policy", candidate="beta")

    chunks = _select(tmp_path, exclude_candidate="beta")

    assert [chunk.chunk_id for chunk in chunks] == ["run:run-1/alpha#answer"]


def test_empty_answers_are_skipped(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "empty", answer="")
    _write_cell(tmp_path, "blank", answer="   ")
    _write_cell(tmp_path, "kept", answer="refund")

    chunks = _select(tmp_path)

    assert [chunk.chunk_id for chunk in chunks] == ["run:run-1/kept#answer"]


def test_answer_text_file_is_the_fallback(tmp_path: Path) -> None:
    _write_run(tmp_path)
    directory = _write_cell(tmp_path, "alpha", answer="")
    (directory / "answer.txt").write_text("from the file", encoding="utf-8")

    chunks = _select(tmp_path)

    assert [chunk.text for chunk in chunks] == ["from the file"]


def test_a_missing_answer_file_with_an_empty_answer_is_skipped(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "alpha", answer="", write_answer_file=False)

    assert _select(tmp_path) == []


def test_an_unreadable_answer_file_is_actionable(tmp_path: Path) -> None:
    _write_run(tmp_path)
    directory = _write_cell(tmp_path, "alpha", answer="", write_answer_file=False)
    (directory / "answer.txt").write_bytes(b"\xff\xfe")

    with pytest.raises(SpecError) as info:
        _select(tmp_path)

    assert info.value.field == "contexts.graph.options.run"
    assert "unreadable" in str(info.value)


def test_a_non_string_answer_is_actionable(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "alpha", answer=5)

    with pytest.raises(SpecError) as info:
        _select(tmp_path)

    assert "answer is not a string" in str(info.value)


def test_corrupt_cell_json_is_actionable(tmp_path: Path) -> None:
    directory = _write_run(tmp_path)
    cell_dir = directory / "alpha"
    cell_dir.mkdir()
    (cell_dir / "cell.json").write_text("{oops", encoding="utf-8")

    with pytest.raises(SpecError) as info:
        _select(tmp_path)

    assert info.value.field == "contexts.graph.options.run"
    assert "cell artifact is corrupt" in str(info.value)


def test_a_cell_json_with_invalid_utf8_is_actionable(tmp_path: Path) -> None:
    directory = _write_run(tmp_path)
    cell_dir = directory / "alpha"
    cell_dir.mkdir()
    (cell_dir / "cell.json").write_bytes(b"\xff\xfe")

    with pytest.raises(SpecError) as info:
        _select(tmp_path)

    assert "cell artifact is corrupt" in str(info.value)


def test_a_missing_cell_json_is_actionable(tmp_path: Path) -> None:
    directory = _write_run(tmp_path)
    (directory / "alpha").mkdir()

    with pytest.raises(SpecError) as info:
        _select(tmp_path)

    assert "cell artifact is corrupt" in str(info.value)


def test_a_cell_artifact_must_be_an_object(tmp_path: Path) -> None:
    directory = _write_run(tmp_path)
    cell_dir = directory / "alpha"
    cell_dir.mkdir()
    (cell_dir / "cell.json").write_text("[]", encoding="utf-8")

    with pytest.raises(SpecError) as info:
        _select(tmp_path)

    assert "not a JSON object" in str(info.value)


def test_a_cell_artifact_needs_a_cell_object(tmp_path: Path) -> None:
    directory = _write_run(tmp_path)
    cell_dir = directory / "alpha"
    cell_dir.mkdir()
    (cell_dir / "cell.json").write_text('{"checks": []}', encoding="utf-8")

    with pytest.raises(SpecError) as info:
        _select(tmp_path)

    assert "has no cell object" in str(info.value)


def test_a_stray_file_in_the_run_directory_is_ignored(tmp_path: Path) -> None:
    directory = _write_run(tmp_path)
    (directory / "notes.txt").write_text("not a cell", encoding="utf-8")
    _write_cell(tmp_path, "alpha", answer="refund")

    assert [chunk.chunk_id for chunk in _select(tmp_path)] == [
        "run:run-1/alpha#answer"
    ]


def test_a_missing_cell_id_falls_back_to_the_directory_name(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "alpha", answer="refund", cell_fields={"cell_id": ""})

    assert [chunk.chunk_id for chunk in _select(tmp_path)] == [
        "run:run-1/alpha#answer"
    ]


def test_malformed_variants_yield_no_graph_nodes(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "a", answer="refund one", variant=None)
    _write_cell(tmp_path, "b", answer="refund two", variant={"context": "nope"})
    _write_cell(
        tmp_path,
        "c",
        answer="refund three",
        variant={
            "context": [
                1,
                {"chunk_id": ""},
                {"chunk_id": 3},
                {"chunk_id": "kept"},
            ]
        },
    )

    chunks = _select(tmp_path)

    assert len(chunks) == 3
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    assert by_id["run:run-1/c#answer"].graph_nodes == ["kept"]
    assert by_id["run:run-1/a#answer"].graph_nodes == []
    assert by_id["run:run-1/b#answer"].graph_nodes == []


def test_max_chunks_caps_the_selection(tmp_path: Path) -> None:
    _write_run(tmp_path)
    for index in range(4):
        _write_cell(tmp_path, f"cell-{index}", answer=f"refund {index}")

    assert len(_select(tmp_path, max_chunks=2)) == 2


def test_the_default_max_chunks_is_six(tmp_path: Path) -> None:
    _write_run(tmp_path)
    for index in range(7):
        _write_cell(tmp_path, f"cell-{index}", answer=f"refund {index}")

    assert len(_select(tmp_path)) == 6


def test_max_bytes_skips_an_answer_that_would_exceed(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "large", answer="x " * 20)
    _write_cell(tmp_path, "small", answer="y")

    chunks = _select(tmp_path, prompt="x", max_bytes=30)

    assert [chunk.chunk_id for chunk in chunks] == ["run:run-1/small#answer"]


def test_max_bytes_accepts_an_answer_that_fits_exactly(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "large", answer="x " * 20)

    chunks = _select(tmp_path, prompt="x", max_bytes=40)

    assert [chunk.chunk_id for chunk in chunks] == ["run:run-1/large#answer"]


def test_duplicate_cell_ids_are_emitted_once(tmp_path: Path) -> None:
    _write_run(tmp_path)
    _write_cell(tmp_path, "one", answer="refund", cell_fields={"cell_id": "same"})
    _write_cell(tmp_path, "two", answer="refund", cell_fields={"cell_id": "same"})

    assert len(_select(tmp_path)) == 1


def test_the_recorded_run_id_names_the_chunks(tmp_path: Path) -> None:
    directory = _write_run(tmp_path, run_id="dir-name")
    (directory / "run.json").write_text(
        json.dumps({"run_id": "recorded", "status": "ok"}), encoding="utf-8"
    )
    _write_cell(tmp_path, "alpha", answer="refund", run_id="dir-name")

    chunks = build("graph", {"run": "dir-name"}).select(
        _case(), _context(tmp_path)
    )

    assert [chunk.chunk_id for chunk in chunks] == [
        "run:recorded/alpha#answer"
    ]


def test_a_run_json_without_run_id_falls_back_to_the_option(tmp_path: Path) -> None:
    _write_run(tmp_path, record_run_id=False)
    _write_cell(tmp_path, "alpha", answer="refund")

    chunks = _select(tmp_path)

    assert [chunk.chunk_id for chunk in chunks] == ["run:run-1/alpha#answer"]

def test_conformance_setup_writes_a_closed_run(tmp_path: Path) -> None:
    from evalharness.adapters.contexts.artifact_graph import conformance_setup

    options, repos, artifacts = conformance_setup(tmp_path)

    assert repos == {}
    chunks = build("context", options).select(
        _case(), _context(artifacts, run_id="conformance-run")
    )
    assert [chunk.chunk_id for chunk in chunks] == [
        "run:conformance-run/alpha#answer",
        "run:conformance-run/beta#answer",
    ]


def test_the_public_context_conformance_suite_passes(tmp_path: Path) -> None:
    from evalharness.conformance.runner import run_suite

    report = run_suite(
        "evalharness.adapters.contexts.artifact_graph:build",
        "context",
        workdir=tmp_path,
    )

    failures = [
        (result.clause, result.detail)
        for result in report.results
        if not result.ok
    ]
    assert report.passed, failures