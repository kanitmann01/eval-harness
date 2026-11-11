"""Page and JSON shaping for the compare surface.

The arithmetic lives in :mod:`evalharness.core.compare`. This module resolves
the query string to two runs and, optionally, two candidates, then turns the
comparison into the plain data the API returns and the display strings the
template renders. Both outputs read the same object, so JSON and HTML cannot
disagree about a value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from evalharness.api.graph_views import SpecGraph, run_graph
from evalharness.api.templating import format_usd
from evalharness.core.compare import (
    BETTER,
    INCONCLUSIVE,
    UNDEFINED,
    WORSE,
    Comparison,
    CompareSide,
    JudgeIdentity,
    MetricComparison,
    MetricSide,
    OneSidedCase,
    compare_runs,
)
from evalharness.core.models import CellRecord
from evalharness.core.stats import required_sample_size
from evalharness.read_model import RunRead, load_run_read, run_diff_data
from evalharness.storage.repository import Repository, RunSummary

GRAPH_WIDTH = 640.0
"""The compare page draws two labelled graphs side by side, so each one is narrow."""


class CompareError(Exception):
    """A comparison request that cannot be answered, with an actionable fix."""


@dataclass(frozen=True, slots=True)
class Selection:
    """The two run ids and optional candidate names a query string names."""

    a: str
    b: str
    candidate_a: str | None
    candidate_b: str | None
    mode: str


@dataclass(frozen=True, slots=True)
class DecisionView:
    """The one-sentence answer the compare page leads with."""

    tone: str
    glyph: str
    label: str
    headline: str
    sentence: str
    support: tuple[tuple[str, str], ...]
    caveat: str
    next_step: str


def select(
    a: str = "",
    b: str = "",
    run: str = "",
    candidate_a: str = "",
    candidate_b: str = "",
) -> Selection | None:
    """Resolve the query parameters to a selection, or None when nothing is asked.

    Raises:
        CompareError: when the parameters are incomplete or ambiguous, naming
            the query argument to add and what it should contain.
    """
    left = a or run
    right = b or run
    cand_a = candidate_a or None
    cand_b = candidate_b or None
    if cand_a is not None or cand_b is not None:
        if cand_a is None or cand_b is None:
            raise CompareError(
                "a candidate comparison needs both candidate_a and "
                "candidate_b; add the missing candidate name"
            )
        if not left or not right:
            raise CompareError(
                "a candidate comparison needs a run; add ?run=<run id>, or "
                "?a=<run id>&b=<run id> for two runs"
            )
        return Selection(left, right, cand_a, cand_b, "candidates")
    if not a and not b and not run:
        return None
    if run and not a and not b:
        raise CompareError(
            "comparing a run with itself needs two candidates; add "
            "?candidate_a=<name>&candidate_b=<name>, or pass two run ids "
            "with ?a=<run id>&b=<run id>"
        )
    if not a or not b:
        missing = "b" if a else "a"
        raise CompareError(
            f"a comparison needs two run ids; add ?{missing}=<run id> to the "
            "query string"
        )
    return Selection(a, b, None, None, "runs")


def load_comparison(
    repository: Repository,
    *,
    a: str = "",
    b: str = "",
    run: str = "",
    candidate_a: str = "",
    candidate_b: str = "",
) -> Comparison | None:
    """Read both sides and compare them, or return None when nothing was asked.

    Raises:
        CompareError: for an unknown run, an unknown candidate, or an
            incomplete query string.
    """
    selection = select(a, b, run, candidate_a, candidate_b)
    if selection is None:
        return None
    return _compare(selection, _load_reads(repository, selection))


def _load_reads(
    repository: Repository, selection: Selection
) -> dict[str, RunRead]:
    """Load each distinct run in a selection once, keyed by run id."""
    reads: dict[str, RunRead] = {}
    for run_id in (selection.a, selection.b):
        if run_id in reads:
            continue
        read = load_run_read(repository, run_id)
        if read is None:
            raise CompareError(
                f"run {run_id} does not exist; open / for the list of runs, or "
                "run a demo from Demos to record one"
            )
        reads[run_id] = read
    return reads


def _compare(selection: Selection, reads: dict[str, RunRead]) -> Comparison:
    """Compare two loaded sides, refusing an unknown candidate."""
    read_a = reads[selection.a]
    read_b = reads[selection.b]
    _require_candidate(read_a.cells, selection.candidate_a, selection.a)
    _require_candidate(read_b.cells, selection.candidate_b, selection.b)
    return compare_runs(
        read_a.run,
        read_a.cells,
        read_a.checks_by_cell,
        read_b.run,
        read_b.cells,
        read_b.checks_by_cell,
        candidate_a=selection.candidate_a,
        candidate_b=selection.candidate_b,
    )


def _require_candidate(
    cells: list[CellRecord],
    candidate: str | None,
    run_id: str,
) -> None:
    """Reject a candidate name that has no cells in the run it was given."""
    if candidate is None:
        return
    choices = (
        ", ".join(sorted({cell.candidate for cell in cells}))
        or "none (this run has no cells yet)"
    )
    if candidate not in {cell.candidate for cell in cells}:
        raise CompareError(
            f"candidate {candidate} is not part of run {run_id}; use one of: "
            f"{choices}"
        )


def comparison_data(comparison: Comparison) -> dict[str, Any]:
    """Return the JSON shape of a comparison."""
    return {
        "mode": comparison.mode,
        "a": _side_data(comparison.a),
        "b": _side_data(comparison.b),
        "same_price_table": (
            comparison.a.price_table.id == comparison.b.price_table.id
        ),
        "metrics": [_metric_data(item) for item in comparison.metrics],
        "one_sided_cases": [
            _one_sided_data(item) for item in comparison.one_sided_cases
        ],
        "comparable_metrics": list(comparison.comparable_metrics),
        "cross_judge_metrics": list(comparison.cross_judge_metrics),
        "one_sided_metrics": list(comparison.one_sided_metrics),
    }


def _side_data(side: CompareSide) -> dict[str, Any]:
    """Return the JSON shape of one side's identity and totals."""
    return {
        "run_id": side.run_id,
        "spec_name": side.spec_name,
        "status": side.status,
        "candidate": side.candidate,
        "cost_usd": side.cost_usd,
        "cost_per_correct": side.cost_per_correct,
        "n_cells": side.n_cells,
        "n_ok": side.n_ok,
        "n_checks": side.n_checks,
        "n_passed": side.n_passed,
        "candidates": list(side.candidates),
        "price_table": {
            "id": side.price_table.id,
            "prices": side.price_table.prices,
        },
    }


def _metric_data(item: MetricComparison) -> dict[str, Any]:
    """Return the JSON shape of one metric comparison."""
    return {
        "metric": item.metric,
        "a": _metric_side_data(item.a),
        "b": _metric_side_data(item.b),
        "delta": item.delta,
        "interval_low": item.interval_low,
        "interval_high": item.interval_high,
        "verdict": item.verdict,
        "reason": item.reason,
        "comparable": item.comparable,
        "different_judge": item.different_judge,
    }


def _metric_side_data(side: MetricSide) -> dict[str, Any]:
    """Return the JSON shape of one metric's numbers on one side."""
    return {
        "n": side.n,
        "passed": side.passed,
        "value": side.value,
        "ci_low": side.ci_low,
        "ci_high": side.ci_high,
        "cost_per_correct": side.cost_per_correct,
        "judge": _judge_data(side.judge),
    }


def _judge_data(judge: JudgeIdentity) -> dict[str, Any]:
    """Return the JSON shape of a judge identity, including where the hash came from."""
    return {
        "name": judge.name,
        "adapter": judge.adapter,
        "model": judge.model,
        "config_hash": judge.config_hash,
        "hash_source": judge.source,
    }


def _one_sided_data(item: OneSidedCase) -> dict[str, Any]:
    """Return the JSON shape of one case recorded on only one side."""
    return {
        "case_id": item.case_id,
        "side": item.side,
        "count": len(item.cells),
        "cells": [
            {
                "cell_id": cell.cell_id,
                "run_id": cell.run_id,
                "status": cell.status,
                "url": f"/runs/{cell.run_id}/cells/{cell.cell_id}",
            }
            for cell in item.cells
        ],
    }


def comparison_page(
    repository: Repository,
    *,
    a: str = "",
    b: str = "",
    run: str = "",
    candidate_a: str = "",
    candidate_b: str = "",
) -> dict[str, Any]:
    """Build the context for the compare page, including the form choices.

    With no query parameters the page still renders: it explains what a
    comparison needs and offers the run list. A malformed or unknown selection
    raises :class:`CompareError` so the route can answer with a 404 page.
    """
    runs = repository.list_run_summaries()
    selection = select(a, b, run, candidate_a, candidate_b)
    context: dict[str, Any] = {
        "runs": runs,
        "run_choices": [
            {"value": summary.run_id, "label": _run_label(summary)}
            for summary in runs
        ],
        "selected_a": a or run,
        "selected_b": b or run,
        "candidate_a": candidate_a,
        "candidate_b": candidate_b,
        "comparison": None,
        "graph_a": None,
        "graph_b": None,
        "empty_message": _empty_message(runs),
        "json_url": _json_url(a, b, run, candidate_a, candidate_b),
        "swap_url": _swap_url(a, b, run, candidate_a, candidate_b),
    }
    if selection is None:
        return context
    reads = _load_reads(repository, selection)
    comparison = _compare(selection, reads)
    context["comparison"] = comparison_view(comparison)
    context["graph_a"] = _side_graph(reads[selection.a])
    context["graph_b"] = _side_graph(reads[selection.b])
    if selection.a != selection.b:
        context["spec_diff"] = run_diff_data(
            reads[selection.b], reads[selection.a]
        )
    return context


def _side_graph(read: RunRead) -> SpecGraph:
    """Return the spec graph of one already loaded side.

    The graph uses the run's stored cells and gates, so the picture reports
    real run outcomes rather than the pending draft the wizard rail shows.
    """
    return run_graph(
        read.run,
        read.cells,
        read.gates,
        width=GRAPH_WIDTH,
        short_labels=True,
    )


def _run_label(summary: RunSummary) -> str:
    """Return the human label for one run option in the form."""
    name = summary.spec_name or "(unnamed spec)"
    return f"{name} · {summary.status} · {summary.run_id[:12]}"


def _empty_message(runs: Sequence[RunSummary]) -> str:
    """Explain what the page needs, naming how many runs it can offer."""
    if runs:
        count = len(runs)
        return (
            f"Pick two of the {count} run{'s' if count != 1 else ''} in the menus "
            "above and choose Compare, or add ?a=<run id>&b=<run id> to the "
            "URL. For two candidates, add "
            "?run=<run id>&candidate_a=<name>&candidate_b=<name>."
        )
    return (
        "No runs yet. Run a demo from Demos, or build a comparison with the "
        "wizard and launch it; two runs are needed before they can be compared."
    )


def _json_url(
    a: str,
    b: str,
    run: str,
    candidate_a: str,
    candidate_b: str,
) -> str:
    """Return the API URL that answers the same query as this page."""
    params = {
        key: value
        for key, value in {
            "a": a,
            "b": b,
            "run": run,
            "candidate_a": candidate_a,
            "candidate_b": candidate_b,
        }.items()
        if value
    }
    return "/api/compare?" + urlencode(params) if params else "/api/compare"


def comparison_view(comparison: Comparison) -> dict[str, Any]:
    """Return the display shape the compare template renders."""
    return {
        "mode": comparison.mode,
        "a": _side_view(comparison.a),
        "b": _side_view(comparison.b),
        "same_price_table": (
            comparison.a.price_table.id == comparison.b.price_table.id
        ),
        "metrics": [_metric_view(item) for item in comparison.metrics],
        "one_sided_cases": [
            _one_sided_view(item) for item in comparison.one_sided_cases
        ],
        "decision": decision_view(comparison),
        "comparable_metrics": list(comparison.comparable_metrics),
        "cross_judge_metrics": list(comparison.cross_judge_metrics),
        "one_sided_metrics": list(comparison.one_sided_metrics),
    }


def _side_view(side: CompareSide) -> dict[str, Any]:
    """Return the display strings for one side."""
    return {
        "label": side.candidate or "all candidates",
        "run_id": side.run_id,
        "spec_name": side.spec_name or "(unnamed spec)",
        "status": side.status,
        "candidate": side.candidate,
        "cost_text": _usd(side.cost_usd),
        "cost_per_correct_text": _usd(side.cost_per_correct),
        "n_cells": side.n_cells,
        "n_ok": side.n_ok,
        "n_checks": side.n_checks,
        "n_passed": side.n_passed,
        "price_table_id": side.price_table.id,
        "price_table_short": side.price_table.id[:12],
        "price_models": (
            ", ".join(sorted(side.price_table.prices)) or "none recorded"
        ),
    }


def _metric_view(item: MetricComparison) -> dict[str, Any]:
    """Return the display strings for one metric row."""
    return {
        "metric": item.metric,
        "a_n": item.a.n,
        "a_value_text": _pct(item.a.value),
        "a_interval_text": _ci(item.a.ci_low, item.a.ci_high),
        "a_cost_text": _usd(item.a.cost_per_correct),
        "b_n": item.b.n,
        "b_value_text": _pct(item.b.value),
        "b_interval_text": _ci(item.b.ci_low, item.b.ci_high),
        "b_cost_text": _usd(item.b.cost_per_correct),
        "delta_text": _pp(item.delta),
        "delta_interval_text": _pp_interval(
            item.interval_low, item.interval_high
        ),
        "verdict": item.verdict,
        "verdict_label": _verdict_label(item),
        "verdict_class": _verdict_class(item),
        "verdict_glyph": _verdict_glyph(item.verdict),
        "different_judge": item.different_judge,
        "comparable": item.comparable,
        "judge_a_name": _judge_name(item.a.judge),
        "judge_a_detail": _judge_detail(item.a.judge),
        "judge_b_name": _judge_name(item.b.judge),
        "judge_b_detail": _judge_detail(item.b.judge),
        "reason": item.reason,
    }


def _one_sided_view(item: OneSidedCase) -> dict[str, Any]:
    """Return the display shape for one case recorded on only one side."""
    return {
        "case_id": item.case_id,
        "side": item.side,
        "side_label": item.side.upper(),
        "count": len(item.cells),
        "cells": [
            {
                "cell_id": cell.cell_id,
                "status": cell.status,
                "url": f"/runs/{cell.run_id}/cells/{cell.cell_id}",
            }
            for cell in item.cells
        ],
    }


def decision_view(comparison: Comparison) -> DecisionView:
    """Return the decision sentence the compare page leads with.

    The sentence is built from the same MetricComparison objects the table
    renders, so the headline, the verdict chips and the numbers cannot
    disagree. A cross-judge comparison never produces a win.
    """
    metrics = comparison.metrics
    comparable = [item for item in metrics if item.comparable]
    if comparison.cross_judge_metrics:
        names = ", ".join(comparison.cross_judge_metrics)
        return _decision(
            "cross-judge",
            "!",
            "Not comparable",
            f"Different judge on {names}",
            "The two sides were not scored by the same judge configuration, "
            "so the delta is context only and no win is claimed.",
            (),
            _compare_caveat(comparison, None),
        )
    if not metrics:
        return _decision(
            "undefined",
            "–",
            "No metrics",
            "No metric was declared",
            "Neither side declares a metric, so there is no delta to judge.",
            (),
            "",
        )
    if not comparable:
        return _decision(
            "undefined",
            "–",
            "Undefined",
            "No comparable samples",
            "No metric has samples on both sides under the same judge, so the "
            "rows below read undefined, never zero.",
            (),
            _compare_caveat(comparison, None),
        )

    primary = next(
        (item for item in comparable if item.verdict != UNDEFINED),
        comparable[0],
    )
    support = _decision_support(primary)
    caveat = _compare_caveat(comparison, primary)
    verdicts = {item.verdict for item in comparable}
    if verdicts == {BETTER}:
        return _decision(
            "better",
            "▲",
            "B is better",
            f"B is better on {primary.metric}",
            f"B is {_pp(primary.delta)} on {primary.metric} (95% interval "
            f"{_pp_interval(primary.interval_low, primary.interval_high)}); "
            "the interval excludes zero.",
            support,
            caveat,
            _sample_size_line(primary),
        )
    if verdicts == {WORSE}:
        return _decision(
            "worse",
            "▼",
            "A is better",
            f"A is better on {primary.metric}",
            f"B is {_pp(primary.delta)} on {primary.metric} (95% interval "
            f"{_pp_interval(primary.interval_low, primary.interval_high)}); "
            "the interval excludes zero, in the other direction.",
            support,
            caveat,
            _sample_size_line(primary),
        )
    if verdicts == {INCONCLUSIVE}:
        return _decision(
            "inconclusive",
            "?",
            "Inconclusive",
            f"No established difference on {primary.metric}",
            f"B is {_pp(primary.delta)} from A on {primary.metric}, but the "
            f"95% interval {_pp_interval(primary.interval_low, primary.interval_high)} "
            "crosses zero, so the result is inconclusive.",
            support,
            caveat,
            _sample_size_line(primary),
        )
    better = [item.metric for item in comparable if item.verdict == BETTER]
    worse = [item.metric for item in comparable if item.verdict == WORSE]
    return _decision(
        "mixed",
        "±",
        "Mixed result",
        "The metrics disagree",
        f"B is better on {_join(better)} and worse on {_join(worse)}; read "
        "each metric row rather than one headline.",
        support,
        caveat,
        _sample_size_line(primary),
    )


def _decision(
    tone: str,
    glyph: str,
    label: str,
    headline: str,
    sentence: str,
    support: tuple[tuple[str, str], ...],
    caveat: str,
    next_step: str = "",
) -> DecisionView:
    """Build one decision view; the fields mirror the page layout."""
    return DecisionView(
        tone=tone,
        glyph=glyph,
        label=label,
        headline=headline,
        sentence=sentence,
        support=support,
        caveat=caveat,
        next_step=next_step,
    )


def _sample_size_line(item: MetricComparison) -> str:
    """Return the sample size that would settle the primary metric."""
    if item.a.value is None or item.b.value is None or item.delta is None:
        return (
            f"Change my mind: the delta on {item.metric} is undefined, so a "
            "required sample size cannot be computed."
        )
    if item.verdict in (BETTER, WORSE):
        return (
            "Change my mind: the 95% interval already excludes zero with "
            f"n={item.a.n} on side A and n={item.b.n} on side B, so no larger "
            "sample is needed to establish this direction."
        )
    if item.delta == 0:
        return (
            f"Change my mind: the observed delta on {item.metric} is exactly "
            "zero, so no sample size would exclude zero."
        )
    try:
        needed = required_sample_size(item.a.value, item.delta)
    except ValueError:
        return (
            f"Change my mind: the values on {item.metric} fall outside 0 to 1, "
            "so the two-proportion sample size does not apply."
        )
    return (
        "Change my mind: to exclude zero at 80 percent power (two-sided alpha "
        f"0.05) you would need about n={needed} per side; this comparison has "
        f"n={item.a.n} on side A and n={item.b.n} on side B."
    )


def _decision_support(item: MetricComparison) -> tuple[tuple[str, str], ...]:
    """Return the small print under the decision: values, n and cost."""
    return (
        ("A", f"{_pct(item.a.value)} (n={item.a.n})"),
        ("B", f"{_pct(item.b.value)} (n={item.b.n})"),
        (
            "Cost per correct",
            f"A {_usd(item.a.cost_per_correct)} · B {_usd(item.b.cost_per_correct)}",
        ),
    )


def _compare_caveat(
    comparison: Comparison,
    primary: MetricComparison | None,
) -> str:
    """Return the caveats that stop a reader over-reading the headline."""
    parts: list[str] = []
    if comparison.a.price_table.id != comparison.b.price_table.id:
        parts.append(
            "The sides used different price tables, so the cost figures are "
            "not directly comparable."
        )
    if comparison.one_sided_cases:
        count = len(comparison.one_sided_cases)
        parts.append(
            f"{count} case{'s' if count != 1 else ''} recorded on only one "
            "side; listed below and never counted as a tie."
        )
    if primary is not None:
        smallest = min(primary.a.n, primary.b.n)
        if smallest < 5:
            parts.append(
                f"Small samples: the smaller side has n={smallest}, which the "
                "interval reflects."
            )
    return " ".join(parts)


def _join(names: list[str]) -> str:
    """Join metric names for a sentence, saying something for an empty list."""
    return ", ".join(names) if names else "none"


def _ci(low: float | None, high: float | None) -> str:
    """Format a side's Wilson interval, or say it is undefined."""
    if low is None or high is None:
        return "undefined"
    return f"{low * 100:.1f}% to {high * 100:.1f}%"


def _swap_url(
    a: str,
    b: str,
    run: str,
    candidate_a: str,
    candidate_b: str,
) -> str:
    """Return the same comparison with its two sides exchanged."""
    if candidate_a and candidate_b:
        params = {
            "run": run or a,
            "candidate_a": candidate_b,
            "candidate_b": candidate_a,
        }
    else:
        params = {"a": b or run, "b": a or run}
    return "/compare?" + urlencode(params)


def _usd(value: float | None) -> str:
    """Format a USD amount, or undefined when it cannot be computed."""
    return "undefined" if value is None else format_usd(value)


def _pct(value: float | None) -> str:
    """Format a proportion, or undefined when there are no samples."""
    return "undefined" if value is None else f"{value * 100:.1f}%"


def _pp(value: float | None) -> str:
    """Format a delta as percentage points, or undefined when absent."""
    return "undefined" if value is None else f"{value * 100:+.1f} pp"


def _pp_interval(low: float | None, high: float | None) -> str:
    """Format a delta interval as percentage points, or undefined."""
    if low is None or high is None:
        return "undefined"
    return f"{low * 100:+.1f} to {high * 100:+.1f} pp"


def _judge_name(judge: JudgeIdentity) -> str:
    """Return the judge's name, or say the metric is deterministic."""
    return judge.name or "none (deterministic metric)"


def _judge_detail(judge: JudgeIdentity) -> str:
    """Return adapter, model and hash provenance for one judge."""
    parts = [
        judge.adapter or "built-in metric",
        judge.model or "no model",
        f"hash {judge.config_hash[:12]} ({judge.source})",
    ]
    return " · ".join(parts)


def _verdict_label(item: MetricComparison) -> str:
    """Return the human verdict, never calling a cross-judge delta a win."""
    if item.verdict == UNDEFINED:
        return "Undefined"
    if item.different_judge:
        return "Different judge: not a win"
    return {
        BETTER: "B better",
        WORSE: "B worse",
        INCONCLUSIVE: "Inconclusive",
    }[item.verdict]


def _verdict_class(item: MetricComparison) -> str:
    """Return the CSS class carrying the verdict, with a distinct cross-judge tone."""
    if item.verdict == UNDEFINED:
        return "undefined"
    if item.different_judge:
        return "different-judge"
    return item.verdict


def _verdict_glyph(verdict: str) -> str:
    """Return a text glyph so colour is never the only verdict signal."""
    return {
        BETTER: "▲",
        WORSE: "▼",
        INCONCLUSIVE: "?",
        UNDEFINED: "–",
    }[verdict]
