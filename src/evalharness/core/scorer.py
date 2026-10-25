"""Run metrics per cell and compute proportion-only aggregates for now."""

from __future__ import annotations

from typing import Iterable

from evalharness.core.models import (
    Aggregate,
    CellRecord,
    Check,
    GateSpec,
    GateResult,
    ScoreContext,
)
from evalharness.core.protocols import Metric


def checks_for_cell(
    cell: CellRecord,
    metrics: Iterable[Metric],
    ctx: ScoreContext,
) -> list[Check]:
    """Run every metric for one cell and return the checks in metric order."""
    checks: list[Check] = []
    for metric in metrics:
        checks.extend(metric.check(cell, ctx))
    return checks


def aggregate_cells(
    run_id: str,
    cells: Iterable[CellRecord],
    checks_by_cell: dict[str, list[Check]],
) -> list[Aggregate]:
    """Compute per-candidate, per-metric proportions from stored checks.

    Intervals and pass@k arrive in issue #4; this ticket stores simple
    proportions and leaves those columns null.
    """
    totals: dict[tuple[str, str], list[int]] = {}
    for cell in cells:
        for check in checks_by_cell.get(cell.cell_id, []):
            bucket = totals.setdefault((cell.candidate, check.metric), [0, 0])
            bucket[0] += int(check.passed)
            bucket[1] += 1
    aggregates = [
        Aggregate(
            run_id=run_id,
            candidate=candidate,
            metric=metric,
            n=passed_and_total[1],
            value=passed_and_total[0] / passed_and_total[1],
        )
        for (candidate, metric), passed_and_total in totals.items()
    ]
    return sorted(
        aggregates, key=lambda aggregate: (aggregate.candidate, aggregate.metric)
    )


def evaluate_gates(
    gates: Iterable[GateSpec],
    checks_by_cell: dict[str, list[Check]],
) -> list[GateResult]:
    """Evaluate each spec gate against the run-wide proportion for its metric."""
    totals: dict[str, list[int]] = {}
    for checks in checks_by_cell.values():
        for check in checks:
            bucket = totals.setdefault(check.metric, [0, 0])
            bucket[0] += int(check.passed)
            bucket[1] += 1
    results: list[GateResult] = []
    for gate in gates:
        passed_count, total = totals.get(gate.metric, [0, 0])
        value = passed_count / total if total else 0.0
        passed = total > 0
        if gate.min is not None and value < gate.min:
            passed = False
        if gate.max is not None and value > gate.max:
            passed = False
        results.append(
            GateResult(
                name=gate.metric,
                metric=gate.metric,
                passed=passed,
                detail={
                    "value": value,
                    "n": total,
                    "min": gate.min,
                    "max": gate.max,
                },
            )
        )
    return results
