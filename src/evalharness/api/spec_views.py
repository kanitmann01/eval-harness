"""Read models for the spec editor: validation, estimates and view shapes.

The browser and the CLI share one validator: :func:`check_document` calls
:func:`evalharness.core.spec.validate_yaml_text`, the same loader
`evalh validate` uses, so a field error reads identically in both places. The
estimate is built from the planner and the cost table, using the medians an
earlier run of the same spec measured when one exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from evalharness.api.spec_store import SpecFile, SpecStore
from evalharness.core.context import RunContext
from evalharness.core.cost import (
    DEFAULT_MEDIAN_COMPLETION_TOKENS,
    DEFAULT_MEDIAN_PROMPT_TOKENS,
    estimate_cost,
    observed_medians,
)
from evalharness.core.events import EventEmitter
from evalharness.core.models import Case
from evalharness.core.planner import expand, per_candidate_counts
from evalharness.core.spec import ResolvedSpec, validate_yaml_text
from evalharness.errors import SpecError
from evalharness.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class SpecEstimate:
    """What the editor shows before a save: cells per candidate and cost."""

    cells: int
    cells_per_candidate: dict[str, int]
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    median_prompt_tokens: int
    median_completion_tokens: int
    observed: bool


@dataclass(frozen=True, slots=True)
class SpecCheck:
    """The outcome of validating and estimating one spec document."""

    resolved: ResolvedSpec | None
    errors: tuple[SpecError, ...]
    warnings: tuple[str, ...]
    cases: int
    estimate: SpecEstimate | None

    @property
    def valid(self) -> bool:
        """Return whether the document validated and planned without errors."""
        return self.resolved is not None and not self.errors


@dataclass(frozen=True, slots=True)
class SpecRow:
    """One row of the spec list: file metadata plus its validation outcome."""

    name: str
    path: Path
    size: int
    modified: datetime
    spec_name: str
    candidates: int
    check: SpecCheck


def check_document(text: str, repository: Repository) -> SpecCheck:
    """Validate YAML text with the CLI's validator and estimate its cells.

    A spec is valid only when it parses, passes semantic validation and its
    source yields at least one case, because those are the conditions under
    which the CLI would accept the same document.
    """
    names = [record.name for record in repository.list_credentials()]
    try:
        resolved = validate_yaml_text(text, stored_credentials=names)
    except SpecError as exc:
        return SpecCheck(
            resolved=None,
            errors=(exc,),
            warnings=(),
            cases=0,
            estimate=None,
        )
    warnings = tuple(resolved.warnings)
    try:
        cases = _load_cases(resolved)
        estimate = _estimate(resolved, cases, _medians(repository, resolved))
    except SpecError as exc:
        return SpecCheck(
            resolved=resolved,
            errors=(exc,),
            warnings=warnings,
            cases=0,
            estimate=None,
        )
    return SpecCheck(
        resolved=resolved,
        errors=(),
        warnings=warnings,
        cases=len(cases),
        estimate=estimate,
    )


def row_for(
    file: SpecFile,
    store: SpecStore,
    repository: Repository,
) -> SpecRow:
    """Validate one spec file and build its list row."""
    check = check_document(store.read(file.name), repository)
    resolved = check.resolved
    return SpecRow(
        name=file.name,
        path=file.path,
        size=file.size,
        modified=file.modified,
        spec_name=resolved.spec.name if resolved is not None else "",
        candidates=len(resolved.spec.candidates) if resolved is not None else 0,
        check=check,
    )


def field_value(value: Any) -> str:
    """Render a rejected value as text for JSON and HTML."""
    return value if isinstance(value, str) else repr(value)


def error_data(error: SpecError) -> dict[str, Any]:
    """Return the JSON shape of one field error."""
    return {
        "field": error.field,
        "value": field_value(error.value),
        "reason": error.reason,
        "fix": error.fix,
    }


def error_body(error: SpecError) -> dict[str, Any]:
    """Return the JSON body for a request that failed before validation."""
    return check_data(
        SpecCheck(
            resolved=None,
            errors=(error,),
            warnings=(),
            cases=0,
            estimate=None,
        )
    )


def check_data(check: SpecCheck) -> dict[str, Any]:
    """Return the JSON body for one validation and estimate."""
    estimate = check.estimate
    return {
        "valid": check.valid,
        "errors": [error_data(error) for error in check.errors],
        "warnings": list(check.warnings),
        "cases": check.cases,
        "cells": estimate.cells if estimate is not None else None,
        "cells_per_candidate": (
            dict(estimate.cells_per_candidate) if estimate is not None else {}
        ),
        "cost_estimate": estimate_data(estimate) if estimate is not None else None,
    }


def estimate_data(estimate: SpecEstimate) -> dict[str, Any]:
    """Return the JSON shape of one cell and cost estimate."""
    return {
        "cost_usd": estimate.cost_usd,
        "prompt_tokens": estimate.prompt_tokens,
        "completion_tokens": estimate.completion_tokens,
        "median_prompt_tokens": estimate.median_prompt_tokens,
        "median_completion_tokens": estimate.median_completion_tokens,
        "observed": estimate.observed,
    }


def summary_data(resolved: ResolvedSpec) -> dict[str, Any]:
    """Return the parsed summary of a valid spec."""
    spec = resolved.spec
    return {
        "spec_name": spec.name,
        "description": spec.description,
        "seed": spec.seed,
        "replicas": spec.replicas,
        "budget_usd": spec.budget_usd,
        "sha256": resolved.sha,
        "candidates": [
            {"name": candidate.name, "model": candidate.model}
            for candidate in spec.candidates
        ],
        "metrics": [metric.key for metric in spec.metrics],
        "gates": [gate.model_dump(mode="json") for gate in spec.gates],
    }


def row_data(row: SpecRow) -> dict[str, Any]:
    """Return the JSON shape of one spec list row."""
    first = row.check.errors[0] if row.check.errors else None
    return {
        "name": row.name,
        "path": str(row.path),
        "size": row.size,
        "modified": row.modified.isoformat(),
        "spec_name": row.spec_name,
        "candidates": row.candidates,
        "valid": row.check.valid,
        "error": error_data(first) if first is not None else None,
        "cells": row.check.estimate.cells if row.check.estimate is not None else None,
    }


def file_data(file: SpecFile, text: str, check: SpecCheck) -> dict[str, Any]:
    """Return raw YAML plus its parsed summary for one spec file."""
    payload = {
        "name": file.name,
        "path": str(file.path),
        "size": file.size,
        "modified": file.modified.isoformat(),
        "yaml": text,
    }
    payload.update(check_data(check))
    payload["summary"] = summary_data(check.resolved) if check.resolved else None
    return payload


def _load_cases(resolved: ResolvedSpec) -> list[Case]:
    """Load the spec's cases once so the editor can count the cells they plan."""
    context = RunContext(
        run_id="spec-editor",
        seed=resolved.spec.seed,
        credential=None,
        events=EventEmitter("spec-editor"),
    )
    try:
        cases = list(resolved.source.load(resolved.spec.source, context))
    except Exception as exc:  # source adapters are external code
        raise SpecError(
            "source",
            resolved.spec.source.adapter,
            f"the source failed to load cases: {exc}",
            "fix the source options",
        ) from exc
    if not cases:
        raise SpecError(
            "source",
            resolved.spec.source.adapter,
            "the source returned no cases",
            "add at least one case",
        )
    return cases


def _estimate(
    resolved: ResolvedSpec,
    cases: list[Case],
    medians: tuple[int, int] | None,
) -> SpecEstimate:
    """Expand the planned cells and price them at the fallback or observed medians."""
    prompt, completion = medians or (
        DEFAULT_MEDIAN_PROMPT_TOKENS,
        DEFAULT_MEDIAN_COMPLETION_TOKENS,
    )
    cells = expand(resolved, cases)
    counts = per_candidate_counts(cells)
    cost = estimate_cost(
        resolved.spec,
        resolved.prices,
        counts,
        median_prompt_tokens=prompt,
        median_completion_tokens=completion,
    )
    return SpecEstimate(
        cells=cost.cells,
        cells_per_candidate=counts,
        cost_usd=cost.cost_usd,
        prompt_tokens=cost.prompt_tokens,
        completion_tokens=cost.completion_tokens,
        median_prompt_tokens=prompt,
        median_completion_tokens=completion,
        observed=medians is not None,
    )


def _medians(
    repository: Repository,
    resolved: ResolvedSpec,
) -> tuple[int, int] | None:
    """Return the medians an earlier run of this exact spec measured, if any."""
    earlier = repository.latest_run_for_spec(resolved.sha)
    if earlier is None:
        return None
    return observed_medians(repository.list_cells(earlier.run_id))
