"""The trace page's view model: one case, every candidate, every check.

The trace is read-only and answers a narrower question than the run page: for
one case, what prompt did each candidate actually receive, what did it answer,
which checks passed and why, and how much did that cell cost. It is built from
one :class:`RunRead` so the page and the JSON API share one load and one set
of numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from evalharness.core.models import CellRecord, Check
from evalharness.read_model import RunRead


@dataclass(frozen=True, slots=True)
class TraceCheck:
    """One check on one cell, with the evidence an operator can inspect."""

    metric: str
    passed: bool
    score: float | None
    reason: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TraceChunk:
    """One context chunk the candidate actually received."""

    chunk_id: str
    text: str
    path: str | None


@dataclass(frozen=True, slots=True)
class TraceCell:
    """One recorded cell as the trace renders it, including the effective prompt."""

    cell_id: str
    candidate: str
    case_id: str
    replica: int
    status: str
    answer: str
    error: str | None
    effective_prompt: str
    prompt_variant: str
    context_name: str
    context_chunks: tuple[TraceChunk, ...]
    prompt_tokens: int
    completion_tokens: int
    tool_calls: int
    wall_ms: int
    cost_usd: float
    usage_source: str
    provider: str
    model: str
    checks: tuple[TraceCheck, ...]
    artifact_url: str


@dataclass(frozen=True, slots=True)
class TraceColumn:
    """One candidate's column in one case, with every replica it recorded."""

    candidate: str
    cells: tuple[TraceCell, ...]
    checks_passed: int
    checks_total: int
    errored: int


@dataclass(frozen=True, slots=True)
class TraceCase:
    """One case, its candidate columns and the two-candidate word diff."""

    case_id: str
    prompt: str
    columns: tuple[TraceColumn, ...]
    answers_match: bool
    candidate_count: int
    diff_segments: tuple[tuple[str, str], ...]
    diff_left: str
    diff_right: str


@dataclass(frozen=True, slots=True)
class TraceCandidate:
    """One candidate's run-wide totals, for the trace header."""

    candidate: str
    checks_passed: int
    checks_total: int
    errored: int


@dataclass(frozen=True, slots=True)
class TracePage:
    """Every case in a run, plus the run-wide totals the header shows."""

    candidates: tuple[TraceCandidate, ...]
    cases: tuple[TraceCase, ...]
    cells_recorded: int
    cells_total: int
    running: bool

    @property
    def has_cells(self) -> bool:
        """Return whether the run recorded at least one cell."""
        return self.cells_recorded > 0


def build_trace(read: RunRead) -> TracePage:
    """Build the trace for one loaded run.

    Candidate and case order come from the spec snapshot so the page follows
    the spec even before a cell is recorded. When the snapshot has no cases
    (or no candidates), the stored cells are the fallback order.
    """
    snapshot = read.run.spec_snapshot or {}
    snapshot_cases = _snapshot_cases(snapshot)
    snapshot_candidates, snapshot_models = _snapshot_candidates(snapshot)
    cells_by_key: dict[tuple[str, str], list[CellRecord]] = {}
    for cell in read.cells:
        cells_by_key.setdefault((cell.case_id, cell.candidate), []).append(cell)
    for cells in cells_by_key.values():
        cells.sort(key=lambda cell: cell.replica)
    case_order = [case_id for case_id, _ in snapshot_cases]
    for case in read.cells:
        if case.case_id not in case_order:
            case_order.append(case.case_id)
    candidate_order = list(snapshot_candidates)
    for cell in read.cells:
        if cell.candidate not in candidate_order:
            candidate_order.append(cell.candidate)
    prompt_by_case = dict(snapshot_cases)
    checks_by_cell = read.checks_by_cell
    cases: list[TraceCase] = []
    for case_id in case_order:
        prompt = prompt_by_case.get(case_id, "")
        columns: list[TraceColumn] = []
        for candidate in candidate_order:
            records = cells_by_key.get((case_id, candidate), [])
            trace_cells = tuple(
                _trace_cell(
                    cell,
                    checks_by_cell.get(cell.cell_id, []),
                    prompt,
                    snapshot_models.get(candidate, ""),
                )
                for cell in records
            )
            columns.append(
                TraceColumn(
                    candidate=candidate,
                    cells=trace_cells,
                    checks_passed=sum(
                        1 for cell in trace_cells for check in cell.checks if check.passed
                    ),
                    checks_total=sum(len(cell.checks) for cell in trace_cells),
                    errored=sum(1 for cell in trace_cells if cell.status != "ok"),
                )
            )
        answers = [
            column.cells[0].answer for column in columns if column.cells
        ]
        all_answered = bool(columns) and all(answer for answer in answers)
        diff_segments: tuple[tuple[str, str], ...] = ()
        diff_left = ""
        diff_right = ""
        if len(columns) == 2 and all(column.cells for column in columns):
            diff_left = columns[0].candidate
            diff_right = columns[1].candidate
            diff_segments = _word_diff(
                columns[0].cells[0].answer,
                columns[1].cells[0].answer,
            )
        cases.append(
            TraceCase(
                case_id=case_id,
                prompt=prompt,
                columns=tuple(columns),
                answers_match=all_answered and len(set(answers)) == 1,
                candidate_count=len(columns),
                diff_segments=diff_segments,
                diff_left=diff_left,
                diff_right=diff_right,
            )
        )
    candidates = tuple(
        TraceCandidate(
            candidate=candidate,
            checks_passed=sum(
                1
                for cell in read.cells
                if cell.candidate == candidate
                for check in checks_by_cell.get(cell.cell_id, [])
                if check.passed
            ),
            checks_total=sum(
                len(checks_by_cell.get(cell.cell_id, []))
                for cell in read.cells
                if cell.candidate == candidate
            ),
            errored=sum(
                1 for cell in read.cells if cell.candidate == candidate and cell.status != "ok"
            ),
        )
        for candidate in candidate_order
    )
    return TracePage(
        candidates=candidates,
        cases=tuple(cases),
        cells_recorded=len(read.cells),
        cells_total=read.run.n_cells,
        running=read.run.status == "running",
    )


def _trace_cell(
    cell: CellRecord,
    checks: list[Check],
    case_prompt: str,
    snapshot_model: str,
) -> TraceCell:
    """Return one cell as the trace renders it, never inventing a value."""
    variant = cell.variant or {}
    override = variant.get("prompt_override")
    effective_prompt = (
        override if isinstance(override, str) and override else case_prompt
    )
    chunks: list[TraceChunk] = []
    raw_chunks = variant.get("context")
    if isinstance(raw_chunks, list):
        for raw in raw_chunks:
            if not isinstance(raw, dict):
                continue
            chunks.append(
                TraceChunk(
                    chunk_id=str(raw.get("chunk_id", "")),
                    text=str(raw.get("text", "")),
                    path=str(raw["path"]) if raw.get("path") else None,
                )
            )
    error = cell.output.get("error")
    model = str(cell.output.get("model") or snapshot_model or "")
    return TraceCell(
        cell_id=cell.cell_id,
        candidate=cell.candidate,
        case_id=cell.case_id,
        replica=cell.replica,
        status=cell.status,
        answer=cell.answer,
        error=str(error) if error else None,
        effective_prompt=effective_prompt,
        prompt_variant=str(variant.get("prompt_variant") or ""),
        context_name=str(variant.get("context_name") or "inline"),
        context_chunks=tuple(chunks),
        prompt_tokens=cell.prompt_tokens,
        completion_tokens=cell.completion_tokens,
        tool_calls=cell.tool_calls,
        wall_ms=cell.wall_ms,
        cost_usd=cell.cost_usd,
        usage_source=cell.usage_source,
        provider=str(cell.provider or ""),
        model=model,
        checks=tuple(
            TraceCheck(
                metric=check.metric,
                passed=check.passed,
                score=check.score,
                reason=check.reason,
                evidence=dict(check.evidence),
            )
            for check in checks
        ),
        artifact_url=f"/runs/{cell.run_id}/cells/{cell.cell_id}/artifact",
    )


def _snapshot_cases(snapshot: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the case ids and prompts declared by the snapshot, in order."""
    cases: list[tuple[str, str]] = []
    source = snapshot.get("source")
    options = source.get("options") if isinstance(source, dict) else None
    raw_cases = options.get("cases") if isinstance(options, dict) else None
    if not isinstance(raw_cases, list):
        return cases
    for raw in raw_cases:
        if not isinstance(raw, dict) or not raw.get("case_id"):
            continue
        cases.append((str(raw["case_id"]), str(raw.get("prompt", ""))))
    return cases


def _snapshot_candidates(
    snapshot: dict[str, Any],
) -> tuple[list[str], dict[str, str]]:
    """Return the candidate names and models declared by the snapshot."""
    names: list[str] = []
    models: dict[str, str] = {}
    raw_candidates = snapshot.get("candidates")
    if not isinstance(raw_candidates, list):
        return names, models
    for raw in raw_candidates:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        name = str(raw["name"])
        model = raw.get("model")
        if not isinstance(model, str) or not model:
            continue
        names.append(name)
        models[name] = model
    return names, models


def _word_diff(left: str, right: str) -> tuple[tuple[str, str], ...]:
    """Return same, add and del word segments for a two-candidate answer.

    The segments are tokenised by whitespace and join back with spaces, so the
    template can put each class on its own span and escape every token.
    """
    matcher = SequenceMatcher(None, left.split(), right.split())
    segments: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            segments.append(("same", " ".join(left.split()[i1:i2])))
        elif tag == "delete":
            segments.append(("del", " ".join(left.split()[i1:i2])))
        elif tag == "insert":
            segments.append(("add", " ".join(right.split()[j1:j2])))
        else:
            segments.append(("del", " ".join(left.split()[i1:i2])))
            segments.append(("add", " ".join(right.split()[j1:j2])))
    return tuple(segments)
