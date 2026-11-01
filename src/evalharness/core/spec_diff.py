"""Structured differences between two run snapshots and their price tables.

The receipt and the two-run diff page both need the same answer: what changed
between the snapshot a run was launched from and the snapshot of another run.
This module is pure: it reads the JSON snapshots the repository stored and
returns typed changes with stable ordering, counts and one human headline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

CATEGORY_ORDER = ("cases", "candidates", "judges", "prices", "run", "gates")
"""The order categories appear in a diff, whatever order they were found in."""

_CATEGORY_LABELS = {
    "cases": "case",
    "candidates": "candidate",
    "judges": "judge",
    "prices": "price",
    "run": "run field",
    "gates": "gate",
}
"""Singular labels used in the headline and the per-category counts."""


@dataclass(frozen=True, slots=True)
class SpecChange:
    """One structured difference between two snapshots.

    category names the group, path locates the field inside the spec snapshot
    or the price snapshot, and before/after are the JSON values found there.
    summary is one line written for the console, never parsed back.
    """

    category: str
    path: str
    before: Any
    after: Any
    summary: str


@dataclass(frozen=True, slots=True)
class SpecDiff:
    """Every change between two snapshots, with counts and a headline."""

    changes: tuple[SpecChange, ...]
    counts: dict[str, int]
    total: int
    headline: str


def diff_run_snapshots(
    a_spec: Mapping[str, Any],
    b_spec: Mapping[str, Any],
    a_prices: Mapping[str, Any],
    b_prices: Mapping[str, Any],
) -> SpecDiff:
    """Return the structured difference from snapshot A to snapshot B.

    Parameter:
        a_spec, b_spec: the stored spec snapshots, as run records carry them.
        a_prices, b_prices: the stored price snapshots, either the version 2
            provider-nested shape or the older flat shape.

    Missing keys are read as empty, so a snapshot written by an older harness
    still produces a diff rather than an error.
    """
    a = dict(a_spec or {})
    b = dict(b_spec or {})
    changes = [
        *_case_changes(a, b),
        *_candidate_changes(a, b),
        *_judge_changes(a, b),
        *_price_changes(a_prices, b_prices),
        *_run_changes(a, b),
        *_gate_changes(a, b),
    ]
    ordered = tuple(sorted(changes, key=_order_key))
    counts = {category: 0 for category in CATEGORY_ORDER}
    for change in ordered:
        counts[change.category] += 1
    total = len(ordered)
    return SpecDiff(
        changes=ordered,
        counts=counts,
        total=total,
        headline=_headline(total, counts),
    )


def spec_diff_data(diff: SpecDiff) -> dict[str, Any]:
    """Return a JSON-ready mapping for one spec diff."""
    return {
        "headline": diff.headline,
        "total": diff.total,
        "counts": dict(diff.counts),
        "changes": [
            {
                "category": change.category,
                "path": change.path,
                "before": change.before,
                "after": change.after,
                "summary": change.summary,
            }
            for change in diff.changes
        ],
    }


def _order_key(change: SpecChange) -> tuple[int, str]:
    """Sort by the fixed category order, then by path."""
    return CATEGORY_ORDER.index(change.category), change.path


def _headline(total: int, counts: Mapping[str, int]) -> str:
    """Write one human line naming the non-zero categories."""
    if total == 0:
        return "No differences between the two snapshots."
    parts = [
        f"{counts[category]} {label}{'' if counts[category] == 1 else 's'}"
        for category, label in _CATEGORY_LABELS.items()
        if counts[category]
    ]
    return f"{total} change{'' if total == 1 else 's'}: {', '.join(parts)}."


def _change(
    category: str,
    path: str,
    before: Any,
    after: Any,
    summary: str,
) -> SpecChange:
    """Build one change record."""
    return SpecChange(
        category=category,
        path=path,
        before=before,
        after=after,
        summary=summary,
    )


def _named(items: Any) -> dict[str, Mapping[str, Any]]:
    """Index a snapshot list by its name field."""
    return {str(item.get("name")): item for item in items or []}


def _text(value: Any) -> str:
    """Read a snapshot value as text, mapping absent values to an empty string."""
    return str(value or "")


def _case_changes(
    a: Mapping[str, Any], b: Mapping[str, Any]
) -> list[SpecChange]:
    """Diff case ids and the prompt each case carries."""
    before = _cases(a)
    after = _cases(b)
    changes: list[SpecChange] = []
    for case_id in sorted(set(before) | set(after)):
        old = before.get(case_id)
        new = after.get(case_id)
        path = f"source.options.cases[{case_id}].prompt"
        if old is None:
            changes.append(
                _change("cases", path, None, _prompt(new), f"case {case_id} added")
            )
        elif new is None:
            changes.append(
                _change(
                    "cases", path, _prompt(old), None, f"case {case_id} removed"
                )
            )
        elif _prompt(old) != _prompt(new):
            changes.append(
                _change(
                    "cases",
                    path,
                    _prompt(old),
                    _prompt(new),
                    f"case {case_id} prompt changed",
                )
            )
    return changes


def _cases(spec: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return the cases declared by the inline source, indexed by case id."""
    source = spec.get("source") or {}
    options = source.get("options") or {}
    return {
        str(case.get("case_id")): case
        for case in options.get("cases") or []
    }


def _prompt(case: Mapping[str, Any] | None) -> str:
    """Read one case prompt, defaulting to an empty string."""
    return _text((case or {}).get("prompt"))


def _candidate_changes(
    a: Mapping[str, Any], b: Mapping[str, Any]
) -> list[SpecChange]:
    """Diff candidate names, adapters and models."""
    before = _named(a.get("candidates"))
    after = _named(b.get("candidates"))
    changes: list[SpecChange] = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name)
        new = after.get(name)
        if old is None:
            changes.append(
                _change(
                    "candidates",
                    f"candidates[{name}]",
                    None,
                    _candidate_detail(new),
                    f"candidate {name} added",
                )
            )
        elif new is None:
            changes.append(
                _change(
                    "candidates",
                    f"candidates[{name}]",
                    _candidate_detail(old),
                    None,
                    f"candidate {name} removed",
                )
            )
        else:
            old_adapter = _text(old.get("adapter"))
            new_adapter = _text(new.get("adapter"))
            if old_adapter != new_adapter:
                changes.append(
                    _change(
                        "candidates",
                        f"candidates[{name}].adapter",
                        old_adapter,
                        new_adapter,
                        f"candidate {name} adapter: {old_adapter} -> "
                        f"{new_adapter}",
                    )
                )
            old_model = _text(old.get("model"))
            new_model = _text(new.get("model"))
            if old_model != new_model:
                changes.append(
                    _change(
                        "candidates",
                        f"candidates[{name}].model",
                        old_model,
                        new_model,
                        f"candidate {name} model: {old_model} -> {new_model}",
                    )
                )
    return changes


def _candidate_detail(candidate: Mapping[str, Any] | None) -> dict[str, str]:
    """Return the adapter and model a candidate snapshot declares."""
    item = candidate or {}
    return {
        "adapter": _text(item.get("adapter")),
        "model": _text(item.get("model")),
    }


def _judge_changes(
    a: Mapping[str, Any], b: Mapping[str, Any]
) -> list[SpecChange]:
    """Diff judge declarations and the judge each metric names."""
    before = _named(a.get("judges"))
    after = _named(b.get("judges"))
    changes: list[SpecChange] = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name)
        new = after.get(name)
        if old is None:
            changes.append(
                _change(
                    "judges",
                    f"judges[{name}]",
                    None,
                    _judge_detail(new),
                    f"judge {name} added",
                )
            )
        elif new is None:
            changes.append(
                _change(
                    "judges",
                    f"judges[{name}]",
                    _judge_detail(old),
                    None,
                    f"judge {name} removed",
                )
            )
        else:
            old_adapter = _text(old.get("adapter"))
            new_adapter = _text(new.get("adapter"))
            if old_adapter != new_adapter:
                changes.append(
                    _change(
                        "judges",
                        f"judges[{name}].adapter",
                        old_adapter,
                        new_adapter,
                        f"judge {name} adapter: {old_adapter} -> {new_adapter}",
                    )
                )
            old_model = _text(old.get("model"))
            new_model = _text(new.get("model"))
            if old_model != new_model:
                changes.append(
                    _change(
                        "judges",
                        f"judges[{name}].model",
                        old_model,
                        new_model,
                        f"judge {name} model: {old_model} -> {new_model}",
                    )
                )
            old_options = dict(old.get("options") or {})
            new_options = dict(new.get("options") or {})
            if old_options != new_options:
                changes.append(
                    _change(
                        "judges",
                        f"judges[{name}].options",
                        old_options,
                        new_options,
                        f"judge {name} options changed",
                    )
                )
    before_metrics = _metric_judges(a)
    after_metrics = _metric_judges(b)
    for key in sorted(set(before_metrics) | set(after_metrics)):
        old_judge = before_metrics.get(key)
        new_judge = after_metrics.get(key)
        if old_judge != new_judge:
            changes.append(
                _change(
                    "judges",
                    f"metrics[{key}].options.judge",
                    old_judge,
                    new_judge,
                    f"metric {key} judge: {_judge_text(old_judge)} -> "
                    f"{_judge_text(new_judge)}",
                )
            )
    return changes


def _judge_detail(judge: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the adapter, model and options a judge snapshot declares."""
    item = judge or {}
    return {
        "adapter": _text(item.get("adapter")),
        "model": _text(item.get("model")),
        "options": dict(item.get("options") or {}),
    }


def _metric_judges(spec: Mapping[str, Any]) -> dict[str, str]:
    """Return the judge name each metric names, skipping deterministic metrics."""
    judges: dict[str, str] = {}
    for metric in spec.get("metrics") or []:
        key = _text(metric.get("key"))
        options = metric.get("options") or {}
        judge = options.get("judge")
        if key and judge:
            judges[key] = str(judge)
    return judges


def _judge_text(name: str | None) -> str:
    """Render a judge name for a one-line summary, including absence."""
    return name if name else "none"


def _price_changes(
    a_prices: Mapping[str, Any], b_prices: Mapping[str, Any]
) -> list[SpecChange]:
    """Diff every model rate in the two price snapshots."""
    before = _flat_prices(a_prices)
    after = _flat_prices(b_prices)
    changes: list[SpecChange] = []
    for model in sorted(set(before) | set(after)):
        old = before.get(model)
        new = after.get(model)
        if old is None:
            changes.append(
                _change(
                    "prices",
                    f"prices[{model}]",
                    None,
                    dict(new or {}),
                    f"price for {model} added",
                )
            )
        elif new is None:
            changes.append(
                _change(
                    "prices",
                    f"prices[{model}]",
                    dict(old),
                    None,
                    f"price for {model} removed",
                )
            )
        else:
            for rate in sorted(set(old) | set(new)):
                old_rate = float(old.get(rate, 0.0))
                new_rate = float(new.get(rate, 0.0))
                if old_rate != new_rate:
                    changes.append(
                        _change(
                            "prices",
                            f"prices[{model}].{rate}",
                            old_rate,
                            new_rate,
                            f"{model} {rate} price: ${old_rate:g} -> "
                            f"${new_rate:g} per million tokens",
                        )
                    )
    return changes


def _flat_prices(snapshot: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Flatten a price snapshot the same way the compare view does.

    A version 2 snapshot nests rates by provider, so its keys become
    provider/model. The pre-issue-#17 shape is already flat. Keeping this
    identical to the compare view makes the receipt's fingerprint match.
    """
    data = dict(snapshot or {})
    if data.get("version") == 2:
        providers = data.get("providers") or {}
        return {
            (f"{provider}/{model}" if provider else str(model)): {
                str(key): float(value) for key, value in dict(price).items()
            }
            for provider, models in providers.items()
            for model, price in models.items()
        }
    return {
        str(model): {
            str(key): float(value) for key, value in dict(price).items()
        }
        for model, price in data.items()
    }


def _run_changes(
    a: Mapping[str, Any], b: Mapping[str, Any]
) -> list[SpecChange]:
    """Diff the run-level knobs: seed, replicas and budget."""
    changes: list[SpecChange] = []
    for field, default in (("seed", 0), ("replicas", 1)):
        before = a.get(field, default)
        after = b.get(field, default)
        if before != after:
            changes.append(
                _change(
                    "run",
                    field,
                    before,
                    after,
                    f"{field}: {before} -> {after}",
                )
            )
    before_budget = a.get("budget_usd")
    after_budget = b.get("budget_usd")
    if before_budget != after_budget:
        changes.append(
            _change(
                "run",
                "budget_usd",
                before_budget,
                after_budget,
                f"budget: {_budget_text(before_budget)} -> "
                f"{_budget_text(after_budget)}",
            )
        )
    return changes


def _budget_text(value: Any) -> str:
    """Render a budget or its absence for a one-line summary."""
    return "no cap" if value is None else f"${float(value):.2f}"


def _gate_changes(
    a: Mapping[str, Any], b: Mapping[str, Any]
) -> list[SpecChange]:
    """Diff gates by metric and candidate, then by their min and max bounds."""
    before = _gates(a)
    after = _gates(b)
    changes: list[SpecChange] = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        path = _gate_path(key)
        label = _gate_label(key)
        if old is None:
            changes.append(
                _change(
                    "gates",
                    path,
                    None,
                    _gate_bounds(new),
                    f"gate {label} added",
                )
            )
        elif new is None:
            changes.append(
                _change(
                    "gates",
                    path,
                    _gate_bounds(old),
                    None,
                    f"gate {label} removed",
                )
            )
        else:
            for bound in ("min", "max"):
                old_value = old.get(bound)
                new_value = new.get(bound)
                if old_value != new_value:
                    changes.append(
                        _change(
                            "gates",
                            f"{path}.{bound}",
                            old_value,
                            new_value,
                            f"gate {label} {bound}: "
                            f"{_bound_text(old_value)} -> "
                            f"{_bound_text(new_value)}",
                        )
                    )
    return changes


def _gates(
    spec: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    """Index gates by metric and candidate, treating no candidate as empty."""
    gates: dict[tuple[str, str], Mapping[str, Any]] = {}
    for gate in spec.get("gates") or []:
        metric = _text(gate.get("metric"))
        candidate = _text(gate.get("candidate"))
        gates[(metric, candidate)] = gate
    return gates


def _gate_path(key: tuple[str, str]) -> str:
    """Locate one gate inside the spec snapshot."""
    metric, candidate = key
    if candidate:
        return f"gates[{metric}@{candidate}]"
    return f"gates[{metric}]"


def _gate_label(key: tuple[str, str]) -> str:
    """Name one gate in a one-line summary."""
    metric, candidate = key
    if candidate:
        return f"{metric} for {candidate}"
    return metric


def _gate_bounds(gate: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the two optional bounds a gate snapshot declares."""
    item = gate or {}
    return {"min": item.get("min"), "max": item.get("max")}


def _bound_text(value: Any) -> str:
    """Render a gate bound or its absence for a one-line summary."""
    return "none" if value is None else f"{float(value):g}"
