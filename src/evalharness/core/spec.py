"""YAML loading, schema versioning, semantic validation and price resolution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import ValidationError

from evalharness.core.models import CandidateSpec, ContextChunk, Price, Spec
from evalharness.core.protocols import Capabilities, Judge, Metric, Source
from evalharness.core.registry import AdapterFactory, resolve
from evalharness.errors import RegistryError, SpecError

SPEC_VERSION = 1
"""The only spec version this harness understands."""

DEFAULT_SINK_ADAPTER = "evalharness.adapters.sinks.json_sink:build"
"""The artifact sink used when a spec does not override it."""


@dataclass(frozen=True, slots=True)
class ResolvedSpec:
    """A structurally and semantically valid spec plus its resolved adapters."""

    spec: Spec
    raw_text: str
    sha: str
    prices: dict[str, Price]
    candidates: dict[str, CandidateSpec]
    candidate_factories: dict[str, AdapterFactory]
    source: Source
    metrics: dict[str, Metric]
    judges: dict[str, Judge]
    sink_factory: AdapterFactory
    sink_options: dict[str, Any]
    prompt_overrides: dict[str, str | None]
    chunks: dict[str, list[ContextChunk]]
    warnings: list[str] = field(default_factory=list)


def parse_spec(text: str) -> Spec:
    """Parse YAML text into the structural spec model."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(
            "spec",
            "<yaml>",
            f"the YAML did not parse: {exc}",
            "fix the YAML syntax",
        ) from exc
    if not isinstance(data, dict):
        raise SpecError(
            "spec",
            data,
            "the spec must be a YAML mapping",
            "start the file with spec_version: 1",
        )
    try:
        spec = Spec.model_validate(data)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error.get("loc", ())) or "spec"
        raise SpecError(
            field,
            error.get("input"),
            str(error.get("msg", "invalid value")),
            "see docs/spec-v1.md",
        ) from exc
    if spec.spec_version != SPEC_VERSION:
        raise SpecError(
            "spec_version",
            spec.spec_version,
            f"unsupported spec version; this harness supports {SPEC_VERSION}",
            "set spec_version: 1",
        )
    return spec


def read_spec(path: str | Path) -> str:
    """Read a spec file as UTF-8 text, raising an actionable spec error."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(
            "spec",
            str(path),
            f"the spec file could not be read: {exc}",
            "check the path and permissions",
        ) from exc


def load_spec(path: str | Path) -> Spec:
    """Read and structurally parse a spec file."""
    return parse_spec(read_spec(path))


def spec_sha(text: str) -> str:
    """Return the sha256 of the spec text, used to identify a snapshot."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_and_validate(path: str | Path) -> ResolvedSpec:
    """Load a spec file and validate it in one step."""
    raw_text = read_spec(path)
    return validate_spec(parse_spec(raw_text), raw_text=raw_text)


def _resolve_factory(path: str, field: str) -> AdapterFactory:
    try:
        return resolve(path)
    except RegistryError as exc:
        raise SpecError(field, path, exc.reason, exc.fix) from exc


def _build(path: str, identity: str, options: dict[str, Any], field: str) -> Any:
    factory = _resolve_factory(path, field)
    try:
        return factory(identity, dict(options))
    except Exception as exc:  # adapter factories are external code
        raise SpecError(
            field,
            path,
            f"the adapter factory raised: {exc}",
            "fix the adapter or its options",
        ) from exc


def _unique(values: Iterable[str], field: str, noun: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise SpecError(
                field,
                value,
                f"duplicate {noun} name",
                f"give every {noun} a unique name",
            )
        seen.add(value)


def _require_price(spec: Spec, model: str) -> None:
    if model not in spec.prices:
        raise SpecError(
            f"prices.{model}",
            model,
            "no price is declared for this model",
            f"add prices.{model}: {{input: <usd_per_mtok>, output: <usd_per_mtok>}}",
        )


def _declared_credential(
    known: set[str],
    name: str | None,
    field: str,
) -> None:
    if name is not None and name not in known:
        choices = ", ".join(sorted(known)) or "none declared"
        raise SpecError(
            field,
            name,
            "unknown credential",
            f"declare it under credentials: or use one of: {choices}",
        )


def _candidate_axes(
    spec: Spec,
    candidate: CandidateSpec,
    capabilities: Capabilities,
    index: int,
) -> tuple[str | None, list[ContextChunk]]:
    options = candidate.options
    prompt_name = options.get("prompt")
    override: str | None = None
    if prompt_name is not None:
        field = f"candidates[{index}].options.prompt"
        if not isinstance(prompt_name, str):
            raise SpecError(
                field,
                prompt_name,
                "a prompt variant must be a string name",
                "name a variant declared under prompts:",
            )
        if prompt_name not in spec.prompts:
            choices = ", ".join(sorted(spec.prompts)) or "none declared"
            raise SpecError(
                field,
                prompt_name,
                "unknown prompt variant",
                f"declare prompts.{prompt_name} or use one of: {choices}",
            )
        if not capabilities.supports_prompt_override:
            raise SpecError(
                field,
                candidate.adapter,
                "the candidate cannot accept a prompt override",
                "remove options.prompt or use a candidate that supports overrides",
            )
        override = spec.prompts[prompt_name]

    context_name = options.get("context")
    chunks: list[ContextChunk] = []
    if context_name is not None:
        field = f"candidates[{index}].options.context"
        if isinstance(context_name, str):
            if context_name not in spec.contexts:
                choices = ", ".join(sorted(spec.contexts)) or "none declared"
                raise SpecError(
                    field,
                    context_name,
                    "unknown context",
                    f"declare contexts.{context_name} or use one of: {choices}",
                )
            chunks = list(spec.contexts[context_name])
        elif isinstance(context_name, dict) and isinstance(
            context_name.get("chunks"), list
        ):
            try:
                chunks = [
                    ContextChunk.model_validate(chunk)
                    for chunk in context_name["chunks"]
                ]
            except ValidationError as exc:
                raise SpecError(
                    field,
                    context_name,
                    f"inline context is malformed: {exc.errors()[0].get('msg')}",
                    "give every chunk a chunk_id and a text",
                ) from exc
        else:
            raise SpecError(
                field,
                context_name,
                "context must be a declared context name or {chunks: [...]}",
                "set context to a name under contexts:",
            )
        if not capabilities.supports_context_injection:
            raise SpecError(
                field,
                candidate.adapter,
                "the candidate cannot accept context injection",
                "remove options.context or use a candidate that supports injection",
            )
    return override, chunks


def validate_spec(spec: Spec, raw_text: str = "") -> ResolvedSpec:
    """Validate every declarative reference before anything executes."""
    _unique(
        (candidate.name for candidate in spec.candidates), "candidates", "candidate"
    )
    _unique((metric.key for metric in spec.metrics), "metrics", "metric")
    _unique((judge.name for judge in spec.judges), "judges", "judge")
    _unique(
        (credential.name for credential in spec.credentials),
        "credentials",
        "credential",
    )

    known_credentials = {credential.name for credential in spec.credentials}

    for index, candidate in enumerate(spec.candidates):
        _declared_credential(
            known_credentials,
            candidate.credential,
            f"candidates[{index}].credential",
        )
        _require_price(spec, candidate.model)

    for index, judge in enumerate(spec.judges):
        _declared_credential(
            known_credentials,
            judge.credential,
            f"judges[{index}].credential",
        )
        if judge.model is not None:
            _require_price(spec, judge.model)

    source = _build(
        spec.source.adapter,
        "source",
        spec.source.options,
        "source.adapter",
    )

    candidate_factories: dict[str, AdapterFactory] = {}
    prompt_overrides: dict[str, str | None] = {}
    chunks_by_candidate: dict[str, list[ContextChunk]] = {}
    for index, candidate in enumerate(spec.candidates):
        factory = _resolve_factory(candidate.adapter, f"candidates[{index}].adapter")
        instance = _build(
            candidate.adapter,
            candidate.name,
            candidate.options,
            f"candidates[{index}].adapter",
        )
        capabilities = getattr(instance, "capabilities", None)
        if not isinstance(capabilities, Capabilities):
            raise SpecError(
                f"candidates[{index}].adapter",
                candidate.adapter,
                "the candidate adapter did not expose Capabilities",
                "return Candidate.capabilities as a Capabilities instance",
            )
        override, chunks = _candidate_axes(spec, candidate, capabilities, index)
        candidate_factories[candidate.name] = factory
        prompt_overrides[candidate.name] = override
        chunks_by_candidate[candidate.name] = chunks

    metrics: dict[str, Metric] = {}
    for index, metric_spec in enumerate(spec.metrics):
        instance = _build(
            metric_spec.adapter,
            metric_spec.key,
            metric_spec.options,
            f"metrics[{index}].adapter",
        )
        if getattr(instance, "key", None) != metric_spec.key:
            raise SpecError(
                f"metrics[{index}].key",
                metric_spec.key,
                "the metric adapter reports a different key",
                f"return key={metric_spec.key!r} from the adapter or align the spec",
            )
        judge_name = metric_spec.options.get("judge")
        if judge_name is not None:
            declared = {judge.name for judge in spec.judges}
            if judge_name not in declared:
                choices = ", ".join(sorted(declared)) or "none declared"
                raise SpecError(
                    f"metrics[{index}].options.judge",
                    judge_name,
                    "the metric names an undeclared judge",
                    f"declare judges: {judge_name} or use one of: {choices}",
                )
        metrics[metric_spec.key] = instance

    judges: dict[str, Judge] = {}
    for index, judge_spec in enumerate(spec.judges):
        judges[judge_spec.name] = _build(
            judge_spec.adapter,
            judge_spec.name,
            judge_spec.options,
            f"judges[{index}].adapter",
        )

    metric_keys = set(metrics)
    gate_metrics: set[str] = set()
    warnings: list[str] = []
    for index, gate in enumerate(spec.gates):
        if gate.metric not in metric_keys:
            choices = ", ".join(sorted(metric_keys)) or "none declared"
            raise SpecError(
                f"gates[{index}].metric",
                gate.metric,
                "the gate names an undeclared metric",
                f"use one of: {choices}",
            )
        if gate.min is None and gate.max is None:
            raise SpecError(
                f"gates[{index}]",
                gate.metric,
                "the gate has neither min nor max",
                "set min, max, or both",
            )
        if gate.metric in gate_metrics:
            raise SpecError(
                f"gates[{index}].metric",
                gate.metric,
                "duplicate gate for this metric",
                "combine the bounds into one gate",
            )
        gate_metrics.add(gate.metric)
        if "latency" in gate.metric and spec.concurrency > 1:
            warnings.append(
                "latency gate with concurrency > 1: latency measures queueing, "
                "not the model; use concurrency 1 for latency comparisons"
            )

    sink_path = spec.sink.adapter if spec.sink is not None else DEFAULT_SINK_ADAPTER
    sink_options = dict(spec.sink.options) if spec.sink is not None else {}
    sink_factory = _resolve_factory(
        sink_path,
        "sink.adapter" if spec.sink is not None else "sink",
    )

    return ResolvedSpec(
        spec=spec,
        raw_text=raw_text,
        sha=spec_sha(raw_text),
        prices=dict(spec.prices),
        candidates={candidate.name: candidate for candidate in spec.candidates},
        candidate_factories=candidate_factories,
        source=source,
        metrics=metrics,
        judges=judges,
        sink_factory=sink_factory,
        sink_options=sink_options,
        prompt_overrides=prompt_overrides,
        chunks=chunks_by_candidate,
        warnings=warnings,
    )
