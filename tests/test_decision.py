"""Behaviour tests for the decision report over one run."""

from __future__ import annotations

import pytest

from evalharness.core.decision import CandidateDecision, DecisionReport, decide_run
from evalharness.core.models import CellRecord, Check, RunRecord


def _run(metrics: list[str] | None = None) -> RunRecord:
    """Return one completed run, optionally declaring metric keys."""
    snapshot = {"metrics": [{"key": key} for key in metrics]} if metrics else {}
    return RunRecord(
        run_id="run-1",
        spec_name="decision",
        spec_snapshot=snapshot,
        status="completed",
    )


def _cell(
    candidate: str,
    case_id: str,
    *,
    status: str = "ok",
    wall_ms: int = 100,
    cost_usd: float = 0.0,
) -> CellRecord:
    """Return one cell for candidate on case."""
    return CellRecord(
        cell_id=f"{candidate}-{case_id}",
        run_id="run-1",
        candidate=candidate,
        case_id=case_id,
        replica=0,
        status=status,
        wall_ms=wall_ms,
        cost_usd=cost_usd,
    )


def _checks(passed: int, total: int, metric: str = "exact_match") -> list[Check]:
    """Return total checks, the first passed of them passing."""
    return [
        Check(metric=metric, passed=index < passed) for index in range(total)
    ]



def _report(
    cells: list[CellRecord],
    checks: dict[str, list[Check]],
    metrics: list[str] | None = None,
) -> DecisionReport:
    """Call decide_run with one run."""
    return decide_run(_run(metrics), cells, checks)


def test_report_ranks_candidates_and_reports_their_measures():
    alpha_a = _cell("alpha", "case-1", wall_ms=100, cost_usd=0.10)
    alpha_b = _cell("alpha", "case-2", wall_ms=300, cost_usd=0.20)
    beta_a = _cell("beta", "case-1", wall_ms=50, cost_usd=0.15)
    beta_b = _cell("beta", "case-2", wall_ms=50, cost_usd=0.15)
    checks = {
        alpha_a.cell_id: _checks(5, 5),
        alpha_b.cell_id: _checks(5, 5),
        beta_a.cell_id: _checks(2, 5),
        beta_b.cell_id: _checks(3, 5, metric="contains"),
    }
    report = _report(
        [alpha_a, alpha_b, beta_a, beta_b],
        checks,
        metrics=["exact_match", "contains"],
    )
    assert isinstance(report, DecisionReport)
    assert report.run_id == "run-1"
    assert report.primary_metrics == ("contains", "exact_match")
    assert [item.candidate for item in report.candidates] == ["alpha", "beta"]

    alpha = report.candidates[0]
    assert isinstance(alpha, CandidateDecision)
    assert alpha.rank == 1
    assert alpha.n_checks == 10
    assert alpha.passed == 10
    assert alpha.pass_rate == 1.0
    assert alpha.ci_low is not None and alpha.ci_high == 1.0
    assert alpha.n_cells == 2
    assert alpha.n_ok == 2
    assert alpha.n_errored == 0
    assert alpha.cost_usd == pytest.approx(0.30)
    assert alpha.cost_per_correct == pytest.approx(0.03)
    assert alpha.p50_latency_ms == 200.0
    assert alpha.p95_latency_ms == 290.0

    beta = report.candidates[1]
    assert beta.rank == 2
    assert beta.n_checks == 10
    assert beta.passed == 5
    assert beta.pass_rate == 0.5
    assert beta.cost_per_correct == pytest.approx(0.06)
    assert beta.p50_latency_ms == 50.0
    assert beta.p95_latency_ms == 50.0


def test_conservative_dominance_needs_interval_cost_and_latency():
    fast = [
        _cell("fast", "case-1", wall_ms=100, cost_usd=1.0),
        _cell("fast", "case-2", wall_ms=100, cost_usd=1.0),
    ]
    slow = [
        _cell("slow", "case-1", wall_ms=100, cost_usd=0.5),
        _cell("slow", "case-2", wall_ms=200, cost_usd=0.5),
    ]
    costlier = [_cell("costlier", "case-1", wall_ms=100, cost_usd=10.0)]
    checks = {
        fast[0].cell_id: _checks(5, 5),
        fast[1].cell_id: _checks(5, 5),
        slow[0].cell_id: _checks(1, 5),
        slow[1].cell_id: _checks(1, 5),
        costlier[0].cell_id: _checks(5, 5),
    }
    report = _report(fast + slow + costlier, checks, metrics=["exact_match"])
    by_name = {item.candidate: item for item in report.candidates}
    assert by_name["fast"].dominates == ("slow",)
    assert by_name["slow"].dominated_by == ("fast",)
    assert by_name["fast"].on_pareto_frontier is True
    assert by_name["slow"].on_pareto_frontier is False
    assert by_name["costlier"].on_pareto_frontier is True
    assert report.pareto_frontier == ("fast", "costlier")


def test_overlapping_intervals_and_worse_cost_never_dominate():
    a = [_cell("a", f"case-{index}", wall_ms=100, cost_usd=0.1) for index in range(1, 3)]
    b = [_cell("b", f"case-{index}", wall_ms=100, cost_usd=0.1) for index in range(1, 3)]
    checks = {
        a[0].cell_id: _checks(3, 5),
        a[1].cell_id: _checks(3, 5),
        b[0].cell_id: _checks(2, 5),
        b[1].cell_id: _checks(3, 5),
    }
    report = _report(a + b, checks)
    by_name = {item.candidate: item for item in report.candidates}
    assert by_name["a"].dominates == ()
    assert by_name["b"].dominates == ()
    assert by_name["a"].dominated_by == ()
    assert by_name["b"].dominated_by == ()


def test_unknown_cost_or_latency_cannot_dominate_or_be_dominated():
    known = [
        _cell("known", "case-1", wall_ms=100, cost_usd=2.0),
        _cell("known", "case-2", wall_ms=100, cost_usd=2.0),
    ]
    zero = [_cell("zero", "case-1", wall_ms=100, cost_usd=1.0)]
    unmeasured = [_cell("unmeasured", "case-1", status="error", cost_usd=0.5)]
    checks = {
        known[0].cell_id: _checks(5, 5),
        known[1].cell_id: _checks(5, 5),
        zero[0].cell_id: _checks(0, 5),
        unmeasured[0].cell_id: _checks(5, 5),
    }
    report = _report(known + zero + unmeasured, checks)
    by_name = {item.candidate: item for item in report.candidates}
    assert by_name["zero"].cost_per_correct is None
    assert by_name["zero"].dominated_by == ()
    assert by_name["unmeasured"].p95_latency_ms is None
    assert by_name["unmeasured"].dominates == ()
    assert by_name["known"].dominates == ()
    assert "known" in report.pareto_frontier
    assert "zero" not in report.pareto_frontier
    assert "unmeasured" not in report.pareto_frontier


def test_latency_uses_ok_cells_and_cost_counts_every_cell():
    ok = _cell("alpha", "case-1", wall_ms=100, cost_usd=1.0)
    broken = _cell("alpha", "case-2", status="error", wall_ms=9000, cost_usd=2.0)
    checks = {ok.cell_id: _checks(1, 1), broken.cell_id: _checks(0, 1)}
    report = _report([ok, broken], checks)
    alpha = report.candidates[0]
    assert alpha.n_cells == 2
    assert alpha.n_ok == 1
    assert alpha.n_errored == 1
    assert alpha.cost_usd == pytest.approx(3.0)
    assert alpha.p50_latency_ms == 100.0
    assert alpha.p95_latency_ms == 100.0


def test_no_cells_or_checks_is_undefined_not_an_exception():
    empty = _report([], {}, metrics=["exact_match"])
    assert empty.candidates == ()
    assert empty.pareto_frontier == ()
    assert empty.primary_metrics == ("exact_match",)

    cell = _cell("alpha", "case-1")
    no_checks = _report([cell], {}, metrics=["exact_match"])
    alpha = no_checks.candidates[0]
    assert alpha.pass_rate is None
    assert alpha.ci_low is None
    assert alpha.cost_per_correct is None
    assert alpha.p50_latency_ms == 100.0
    assert alpha.on_pareto_frontier is False
    assert alpha.rank == 1

    zero = _report([cell], {cell.cell_id: _checks(0, 4)})
    assert zero.candidates[0].pass_rate == 0.0
    assert zero.candidates[0].cost_per_correct is None
    assert zero.primary_metrics == ("exact_match",)


def test_checks_outside_the_runs_cells_and_undeclared_metrics_are_ignored():
    cell = _cell("alpha", "case-1")
    ghost = Check(metric="ghost", passed=True)
    report = _report(
        [cell],
        {cell.cell_id: _checks(1, 2, metric="contains"), "ghost": [ghost]},
    )
    alpha = report.candidates[0]
    assert alpha.n_checks == 2
    assert alpha.passed == 1
    assert report.primary_metrics == ("contains",)