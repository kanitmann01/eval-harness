"""Pure view models for the four step comparison wizard.

The wizard is stateless: the only state is the YAML draft in the hidden form
field. Every function here is side-effect free. The route owns the repository,
the validator and the single disk write, and calls these helpers to read a
step's fields, merge the posted rows into the draft and map validator errors
back to form fields.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from evalharness.adapters.manifest import MANIFEST
from evalharness.api.credential_views import CredentialChoice, provider_options
from evalharness.api.graph_views import SpecGraph, spec_graph
from evalharness.api.spec_store import SLUG_PATTERN
from evalharness.api.spec_views import SpecCheck, field_value
from evalharness.errors import SpecError

INLINE_SOURCE = "evalharness.adapters.sources.inline:build"
STUB_CANDIDATE = "evalharness.adapters.candidates.stub:build"
EXACT_MATCH = "evalharness.adapters.metrics.exact_match:build"
JUDGE_PASS = "evalharness.adapters.metrics.judge_pass:build"
SCRIPTED_JUDGE = "evalharness.adapters.judges.scripted:build"
DEFAULT_MODEL = "stub-1"

_INT_TEXT = re.compile(r"-?\d+")
_FLOAT_TEXT = re.compile(r"-?\d+(?:\.\d+)?")
_ROW_KEY = re.compile(r"^(\w+)_(\d+)_")

_FIELD_ALIASES = {
    "name": "name",
    "description": "description",
    "seed": "seed",
    "source.adapter": "source_adapter",
    "budget_usd": "budget_usd",
    "replicas": "replicas",
    "concurrency": "concurrency",
}

_REMOVE_PATHS: dict[str, tuple[str, ...]] = {
    "case": ("source", "options", "cases"),
    "prompt": ("prompts",),
    "context": ("contexts",),
    "credential": ("credentials",),
    "candidate": ("candidates",),
    "metric": ("metrics",),
    "judge": ("judges",),
    "gate": ("gates",),
    "price": ("prices",),
}

REMOVABLE_KINDS: tuple[str, ...] = tuple(_REMOVE_PATHS)
"""The row kinds a Remove submit button may name."""


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


def default_draft() -> dict[str, Any]:
    """Return the spec document step 1 starts from."""
    return {
        "spec_version": 1,
        "name": "comparison",
        "description": "",
        "seed": 0,
        "source": {
            "adapter": INLINE_SOURCE,
            "options": {"cases": [{"case_id": "case-1", "prompt": ""}]},
        },
        "prompts": {},
        "contexts": {},
        "credentials": [],
        "candidates": [
            {
                "name": "baseline",
                "adapter": STUB_CANDIDATE,
                "credential": None,
                "model": DEFAULT_MODEL,
                "options": {},
            }
        ],
        "replicas": 1,
        "concurrency": 1,
        "budget_usd": None,
        "metrics": [{"key": "exact_match", "adapter": EXACT_MATCH, "options": {}}],
        "judges": [],
        "gates": [],
        "prices": {DEFAULT_MODEL: {"input": 0.15, "output": 0.6}},
        "provider_prices": {},
    }


def draft_text(document: Mapping[str, Any]) -> str:
    """Return the YAML text the hidden draft field carries."""
    return yaml.safe_dump(dict(document), sort_keys=False, default_flow_style=False)


def parse_draft(text: str) -> tuple[dict[str, Any] | None, str]:
    """Parse the hidden draft, returning the document or why it failed."""
    if not text.strip():
        return None, ""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"the draft did not parse as YAML: {exc}"
    if not isinstance(data, dict):
        return None, "the draft must be a YAML mapping at the top level"
    return data, ""


def parse_action(action: str) -> tuple[str, str, int]:
    """Split a submit button value into a verb, a row kind and an index."""
    verb, _, rest = action.partition(":")
    kind, _, number = rest.partition(":")
    target = int(number) if number.isdigit() else (int(kind) if kind.isdigit() else 0)
    return verb or "continue", kind, target


def merge_step(
    document: Mapping[str, Any],
    step: int,
    fields: Mapping[str, str],
) -> dict[str, Any]:
    """Return the draft with the step's own fields applied."""
    merged: dict[str, Any] = copy.deepcopy(dict(document))
    if step == 1:
        _merge_identity(merged, fields)
        _merge_cases(merged, fields)
    if step == 2:
        _merge_context(merged, fields)
    if step == 3:
        _merge_candidates(merged, fields)
    if step == 4:
        _merge_rules(merged, fields)
    return merged


def remove_row(document: dict[str, Any], kind: str, index: int) -> dict[str, Any]:
    """Remove one owned row from the document; a bad index is ignored."""
    path = _REMOVE_PATHS[kind]
    container: dict[str, Any] = document
    for key in path[:-1]:
        container = container.setdefault(key, {})
    target = container.get(path[-1])
    if isinstance(target, list) and 0 <= index < len(target):
        target.pop(index)
        container[path[-1]] = target
    elif isinstance(target, Mapping):
        names = list(target)
        if 0 <= index < len(names):
            container[path[-1]] = {
                name: target[name] for name in names if name != names[index]
            }
    return document


def read_values(
    document: Mapping[str, Any],
    step: int,
    extra_rows: Iterable[str] = (),
) -> WizardValues:
    """Read the current draft into the row model the template renders."""
    extra = set(extra_rows)
    source = _mapping(document.get("source"))
    options = _mapping(source.get("options"))
    cases = _with_extra(
        tuple(
            CaseRow(
                number,
                _text(case.get("case_id")),
                _text(case.get("prompt")),
                _text(_mapping(case.get("expected")).get("answer")),
                _text(case.get("rubric")),
                _text(case.get("tier")),
            )
            for number, case in enumerate(_sequence(options.get("cases")))
            if isinstance(case, Mapping)
        ),
        "case",
        extra,
        lambda number: CaseRow(number, "", "", "", "", ""),
    )
    prompts = _with_extra(
        tuple(
            PromptRow(number, name, _text(text))
            for number, (name, text) in enumerate(
                _mapping(document.get("prompts")).items()
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
                _mapping(document.get("contexts")).items()
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
                _text(_mapping(record).get("name")),
                _text(_mapping(record).get("provider")) or "none",
            )
            for number, record in enumerate(_sequence(document.get("credentials")))
        ),
        "credential",
        extra,
        lambda number: CredentialRow(number, "", "none"),
    )
    candidates = _with_extra(
        tuple(
            _candidate_row(number, candidate)
            for number, candidate in enumerate(_sequence(document.get("candidates")))
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
                _text(_mapping(metric).get("key")),
                _text(_mapping(metric).get("adapter")) or EXACT_MATCH,
            )
            for number, metric in enumerate(_sequence(document.get("metrics")))
            if _text(_mapping(metric).get("adapter")) != JUDGE_PASS
        ),
        "metric",
        extra,
        lambda number: MetricRow(number, "", EXACT_MATCH),
    )
    judges = _with_extra(
        tuple(
            JudgeRow(
                number,
                _text(_mapping(judge).get("options", {}).get("metric"))
                or _judge_metric(document, _text(_mapping(judge).get("name"))),
                _text(_mapping(judge).get("model")),
                "\n".join(
                    _text(claim)
                    for claim in _sequence(
                        _mapping(_mapping(judge).get("options")).get("claims")
                    )
                ),
            )
            for number, judge in enumerate(_sequence(document.get("judges")))
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
                _text(_mapping(gate).get("metric")),
                "at most" if _mapping(gate).get("max") is not None else "at least",
                _text(
                    _mapping(gate).get("max")
                    if _mapping(gate).get("max") is not None
                    else _mapping(gate).get("min")
                ),
            )
            for number, gate in enumerate(_sequence(document.get("gates")))
        ),
        "gate",
        extra,
        lambda number: GateRow(number, "", "at least", ""),
    )
    prices = _with_extra(
        tuple(
            PriceRow(
                number,
                _text(model),
                _text(_mapping(price).get("input")),
                _text(_mapping(price).get("output")),
            )
            for number, (model, price) in enumerate(
                _mapping(document.get("prices")).items()
            )
        ),
        "price",
        extra,
        lambda number: PriceRow(number, "", "", ""),
    )
    return WizardValues(
        name=_text(document.get("name")),
        description=_text(document.get("description")),
        seed=_text(document.get("seed")),
        source_adapter=_text(source.get("adapter")) or INLINE_SOURCE,
        cases=cases,
        prompts=prompts,
        contexts=contexts,
        credentials=credentials,
        candidates=candidates,
        replicas=_text(document.get("replicas")) or "1",
        concurrency=_text(document.get("concurrency")) or "1",
        budget=_text(document.get("budget_usd")),
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
        graph=spec_graph(document),
    )


def _identity_errors(document: Mapping[str, Any]) -> tuple[WizardError, ...]:
    name = _text(document.get("name")).strip()
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
    source = _mapping(document.get("source"))
    adapter = _text(source.get("adapter")).strip()
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
    cases = _sequence(_mapping(source.get("options")).get("cases"))
    for number, case in enumerate(cases):
        row = _mapping(case)
        if not _text(row.get("case_id")).strip():
            errors.append(
                WizardError(
                    f"case_{number}_case_id",
                    f"Case {number + 1} id",
                    _text(row.get("case_id")),
                    "the case id is required",
                    "give the case a unique id such as case-1",
                )
            )
        if not _text(row.get("prompt")).strip():
            errors.append(
                WizardError(
                    f"case_{number}_prompt",
                    f"Case {number + 1} prompt",
                    _text(row.get("prompt")),
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
                    _text(tier),
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
    for number, (name, chunks) in enumerate(_mapping(document.get("prompts")).items()):
        if not name and _text(chunks).strip():
            errors.append(
                WizardError(
                    f"prompt_{number}_name",
                    f"Prompt {number + 1} name",
                    name,
                    "the name is required",
                    "name the prompt such as concise",
                )
            )
    for number, (name, chunks) in enumerate(_mapping(document.get("contexts")).items()):
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
    for number, record in enumerate(_sequence(document.get("credentials"))):
        row = _mapping(record)
        name = _text(row.get("name")).strip()
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
        provider = _text(row.get("provider")).strip() or "none"
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
                _text(replicas),
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
                _text(concurrency),
                "concurrency must be a whole number of at least 1",
                "enter 1 or more",
            )
        )
    stored = {choice.name for choice in credentials} | {"none"}
    prompts = {"none"} | set(_mapping(document.get("prompts")))
    contexts = {"none"} | set(_mapping(document.get("contexts")))
    for number, candidate in enumerate(_sequence(document.get("candidates"))):
        row = _mapping(candidate)
        if not _text(row.get("name")).strip():
            errors.append(
                WizardError(
                    f"candidate_{number}_name",
                    f"Candidate {number + 1} name",
                    "",
                    "the name is required",
                    "name the candidate such as stub-concise",
                )
            )
        if not _text(row.get("model")).strip():
            errors.append(
                WizardError(
                    f"candidate_{number}_model",
                    f"Candidate {number + 1} model",
                    "",
                    "the model is required",
                    "name the model the adapter calls, or keep the stub model",
                )
            )
        credential = _text(row.get("credential")) or "none"
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
        options = _mapping(row.get("options"))
        prompt = _text(options.get("prompt")) or "none"
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
        context = _text(options.get("context")) or "none"
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
    for number in _indices(fields, prefix):
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


def _merge_identity(document: dict[str, Any], fields: Mapping[str, str]) -> None:
    document["name"] = fields.get("name", "").strip()
    document["description"] = fields.get("description", "")
    seed = fields.get("seed", "").strip()
    if seed:
        document["seed"] = _int_field(seed, 0)
    else:
        document.pop("seed", None)


def _merge_cases(document: dict[str, Any], fields: Mapping[str, str]) -> None:
    source = _mapping(document.get("source"))
    source["adapter"] = (
        fields.get("source_adapter", "").strip() or INLINE_SOURCE
    )
    options = dict(_mapping(source.get("options")))
    base = [
        dict(item)
        for item in _sequence(options.get("cases"))
        if isinstance(item, Mapping)
    ]
    cases: list[dict[str, Any]] = []
    for number in _indices(fields, "case"):
        case = dict(base[number]) if number < len(base) else {}
        case_id = fields.get(f"case_{number}_case_id", "").strip()
        prompt = fields.get(f"case_{number}_prompt", "")
        expected = fields.get(f"case_{number}_expected", "").strip()
        rubric = fields.get(f"case_{number}_rubric", "").strip()
        tier = fields.get(f"case_{number}_tier", "").strip()
        if not (case_id or prompt.strip() or expected or rubric or tier):
            continue
        case["case_id"] = case_id
        case["prompt"] = prompt
        expected_values = _mapping(case.get("expected"))
        if expected:
            expected_values["answer"] = expected
            case["expected"] = expected_values
        else:
            case["expected"] = None
        case["rubric"] = rubric or None
        case["tier"] = _int_field(tier, 1)
        cases.append(case)
    options["cases"] = cases
    source["options"] = options
    document["source"] = source


def _merge_context(document: dict[str, Any], fields: Mapping[str, str]) -> None:
    prompts: dict[str, str] = {}
    for number in _indices(fields, "prompt"):
        name = fields.get(f"prompt_{number}_name", "").strip()
        text = fields.get(f"prompt_{number}_text", "")
        if name or text.strip():
            prompts[name] = text
    document["prompts"] = prompts
    contexts: dict[str, list[dict[str, str]]] = {}
    for number in _indices(fields, "context"):
        name = fields.get(f"context_{number}_name", "").strip()
        text = fields.get(f"context_{number}_text", "").strip()
        if not (name or text):
            continue
        parts = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        chunks = parts or [""]
        contexts[name] = [
            {"chunk_id": f"{name or 'context'}-{position}", "text": part}
            for position, part in enumerate(chunks, 1)
        ]
    document["contexts"] = contexts
    credentials: list[dict[str, str]] = []
    for number in _indices(fields, "credential"):
        name = fields.get(f"credential_{number}_name", "").strip()
        provider = fields.get(f"credential_{number}_provider", "").strip() or "none"
        if name or provider != "none":
            credentials.append({"name": name, "provider": provider})
    document["credentials"] = credentials


def _merge_candidates(document: dict[str, Any], fields: Mapping[str, str]) -> None:
    base = [
        dict(item)
        for item in _sequence(document.get("candidates"))
        if isinstance(item, Mapping)
    ]
    candidates: list[dict[str, Any]] = []
    for number in _indices(fields, "candidate"):
        candidate = dict(base[number]) if number < len(base) else {}
        name = fields.get(f"candidate_{number}_name", "").strip()
        model = fields.get(f"candidate_{number}_model", "").strip()
        if not (name or model):
            continue
        options = dict(_mapping(candidate.get("options")))
        prompt = fields.get(f"candidate_{number}_prompt", "").strip()
        context = fields.get(f"candidate_{number}_context", "").strip()
        if prompt and prompt != "none":
            options["prompt"] = prompt
        else:
            options.pop("prompt", None)
        if context and context != "none":
            options["context"] = context
        else:
            options.pop("context", None)
        credential = fields.get(f"candidate_{number}_credential", "").strip()
        candidate.update(
            {
                "name": name,
                "adapter": fields.get(f"candidate_{number}_adapter", "").strip()
                or STUB_CANDIDATE,
                "credential": None if credential in ("", "none") else credential,
                "model": model,
                "options": options,
            }
        )
        candidates.append(candidate)
    document["candidates"] = candidates
    replicas = fields.get("replicas", "").strip()
    document["replicas"] = _int_field(replicas, 1)
    concurrency = fields.get("concurrency", "").strip()
    if concurrency:
        document["concurrency"] = _int_field(concurrency, 1)
    else:
        document.pop("concurrency", None)


def _merge_rules(document: dict[str, Any], fields: Mapping[str, str]) -> None:
    budget = fields.get("budget_usd", "").strip()
    if budget:
        document["budget_usd"] = _float_field(budget)
    else:
        document.pop("budget_usd", None)
    base = {
        _text(_mapping(metric).get("key")): dict(_mapping(metric))
        for metric in _sequence(document.get("metrics"))
        if isinstance(metric, Mapping)
    }
    metrics: list[dict[str, Any]] = []
    for number in _indices(fields, "metric"):
        key = fields.get(f"metric_{number}_key", "").strip()
        if not key:
            continue
        metric = dict(base.get(key, {}))
        metric.update(
            {
                "key": key,
                "adapter": fields.get(f"metric_{number}_adapter", "").strip()
                or EXACT_MATCH,
                "options": dict(_mapping(metric.get("options"))),
            }
        )
        metrics.append(metric)
    judges: list[dict[str, Any]] = []
    judge_metrics: list[dict[str, Any]] = []
    for number in _indices(fields, "judge"):
        metric_key = fields.get(f"judge_{number}_metric", "").strip()
        if not metric_key:
            continue
        name = f"{metric_key}-judge"
        claims = [
            line.strip()
            for line in fields.get(f"judge_{number}_rubric", "").splitlines()
            if line.strip()
        ]
        model = fields.get(f"judge_{number}_model", "").strip()
        judges.append(
            {
                "name": name,
                "adapter": SCRIPTED_JUDGE,
                "model": model or None,
                "options": {"claims": claims} if claims else {},
            }
        )
        judge_metrics.append(
            {
                "key": metric_key,
                "adapter": JUDGE_PASS,
                "options": {"judge": name},
            }
        )
    document["metrics"] = metrics + judge_metrics
    document["judges"] = judges
    gates: list[dict[str, Any]] = []
    for number in _indices(fields, "gate"):
        metric_key = fields.get(f"gate_{number}_metric", "").strip()
        threshold = fields.get(f"gate_{number}_threshold", "").strip()
        if not (metric_key or threshold):
            continue
        gate: dict[str, Any] = {"metric": metric_key}
        if fields.get(f"gate_{number}_op", "at least") == "at most":
            gate["max"] = _float_field(threshold)
        else:
            gate["min"] = _float_field(threshold)
        gates.append(gate)
    document["gates"] = gates
    prices: dict[str, dict[str, Any]] = {}
    for number in _indices(fields, "price"):
        model = fields.get(f"price_{number}_model", "").strip()
        input_text = fields.get(f"price_{number}_input", "").strip()
        output_text = fields.get(f"price_{number}_output", "").strip()
        if not (model or input_text or output_text):
            continue
        prices[model] = {
            "input": _float_field(input_text),
            "output": _float_field(output_text),
        }
    document["prices"] = prices


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
    module, _, factory = adapter.partition(":")
    short = module.removeprefix("evalharness.adapters.").replace(".", " / ")
    return f"{short} ({factory})"


def _indices(fields: Mapping[str, str], prefix: str) -> list[int]:
    found = {
        int(match.group(2))
        for key in fields
        for match in [_ROW_KEY.match(key)]
        if match and match.group(1) == prefix
    }
    return sorted(found)


def _candidate_row(number: int, candidate: Mapping[str, Any]) -> CandidateRow:
    options = _mapping(candidate.get("options"))
    return CandidateRow(
        number,
        _text(candidate.get("name")),
        _text(candidate.get("adapter")) or STUB_CANDIDATE,
        _text(candidate.get("model")),
        _text(candidate.get("credential")) or "none",
        _text(options.get("prompt")) or "none",
        _text(options.get("context")) or "none",
    )


def _judge_metric(document: Mapping[str, Any], name: str) -> str:
    metrics = [
        _text(_mapping(metric).get("key"))
        for metric in _sequence(document.get("metrics"))
        if _mapping(_mapping(metric).get("options")).get("judge") == name
    ]
    return metrics[0] if metrics else ""


def _row_field(normalized: str) -> str | None:
    kind, _, rest = normalized.partition(".")
    index, _, suffix = rest.partition(".")
    if kind == "candidates":
        return _candidate_field(index, suffix)
    if kind == "metrics":
        return f"metric_{index}_{'key' if suffix.startswith('key') else 'adapter'}"
    if kind == "judges": return None
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
    models = [_text(value) for value in _mapping(document.get("prices"))]
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


def _int_field(text: str, fallback: Any = None) -> Any:
    value = text.strip()
    return int(value) if _INT_TEXT.fullmatch(value) else (value or fallback)


def _float_field(text: str) -> Any:
    value = text.strip()
    if not value:
        return None
    return float(value) if _FLOAT_TEXT.fullmatch(value) else value


def _budget_value(value: Any) -> float | None:
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if numeric else None


def _context_text(value: Any) -> str:
    return "\n\n".join(
        _text(_mapping(chunk).get("text")) for chunk in _sequence(value)
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return "" if value is None else str(value)
