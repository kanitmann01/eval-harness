"""Read models for the spec editor: the check policy, list rows and view shapes.

The estimate pipeline itself lives in :mod:`evalharness.service`
(:func:`evalharness.service.check_spec_document`), the same loader and
medians ritual ``evalh run --dry-run`` uses, so the editor's numbers and
field-named errors cannot drift from the launch path. This module adds the
editor's stricter credential rule and shapes the results for HTML and JSON.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from evalharness.api.credential_views import CredentialChoice, credential_choices
from evalharness.api.spec_projection import project_spec
from evalharness.api.spec_store import SpecFile, SpecStore
from evalharness.api.templating import format_usd, format_usd_exact
from evalharness.core.spec import ResolvedSpec
from evalharness.errors import SpecError
from evalharness.service import SpecCheck, SpecEstimate, check_spec_document
from evalharness.storage.repository import Repository


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


_CHECK_CACHE: dict[str, tuple[tuple[int, datetime], SpecCheck]] = {}
"""One cached check per spec path, with the file identity it was computed for."""

_CHECK_CACHE_LOCK = threading.Lock()
"""Guards the cache while the console serves requests from a thread pool."""


def check_document(text: str, repository: Repository) -> SpecCheck:
    """Validate YAML text with the launch pipeline and estimate its cells.

    The editor is stricter than a launch: a named credential must carry a
    passing test before it can be selected here, so the check runs with the
    verified-credential requirement the raw editor and wizard share.
    """
    return check_spec_document(
        text,
        repository=repository,
        run_id="spec-editor",
        require_verified_credentials=True,
    )


def editor_credentials(repository: Repository) -> list[CredentialChoice]:
    """Return the credentials the editor sidebar lists with their state."""
    return credential_choices(repository.list_credentials())


def list_rows(
    files: Sequence[SpecFile],
    store: SpecStore,
    repository: Repository,
) -> list[SpecRow]:
    """Return the list projection, reusing the check of every unchanged file."""
    return [row_for(file, store, repository) for file in files]


def row_for(
    file: SpecFile,
    store: SpecStore,
    repository: Repository,
) -> SpecRow:
    """Build one list row, reusing its cached check while the file is unchanged."""
    check = _list_check(file, store, repository)
    resolved = check.resolved
    projection = project_spec(resolved.spec) if resolved is not None else None
    return SpecRow(
        name=file.name,
        path=file.path,
        size=file.size,
        modified=file.modified,
        spec_name=projection.name if projection is not None else "",
        candidates=len(projection.candidates) if projection is not None else 0,
        check=check,
    )


def _list_check(
    file: SpecFile,
    store: SpecStore,
    repository: Repository,
) -> SpecCheck:
    """Return one file's check, computing it only when its identity changes.

    The list already read the path, size and modified time, so those three form
    the identity: a repeat request does no file read, validation, adapter
    build, source load or median lookup, and an edited file is checked again at
    once. One entry per path means the cache cannot grow with edits.
    """
    path = str(file.path)
    identity = (file.size, file.modified)
    with _CHECK_CACHE_LOCK:
        entry = _CHECK_CACHE.get(path)
        if entry is not None and entry[0] == identity:
            return entry[1]
    check = check_document(store.read(file.name), repository)
    with _CHECK_CACHE_LOCK:
        _CHECK_CACHE[path] = (identity, check)
    return check


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
        "cost_text": format_usd(estimate.cost_usd),
        "cost_exact": format_usd_exact(estimate.cost_usd),
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


# SpecCheck and SpecEstimate moved to evalharness.service, the module every
# estimate consumer shares. Re-exported here so the view modules and tests
# that shaped them keep one import site.
__all__ = [
    "SpecCheck",
    "SpecEstimate",
    "check_document",
    "check_data",
    "editor_credentials",
    "error_body",
    "error_data",
    "estimate_data",
    "field_value",
    "file_data",
    "list_rows",
    "row_data",
    "row_for",
    "summary_data",
]
