"""Compare two runs or two candidates from stored cells, checks and snapshots.

A comparison is a decision, not a chart. Every metric row carries the sample
size and value on each side, the difference with a 95 percent Newcombe
interval, a verdict, the judge identity and the cost per correct answer. A
difference whose interval crosses zero is inconclusive, and a difference
measured under two different judges is never called a win.

Nothing here reads a database: the repository supplies stored records and this
module does the arithmetic. That keeps the comparison testable against hand
computed numbers and independent of how the console renders it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from evalharness.core.judges import judge_config_hash
from evalharness.core.models import CellRecord, Check, JudgeSpec, RunRecord
from evalharness.core.prices import price_table_identity
from evalharness.core.spec_projection import ProjectedJudge, project_document
from evalharness.core.stats import (
    delta_or_none,
    difference_interval,
    proportion,
    wilson_interval,
)

BETTER = "better"
"""B is higher and the interval lies above zero."""

WORSE = "worse"
"""B is lower and the interval lies below zero."""

INCONCLUSIVE = "inconclusive"
"""The interval crosses zero, so the difference is not a decision."""

UNDEFINED = "undefined"
"""A side has no samples, so there is no delta to judge."""

_NO_JUDGE_HASH = hashlib.sha256(b"no-judge").hexdigest()
"""Stable identity for a deterministic metric that names no judge."""


@dataclass(frozen=True, slots=True)
class PriceTable:
    """A run's resolved price table and a stable identity for it."""

    id: str
    prices: dict[str, dict[str, float]]


@dataclass(frozen=True, slots=True)
class JudgeIdentity:
    """The judge behind one metric on one side, with its configuration hash.

    name is None when the metric is deterministic and names no judge. source is
    "checks" when the hash came from stored checks and "spec" when it was
    derived from the run's spec snapshot because no check carried one.
    """

    name: str | None
    adapter: str | None
    model: str | None
    config_hash: str
    source: str


@dataclass(frozen=True, slots=True)
class MetricSide:
    """One metric's numbers on one side of a comparison."""

    n: int
    passed: int
    value: float | None
    ci_low: float | None
    ci_high: float | None
    cost_per_correct: float | None
    judge: JudgeIdentity


@dataclass(frozen=True, slots=True)
class MetricComparison:
    """One metric compared across the two sides, with its verdict."""

    metric: str
    a: MetricSide
    b: MetricSide
    delta: float | None
    interval_low: float | None
    interval_high: float | None
    verdict: str
    reason: str
    comparable: bool
    different_judge: bool


@dataclass(frozen=True, slots=True)
class CellLink:
    """A stored cell on the one side that has a case, enough to link to it."""

    run_id: str
    cell_id: str
    status: str


@dataclass(frozen=True, slots=True)
class OneSidedCase:
    """A case id recorded on only one side, with the cells that carry it."""

    case_id: str
    side: str
    cells: tuple[CellLink, ...]


@dataclass(frozen=True, slots=True)
class CompareSide:
    """One side's identity and totals, independent of any single metric."""

    run_id: str
    spec_name: str
    status: str
    candidate: str | None
    cost_usd: float
    n_cells: int
    n_ok: int
    n_checks: int
    n_passed: int
    cost_per_correct: float | None
    price_table: PriceTable
    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Comparison:
    """The whole decision: two sides, every metric row and the one-sided cases."""

    mode: str
    a: CompareSide
    b: CompareSide
    metrics: tuple[MetricComparison, ...]
    one_sided_cases: tuple[OneSidedCase, ...]
    comparable_metrics: tuple[str, ...]
    cross_judge_metrics: tuple[str, ...]
    one_sided_metrics: tuple[str, ...]


def compare_runs(
    run_a: RunRecord,
    cells_a: Iterable[CellRecord],
    checks_a: Mapping[str, list[Check]],
    run_b: RunRecord,
    cells_b: Iterable[CellRecord],
    checks_b: Mapping[str, list[Check]],
    *,
    candidate_a: str | None = None,
    candidate_b: str | None = None,
) -> Comparison:
    """Compare two runs, optionally narrowed to one candidate on each side.

    Parameter:
        checks_a, checks_b: checks per cell id, as the repository stored them.
        candidate_a, candidate_b: candidate names to narrow each side to; None
            pools every candidate the run contains.

    The delta on every row is B minus A. A metric that only one side declares
    still gets a row, with n = 0 and verdict undefined on the empty side, so a
    missing metric is visible rather than silently absent.
    """
    materialized_a = _select(cells_a, candidate_a)
    materialized_b = _select(cells_b, candidate_b)
    side_a = _side(run_a, materialized_a, checks_a, candidate_a)
    side_b = _side(run_b, materialized_b, checks_b, candidate_b)
    metrics = tuple(
        _metric_comparison(
            run_a,
            materialized_a,
            checks_a,
            run_b,
            materialized_b,
            checks_b,
            metric,
            side_a.cost_usd,
            side_b.cost_usd,
        )
        for metric in _metric_names(
            run_a, run_b, materialized_a, checks_a, materialized_b, checks_b
        )
    )
    return Comparison(
        mode="candidates" if candidate_a or candidate_b else "runs",
        a=side_a,
        b=side_b,
        metrics=metrics,
        one_sided_cases=_one_sided_cases(materialized_a, materialized_b),
        comparable_metrics=tuple(
            item.metric for item in metrics if item.comparable
        ),
        cross_judge_metrics=tuple(
            item.metric for item in metrics if item.different_judge
        ),
        one_sided_metrics=tuple(
            item.metric for item in metrics if item.a.n == 0 or item.b.n == 0
        ),
    )


def _select(
    cells: Iterable[CellRecord], candidate: str | None
) -> list[CellRecord]:
    """Narrow one side to a single candidate, or keep every cell."""
    materialized = list(cells)
    if candidate is None:
        return materialized
    return [cell for cell in materialized if cell.candidate == candidate]


def _metric_names(
    run_a: RunRecord,
    run_b: RunRecord,
    cells_a: list[CellRecord],
    checks_a: Mapping[str, list[Check]],
    cells_b: list[CellRecord],
    checks_b: Mapping[str, list[Check]],
) -> list[str]:
    """Return the union of declared and observed metric names, in stable order."""
    return list(
        dict.fromkeys(
            [
                *project_document(run_a.spec_snapshot).metrics,
                *project_document(run_b.spec_snapshot).metrics,
                *(
                    check.metric
                    for cells, checks in ((cells_a, checks_a), (cells_b, checks_b))
                    for cell in cells
                    for check in checks.get(cell.cell_id, [])
                ),
            ]
        )
    )


def _side(
    run: RunRecord,
    cells: list[CellRecord],
    checks_by_cell: Mapping[str, list[Check]],
    candidate: str | None,
) -> CompareSide:
    """Build one side's totals from its cells and checks."""
    checks = [
        check
        for cell in cells
        for check in checks_by_cell.get(cell.cell_id, [])
    ]
    passed = sum(1 for check in checks if check.passed)
    cost = sum(float(cell.cost_usd) for cell in cells)
    return CompareSide(
        run_id=run.run_id,
        spec_name=project_document(run.spec_snapshot).name,
        status=run.status,
        candidate=candidate,
        cost_usd=cost,
        n_cells=len(cells),
        n_ok=sum(1 for cell in cells if cell.status == "ok"),
        n_checks=len(checks),
        n_passed=passed,
        cost_per_correct=cost / passed if passed else None,
        price_table=_price_table(run),
        candidates=tuple(sorted({cell.candidate for cell in cells})),
    )


def _price_table(run: RunRecord) -> PriceTable:
    """Return the run's resolved prices with their canonical sha256 identity.

    The flattening and the fingerprint live in evalharness.core.prices, so the
    compare page and the run receipt quote the same table and the same identity
    for a run.
    """
    identity, prices = price_table_identity(run.prices_snapshot)
    return PriceTable(id=identity, prices=prices)


def _metric_comparison(
    run_a: RunRecord,
    cells_a: list[CellRecord],
    checks_a: Mapping[str, list[Check]],
    run_b: RunRecord,
    cells_b: list[CellRecord],
    checks_b: Mapping[str, list[Check]],
    metric: str,
    cost_a: float,
    cost_b: float,
) -> MetricComparison:
    """Compare one metric across the two sides and decide its verdict."""
    a = _metric_side(run_a, cells_a, checks_a, metric, cost_a)
    b = _metric_side(run_b, cells_b, checks_b, metric, cost_b)
    different_judge = a.judge.config_hash != b.judge.config_hash
    delta = delta_or_none(a.passed, a.n, b.passed, b.n)
    if delta is None:
        return MetricComparison(
            metric=metric,
            a=a,
            b=b,
            delta=None,
            interval_low=None,
            interval_high=None,
            verdict=UNDEFINED,
            reason="no samples on one side",
            comparable=False,
            different_judge=different_judge,
        )
    _, interval_low, interval_high = difference_interval(
        a.passed, a.n, b.passed, b.n
    )
    if different_judge:
        verdict = INCONCLUSIVE
        reason = "different judge: shown for context, not a win"
    elif interval_low > 0:
        verdict = BETTER
        reason = "B is higher and the interval excludes zero"
    elif interval_high < 0:
        verdict = WORSE
        reason = "B is lower and the interval excludes zero"
    else:
        verdict = INCONCLUSIVE
        reason = "the interval crosses zero"
    return MetricComparison(
        metric=metric,
        a=a,
        b=b,
        delta=delta,
        interval_low=interval_low,
        interval_high=interval_high,
        verdict=verdict,
        reason=reason,
        comparable=not different_judge,
        different_judge=different_judge,
    )


def _metric_side(
    run: RunRecord,
    cells: list[CellRecord],
    checks_by_cell: Mapping[str, list[Check]],
    metric: str,
    cost_usd: float,
) -> MetricSide:
    """Return one metric's n, value, interval, judge and cost per correct."""
    checks = [
        check
        for cell in cells
        for check in checks_by_cell.get(cell.cell_id, [])
        if check.metric == metric
    ]
    passed = sum(1 for check in checks if check.passed)
    n = len(checks)
    low, high = wilson_interval(passed, n) if n else (None, None)
    return MetricSide(
        n=n,
        passed=passed,
        value=proportion(passed, n),
        ci_low=low,
        ci_high=high,
        cost_per_correct=cost_usd / passed if passed else None,
        judge=_judge_identity(run.spec_snapshot, metric, checks),
    )


def _judge_identity(
    spec: Mapping[str, Any],
    metric: str,
    checks: list[Check],
) -> JudgeIdentity:
    """Return the judge named for one metric, preferring a stored check hash.

    A check's judge_config_hash is the authoritative record of what scored the
    cell. Before a run stores one, the same identity is derived from the spec
    snapshot with the scorer's own configuration hash, so a stored hash and a
    derived hash for the same declaration are directly comparable.
    """
    name, adapter, model, options = _judge_config(
        project_document(spec).judged_metrics, metric
    )
    snapshot_hash = (
        judge_config_hash(
            JudgeSpec(
                name=name,
                adapter=str(adapter or ""),
                model=model,
                options=options,
            )
        )
        if name is not None
        else _NO_JUDGE_HASH
    )
    stored = sorted(
        {check.judge_config_hash for check in checks if check.judge_config_hash}
    )
    if stored:
        return JudgeIdentity(name, adapter, model, stored[0], "checks")
    return JudgeIdentity(name, adapter, model, snapshot_hash, "spec")


def _judge_config(
    judged: Mapping[str, ProjectedJudge],
    metric: str,
) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    """Return the judge name, adapter, model and options a metric names."""
    judge = judged.get(metric)
    if judge is None:
        return None, None, None, {}
    return judge.name, judge.adapter, judge.model, dict(judge.options)


def _one_sided_cases(
    cells_a: list[CellRecord], cells_b: list[CellRecord]
) -> tuple[OneSidedCase, ...]:
    """Return every case id recorded on exactly one side, sorted by case id."""
    grouped_a = _by_case(cells_a)
    grouped_b = _by_case(cells_b)
    entries: list[OneSidedCase] = []
    for case_id in sorted(set(grouped_a) | set(grouped_b)):
        if case_id not in grouped_b:
            entries.append(_one_sided(case_id, "a", grouped_a[case_id]))
        elif case_id not in grouped_a:
            entries.append(_one_sided(case_id, "b", grouped_b[case_id]))
    return tuple(entries)


def _by_case(cells: list[CellRecord]) -> dict[str, list[CellRecord]]:
    """Group stored cells by case id."""
    grouped: dict[str, list[CellRecord]] = {}
    for cell in cells:
        grouped.setdefault(cell.case_id, []).append(cell)
    return grouped


def _one_sided(
    case_id: str, side: str, cells: list[CellRecord]
) -> OneSidedCase:
    """Build one one-sided case entry with a link to each of its cells."""
    return OneSidedCase(
        case_id=case_id,
        side=side,
        cells=tuple(
            CellLink(
                run_id=cell.run_id,
                cell_id=cell.cell_id,
                status=cell.status,
            )
            for cell in cells
        ),
    )
