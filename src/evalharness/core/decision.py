"""Decide which candidate from one run to trust.

The report pools every check a candidate recorded in the run, pairs the
pass-rate interval with the cost per correct check and the cell latency, and
only calls one candidate better when the evidence is conservative: the
pass-rate interval must not overlap, and the other two measures must be known
and no worse. An undefined measure never wins a comparison, because a missing
number is not a good number.

The report is deliberately about one run. Comparing two runs, with their
possibly different judges and price tables, is the compare surface's job.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from evalharness.core.models import CellRecord, Check, RunRecord
from evalharness.core.stats import percentile, proportion, wilson_interval


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    """One candidate's ranked evidence from a single run."""

    candidate: str
    rank: int
    n_checks: int
    passed: int
    pass_rate: float | None
    ci_low: float | None
    ci_high: float | None
    cost_usd: float
    cost_per_correct: float | None
    n_cells: int
    n_ok: int
    n_errored: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    dominates: tuple[str, ...]
    dominated_by: tuple[str, ...]
    on_pareto_frontier: bool


@dataclass(frozen=True, slots=True)
class DecisionReport:
    """The decision view of one run: ranked candidates and its Pareto set."""

    run_id: str
    primary_metrics: tuple[str, ...]
    candidates: tuple[CandidateDecision, ...]
    pareto_frontier: tuple[str, ...]


def decide_run(
    run: RunRecord,
    cells: Sequence[CellRecord],
    checks_by_cell: Mapping[str, Sequence[Check]],
) -> DecisionReport:
    """Rank every candidate in one run and report the Pareto frontier.

    A candidate's pass rate pools every check its cells recorded, so a metric
    that records two checks per cell contributes twice. Cost per correct check
    divides the candidate's whole recorded cost by its passing checks, and is
    undefined when nothing passed. The latency percentiles use ok cells only,
    because an errored cell never measured an answer.

    Dominance is conservative. A dominates B only when A's Wilson lower bound
    is above B's upper bound, A's cost per correct check is no higher, A's p95
    latency is no higher, and all three of A's measures are known. A candidate
    with an undefined pass rate or cost per correct check is plotted as
    undefined rather than zero, so it is never on the frontier.

    Returns:
        A report whose candidates are in rank order, best first.
    """
    grouped: dict[str, list[CellRecord]] = {}
    for cell in cells:
        grouped.setdefault(cell.candidate, []).append(cell)

    candidates: list[CandidateDecision] = []
    all_checks: list[Check] = []
    for name in sorted(grouped):
        candidate_cells = grouped[name]
        candidate_checks = [
            check
            for cell in candidate_cells
            for check in checks_by_cell.get(cell.cell_id, ())
        ]
        all_checks.extend(candidate_checks)
        passed = sum(1 for check in candidate_checks if check.passed)
        n_checks = len(candidate_checks)
        pass_rate = proportion(passed, n_checks)
        if n_checks:
            ci_low, ci_high = wilson_interval(passed, n_checks)
        else:
            ci_low, ci_high = (None, None)
        cost_usd = sum(cell.cost_usd for cell in candidate_cells)
        ok_cells = [cell for cell in candidate_cells if cell.status == "ok"]
        latencies = [float(cell.wall_ms) for cell in ok_cells]
        candidates.append(
            CandidateDecision(
                candidate=name,
                rank=0,
                n_checks=n_checks,
                passed=passed,
                pass_rate=pass_rate,
                ci_low=ci_low,
                ci_high=ci_high,
                cost_usd=cost_usd,
                cost_per_correct=_cost_per_correct(cost_usd, passed),
                n_cells=len(candidate_cells),
                n_ok=len(ok_cells),
                n_errored=len(candidate_cells) - len(ok_cells),
                p50_latency_ms=_quantile(latencies, 0.50),
                p95_latency_ms=_quantile(latencies, 0.95),
                dominates=(),
                dominated_by=(),
                on_pareto_frontier=False,
            )
        )

    dominates = {item.candidate: [] for item in candidates}
    dominated_by = {item.candidate: [] for item in candidates}
    for candidate in candidates:
        for other in candidates:
            if candidate.candidate == other.candidate:
                continue
            if _dominates(candidate, other):
                dominates[candidate.candidate].append(other.candidate)
                dominated_by[other.candidate].append(candidate.candidate)

    ranked = sorted(candidates, key=_rank_key)
    decisions = tuple(
        replace(
            item,
            rank=index,
            dominates=tuple(sorted(dominates[item.candidate])),
            dominated_by=tuple(sorted(dominated_by[item.candidate])),
            on_pareto_frontier=(
                _known(item) and not dominated_by[item.candidate]
            ),
        )
        for index, item in enumerate(ranked, start=1)
    )
    return DecisionReport(
        run_id=run.run_id,
        primary_metrics=_primary_metrics(run, all_checks),
        candidates=decisions,
        pareto_frontier=tuple(
            item.candidate for item in decisions if item.on_pareto_frontier
        ),
    )


def _cost_per_correct(cost_usd: float, passed: int) -> float | None:
    """Return cost per passing check, or None when nothing passed."""
    return cost_usd / passed if passed else None


def _quantile(values: list[float], quantile: float) -> float | None:
    """Return an observed percentile, or None when there is no sample."""
    return percentile(values, quantile) if values else None


def _known(item: CandidateDecision) -> bool:
    """Return whether all three frontier measures are defined."""
    return (
        item.pass_rate is not None
        and item.ci_low is not None
        and item.ci_high is not None
        and item.cost_per_correct is not None
        and item.p95_latency_ms is not None
    )


def _dominates(a: CandidateDecision, b: CandidateDecision) -> bool:
    """Return whether a dominates b under the conservative rule."""
    if not (_known(a) and _known(b)):
        return False
    return (
        a.ci_low is not None
        and b.ci_high is not None
        and a.cost_per_correct is not None
        and b.cost_per_correct is not None
        and a.p95_latency_ms is not None
        and b.p95_latency_ms is not None
        and a.ci_low > b.ci_high
        and a.cost_per_correct <= b.cost_per_correct
        and a.p95_latency_ms <= b.p95_latency_ms
    )


def _rank_key(
    item: CandidateDecision,
) -> tuple[bool, float, bool, float, bool, float, str]:
    """Sort a known pass rate first, then the cheaper and faster candidate."""
    return (
        item.pass_rate is None,
        -(item.pass_rate or 0.0),
        item.cost_per_correct is None,
        item.cost_per_correct or 0.0,
        item.p95_latency_ms is None,
        item.p95_latency_ms or 0.0,
        item.candidate,
    )


def _primary_metrics(run: RunRecord, checks: Sequence[Check]) -> tuple[str, ...]:
    """Return the spec's declared metric keys, or the observed ones."""
    declared: list[str] = []
    metrics = run.spec_snapshot.get("metrics", [])
    if isinstance(metrics, list):
        for item in metrics:
            if isinstance(item, dict) and isinstance(item.get("key"), str):
                declared.append(item["key"])
    if declared:
        return tuple(sorted(dict.fromkeys(declared)))
    return tuple(sorted({check.metric for check in checks}))