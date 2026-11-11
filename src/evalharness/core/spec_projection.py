"""Typed projection of a spec document or a stored run snapshot.

The console read views and the core trust surfaces need the same handful of
fields from a spec: its name, the cases with their prompts, the prompts and
contexts a candidate may select, the candidates with their adapter and model,
the metric keys, the judge each metric names, the declared gates and the
replica count. This module is the one place that knows the shape of the raw
mapping stored in ``RunRecord.spec_snapshot``, so no renderer or core reader
walks that mapping itself.

It lives in core because core readers (compare, calibration, decision, judge
pins) need it too; the api layer may import core, never the reverse.

project_spec reads the validated core Spec, the contract the console calls
valid. project_document reads a raw document tolerantly, so a draft saved
before validation, an old run snapshot or a hand-written fixture still renders.
A missing or unusable key removes a box; it never raises.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from evalharness.core.models import Spec


@dataclass(frozen=True, slots=True)
class ProjectedPrompt:
    """One named prompt and the text a candidate would send."""

    name: str
    text: str | None


@dataclass(frozen=True, slots=True)
class ProjectedContext:
    """One named context and the number of chunks it injects."""

    name: str
    chunks: int


@dataclass(frozen=True, slots=True)
class ProjectedCase:
    """One declared case with the prompt every candidate answers.

    declared is False when the id was invented for rendering (the document
    carried a case row without a usable case_id), so a consumer that must not
    invent values can skip those while the graph still labels every row.
    """

    id: str
    prompt: str
    declared: bool


@dataclass(frozen=True, slots=True)
class ProjectedCandidate:
    """One candidate with its adapter, model and the names it selects.

    declared is False when the name was invented for rendering (the document
    carried a candidate row without a usable name).
    """

    name: str
    adapter: str | None
    model: str | None
    prompt: str | None
    context: str | None
    declared: bool


@dataclass(frozen=True, slots=True)
class ProjectedJudge:
    """One judge declaration, as the metric that names it can read."""

    name: str
    adapter: str | None
    model: str | None
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectedGate:
    """One declared gate as the graph labels it, pending until a run measures it."""

    label: str
    metric: str
    candidate: str | None


@dataclass(frozen=True, slots=True)
class SpecProjection:
    """The spec fields every surface reads, independent of the document shape."""

    name: str
    cases: tuple[ProjectedCase, ...]
    prompts: tuple[ProjectedPrompt, ...]
    contexts: tuple[ProjectedContext, ...]
    candidates: tuple[ProjectedCandidate, ...]
    metrics: tuple[str, ...]
    judged_metrics: Mapping[str, ProjectedJudge]
    gates: tuple[ProjectedGate, ...]
    replicas: int


def project_spec(spec: Spec) -> SpecProjection:
    """Return the projection of a validated core spec."""
    judges = {
        judge.name: ProjectedJudge(
            name=judge.name,
            adapter=judge.adapter,
            model=judge.model,
            options=judge.options,
        )
        for judge in spec.judges
    }
    return SpecProjection(
        name=spec.name,
        cases=_cases(spec.source.options),
        prompts=tuple(
            ProjectedPrompt(name=name, text=text)
            for name, text in spec.prompts.items()
        ),
        contexts=tuple(
            ProjectedContext(
                name=name,
                chunks=len(value) if isinstance(value, list) else 0,
            )
            for name, value in spec.contexts.items()
        ),
        candidates=tuple(
            ProjectedCandidate(
                name=candidate.name,
                adapter=candidate.adapter,
                model=candidate.model,
                prompt=_text(candidate.options.get("prompt")),
                context=_text(candidate.options.get("context")),
                declared=True,
            )
            for candidate in spec.candidates
        ),
        metrics=_unique(metric.key for metric in spec.metrics),
        judged_metrics=_judged_metrics(
            (
                (metric.key, _text(metric.options.get("judge")))
                for metric in spec.metrics
            ),
            judges,
        ),
        gates=tuple(
            _gate(index, gate.metric, gate.candidate)
            for index, gate in enumerate(spec.gates, start=1)
        ),
        replicas=_replicas(spec.replicas),
    )


def project_document(document: Mapping[str, Any]) -> SpecProjection:
    """Return the projection of a raw spec document, tolerating bad values."""
    prompts = _mapping(document.get("prompts"))
    contexts = _mapping(document.get("contexts"))
    judges = {
        name: _judge_from(declared)
        for name, declared in (
            (_text(_field(declared, "name")), declared)
            for declared in _sequence(document.get("judges"))
        )
        if name is not None
    }
    return SpecProjection(
        name=_text(document.get("name")) or "",
        cases=_cases(
            _mapping(_mapping(document.get("source")).get("options"))
        ),
        prompts=tuple(
            ProjectedPrompt(name=name, text=_text(prompts.get(name)))
            for name in _text_keys(prompts)
        ),
        contexts=tuple(
            ProjectedContext(
                name=name,
                chunks=len(_sequence(contexts.get(name))),
            )
            for name in _text_keys(contexts)
        ),
        candidates=tuple(
            ProjectedCandidate(
                name=_text(_field(candidate, "name")) or f"candidate-{index}",
                adapter=_text(_field(candidate, "adapter")),
                model=_text(_field(candidate, "model")),
                prompt=_text(
                    _mapping(_field(candidate, "options")).get("prompt")
                ),
                context=_text(
                    _mapping(_field(candidate, "options")).get("context")
                ),
                declared=bool(_text(_field(candidate, "name"))),
            )
            for index, candidate in enumerate(
                _sequence(document.get("candidates")), start=1
            )
        ),
        metrics=_unique(
            _text(_field(metric, "key")) or ""
            for metric in _sequence(document.get("metrics"))
        ),
        judged_metrics=_judged_metrics(
            (
                (
                    _text(_field(metric, "key")),
                    _text(_mapping(_field(metric, "options")).get("judge")),
                )
                for metric in _sequence(document.get("metrics"))
            ),
            judges,
        ),
        gates=tuple(
            _gate(
                index,
                _text(_field(declared, "metric")),
                _text(_field(declared, "candidate")),
            )
            for index, declared in enumerate(
                _sequence(document.get("gates")), start=1
            )
        ),
        replicas=_replicas(document.get("replicas")),
    )


def projection_for(value: object) -> SpecProjection:
    """Return the projection of a projection, a core spec or a raw document."""
    if isinstance(value, SpecProjection):
        return value
    if isinstance(value, Spec):
        return project_spec(value)
    return project_document(_mapping(value))


def _cases(options: Mapping[str, Any]) -> tuple[ProjectedCase, ...]:
    """Return one stable case per declared case, numbering unnamed cases."""
    return tuple(
        ProjectedCase(
            id=_text(_field(case, "case_id")) or f"case-{index}",
            prompt=_text(_field(case, "prompt")) or "",
            declared=bool(_text(_field(case, "case_id"))),
        )
        for index, case in enumerate(_sequence(options.get("cases")), start=1)
    )


def _judged_metrics(
    named: Iterable[tuple[str | None, str | None]],
    judges: Mapping[str, ProjectedJudge],
) -> dict[str, ProjectedJudge]:
    """Return the judge declaration each judged metric names, keyed by metric."""
    declared: dict[str, ProjectedJudge] = {}
    for key, judge_name in named:
        if not key or not judge_name:
            continue
        declared[key] = judges.get(judge_name) or ProjectedJudge(
            name=judge_name, adapter=None, model=None, options={}
        )
    return declared


def _judge_from(declared: Mapping[str, Any]) -> ProjectedJudge:
    """Return the projection of one raw judge declaration."""
    return ProjectedJudge(
        name=_text(declared.get("name")) or "",
        adapter=_text(declared.get("adapter")),
        model=_text(declared.get("model")),
        options=_mapping(declared.get("options")),
    )


def _gate(index: int, metric: str | None, candidate: str | None) -> ProjectedGate:
    """Return one declared gate, naming an unnamed gate by its position."""
    label = metric or f"gate-{index}"
    return ProjectedGate(label=label, metric=label, candidate=candidate or None)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """Return the non-empty values in first-seen order, without duplicates."""
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def _replicas(value: Any) -> int:
    """Return a positive replica count, defaulting to one when unusable."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _text_keys(value: Any) -> Iterator[str]:
    """Yield the string keys of a named mapping in insertion order."""
    for key in _mapping(value):
        name = _text(key)
        if name is not None:
            yield name


def _mapping(value: Any) -> Mapping[str, Any]:
    """Return the value when it is a mapping, otherwise an empty mapping."""
    if isinstance(value, Mapping):
        return value
    return {}


def _sequence(value: Any) -> list[Any]:
    """Return the value when it is a list or tuple, otherwise empty."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _field(value: Any, name: str) -> Any:
    """Return one field from a mapping without requiring the key."""
    return _mapping(value).get(name)


def _text(value: Any) -> str | None:
    """Return a string value, or None when the value is absent."""
    if isinstance(value, str):
        return value
    return None
