"""The comparison draft: the YAML document the wizard carries between steps.

The wizard keeps no session: the only state is the YAML draft in the hidden
form field. This module owns that document. It defines the adapter URIs a
fresh draft names, the round trip between the document and the hidden field,
the action verbs the submit buttons post, the merge of one step's posted fields
into the document and the removal of one row.

It reads and writes the document, it never renders it. comparison_rows builds
the page model from the same draft.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

import yaml



INLINE_SOURCE = "evalharness.adapters.sources.inline:build"
STUB_CANDIDATE = "evalharness.adapters.candidates.stub:build"
EXACT_MATCH = "evalharness.adapters.metrics.exact_match:build"
JUDGE_PASS = "evalharness.adapters.metrics.judge_pass:build"
SCRIPTED_JUDGE = "evalharness.adapters.judges.scripted:build"
DEFAULT_MODEL = "stub-1"


_INT_TEXT = re.compile(r"-?\d+")
_FLOAT_TEXT = re.compile(r"-?\d+(?:\.\d+)?")
_ROW_KEY = re.compile(r"^(\w+)_(\d+)_")


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


def read_mapping(value: Any) -> dict[str, Any]:
    """Return a YAML mapping value as a dict; anything else reads as empty."""
    return dict(value) if isinstance(value, Mapping) else {}


def read_sequence(value: Any) -> list[Any]:
    """Return a YAML sequence value as a list; anything else reads as empty."""
    return list(value) if isinstance(value, (list, tuple)) else []


def read_text(value: Any) -> str:
    """Return a YAML scalar as text; None reads as the empty string."""
    return "" if value is None else str(value)


def read_indices(fields: Mapping[str, str], prefix: str) -> list[int]:
    """Return the sorted row indices the posted form fields name for one kind."""
    found = {
        int(match.group(2))
        for key in fields
        for match in [_ROW_KEY.match(key)]
        if match and match.group(1) == prefix
    }
    return sorted(found)



def default_draft() -> dict[str, Any]:
    """Return the spec document step 1 starts from."""
    return {
        "spec_version": 1,
        "name": "comparison",
        "description": "",
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


def _merge_identity(document: dict[str, Any], fields: Mapping[str, str]) -> None:
    document["name"] = fields.get("name", "").strip()
    document["description"] = fields.get("description", "")
    seed = fields.get("seed", "").strip()
    if seed:
        document["seed"] = _int_field(seed, 0)
    else:
        document.pop("seed", None)


def _merge_cases(document: dict[str, Any], fields: Mapping[str, str]) -> None:
    source = read_mapping(document.get("source"))
    source["adapter"] = (
        fields.get("source_adapter", "").strip() or INLINE_SOURCE
    )
    options = dict(read_mapping(source.get("options")))
    base = [
        dict(item)
        for item in read_sequence(options.get("cases"))
        if isinstance(item, Mapping)
    ]
    cases: list[dict[str, Any]] = []
    for number in read_indices(fields, "case"):
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
        expected_values = read_mapping(case.get("expected"))
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
    for number in read_indices(fields, "prompt"):
        name = fields.get(f"prompt_{number}_name", "").strip()
        text = fields.get(f"prompt_{number}_text", "")
        if name or text.strip():
            prompts[name] = text
    document["prompts"] = prompts
    contexts: dict[str, list[dict[str, str]]] = {}
    for number in read_indices(fields, "context"):
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
    for number in read_indices(fields, "credential"):
        name = fields.get(f"credential_{number}_name", "").strip()
        provider = fields.get(f"credential_{number}_provider", "").strip() or "none"
        if name or provider != "none":
            credentials.append({"name": name, "provider": provider})
    document["credentials"] = credentials


def _merge_candidates(document: dict[str, Any], fields: Mapping[str, str]) -> None:
    base = [
        dict(item)
        for item in read_sequence(document.get("candidates"))
        if isinstance(item, Mapping)
    ]
    candidates: list[dict[str, Any]] = []
    for number in read_indices(fields, "candidate"):
        candidate = dict(base[number]) if number < len(base) else {}
        name = fields.get(f"candidate_{number}_name", "").strip()
        model = fields.get(f"candidate_{number}_model", "").strip()
        if not (name or model):
            continue
        options = dict(read_mapping(candidate.get("options")))
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
        read_text(read_mapping(metric).get("key")): dict(read_mapping(metric))
        for metric in read_sequence(document.get("metrics"))
        if isinstance(metric, Mapping)
    }
    metrics: list[dict[str, Any]] = []
    for number in read_indices(fields, "metric"):
        key = fields.get(f"metric_{number}_key", "").strip()
        if not key:
            continue
        metric = dict(base.get(key, {}))
        metric.update(
            {
                "key": key,
                "adapter": fields.get(f"metric_{number}_adapter", "").strip()
                or EXACT_MATCH,
                "options": dict(read_mapping(metric.get("options"))),
            }
        )
        metrics.append(metric)
    judges: list[dict[str, Any]] = []
    judge_metrics: list[dict[str, Any]] = []
    for number in read_indices(fields, "judge"):
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
    for number in read_indices(fields, "gate"):
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
    for number in read_indices(fields, "price"):
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


def _int_field(text: str, fallback: Any = None) -> Any:
    value = text.strip()
    return int(value) if _INT_TEXT.fullmatch(value) else (value or fallback)


def _float_field(text: str) -> Any:
    value = text.strip()
    if not value:
        return None
    return float(value) if _FLOAT_TEXT.fullmatch(value) else value


