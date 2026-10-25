"""Run metrics per cell, aggregate them with intervals, and evaluate gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from evalharness.core.models import (
    Aggregate,
    Case,
    CellRecord,
    Check,
    GateSpec,
    GateResult,
    ScoreContext,
)
from evalharness.core.protocols import Metric
from evalharness.core.stats import pass_at_k, pass_k, proportion, wilson_interval

_GroupKey = tuple[str, str, int | None]


async def checks_for_cell(
    cell: CellRecord,
    metrics: Iterable[Metric],
    ctx: ScoreContext,
) -> list[Check]:
    """Await every metric for one cell and return its checks in metric order."""
    checks: list[Check] = []
    for metric in metrics:
        checks.extend(await metric.check(cell, ctx))
    return checks


def aggregate_cells(
    run_id: str,
    cases: dict[str, Case],
    cells: Iterable[CellRecord],
    checks_by_cell: dict[str, list[Check]],
    metric_keys: Iterable[str],
    replicas: int = 1,
) -> list[Aggregate]:
    """Compute per-candidate, per-metric and per-tier aggregates.

    A group is one (candidate, metric) pair overall plus one group per case
    tier the candidate ran. The overall rows come first, then the tier rows in
    ascending tier order.

    Every group is seeded by the cells that ran, not by the checks that
    passed: a candidate with cells but no checks still gets a row, with n=0
    and value None, because an undefined proportion must stay visible. A
    candidate with no cells produces no row at all.

    pass_at_k and pass_k are filled only when the spec ran more than one
    replica. They are proportions over cases, so a case contributes one
    outcome per replica and the denominator is the number of complete cases.

    Parameter:
        cases: the run's cases by id, used for each case's tier.
        checks_by_cell: checks per cell id, as the executor recorded them.
        metric_keys: the spec's metric keys, so a metric with no checks
            anywhere still produces an undefined row.
        replicas: replicas per cell, from the spec.
    """
    materialized = list(cells)
    metric_names = list(
        dict.fromkeys(
            [
                *metric_keys,
                *(
                    check.metric
                    for checks in checks_by_cell.values()
                    for check in checks
                ),
            ]
        )
    )
    buckets: dict[_GroupKey, _Bucket] = {
        (candidate, metric, tier): _Bucket()
        for candidate, tier in _group_seeds(materialized, cases)
        for metric in metric_names
    }
    for cell in materialized:
        tier = _tier_for(cases, cell.case_id)
        for check in checks_by_cell.get(cell.cell_id, []):
            buckets.setdefault((cell.candidate, check.metric, None), _Bucket()).add(
                cell.case_id, cell.replica, check.passed
            )
            if tier is not None:
                buckets.setdefault(
                    (cell.candidate, check.metric, tier), _Bucket()
                ).add(cell.case_id, cell.replica, check.passed)

    aggregates: list[Aggregate] = []
    for (candidate, metric, tier), bucket in buckets.items():
        value = proportion(bucket.passed, bucket.n)
        ci_low: float | None = None
        ci_high: float | None = None
        if bucket.n:
            ci_low, ci_high = wilson_interval(bucket.passed, bucket.n)
        pass_at: float | None = None
        pass_all: float | None = None
        if replicas > 1:
            outcomes = bucket.case_outcomes(replicas)
            pass_at = pass_at_k(outcomes, replicas)
            pass_all = pass_k(outcomes, replicas)
        aggregates.append(
            Aggregate(
                run_id=run_id,
                candidate=candidate,
                metric=metric,
                tier=tier,
                n=bucket.n,
                value=value,
                ci_low=ci_low,
                ci_high=ci_high,
                pass_at_k=pass_at,
                pass_k=pass_all,
            )
        )
    return sorted(aggregates, key=_row_order)


def evaluate_gates(
    gates: Iterable[GateSpec],
    aggregates: Iterable[Aggregate],
) -> list[GateResult]:
    """Evaluate each spec gate against the stored aggregates, not raw checks.

    A gate that names a candidate reads that candidate's overall row. A gate
    without a candidate pools every candidate's overall row for the metric,
    weighting each row by its n so the run-wide figure is the proportion over
    every check. A metric with no samples is undefined, so its gate fails and
    says "no samples" in the detail: a threshold cannot be met by a value that
    does not exist.

    A gate with both bounds is checked against both; when it fails, the
    stored comparison and threshold are the bound that rejected the value
    (min first when both reject). When it passes, min is the stored bound,
    because it is the floor the gate exists to enforce.
    """
    overall = [row for row in aggregates if row.tier is None]
    by_candidate = {(row.candidate, row.metric): row for row in overall}
    results: list[GateResult] = []
    for gate in gates:
        observed, n = _gate_sample(gate, overall, by_candidate)
        min_failed = observed is not None and gate.min is not None and (
            observed < gate.min
        )
        max_failed = observed is not None and gate.max is not None and (
            observed > gate.max
        )
        passed = observed is not None and not min_failed and not max_failed
        comparison, threshold = _gate_bound(gate, min_failed, observed)
        detail: dict[str, Any] = {
            "value": observed,
            "n": n,
            "min": gate.min,
            "max": gate.max,
        }
        if observed is None:
            detail["reason"] = "no samples"
        results.append(
            GateResult(
                name=_gate_name(gate),
                metric=gate.metric,
                passed=passed,
                candidate=gate.candidate,
                comparison=comparison,
                threshold=threshold,
                observed=observed,
                n=n,
                detail=detail,
            )
        )
    return results


def _gate_name(gate: GateSpec) -> str:
    """Return the stored gate name, unique per metric and candidate."""
    if gate.candidate is None:
        return gate.metric
    return f"{gate.metric}[{gate.candidate}]"


def _gate_sample(
    gate: GateSpec,
    overall: list[Aggregate],
    by_candidate: dict[tuple[str, str], Aggregate],
) -> tuple[float | None, int]:
    """Return the aggregate value and n a gate observes, or (None, 0)."""
    if gate.candidate is not None:
        row = by_candidate.get((gate.candidate, gate.metric))
        if row is None:
            return None, 0
        return row.value, row.n
    rows = [row for row in overall if row.metric == gate.metric]
    n = sum(row.n for row in rows)
    if not n:
        return None, 0
    passed = sum(round((row.value or 0.0) * row.n) for row in rows)
    return proportion(passed, n), n


def _gate_bound(
    gate: GateSpec,
    min_failed: bool,
    observed: float | None,
) -> tuple[str, float | None]:
    """Return the comparison and threshold the gate stored and applied."""
    if gate.min is not None and (gate.max is None or min_failed or observed is None):
        return ">=", gate.min
    return "<=", gate.max


def _tier_for(cases: dict[str, Case], case_id: str) -> int | None:
    """Return the case's tier, or None when the case map does not hold it."""
    return cases[case_id].tier if case_id in cases else None


def _group_seeds(
    cells: list[CellRecord],
    cases: dict[str, Case],
) -> list[tuple[str, int | None]]:
    """Return one (candidate, tier-or-None) seed per distinct cell group."""
    seeds: list[tuple[str, int | None]] = []
    seen: set[tuple[str, int | None]] = set()
    for cell in cells:
        tier = _tier_for(cases, cell.case_id)
        for key in ((cell.candidate, None), (cell.candidate, tier)):
            if key not in seen:
                seen.add(key)
                seeds.append(key)
    return seeds


def _row_order(aggregate: Aggregate) -> tuple[str, str, bool, int]:
    """Sort overall rows before tier rows, then by candidate, metric and tier."""
    return (
        aggregate.candidate,
        aggregate.metric,
        aggregate.tier is not None,
        aggregate.tier or 0,
    )


@dataclass
class _Bucket:
    """Running totals for one candidate, metric and tier."""

    passed: int = 0
    n: int = 0
    by_case: dict[str, dict[int, bool]] = field(default_factory=dict)

    def add(self, case_id: str, replica: int, passed: bool) -> None:
        """Count one check and keep the replica outcome for pass@k."""
        self.passed += int(passed)
        self.n += 1
        per_replica = self.by_case.setdefault(case_id, {})
        per_replica[replica] = per_replica.get(replica, True) and passed

    def case_outcomes(self, replicas: int) -> list[bool]:
        """Return case-major outcomes, keeping only cases with all replicas.

        A case with a missing replica cannot honestly pass pass@k or fail
        pass^k, so it is left out of both.
        """
        return [
            per_replica[replica]
            for case_id, per_replica in sorted(self.by_case.items())
            if len(per_replica) == replicas
            for replica in sorted(per_replica)
        ]
