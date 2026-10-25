"""Plain-data views shared by the HTML pages and the JSON API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from evalharness.core.models import (
    Aggregate,
    CellRecord,
    Check,
    GateResult,
    RunRecord,
)
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
    """One candidate's row in the heatmap."""

    candidate: str
    cells: tuple[HeatmapCell | None, ...]


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


def run_detail_data(
    run: RunRecord,
    cells: list[CellRecord],
    checks_by_cell: dict[str, list[Check]],
    aggregates: list[Aggregate],
    gates: list[GateResult],
) -> dict[str, Any]:
    """Return the JSON shape of one run, including cells and aggregates."""
    checks = [check for items in checks_by_cell.values() for check in items]
    passed = sum(1 for check in checks if check.passed)
    finished = run.finished_at
    duration = (
        (finished - run.started_at).total_seconds() if finished is not None else None
    )
    return {
        "run_id": run.run_id,
        "spec_name": str(run.spec_snapshot.get("name", "")),
        "status": run.status,
        "cost_usd": run.cost_usd,
        "budget_usd": run.budget_usd,
        "pass_rate": passed / len(checks) if checks else None,
        "duration_s": duration,
        "n_cells": run.n_cells,
        "n_ok": run.n_ok,
        "seed": run.seed,
        "harness_sha": run.harness_sha,
        "error": run.error,
        "started_at": _isoformat(run.started_at),
        "finished_at": _isoformat(run.finished_at),
        "aggregates": [aggregate.model_dump(mode="json") for aggregate in aggregates],
        "gates": [gate.model_dump(mode="json") for gate in gates],
        "cells": [
            cell_data(cell, checks_by_cell.get(cell.cell_id, [])) for cell in cells
        ],
    }


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
    """Build the candidate by case matrix for a run detail page."""
    columns = _heatmap_columns(cells)
    lookup = {
        (cell.candidate, cell.case_id, cell.replica): cell for cell in cells
    }
    rows = [
        HeatmapRow(
            candidate=candidate,
            cells=tuple(
                _pick(
                    lookup,
                    candidate,
                    column,
                    checks_by_cell,
                    run_id,
                    selected_cell_id,
                )
                for column in columns
            ),
        )
        for candidate in sorted({cell.candidate for cell in cells})
    ]
    return columns, rows


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


def _pick(
    lookup: dict[tuple[str, str, int], CellRecord],
    candidate: str,
    column: HeatmapColumn,
    checks_by_cell: dict[str, list[Check]],
    run_id: str,
    selected_cell_id: str,
) -> HeatmapCell | None:
    """Return the cell for one matrix position, or None when not planned."""
    cell = lookup.get((candidate, column.case_id, column.replica))
    return (
        _heatmap_cell(cell, checks_by_cell, run_id, selected_cell_id)
        if cell is not None
        else None
    )


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
