"""Plain-data views shared by the HTML pages and the JSON API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from evalharness.api.graph_views import SpecGraph, spec_graph, thumbnail
from evalharness.core.models import (
    CellRecord,
    Check,
    GateResult,
    RunRecord,
)
from evalharness.read_model import RunRead, run_display_name
from evalharness.core.stats import wilson_interval
from evalharness.storage.repository import RunSummary

GLYPHS: dict[str, str] = {
    "pass": "✓",
    "fail": "✗",
    "error": "!",
    "muted": "–",
    "unknown": "?",
}
"""Heatmap glyphs; colour is never the only signal."""


@dataclass(frozen=True, slots=True)
class HeatmapColumn:
    """One heatmap column: a case and replica pair."""

    key: str
    label: str
    case_id: str
    replica: int


@dataclass(frozen=True, slots=True)
class HeatmapCell:
    """One selectable heatmap cell and its accessible label."""

    cell_id: str
    tone: str
    glyph: str
    tooltip: str
    detail_url: str
    selected: bool


@dataclass(frozen=True, slots=True)
class HeatmapRow:
    """One candidate's row in the heatmap, with its recorded check summary."""

    candidate: str
    cells: tuple[HeatmapCell | None, ...]
    cells_recorded: int
    cells_planned: int
    checks_passed: int
    checks_total: int

    @property
    def check_rate(self) -> float | None:
        """Return the row's passed-check proportion, or None without checks."""
        return self.checks_passed / self.checks_total if self.checks_total else None


@dataclass(frozen=True, slots=True)
class VerdictSummary:
    """The above-the-fold answer for one run: outcome, interval and guardrails."""

    tone: str
    glyph: str
    label: str
    headline: str
    pass_rate: float | None
    interval_low: float | None
    interval_high: float | None
    checks_passed: int
    checks_total: int
    cells_recorded: int
    cells_errored: int
    cells_total: int
    gates_passed: int
    gates_total: int
    failed_gates: tuple[str, ...]
    budget_ratio: float | None
    budget_over: bool
    warning: str


@dataclass(frozen=True, slots=True)
class ProgressSummary:
    """How much of a run's matrix is recorded, for the progress treatment."""

    done: int
    total: int
    percent: int
    state: str
    label: str
    note: str


@dataclass(frozen=True, slots=True)
class RunListStats:
    """The one-line state of the run list: volume, attention and spend."""

    total: int
    attention: int
    running: int
    cost_usd: float


def run_summary_data(summary: RunSummary) -> dict[str, Any]:
    """Return the JSON shape of one run-list row."""
    return {
        "run_id": summary.run_id,
        "spec_name": summary.spec_name,
        "status": summary.status,
        "cost_usd": summary.cost_usd,
        "pass_rate": summary.pass_rate,
        "duration_s": summary.duration_s,
        "n_cells": summary.n_cells,
        "n_ok": summary.n_ok,
        "started_at": _isoformat(summary.started_at),
        "finished_at": _isoformat(summary.finished_at),
        "candidates": list(summary.candidates),
        "metrics": list(summary.metrics),
        "gate_failed": bool(summary.failed_gates),
        "failed_gates": list(summary.failed_gates),
    }


def run_detail_data(read: RunRead) -> dict[str, Any]:
    """Return the JSON shape of one loaded run, including cells and aggregates."""
    run = read.run
    return {
        "run_id": run.run_id,
        "spec_name": run_display_name(run),
        "status": run.status,
        "cost_usd": run.cost_usd,
        "budget_usd": run.budget_usd,
        "pass_rate": read.pass_rate,
        "duration_s": read.duration_s,
        "n_cells": run.n_cells,
        "n_ok": run.n_ok,
        "seed": run.seed,
        "harness_sha": run.harness_sha,
        "error": run.error,
        "started_at": _isoformat(run.started_at),
        "finished_at": _isoformat(run.finished_at),
        "aggregates": [
            aggregate.model_dump(mode="json") for aggregate in read.aggregates
        ],
        "gates": [gate.model_dump(mode="json") for gate in read.gates],
        "cells": [
            cell_data(cell, read.checks_for(cell.cell_id)) for cell in read.cells
        ],
    }


def context_summary(cells: list[CellRecord]) -> list[dict[str, Any]]:
    """Group the context each candidate actually received, for the run page.

    Only the recorded variant is read, never the live selector, so the page
    answers what the run used even after the adapter changes.
    """
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cell in cells:
        variant = cell.variant or {}
        context_name = str(variant.get("context_name") or "inline")
        digest = str(variant.get("context_digest") or "")
        chunks = variant.get("context")
        items = chunks if isinstance(chunks, list) else []
        key = (cell.candidate, context_name, digest)
        group = groups.get(key)
        if group is None:
            group = {
                "candidate": cell.candidate,
                "selector": context_name,
                "digest": digest,
                "chunk_count": len(items),
                "bytes": sum(
                    len(str(item.get("text", "")).encode("utf-8"))
                    for item in items
                    if isinstance(item, dict)
                ),
                "chunks": [
                    {
                        "chunk_id": str(item.get("chunk_id", "")),
                        "path": item.get("path"),
                        "start_line": item.get("start_line"),
                        "end_line": item.get("end_line"),
                        "score": item.get("score"),
                        "graph_nodes": list(item.get("graph_nodes") or []),
                    }
                    for item in items[:8]
                    if isinstance(item, dict)
                ],
                "more_chunks": max(0, len(items) - 8),
            }
            groups[key] = group
        elif len(items) > group["chunk_count"]:
            group["chunk_count"] = len(items)
    return sorted(
        groups.values(),
        key=lambda row: (row["candidate"], row["selector"], row["digest"]),
    )


def cell_data(cell: CellRecord, checks: list[Check]) -> dict[str, Any]:
    """Return the JSON shape of one cell and every check behind it."""
    return {
        "cell_id": cell.cell_id,
        "candidate": cell.candidate,
        "case_id": cell.case_id,
        "replica": cell.replica,
        "status": cell.status,
        "answer": cell.answer,
        "citations": citations_for(cell),
        "prompt_tokens": cell.prompt_tokens,
        "completion_tokens": cell.completion_tokens,
        "tool_calls": cell.tool_calls,
        "cost_usd": cell.cost_usd,
        "wall_ms": cell.wall_ms,
        "usage_source": cell.usage_source,
        "error": cell_error(cell),
        "checks": [check.model_dump(mode="json") for check in checks],
        "artifact_url": f"/runs/{cell.run_id}/cells/{cell.cell_id}/artifact",
    }


def citations_for(cell: CellRecord) -> list[str]:
    """Return the citations the candidate reported, always as strings."""
    return [str(item) for item in (cell.output.get("citations") or [])]


def cell_error(cell: CellRecord) -> str | None:
    """Return the recorded error text for a failed cell, or None."""
    value = cell.output.get("error")
    return str(value) if value else None


def heatmap(
    cells: list[CellRecord],
    checks_by_cell: dict[str, list[Check]],
    run_id: str,
    selected_cell_id: str,
) -> tuple[list[HeatmapColumn], list[HeatmapRow]]:
    """Build the candidate by case matrix with per-row check totals."""
    columns = _heatmap_columns(cells)
    lookup = {
        (cell.candidate, cell.case_id, cell.replica): cell for cell in cells
    }
    rows: list[HeatmapRow] = []
    for candidate in sorted({cell.candidate for cell in cells}):
        nodes: list[HeatmapCell | None] = []
        checks_passed = 0
        checks_total = 0
        for column in columns:
            cell = lookup.get((candidate, column.case_id, column.replica))
            if cell is None:
                nodes.append(None)
                continue
            nodes.append(
                _heatmap_cell(cell, checks_by_cell, run_id, selected_cell_id)
            )
            checks = checks_by_cell.get(cell.cell_id, [])
            checks_total += len(checks)
            checks_passed += sum(1 for check in checks if check.passed)
        rows.append(
            HeatmapRow(
                candidate=candidate,
                cells=tuple(nodes),
                cells_recorded=len([node for node in nodes if node is not None]),
                cells_planned=len(columns),
                checks_passed=checks_passed,
                checks_total=checks_total,
            )
        )
    return columns, rows


def run_verdict(
    run: RunRecord,
    cells: list[CellRecord],
    checks: list[Check],
    gates: list[GateResult],
) -> VerdictSummary:
    """Summarise a run's outcome for the verdict panel above the matrix.

    The tone never calls a gate failure a pass, never treats an empty run as
    zero percent, and never reduces a run to colour: the template renders the
    glyph and the label beside it.
    """
    passed = sum(1 for check in checks if check.passed)
    total = len(checks)
    recorded = len(cells)
    errored = sum(1 for cell in cells if cell.status != "ok")
    gates_passed = sum(1 for gate in gates if gate.passed)
    failed = tuple(gate.metric for gate in gates if not gate.passed)
    low, high = (
        wilson_interval(passed, total) if total else (None, None)
    )
    ratio = (
        run.cost_usd / run.budget_usd
        if run.budget_usd is not None and run.budget_usd > 0
        else None
    )
    tone, glyph, label = _verdict_identity(run, failed, passed, total)
    return VerdictSummary(
        tone=tone,
        glyph=glyph,
        label=label,
        headline=_verdict_headline(run, recorded, errored, passed, total, failed),
        pass_rate=passed / total if total else None,
        interval_low=low,
        interval_high=high,
        checks_passed=passed,
        checks_total=total,
        cells_recorded=recorded,
        cells_errored=errored,
        cells_total=run.n_cells,
        gates_passed=gates_passed,
        gates_total=len(gates),
        failed_gates=failed,
        budget_ratio=ratio,
        budget_over=ratio is not None and ratio > 1,
        warning=_verdict_warning(run, failed, errored),
    )


def _verdict_identity(
    run: RunRecord,
    failed: tuple[str, ...],
    passed: int,
    total: int,
) -> tuple[str, str, str]:
    """Return the tone, glyph and label for a run, gates before status."""
    if run.status == "running":
        return "info", "◐", "Running"
    if failed:
        return "warn", "!", "Gate failed"
    if run.status in ("error", "failed"):
        return "bad", "✕", "Failed"
    if run.status == "budget_exceeded":
        return "bad", "✕", "Budget exceeded"
    if run.status == "cancelled":
        return "neutral", "–", "Cancelled"
    if run.status == "partial":
        return "warn", "!", "Partial"
    if total == 0:
        return "info", "?", "No checks"
    if passed == total:
        return "ok", "✓", "Passed"
    return "warn", "!", "Checks failed"


def _verdict_headline(
    run: RunRecord,
    recorded: int,
    errored: int,
    passed: int,
    total: int,
    failed: tuple[str, ...],
) -> str:
    """Return one honest sentence about what happened to the run."""
    if run.status == "running":
        return f"In progress: {recorded} of {run.n_cells} cells recorded."
    if failed:
        names = ", ".join(failed)
        return f"Gate not cleared: {names}. The results are recorded."
    if run.status == "cancelled":
        return (
            f"Cancelled after {recorded} of {run.n_cells} cells; "
            "the completed cells are kept."
        )
    if run.status == "budget_exceeded":
        return (
            f"Stopped at the budget cap after {recorded} of {run.n_cells} "
            "cells."
        )
    if run.status in ("error", "failed"):
        return "The run ended with an error; the recorded cells are shown."
    if run.status == "partial":
        return (
            f"{errored} of {recorded} recorded cells did not produce an "
            "answer; the matrix marks them."
        )
    if total == 0:
        return "The run completed but recorded no checks."
    if recorded < run.n_cells:
        return (
            f"{passed} of {total} checks passed across {recorded} of "
            f"{run.n_cells} cells."
        )
    if passed == total:
        return f"All {total} checks passed across {recorded} cells."
    return f"{passed} of {total} checks passed across {recorded} cells."


def _verdict_warning(
    run: RunRecord,
    failed: tuple[str, ...],
    errored: int,
) -> str:
    """Return the guardrail sentence the operator must not miss, or empty."""
    if failed:
        names = ", ".join(failed)
        return (
            f"Gate failed: {names}. Treat this run as not shippable even "
            "though its results are recorded."
        )
    if run.status == "budget_exceeded":
        return (
            "The budget cap stopped the run. Reduce the matrix or raise "
            "budget_usd before relaunching."
        )
    if run.status == "cancelled":
        return (
            "A cancelled run keeps its completed cells; missing cells are "
            "not evidence of failure."
        )
    if errored:
        return (
            f"{errored} cell{'s' if errored != 1 else ''} errored. Compare "
            "per-metric n before drawing a conclusion."
        )
    return ""


def progress_summary(run: RunRecord, cells: list[CellRecord]) -> ProgressSummary:
    """Describe the stored progress of a run for the progress treatment."""
    done = len(cells)
    total = run.n_cells
    percent = min(100, round(done / total * 100)) if total else 0
    if total == 0:
        state, label = "empty", "no cells planned"
        note = "This run planned no cells."
    elif run.status == "running":
        state, label = "running", "running"
        note = "Cells appear as they finish; the matrix updates in place."
    elif done < total:
        state, label = "partial", "partially recorded"
        note = "The run stopped before every planned cell was recorded."
    else:
        state, label = "complete", "complete"
        note = "The stream replays the stored events from the start."
    return ProgressSummary(
        done=done,
        total=total,
        percent=percent,
        state=state,
        label=label,
        note=note,
    )


def run_list_stats(runs: list[RunSummary]) -> RunListStats:
    """Summarise the run list without hiding failures behind a total."""
    attention = sum(
        1
        for run in runs
        if run.failed_gates
        or run.status in ("partial", "error", "failed", "budget_exceeded", "cancelled")
    )
    return RunListStats(
        total=len(runs),
        attention=attention,
        running=sum(1 for run in runs if run.status == "running"),
        cost_usd=sum(run.cost_usd for run in runs),
    )


def run_thumbnails(runs: list[RunSummary]) -> dict[str, SpecGraph]:
    """Return one decorative spec graph per run-list row, keyed by run id.

    The snapshot comes from the row already loaded for the list, so drawing the
    graph column costs no extra query per row.
    """
    return {run.run_id: thumbnail(spec_graph(run.spec_snapshot)) for run in runs}


def _heatmap_columns(cells: list[CellRecord]) -> list[HeatmapColumn]:
    """Return one column per case and replica pair, in stable order."""
    pairs = sorted({(cell.case_id, cell.replica) for cell in cells})
    return [
        HeatmapColumn(
            key=f"{case_id}:{replica}",
            label=case_id if replica == 1 else f"{case_id} · r{replica}",
            case_id=case_id,
            replica=replica,
        )
        for case_id, replica in pairs
    ]


def _heatmap_cell(
    cell: CellRecord,
    checks_by_cell: dict[str, list[Check]],
    run_id: str,
    selected_cell_id: str,
) -> HeatmapCell:
    """Turn one stored cell into its heatmap node."""
    checks = checks_by_cell.get(cell.cell_id, [])
    if cell.status == "ok":
        tone = _tone_for(checks)
    else:
        tone = "error" if cell.status == "error" else "muted"
    return HeatmapCell(
        cell_id=cell.cell_id,
        tone=tone,
        glyph=GLYPHS[tone],
        tooltip=(
            f"{cell.candidate} / {cell.case_id} r{cell.replica}: "
            f"{cell.status} ({len(checks)} checks)"
        ),
        detail_url=f"/runs/{run_id}/cells/{cell.cell_id}",
        selected=cell.cell_id == selected_cell_id,
    )


def _tone_for(checks: list[Check]) -> str:
    """Return pass, fail or unknown for a completed cell's checks."""
    passed = all(check.passed for check in checks)
    return "pass" if passed else ("fail" if checks else "unknown")


def _isoformat(value: datetime | None) -> str | None:
    """Format a stored timestamp for JSON, or None."""
    return value.isoformat() if value is not None else None