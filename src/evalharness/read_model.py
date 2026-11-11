"""One read model for a stored run.

Every console surface needs the same rows for a run: the run record, its cells,
their checks, its aggregates, its gates and, for the live page, its events.
Loading those in one place keeps every surface on the same numbers, keeps the
repository the only SQL seam, and folds the per-cell check lookup into one
run-wide query. The write side is separate: the executor already returns its
cells and checks, and :mod:`evalharness.service` uses them directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evalharness.core.events import Event
from evalharness.core.judge_pins import JudgeDrift, judge_drift
from evalharness.core.models import (
    Aggregate,
    CellRecord,
    Check,
    GateResult,
    RunRecord,
)
from evalharness.core.spec_diff import diff_run_snapshots, spec_diff_data
from evalharness.core.spec_projection import project_document
from evalharness.storage.repository import Repository

UNNAMED_SPEC = "(unnamed spec)"
"""The one fallback string every HTML surface shows for a nameless run."""


@dataclass(frozen=True, slots=True)
class RunRead:
    """Every stored row one run's surfaces need, loaded exactly once."""

    run: RunRecord
    cells: list[CellRecord]
    checks_by_cell: dict[str, list[Check]]
    aggregates: list[Aggregate]
    gates: list[GateResult]
    events: list[Event]

    @property
    def checks(self) -> list[Check]:
        """Return every stored check flattened across the run's cells."""
        return [
            check for checks in self.checks_by_cell.values() for check in checks
        ]

    @property
    def checks_passed(self) -> int:
        """Return how many of the run's checks passed."""
        return sum(1 for check in self.checks if check.passed)

    @property
    def checks_total(self) -> int:
        """Return how many checks the run stored."""
        return len(self.checks)

    @property
    def pass_rate(self) -> float | None:
        """Return the passed share of checks, or None when none were stored."""
        total = self.checks_total
        return self.checks_passed / total if total else None

    @property
    def duration_s(self) -> float | None:
        """Return the wall duration from start to finish, or None while running."""
        finished = self.run.finished_at
        if finished is None:
            return None
        return (finished - self.run.started_at).total_seconds()

    @property
    def display_name(self) -> str:
        """Return the name every page header shows for this run."""
        return run_display_name(self.run) or UNNAMED_SPEC

    def checks_for(self, cell_id: str) -> list[Check]:
        """Return one cell's checks, or an empty list when it stored none."""
        return list(self.checks_by_cell.get(cell_id, []))


def load_run_read(
    repository: Repository,
    run_id: str,
    *,
    with_events: bool = False,
) -> RunRead | None:
    """Load one run and every row its surfaces need, or None when absent.

    Parameter:
        repository: the run store, and the only SQL seam this module uses.
        run_id: the run to read.
        with_events: also read the stored event stream, for the live run page.
            The JSON detail renders no events, so it never pays for that read.
    """
    run = repository.get_run(run_id)
    if run is None:
        return None
    return RunRead(
        run=run,
        cells=repository.list_cells(run_id),
        checks_by_cell=repository.list_checks_for_run(run_id),
        aggregates=repository.list_aggregates(run_id),
        gates=repository.list_gates(run_id),
        events=repository.list_events(run_id) if with_events else [],
    )


def run_display_name(run: RunRecord) -> str:
    """Return the run's spec name, falling back to the stored snapshot.

    Empty means the run recorded no name anywhere; JSON bodies keep the empty
    string, HTML surfaces add the UNNAMED_SPEC fallback through display_name.
    """
    return run.spec_name or project_document(run.spec_snapshot or {}).name


def drift_data(drift: JudgeDrift) -> dict[str, Any]:
    """Return one judge drift as a JSON-ready mapping."""
    return {
        "kind": drift.kind,
        "judge": drift.judge,
        "metrics": list(drift.metrics),
        "hashes": list(drift.hashes),
        "runs": list(drift.runs),
        "reason": drift.reason,
    }


def run_diff_data(current: RunRead, baseline: RunRead) -> dict[str, Any]:
    """Return the two-run spec diff and judge drift for two loaded runs.

    The compare page and the diff page both show this mapping. Taking reads
    that are already loaded keeps one request to one read per run;
    :mod:`evalharness.api.provenance_views` delegates here instead of keeping
    a second copy.
    """
    diff = diff_run_snapshots(
        baseline.run.spec_snapshot,
        current.run.spec_snapshot,
        baseline.run.prices_snapshot,
        current.run.prices_snapshot,
        a_source=baseline.run.source_snapshot,
        b_source=current.run.source_snapshot,
    )
    drifts = judge_drift(
        baseline.run,
        baseline.checks,
        current.run,
        current.checks,
    )
    return {
        "current": _diff_side(current.run),
        "against": _diff_side(baseline.run),
        "diff": spec_diff_data(diff),
        "drift": [drift_data(drift) for drift in drifts],
        "diff_url": (
            f"/runs/{current.run.run_id}/diff?against={baseline.run.run_id}"
        ),
        "swap_url": (
            f"/runs/{baseline.run.run_id}/diff?against={current.run.run_id}"
        ),
    }


def _diff_side(run: RunRecord) -> dict[str, Any]:
    """Return the header facts for one side of the diff page."""
    return {
        "run_id": run.run_id,
        "spec_name": run_display_name(run),
        "status": run.status,
        "seed": run.seed,
        "operator": run.operator,
        "operator_recorded": bool(run.operator),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "receipt_url": f"/runs/{run.run_id}/receipt",
        "run_url": f"/runs/{run.run_id}",
    }
