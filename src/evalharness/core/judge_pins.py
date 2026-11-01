"""Judge pins and drift detection for one run or two compared runs.

A pin is the configuration that scored a metric: the judge declaration plus the
hash the scorer recorded on every check. Stored checks are authoritative, and
the spec snapshot fills in a pin before any check is written. A metric with two
hashes in one run, or one judge with two hashes across two runs, is drift; the
reason string says plainly that such runs are not like-for-like.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from evalharness.core.judges import judge_config_hash
from evalharness.core.models import Check, JudgeSpec, RunRecord

WITHIN_RUN = "within_run"
"""One metric carried more than one judge configuration inside one run."""

ACROSS_RUNS = "across_runs"
"""The same judge name carried different configurations across two runs."""

_KIND_ORDER = {WITHIN_RUN: 0, ACROSS_RUNS: 1}
"""Drift order on a page: within a run first, then across runs."""


@dataclass(frozen=True, slots=True)
class JudgePin:
    """One judge configuration pinned to one metric in one run.

    source is "checks" when a stored check supplied config_hash and "spec" when
    the hash was derived from the run's spec snapshot because no check carried
    one. judge is None only when checks carry a hash but the snapshot no longer
    declares the judge that wrote it.
    """

    metric: str
    judge: str | None
    adapter: str | None
    model: str | None
    options_digest: str
    temperature: float | None
    config_hash: str
    source: str
    last_used_at: datetime


@dataclass(frozen=True, slots=True)
class JudgeDrift:
    """One reason two judge configurations are not like-for-like."""

    kind: str
    judge: str
    metrics: tuple[str, ...]
    hashes: tuple[str, ...]
    runs: tuple[str, ...]
    reason: str


def pins_for_run(run: RunRecord, checks: Iterable[Check]) -> tuple[JudgePin, ...]:
    """Return every judge pin a run's snapshot and stored checks support.

    Parameter:
        run: the stored run, whose spec snapshot names metrics and judges.
        checks: every check recorded for the run, across all cells.

    Metrics that name no judge and carry no stored judge hash are deterministic
    and contribute no pin. Pins are ordered by metric and then by config hash.
    """
    by_metric: dict[str, list[Check]] = {}
    for check in checks:
        by_metric.setdefault(check.metric, []).append(check)
    declared = _declared_judges(run.spec_snapshot or {})
    observed = {
        metric
        for metric, items in by_metric.items()
        if any(check.judge_config_hash for check in items)
    }
    used_at = run.finished_at or run.started_at
    pins: list[JudgePin] = []
    for metric in sorted(set(declared) | observed):
        judge, adapter, model, options = declared.get(
            metric, (None, None, None, {})
        )
        digest = _options_digest(options)
        temperature = _temperature(options)
        hashes = sorted(
            {
                check.judge_config_hash
                for check in by_metric.get(metric, [])
                if check.judge_config_hash
            }
        )
        if hashes:
            for config_hash in hashes:
                pins.append(
                    JudgePin(
                        metric=metric,
                        judge=judge,
                        adapter=adapter,
                        model=model,
                        options_digest=digest,
                        temperature=temperature,
                        config_hash=config_hash,
                        source="checks",
                        last_used_at=used_at,
                    )
                )
        elif judge is not None:
            pins.append(
                JudgePin(
                    metric=metric,
                    judge=judge,
                    adapter=adapter,
                    model=model,
                    options_digest=digest,
                    temperature=temperature,
                    config_hash=judge_config_hash(
                        JudgeSpec(
                            name=judge,
                            adapter=str(adapter or ""),
                            model=model,
                            options=options,
                        )
                    ),
                    source="spec",
                    last_used_at=used_at,
                )
            )
    return tuple(pins)


def judge_drift(
    run_a: RunRecord,
    checks_a: Iterable[Check],
    run_b: RunRecord | None = None,
    checks_b: Iterable[Check] = (),
) -> tuple[JudgeDrift, ...]:
    """Return every drift that makes two runs, or one run, not like-for-like.

    Parameter:
        run_a, checks_a: the first run and all checks it stored.
        run_b, checks_b: the second run when two runs are compared; omitted for
            a receipt that only asks whether one run drifted internally.

    Within-run drift is reported for every run supplied. Across-run drift is
    reported once per judge name whose configuration hash sets differ.
    """
    pins_a = pins_for_run(run_a, checks_a)
    drifts = list(_within_run_drift(run_a.run_id, pins_a))
    if run_b is None:
        return _ordered(drifts)
    pins_b = pins_for_run(run_b, checks_b)
    drifts.extend(_within_run_drift(run_b.run_id, pins_b))
    drifts.extend(
        _across_runs_drift(run_a.run_id, pins_a, run_b.run_id, pins_b)
    )
    return _ordered(drifts)


def _declared_judges(
    spec: Mapping[str, Any],
) -> dict[str, tuple[str, str | None, str | None, dict[str, Any]]]:
    """Return the judge declaration each judged metric names."""
    judges = _named(spec.get("judges"))
    declared: dict[
        str, tuple[str, str | None, str | None, dict[str, Any]]
    ] = {}
    for metric in spec.get("metrics") or []:
        key = _text(metric.get("key"))
        options = dict(metric.get("options") or {})
        judge_name = options.get("judge")
        if not key or not judge_name:
            continue
        judge = judges.get(str(judge_name), {})
        declared[key] = (
            str(judge_name),
            _optional_text(judge.get("adapter")),
            _optional_text(judge.get("model")),
            dict(judge.get("options") or {}),
        )
    return declared


def _named(items: Any) -> dict[str, Mapping[str, Any]]:
    """Index a snapshot list by its name field."""
    return {str(item.get("name")): item for item in items or []}


def _text(value: Any) -> str:
    """Read a snapshot value as text, mapping absence to an empty string."""
    return str(value or "")


def _optional_text(value: Any) -> str | None:
    """Read a snapshot value as optional text."""
    return None if value is None else str(value)


def _options_digest(options: Mapping[str, Any]) -> str:
    """Return a sha256 digest over one options mapping, independent of key order."""
    payload = json.dumps(
        dict(options), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _temperature(options: Mapping[str, Any]) -> float | None:
    """Return the declared temperature when options carry a number."""
    value = options.get("temperature")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _within_run_drift(
    run_id: str, pins: tuple[JudgePin, ...]
) -> list[JudgeDrift]:
    """Report metrics that carry more than one config hash inside one run."""
    by_metric: dict[str, list[JudgePin]] = {}
    for pin in pins:
        by_metric.setdefault(pin.metric, []).append(pin)
    drifts: list[JudgeDrift] = []
    for metric in sorted(by_metric):
        hashes = sorted({pin.config_hash for pin in by_metric[metric]})
        if len(hashes) <= 1:
            continue
        judges = sorted(
            {pin.judge for pin in by_metric[metric] if pin.judge is not None}
        )
        judge = judges[0] if judges else "unknown judge"
        drifts.append(
            JudgeDrift(
                kind=WITHIN_RUN,
                judge=judge,
                metrics=(metric,),
                hashes=tuple(hashes),
                runs=(run_id,),
                reason=(
                    f"{judge} scored {metric} under {len(hashes)} "
                    "configurations in this run; not like-for-like."
                ),
            )
        )
    return drifts


def _across_runs_drift(
    run_a_id: str,
    pins_a: tuple[JudgePin, ...],
    run_b_id: str,
    pins_b: tuple[JudgePin, ...],
) -> list[JudgeDrift]:
    """Report judges whose config hash sets differ between two runs."""
    before = _pins_by_judge(pins_a)
    after = _pins_by_judge(pins_b)
    drifts: list[JudgeDrift] = []
    for judge in sorted(set(before) & set(after)):
        hashes_a = {pin.config_hash for pin in before[judge]}
        hashes_b = {pin.config_hash for pin in after[judge]}
        if hashes_a == hashes_b:
            continue
        metrics = tuple(
            sorted(
                {pin.metric for pin in before[judge]}
                | {pin.metric for pin in after[judge]}
            )
        )
        hashes = tuple(sorted(hashes_a | hashes_b))
        drifts.append(
            JudgeDrift(
                kind=ACROSS_RUNS,
                judge=judge,
                metrics=metrics,
                hashes=hashes,
                runs=(run_a_id, run_b_id),
                reason=(
                    f"{judge} scored {', '.join(metrics)} under different "
                    "configurations across the compared runs; "
                    "not like-for-like."
                ),
            )
        )
    return drifts


def _pins_by_judge(
    pins: tuple[JudgePin, ...],
) -> dict[str, list[JudgePin]]:
    """Group pins by declared judge name, ignoring undeclared pin sources."""
    grouped: dict[str, list[JudgePin]] = {}
    for pin in pins:
        if pin.judge is not None:
            grouped.setdefault(pin.judge, []).append(pin)
    return grouped


def _ordered(drifts: list[JudgeDrift]) -> tuple[JudgeDrift, ...]:
    """Return drift rows in a stable kind, judge and metric order."""
    return tuple(
        sorted(
            drifts,
            key=lambda drift: (
                _KIND_ORDER[drift.kind],
                drift.judge,
                drift.metrics,
                drift.hashes,
            ),
        )
    )
