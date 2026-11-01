"""Page and JSON shaping for the per-run decision surface.

The page ranks the candidates of one run by pass rate, pairs each pass rate
with its Wilson interval, and places the Pareto frontier on a scatter of pass
rate against cost per correct check. A candidate whose cost or latency is
unknown shows as undefined: a missing number is not a good number.
"""

from __future__ import annotations

from dataclasses import dataclass

from evalharness.api.templating import format_usd
from evalharness.core.decision import CandidateDecision, DecisionReport, decide_run
from evalharness.core.models import RunRecord
from evalharness.storage.repository import Repository

CHART_WIDTH = 640.0
"""Width of the inline SVG scatter, in viewBox units."""

CHART_HEIGHT = 280.0
"""Height of the inline SVG scatter, in viewBox units."""

MARGIN_LEFT = 64.0
"""Left gutter that holds the cost per correct check tick labels."""

MARGIN_RIGHT = 20.0
"""Right gutter so a point at a 100 percent pass rate keeps its label."""

MARGIN_TOP = 24.0
"""Top gutter that holds the vertical axis title."""

MARGIN_BOTTOM = 44.0
"""Bottom gutter that holds the pass rate ticks and axis title."""


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """One stored run together with its decision report."""

    run: RunRecord
    report: DecisionReport


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """One display row of the ranked decision table."""

    candidate: str
    rank: int
    checks_label: str
    pass_rate_label: str
    interval_label: str
    cost_label: str
    latency_label: str
    cells_label: str
    dominance_label: str
    tone: str
    glyph: str


@dataclass(frozen=True, slots=True)
class ChartTick:
    """One labelled position on a scatter axis."""

    position: float
    label: str


@dataclass(frozen=True, slots=True)
class ChartPoint:
    """One Pareto candidate placed on the scatter."""

    candidate: str
    cx: float
    cy: float
    pass_rate_label: str
    cost_label: str


@dataclass(frozen=True, slots=True)
class ParetoChart:
    """The inline SVG geometry and labels of the Pareto scatter."""

    width: float
    height: float
    plot_left: float
    plot_right: float
    plot_top: float
    plot_bottom: float
    points: tuple[ChartPoint, ...]
    x_ticks: tuple[ChartTick, ...]
    y_ticks: tuple[ChartTick, ...]
    x_axis_label: str
    y_axis_label: str
    summary: str


def load_decision(repository: Repository, run_id: str) -> DecisionSnapshot | None:
    """Load one run with its cells and checks, or None when it does not exist."""
    run = repository.get_run(run_id)
    if run is None:
        return None
    report = decide_run(
        run,
        repository.list_cells(run_id),
        repository.list_checks_for_run(run_id),
    )
    return DecisionSnapshot(run=run, report=report)


def decision_page(snapshot: DecisionSnapshot) -> dict[str, object]:
    """Build the template context for one run's decision page."""
    report = snapshot.report
    return {
        "run": snapshot.run,
        "spec_name": snapshot.run.spec_snapshot.get("name") or "(unnamed spec)",
        "report": report,
        "candidates": tuple(_candidate_row(item) for item in report.candidates),
        "chart": _chart(report),
        "primary_metrics": _primary_metrics_label(report),
        "json_url": f"/api/runs/{report.run_id}/decision",
        "compare_url": f"/compare?a={report.run_id}",
    }


def decision_data(report: DecisionReport) -> dict[str, object]:
    """Return one decision report in its public JSON shape."""
    return {
        "run_id": report.run_id,
        "primary_metrics": list(report.primary_metrics),
        "pareto_frontier": list(report.pareto_frontier),
        "candidates": [
            {
                "candidate": item.candidate,
                "rank": item.rank,
                "n_checks": item.n_checks,
                "passed": item.passed,
                "pass_rate": item.pass_rate,
                "ci_low": item.ci_low,
                "ci_high": item.ci_high,
                "cost_usd": item.cost_usd,
                "cost_per_correct": item.cost_per_correct,
                "n_cells": item.n_cells,
                "n_ok": item.n_ok,
                "n_errored": item.n_errored,
                "p50_latency_ms": item.p50_latency_ms,
                "p95_latency_ms": item.p95_latency_ms,
                "dominates": list(item.dominates),
                "dominated_by": list(item.dominated_by),
                "on_pareto_frontier": item.on_pareto_frontier,
            }
            for item in report.candidates
        ],
    }


def _candidate_row(item: CandidateDecision) -> CandidateRow:
    """Turn one candidate decision into its display row."""
    tone, glyph, dominance_label = _dominance(item)
    return CandidateRow(
        candidate=item.candidate,
        rank=item.rank,
        checks_label=_checks_label(item),
        pass_rate_label=(
            _pct(item.pass_rate)
            if item.pass_rate is not None
            else "Pass rate undefined"
        ),
        interval_label=_interval(item),
        cost_label=(
            format_usd(item.cost_per_correct)
            if item.cost_per_correct is not None
            else "No correct check"
        ),
        latency_label=_latency_label(item),
        cells_label=_cells_label(item),
        dominance_label=dominance_label,
        tone=tone,
        glyph=glyph,
    )


def _dominance(item: CandidateDecision) -> tuple[str, str, str]:
    """Return the chip tone, glyph and words for one dominance call."""
    if item.dominated_by:
        return "bad", "▼", f"Dominated by {_names(item.dominated_by)}"
    if item.dominates:
        return "ok", "▲", f"Dominates {_names(item.dominates)}"
    if item.on_pareto_frontier:
        return "ok", "◆", "On the frontier"
    if item.pass_rate is None:
        return "neutral", "–", "Pass rate undefined"
    if item.cost_per_correct is None:
        return "warn", "!", "No correct check"
    return "neutral", "–", "Latency undefined"


def _names(candidates: tuple[str, ...]) -> str:
    """Join candidate names for a dominance sentence."""
    return ", ".join(candidates)


def _checks_label(item: CandidateDecision) -> str:
    """Describe how many checks a candidate passed out of its recorded checks."""
    if not item.n_checks:
        return "No checks recorded"
    noun = "check" if item.n_checks == 1 else "checks"
    return f"{item.passed} of {item.n_checks} {noun}"


def _interval(item: CandidateDecision) -> str:
    """Format the Wilson interval, or undefined when there is no sample."""
    if item.ci_low is None or item.ci_high is None:
        return "undefined"
    return f"{item.ci_low * 100:.1f}% to {item.ci_high * 100:.1f}%"


def _cells_label(item: CandidateDecision) -> str:
    """Describe the cell counts behind one candidate."""
    noun = "cell" if item.n_cells == 1 else "cells"
    label = f"{item.n_cells} {noun} · {item.n_ok} ok"
    if item.n_errored:
        label += f" · {item.n_errored} errored"
    return label


def _latency_label(item: CandidateDecision) -> str:
    """Format the ok-cell latency percentiles, or undefined without a sample."""
    if item.p50_latency_ms is None or item.p95_latency_ms is None:
        return "Latency undefined"
    return f"p50 {_ms(item.p50_latency_ms)} · p95 {_ms(item.p95_latency_ms)}"


def _ms(value: float) -> str:
    """Format one latency in whole milliseconds."""
    return f"{value:.0f} ms"


def _pct(value: float) -> str:
    """Format one proportion as a percentage with one decimal."""
    return f"{value * 100:.1f}%"


def _primary_metrics_label(report: DecisionReport) -> str:
    """Join the primary metric keys, or say none were declared."""
    return ", ".join(report.primary_metrics) if report.primary_metrics else "undefined"


def _chart(report: DecisionReport) -> ParetoChart | None:
    """Build the Pareto scatter, or None when the frontier is empty."""
    points = [item for item in report.candidates if item.on_pareto_frontier]
    if not points:
        return None
    costs = [item.cost_per_correct or 0.0 for item in points]
    low, high = min(costs), max(costs)
    if high - low < 1e-12:
        padding = high * 0.1 if high else 0.01
        low = max(0.0, low - padding)
        high += padding
    plot_left = MARGIN_LEFT
    plot_right = CHART_WIDTH - MARGIN_RIGHT
    plot_top = MARGIN_TOP
    plot_bottom = CHART_HEIGHT - MARGIN_BOTTOM
    chart_points = tuple(
        ChartPoint(
            candidate=item.candidate,
            cx=plot_left + (item.pass_rate or 0.0) * (plot_right - plot_left),
            cy=plot_bottom
            - ((item.cost_per_correct or 0.0) - low)
            / (high - low)
            * (plot_bottom - plot_top),
            pass_rate_label=_pct(item.pass_rate or 0.0),
            cost_label=format_usd(item.cost_per_correct),
        )
        for item in points
    )
    return ParetoChart(
        width=CHART_WIDTH,
        height=CHART_HEIGHT,
        plot_left=plot_left,
        plot_right=plot_right,
        plot_top=plot_top,
        plot_bottom=plot_bottom,
        points=chart_points,
        x_ticks=tuple(
            ChartTick(
                position=plot_left + ratio * (plot_right - plot_left),
                label=f"{ratio * 100:.0f}%",
            )
            for ratio in (0.0, 0.5, 1.0)
        ),
        y_ticks=tuple(
            ChartTick(
                position=plot_bottom - ratio * (plot_bottom - plot_top),
                label=format_usd(low + ratio * (high - low)),
            )
            for ratio in (0.0, 0.5, 1.0)
        ),
        x_axis_label="Pass rate",
        y_axis_label="Cost per correct check (lower is better)",
        summary=(
            f"{len(chart_points)} candidate"
            f"{'' if len(chart_points) == 1 else 's'} on the Pareto frontier: "
            + "; ".join(
                f"{point.candidate} at {point.pass_rate_label} pass rate and "
                f"{point.cost_label} per correct check"
                for point in chart_points
            )
            + ". Pass rate runs left to right; cost per correct check runs "
            "bottom to top, lower is better."
        ),
    )
