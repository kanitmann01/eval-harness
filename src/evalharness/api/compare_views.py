"""Page and JSON shaping for the compare surface.

The arithmetic lives in :mod:`evalharness.core.compare`. This module resolves
the query string to two runs and, optionally, two candidates, then turns the
comparison into the plain data the API returns and the display strings the
template renders. Both outputs read the same object, so JSON and HTML cannot
disagree about a value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

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
from evalharness.core.models import CellRecord, RunRecord
from evalharness.storage.repository import Repository, RunSummary


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
    run_a = _run(repository, selection.a)
    run_b = _run(repository, selection.b)
    cells_a = repository.list_cells(selection.a)
    cells_b = repository.list_cells(selection.b)
    _require_candidate(cells_a, selection.candidate_a, selection.a)
    _require_candidate(cells_b, selection.candidate_b, selection.b)
    return compare_runs(
        run_a,
        cells_a,
        repository.list_checks_for_run(run_a.run_id),
        run_b,
        cells_b,
        repository.list_checks_for_run(run_b.run_id),
        candidate_a=selection.candidate_a,
        candidate_b=selection.candidate_b,
    )


def _run(repository: Repository, run_id: str) -> RunRecord:
    """Read one run, or raise an actionable error naming the fix."""
    record = repository.get_run(run_id)
    if record is None:
        raise CompareError(
            f"run {run_id} does not exist; open / for the list of runs, or "
            "start one with 'evalh run spec.yaml'"
        )
    return record


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
    comparison = load_comparison(
        repository, a=a, b=b, run=run, candidate_a=candidate_a, candidate_b=candidate_b
    )
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
        "empty_message": _empty_message(bool(runs)),
        "json_url": _json_url(a, b, run, candidate_a, candidate_b),
    }
    if comparison is not None:
        context["comparison"] = comparison_view(comparison)
    return context


def _run_label(summary: RunSummary) -> str:
    """Return the human label for one run option in the form."""
    name = summary.spec_name or "(unnamed spec)"
    return f"{name} · {summary.status} · {summary.run_id[:12]}"


def _empty_message(has_runs: bool) -> str:
    """Explain what the page needs, depending on whether any run exists."""
    if has_runs:
        return (
            "Pick two runs above and choose Compare, or add ?a=<run id>&b=<run id> "
            "to the URL. For two candidates, add "
            "?run=<run id>&candidate_a=<name>&candidate_b=<name>."
        )
    return (
        "No runs yet. Start one from a terminal with 'evalh run spec.yaml', then "
        "reload this page to compare it with another run."
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
        "a_cost_text": _usd(item.a.cost_per_correct),
        "b_n": item.b.n,
        "b_value_text": _pct(item.b.value),
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
