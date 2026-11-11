"""The comparison page model: rows, choices, errors and the estimate rail.

This module turns the draft document into the render model the comparison
templates consume. It owns the row models each step shows, the select choices
derived from the document and the credential vault, the mapping from a
validator field path or a posted row to a form field id, the row errors the
wizard reports before the document validates, the estimate rail and the page
assembly.

It never mutates the document. The draft itself belongs to comparison_draft.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalharness.adapters.manifest import MANIFEST
from evalharness.api.comparison_draft import (
    EXACT_MATCH,
    INLINE_SOURCE,
    JUDGE_PASS,
    STUB_CANDIDATE,
    read_indices,
    read_mapping,
    read_sequence,
    read_text,
)
from evalharness.api.credential_views import CredentialChoice, provider_options
from evalharness.api.graph_views import SpecGraph, rail_graph
from evalharness.api.spec_store import SLUG_PATTERN
from evalharness.api.spec_views import SpecCheck, field_value
from evalharness.demos.catalog import DEMOS
from evalharness.errors import SpecError



_FIELD_ALIASES = {
    "name": "name",
    "description": "description",
    "seed": "seed",
    "source.adapter": "source_adapter",
    "budget_usd": "budget_usd",
    "replicas": "replicas",
    "concurrency": "concurrency",
}


@dataclass(frozen=True, slots=True)
class Choice:
    """One option in a select control."""

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class StepDefinition:
    """One step of the wizard, as the step strip renders it."""

    index: int
    title: str
    path: str
    description: str


STEPS = (
    StepDefinition(
        1,
        "What to vary",
        "/comparisons/new/1",
        "Name the comparison, pick the case source and write one row per case.",
    ),
    StepDefinition(
        2,
        "Add context",
        "/comparisons/new/2",
        "Prompts, reference texts and credential declarations the candidates use.",
    ),
    StepDefinition(
        3,
        "Choose candidates",
        "/comparisons/new/3",
        "Each candidate is an adapter, a model and the context it runs with.",
    ),
    StepDefinition(
        4,
        "Cost and rules",
        "/comparisons/new/4",
        "The budget cap, the metrics, the judges, the gates and the prices.",
    ),
)


@dataclass(frozen=True, slots=True)
class WizardError:
    """One field error, or a general one when the field is empty."""

    field: str
    label: str
    value: str
    reason: str
    fix: str

    @property
    def message(self) -> str:
        """Return the one line a reader sees next to the field."""
        return f"{self.label} {self.value!r}: {self.reason}. Fix: {self.fix}"


@dataclass(frozen=True, slots=True)
class CaseRow:
    """One case row as the step 1 form renders it."""

    index: int
    case_id: str
    prompt: str
    expected: str
    rubric: str
    tier: str


@dataclass(frozen=True, slots=True)
class PromptRow:
    """One prompt row as the step 2 form renders it."""

    index: int
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class ContextRow:
    """One context row as the step 2 form renders it."""

    index: int
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class CredentialRow:
    """One credential declaration row as the step 2 form renders it."""

    index: int
    name: str
    provider: str


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """One candidate row as the step 3 form renders it."""

    index: int
    name: str
    adapter: str
    model: str
    credential: str
    prompt: str
    context: str


@dataclass(frozen=True, slots=True)
class MetricRow:
    """One metric row as the step 4 form renders it."""

    index: int
    key: str
    adapter: str


@dataclass(frozen=True, slots=True)
class JudgeRow:
    """One judge row as the step 4 form renders it."""

    index: int
    metric: str
    model: str
    rubric: str


@dataclass(frozen=True, slots=True)
class GateRow:
    """One gate row as the step 4 form renders it."""

    index: int
    metric: str
    op: str
    threshold: str


@dataclass(frozen=True, slots=True)
class PriceRow:
    """One price row as the step 4 form renders it."""

    index: int
    model: str
    input: str
    output: str


@dataclass(frozen=True, slots=True)
class WizardValues:
    """Everything the current step's form needs to redraw its controls."""

    name: str
    description: str
    seed: str
    source_adapter: str
    cases: tuple[CaseRow, ...]
    prompts: tuple[PromptRow, ...]
    contexts: tuple[ContextRow, ...]
    credentials: tuple[CredentialRow, ...]
    candidates: tuple[CandidateRow, ...]
    replicas: str
    concurrency: str
    budget: str
    metrics: tuple[MetricRow, ...]
    judges: tuple[JudgeRow, ...]
    gates: tuple[GateRow, ...]
    prices: tuple[PriceRow, ...]


@dataclass(frozen=True, slots=True)
class WizardChoices:
    """The options every select needs on the current step."""

    source_adapters: tuple[Choice, ...]
    candidate_adapters: tuple[Choice, ...]
    metric_adapters: tuple[Choice, ...]
    providers: tuple[Choice, ...]
    credentials: tuple[Choice, ...]
    prompts: tuple[Choice, ...]
    contexts: tuple[Choice, ...]
    metric_keys: tuple[Choice, ...]
    gate_metrics: tuple[Choice, ...]
    templates: tuple[Choice, ...]


@dataclass(frozen=True, slots=True)
class EstimateRail:
    """The numbers the rail shows beside the form."""

    cells: int
    tokens_per_cell: int
    cost_usd: float
    budget_usd: float | None
    headroom_usd: float | None
    over_budget: bool
    budget_percent: int
    observed: bool


@dataclass(frozen=True, slots=True)
class WizardPage:
    """The complete render model for one wizard page."""

    step: StepDefinition
    steps: tuple[StepDefinition, ...]
    previous_path: str
    draft: str
    values: WizardValues
    choices: WizardChoices
    errors: dict[str, WizardError]
    general_errors: tuple[WizardError, ...]
    warnings: tuple[str, ...]
    estimate: EstimateRail | None
    graph: SpecGraph


def read_values(
    document: Mapping[str, Any],
    step: int,
    extra_rows: Iterable[str] = (),
) -> WizardValues:
    """Read the current draft into the row model the template renders."""
    extra = set(extra_rows)
    source = read_mapping(document.get("source"))
    options = read_mapping(source.get("options"))
    cases = _with_extra(
        tuple(
            CaseRow(
                number,
                read_text(case.get("case_id")),
                read_text(case.get("prompt")),
                read_text(read_mapping(case.get("expected")).get("answer")),
                read_text(case.get("rubric")),
                read_text(case.get("tier")),
            )
            for number, case in enumerate(read_sequence(options.get("cases")))
            if isinstance(case, Mapping)
        ),
        "case",
        extra,
        lambda number: CaseRow(number, "", "", "", "", ""),
    )
    prompts = _with_extra(
        tuple(
            PromptRow(number, name, read_text(text))
            for number, (name, text) in enumerate(
                read_mapping(document.get("prompts")).items()
            )
        ),
        "prompt",
        extra,
        lambda number: PromptRow(number, "", ""),
    )
    contexts = _with_extra(
        tuple(
            ContextRow(number, name, _context_text(chunks))
            for number, (name, chunks) in enumerate(
                read_mapping(document.get("contexts")).items()
            )
        ),
        "context",
        extra,
        lambda number: ContextRow(number, "", ""),
    )
    credentials = _with_extra(
        tuple(
            CredentialRow(
                number,
                read_text(read_mapping(record).get("name")),
                read_text(read_mapping(record).get("provider")) or "none",
            )
            for number, record in enumerate(read_sequence(document.get("credentials")))
        ),
        "credential",
        extra,
        lambda number: CredentialRow(number, "", "none"),
    )
    candidates = _with_extra(
        tuple(
            _candidate_row(number, candidate)
            for number, candidate in enumerate(read_sequence(document.get("candidates")))
            if isinstance(candidate, Mapping)
        ),
        "candidate",
        extra,
        lambda number: CandidateRow(
            number, "", STUB_CANDIDATE, "", "none", "none", "none"
        ),
    )
    metrics = _with_extra(
        tuple(
            MetricRow(
                number,
                read_text(read_mapping(metric).get("key")),
                read_text(read_mapping(metric).get("adapter")) or EXACT_MATCH,
            )
            for number, metric in enumerate(read_sequence(document.get("metrics")))
            if read_text(read_mapping(metric).get("adapter")) != JUDGE_PASS
        ),
        "metric",
        extra,
        lambda number: MetricRow(number, "", EXACT_MATCH),
    )
    judges = _with_extra(
        tuple(
            JudgeRow(
                number,
                read_text(read_mapping(judge).get("options", {}).get("metric"))
                or _judge_metric(document, read_text(read_mapping(judge).get("name"))),
                read_text(read_mapping(judge).get("model")),
                "\n".join(
                    read_text(claim)
                    for claim in read_sequence(
                        read_mapping(read_mapping(judge).get("options")).get("claims")
                    )
                ),
            )
            for number, judge in enumerate(read_sequence(document.get("judges")))
            if isinstance(judge, Mapping)
        ),
        "judge",
        extra,
        lambda number: JudgeRow(number, "", "", ""),
    )
    gates = _with_extra(
        tuple(
            GateRow(
                number,
                read_text(read_mapping(gate).get("metric")),
                "at most" if read_mapping(gate).get("max") is not None else "at least",
                read_text(
                    read_mapping(gate).get("max")
                    if read_mapping(gate).get("max") is not None
                    else read_mapping(gate).get("min")
                ),
            )
            for number, gate in enumerate(read_sequence(document.get("gates")))
        ),
        "gate",
        extra,
        lambda number: GateRow(number, "", "at least", ""),
    )
    prices = _with_extra(
        tuple(
            PriceRow(
                number,
                read_text(model),
                read_text(read_mapping(price).get("input")),
                read_text(read_mapping(price).get("output")),
            )
            for number, (model, price) in enumerate(
                read_mapping(document.get("prices")).items()
            )
        ),
        "price",
        extra,
        lambda number: PriceRow(number, "", "", ""),
    )
    return WizardValues(
        name=read_text(document.get("name")),
        description=read_text(document.get("description")),
        seed=read_text(document.get("seed")),
        source_adapter=read_text(source.get("adapter")) or INLINE_SOURCE,
        cases=cases,
        prompts=prompts,
        contexts=contexts,
        credentials=credentials,
        candidates=candidates,
        replicas=read_text(document.get("replicas")) or "1",
        concurrency=read_text(document.get("concurrency")) or "1",
        budget=read_text(document.get("budget_usd")),
        metrics=metrics,
        judges=judges,
        gates=gates,
        prices=prices,
    )


def build_choices(
    values: WizardValues,
    credentials: Sequence[CredentialChoice],
) -> WizardChoices:
    """Build every select's options from the draft and the stored credentials."""
    prompt_names = tuple(
        Choice(row.name, row.name) for row in values.prompts if row.name
    )
    context_names = tuple(
        Choice(row.name, row.name) for row in values.contexts if row.name
    )
    metrics = tuple(Choice(row.key, row.key) for row in values.metrics if row.key)
    judge_metrics = tuple(
        Choice(row.metric, row.metric) for row in values.judges if row.metric
    )
    credentials_choices = tuple(
        Choice(choice.name, f"{choice.name} ({choice.state})")
        for choice in credentials
    )
    providers = tuple(
        Choice(option.name, f"{option.name} ({option.auth})")
        for option in provider_options()
    )
    return WizardChoices(
        source_adapters=_adapter_choices("source"),
        candidate_adapters=_adapter_choices("candidate"),
        metric_adapters=_adapter_choices("metric"),
        providers=(Choice("none", "none"), *providers),
        credentials=(Choice("none", "none (no credential)"), *credentials_choices),
        prompts=(Choice("none", "none (case prompt)"), *prompt_names),
        contexts=(Choice("none", "none"), *context_names),
        metric_keys=(Choice("", "Choose a metric"), *metrics),
        gate_metrics=(Choice("", "Choose a metric"), *(metrics + judge_metrics)),
        templates=tuple(Choice(demo.slug, demo.title) for demo in DEMOS),
    )


def estimate_rail(check: SpecCheck, document: Mapping[str, Any]) -> EstimateRail | None:
    """Turn a validation result into the rail's estimate, or None."""
    estimate = check.estimate
    if estimate is None:
        return None
    budget = _budget_value(document.get("budget_usd"))
    cost = estimate.cost_usd
    headroom = None if budget is None else budget - cost
    over = budget is not None and cost > budget
    percent = 0 if not budget else min(100, round(cost / budget * 100))
    return EstimateRail(
        cells=estimate.cells,
        tokens_per_cell=estimate.median_prompt_tokens
        + estimate.median_completion_tokens,
        cost_usd=cost,
        budget_usd=budget,
        headroom_usd=headroom,
        over_budget=over,
        budget_percent=percent,
        observed=estimate.observed,
    )


def field_key(field: str, document: Mapping[str, Any]) -> str | None:
    """Return the form field id a validator error belongs to, if one exists."""
    normalized = field.replace("[", ".").replace("]", "")
    if normalized in _FIELD_ALIASES:
        return _FIELD_ALIASES[normalized]
    if normalized.startswith(("candidates.", "metrics.", "judges.", "gates.")):
        return _row_field(normalized)
    if normalized.startswith("prices."):
        return _price_field(normalized, document)
    return None


def collect_errors(
    check: SpecCheck,
    document: Mapping[str, Any],
    step: int,
    credentials: Sequence[CredentialChoice],
    fields: Mapping[str, str],
) -> tuple[dict[str, WizardError], tuple[WizardError, ...]]:
    """Merge the wizard's own errors with the validator's, field by field."""
    errors: dict[str, WizardError] = {}
    general: list[WizardError] = []
    for error in route_errors(document, step, credentials, fields):
        if error.field:
            errors.setdefault(error.field, error)
        else:
            general.append(error)
    for error in check.errors:
        key = field_key(error.field, document)
        mapped = _check_error(error)
        if key:
            errors.setdefault(key, mapped)
        else:
            general.append(mapped)
    return errors, tuple(general)


def route_errors(
    document: Mapping[str, Any],
    step: int,
    credentials: Sequence[CredentialChoice],
    fields: Mapping[str, str],
) -> tuple[WizardError, ...]:
    """Return the wizard's own errors for the step whose fields were posted."""
    errors = list(_identity_errors(document))
    if step == 1:
        errors.extend(_case_errors(document))
    if step == 2:
        errors.extend(_context_errors(document, credentials, fields))
    if step == 3:
        errors.extend(_candidate_errors(document, credentials))
    return tuple(errors)


def collect_edit_errors(
    check: SpecCheck,
    document: Mapping[str, Any],
    credentials: Sequence[CredentialChoice],
    fields: Mapping[str, str],
) -> tuple[dict[str, WizardError], tuple[WizardError, ...]]:
    """Merge the single page's own errors with the validator's, field by field."""
    errors, general = collect_errors(check, document, 1, credentials, fields)
    merged = dict(errors)
    general_errors = list(general)
    for step in (2, 3):
        for error in route_errors(document, step, credentials, fields):
            if error.field:
                merged.setdefault(error.field, error)
            elif error not in general_errors:
                general_errors.append(error)
    return merged, tuple(general_errors)


def build_page(
    step: int,
    document: Mapping[str, Any],
    draft: str,
    check: SpecCheck,
    credentials: Sequence[CredentialChoice],
    *,
    errors: Mapping[str, WizardError] | None = None,
    general_errors: Sequence[WizardError] = (),
    extra_rows: Iterable[str] = (),
) -> WizardPage:
    """Assemble the render model for one step, including the rail and graph."""
    values = read_values(document, step, extra_rows)
    choices = build_choices(values, credentials)
    definition = STEPS[step - 1]
    previous = STEPS[step - 2].path if step > 1 else STEPS[0].path
    return WizardPage(
        step=definition,
        steps=STEPS,
        previous_path=previous,
        draft=draft,
        values=values,
        choices=choices,
        errors=dict(errors or {}),
        general_errors=tuple(general_errors),
        warnings=check.warnings if check.valid else (),
        estimate=estimate_rail(check, document),
        graph=rail_graph(document),
    )


def _identity_errors(document: Mapping[str, Any]) -> tuple[WizardError, ...]:
    name = read_text(document.get("name")).strip()
    if not name:
        return (
            WizardError(
                "name",
                "Name",
                name,
                "the name is required",
                "give it a slug such as math-check",
            ),
        )
    if not SLUG_PATTERN.fullmatch(name):
        return (
            WizardError(
                "name",
                "Name",
                name,
                "the name must be a lower-case slug",
                "use letters, digits, dot, dash or underscore, "
                "starting with a letter or digit",
            ),
        )
    return ()


def _case_errors(document: Mapping[str, Any]) -> tuple[WizardError, ...]:
    errors: list[WizardError] = []
    source = read_mapping(document.get("source"))
    adapter = read_text(source.get("adapter")).strip()
    allowed = {choice.value for choice in _adapter_choices("source")}
    if adapter not in allowed:
        errors.append(
            WizardError(
                "source_adapter",
                "Case source",
                adapter,
                "the adapter is not a registered source",
                "pick one of the listed sources",
            )
        )
    cases = read_sequence(read_mapping(source.get("options")).get("cases"))
    for number, case in enumerate(cases):
        row = read_mapping(case)
        if not read_text(row.get("case_id")).strip():
            errors.append(
                WizardError(
                    f"case_{number}_case_id",
                    f"Case {number + 1} id",
                    read_text(row.get("case_id")),
                    "the case id is required",
                    "give the case a unique id such as case-1",
                )
            )
        if not read_text(row.get("prompt")).strip():
            errors.append(
                WizardError(
                    f"case_{number}_prompt",
                    f"Case {number + 1} prompt",
                    read_text(row.get("prompt")),
                    "the prompt is required",
                    "write what the candidate is asked",
                )
            )
        tier = row.get("tier")
        if not isinstance(tier, int) or isinstance(tier, bool):
            errors.append(
                WizardError(
                    f"case_{number}_tier",
                    f"Case {number + 1} tier",
                    read_text(tier),
                    "the tier must be a whole number",
                    "enter a whole number such as 1",
                )
            )
    return tuple(errors)


def _context_errors(
    document: Mapping[str, Any],
    credentials: Sequence[CredentialChoice],
    fields: Mapping[str, str],
) -> tuple[WizardError, ...]:
    errors: list[WizardError] = []
    for number, (name, chunks) in enumerate(read_mapping(document.get("prompts")).items()):
        if not name and read_text(chunks).strip():
            errors.append(
                WizardError(
                    f"prompt_{number}_name",
                    f"Prompt {number + 1} name",
                    name,
                    "the name is required",
                    "name the prompt such as concise",
                )
            )
    for number, (name, chunks) in enumerate(read_mapping(document.get("contexts")).items()):
        if not name and _context_text(chunks).strip():
            errors.append(
                WizardError(
                    f"context_{number}_name",
                    f"Context {number + 1} name",
                    name,
                    "the name is required",
                    "name the context such as policy",
                )
            )
    providers = {option.name for option in provider_options()} | {"none"}
    for number, record in enumerate(read_sequence(document.get("credentials"))):
        row = read_mapping(record)
        name = read_text(row.get("name")).strip()
        if not name:
            errors.append(
                WizardError(
                    f"credential_{number}_name",
                    f"Credential {number + 1} name",
                    name,
                    "the name is required",
                    "name the credential such as openai-main",
                )
            )
        provider = read_text(row.get("provider")).strip() or "none"
        if provider not in providers:
            errors.append(
                WizardError(
                    f"credential_{number}_provider",
                    f"Credential {number + 1} provider",
                    provider,
                    "the provider is not a known verifier",
                    f"pick one of {', '.join(sorted(providers))}",
                )
            )
    errors.extend(_duplicate_errors(fields, "prompt", "Prompts"))
    errors.extend(_duplicate_errors(fields, "context", "Contexts"))
    errors.extend(_duplicate_errors(fields, "credential", "Credentials"))
    return tuple(errors)


def _candidate_errors(
    document: Mapping[str, Any],
    credentials: Sequence[CredentialChoice],
) -> tuple[WizardError, ...]:
    errors: list[WizardError] = []
    replicas = document.get("replicas")
    if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 1:
        errors.append(
            WizardError(
                "replicas",
                "Replicas",
                read_text(replicas),
                "replicas must be a whole number of at least 1",
                "enter 1 or more",
            )
        )
    concurrency = document.get("concurrency", 1)
    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or concurrency < 1
    ):
        errors.append(
            WizardError(
                "concurrency",
                "Concurrency",
                read_text(concurrency),
                "concurrency must be a whole number of at least 1",
                "enter 1 or more",
            )
        )
    stored = {choice.name for choice in credentials} | {"none"}
    prompts = {"none"} | set(read_mapping(document.get("prompts")))
    contexts = {"none"} | set(read_mapping(document.get("contexts")))
    for number, candidate in enumerate(read_sequence(document.get("candidates"))):
        row = read_mapping(candidate)
        if not read_text(row.get("name")).strip():
            errors.append(
                WizardError(
                    f"candidate_{number}_name",
                    f"Candidate {number + 1} name",
                    "",
                    "the name is required",
                    "name the candidate such as stub-concise",
                )
            )
        if not read_text(row.get("model")).strip():
            errors.append(
                WizardError(
                    f"candidate_{number}_model",
                    f"Candidate {number + 1} model",
                    "",
                    "the model is required",
                    "name the model the adapter calls, or keep the stub model",
                )
            )
        credential = read_text(row.get("credential")) or "none"
        if credential not in stored:
            errors.append(
                WizardError(
                    f"candidate_{number}_credential",
                    f"Candidate {number + 1} credential",
                    credential,
                    "the credential is not stored",
                    "pick a stored credential or none",
                )
            )
        options = read_mapping(row.get("options"))
        prompt = read_text(options.get("prompt")) or "none"
        if prompt not in prompts:
            errors.append(
                WizardError(
                    f"candidate_{number}_prompt",
                    f"Candidate {number + 1} prompt",
                    prompt,
                    "the prompt is not declared on step 2",
                    "pick a declared prompt or none",
                )
            )
        context = read_text(options.get("context")) or "none"
        if context not in contexts:
            errors.append(
                WizardError(
                    f"candidate_{number}_context",
                    f"Candidate {number + 1} context",
                    context,
                    "the context is not declared on step 2",
                    "pick a declared context or none",
                )
            )
    return tuple(errors)


def _duplicate_errors(
    fields: Mapping[str, str],
    prefix: str,
    label: str,
) -> list[WizardError]:
    errors: list[WizardError] = []
    seen: set[str] = set()
    for number in read_indices(fields, prefix):
        name = fields.get(f"{prefix}_{number}_name", "").strip()
        if name and name in seen:
            errors.append(
                WizardError(
                    "",
                    label,
                    name,
                    "the name is declared twice",
                    "give each row a unique name",
                )
            )
        if name:
            seen.add(name)
    return errors


def _with_extra(
    rows: tuple[Any, ...],
    kind: str,
    extra: set[str],
    factory: Any,
) -> tuple[Any, ...]:
    """Return the rows plus one blank render row when this request asked."""
    return (*rows, factory(len(rows))) if kind in extra else rows


def _adapter_choices(kind: str) -> tuple[Choice, ...]:
    rows = [
        entry.adapter
        for entry in MANIFEST
        if entry.kind == kind
        and not (kind == "metric" and entry.adapter == JUDGE_PASS)
    ]
    return tuple(Choice(adapter, _adapter_label(adapter)) for adapter in rows)


def _adapter_label(adapter: str) -> str:
    """Name an adapter the way a first-time operator can read.

    A first-party adapter is labelled by what it does, with its short module
    path beside it for the power user; anything else keeps the raw dotted
    path so the registry stays honest about what is registered.
    """
    module, _, factory = adapter.partition(":")
    short = module.removeprefix("evalharness.adapters.")
    plain = _PLAIN_ADAPTER_LABELS.get(f"{short}:{factory}")
    if plain is not None:
        return f"{plain} ({short.replace('.', '/')})"
    return f"{short.replace('.', ' / ')} ({factory})"


_PLAIN_ADAPTER_LABELS: dict[str, str] = {
    "sources.inline:build": "Inline cases, pasted in the spec",
    "sources.repo:build": "Cases from a repository snapshot",
    "candidates.stub:build": "Stub candidate, offline and deterministic",
    "candidates.stub:build_failing": "Stub that always fails",
    "candidates.stub:build_fixed_prompt": "Stub that echoes the fixed prompt",
    "candidates.stub:build_slow": "Slow stub, for latency runs",
    "candidates.http:build": "HTTP endpoint speaking the v1 contract",
    "candidates.openai_compat:build": "OpenAI-compatible provider, needs a key",
    "candidates.anthropic:build": "Anthropic provider, needs a key",
    "metrics.exact_match:build": "Exact match against the expected answer",
    "metrics.contains:build": "Answer contains the expected text",
    "metrics.citation_support:build": "Citations support the answer",
    "metrics.judge_pass:build": "A judge's pass or fail verdict",
    "judges.rule_based:build": "Rule-based judge, deterministic",
    "judges.scripted:build": "Scripted judge, fixed verdicts",
    "judges.openai_compat:build": "Provider judge, OpenAI-compatible",
    "judges.anthropic:build": "Provider judge, Anthropic",
    "contexts.repo_graph:build": "Repository graph, ranks code windows",
    "contexts.artifact_graph:build": "Artifact graph, co-cited context",
}


def _candidate_row(number: int, candidate: Mapping[str, Any]) -> CandidateRow:
    options = read_mapping(candidate.get("options"))
    return CandidateRow(
        number,
        read_text(candidate.get("name")),
        read_text(candidate.get("adapter")) or STUB_CANDIDATE,
        read_text(candidate.get("model")),
        read_text(candidate.get("credential")) or "none",
        read_text(options.get("prompt")) or "none",
        read_text(options.get("context")) or "none",
    )


def _judge_metric(document: Mapping[str, Any], name: str) -> str:
    metrics = [
        read_text(read_mapping(metric).get("key"))
        for metric in read_sequence(document.get("metrics"))
        if read_mapping(read_mapping(metric).get("options")).get("judge") == name
    ]
    return metrics[0] if metrics else ""


def _row_field(normalized: str) -> str | None:
    kind, _, rest = normalized.partition(".")
    index, _, suffix = rest.partition(".")
    if kind == "candidates":
        return _candidate_field(index, suffix)
    if kind == "metrics":
        return f"metric_{index}_{'key' if suffix.startswith('key') else 'adapter'}"
    if kind == "judges":
        return None
    return f"gate_{index}_{'threshold' if suffix in ('min', 'max') else 'metric'}"


def _candidate_field(index: str, suffix: str) -> str:
    if suffix.startswith("options.prompt"):
        return f"candidate_{index}_prompt"
    if suffix.startswith("options.context"):
        return f"candidate_{index}_context"
    return f"candidate_{index}_{suffix.split('.')[0] or 'name'}"


def _price_field(normalized: str, document: Mapping[str, Any]) -> str | None:
    parts = normalized.split(".")
    model = ".".join(parts[1:-1]) or parts[1]
    part = parts[-1]
    models = [read_text(value) for value in read_mapping(document.get("prices"))]
    if model in models:
        suffix = "model" if part not in ("input", "output") else part
        return f"price_{models.index(model)}_{suffix}"
    return None


def _check_error(error: SpecError) -> WizardError:
    return WizardError(
        "",
        error.field,
        field_value(error.value),
        error.reason,
        error.fix,
    )


def _budget_value(value: Any) -> float | None:
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if numeric else None


def _context_text(value: Any) -> str:
    return "\n\n".join(
        read_text(read_mapping(chunk).get("text")) for chunk in read_sequence(value)
    )
