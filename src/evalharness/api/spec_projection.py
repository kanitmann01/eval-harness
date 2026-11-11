"""Typed projection of a spec document for the console's read views.

The spec list and the spec graph need the same handful of fields from a spec:
its name, the case ids, the prompts and contexts a candidate may select, the
candidates, the metric keys, the declared gates and the replica count. This
module is the one place that knows the shape of the raw mapping stored in
RunRecord.spec_snapshot, so no renderer walks that mapping itself.

project_spec reads the validated core Spec, the contract the console calls
valid. project_document reads a raw document tolerantly, so a draft saved
before validation, an old run snapshot or a hand-written fixture still renders.
A missing or unusable key removes a box; it never raises.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
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
class ProjectedCandidate:
    """One candidate with the prompt and context names it selects."""

    name: str
    prompt: str | None
    context: str | None


@dataclass(frozen=True, slots=True)
class ProjectedGate:
    """One declared gate as the graph labels it, pending until a run measures it."""

    label: str
    metric: str
    candidate: str | None


@dataclass(frozen=True, slots=True)
class SpecProjection:
    """The spec fields the console reads, independent of the document shape."""

    name: str
    cases: tuple[str, ...]
    prompts: tuple[ProjectedPrompt, ...]
    contexts: tuple[ProjectedContext, ...]
    candidates: tuple[ProjectedCandidate, ...]
    metrics: tuple[str, ...]
    gates: tuple[ProjectedGate, ...]
    replicas: int


def project_spec(spec: Spec) -> SpecProjection:
    """Return the projection of a validated core spec."""
    return SpecProjection(
        name=spec.name,
        cases=_case_ids(spec.source.options),
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
                prompt=_text(candidate.options.get("prompt")),
                context=_text(candidate.options.get("context")),
            )
            for candidate in spec.candidates
        ),
        metrics=_unique(metric.key for metric in spec.metrics),
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
    return SpecProjection(
        name=_text(document.get("name")) or "",
        cases=_case_ids(
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
                prompt=_text(
                    _mapping(_field(candidate, "options")).get("prompt")
                ),
                context=_text(
                    _mapping(_field(candidate, "options")).get("context")
                ),
            )
            for index, candidate in enumerate(
                _sequence(document.get("candidates")), start=1
            )
        ),
        metrics=_unique(
            _text(_field(metric, "key")) or ""
            for metric in _sequence(document.get("metrics"))
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


def _case_ids(options: Mapping[str, Any]) -> tuple[str, ...]:
    """Return one stable label per declared case, numbering unnamed cases."""
    return tuple(
        _text(_field(case, "case_id")) or f"case-{index}"
        for index, case in enumerate(_sequence(options.get("cases")), start=1)
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
