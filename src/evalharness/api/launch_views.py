"""Page shaping for the launch page.

The cancel action lives in the shared navigation partial (`site_nav.html`),
rendered from `base.html` on every page. It needs no page shaping here: the
run detail page already carries the `run` record, so the partial can show the
action only while that run is live without the read-only page being edited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evalharness.api.launch import (
    LaunchError,
    SpecRef,
    available_specs,
    resolve_spec,
    specs_directory,
)
from evalharness.api.spec_views import check_document
from evalharness.core.config import Settings
from evalharness.core.cost import CostEstimate
from evalharness.errors import SpecError
from evalharness.service import load_run
from evalharness.storage.repository import Repository

ESTIMATE_RUN_ID = "estimate"
"""The placeholder id a page estimate loads cases under; no run is created."""

PREFERRED_SPEC_NAMES = ("smoke.yaml", "smoke.yml")
"""A fresh checkout ships `smoke.yaml`, so it is the default when valid."""


@dataclass(frozen=True, slots=True)
class SpecOption:
    """One spec in the launch picker and whether a launch would validate it."""

    name: str
    valid: bool
    reason: str


def spec_options(settings: Settings, repository: Repository) -> list[SpecOption]:
    """Return every spec file with its validation verdict for the picker."""
    return [_option_for(spec, repository) for spec in available_specs(settings)]


def launch_page_context(
    *,
    settings: Settings,
    repository: Repository,
    requested: str,
    budget_override: str = "",
    error: str = "",
    running_run_id: str | None = None,
) -> dict[str, Any]:
    """Build the launch page: the picker, its estimate and any refusal.

    The estimate loads the spec exactly as a launch would, so an invalid spec
    is named before an operator spends a click on launching it. With no
    request the picker preselects a spec that validates: a smoke spec when the
    directory has one, otherwise the first valid file. A file that fails
    validation is never preselected, because launching it can only fail.
    """
    options = spec_options(settings, repository)
    selected = requested.strip() or _default_spec(options)
    estimate: CostEstimate | None = None
    estimate_error = ""
    spec_budget: float | None = None
    spec_name = ""
    prompt_per_cell = 0
    completion_per_cell = 0
    if selected:
        try:
            path = resolve_spec(selected, settings=settings)
            prepared = load_run(path, settings=settings, run_id=ESTIMATE_RUN_ID)
        except (LaunchError, SpecError) as exc:
            estimate_error = str(exc)
        else:
            estimate = prepared.estimate
            spec_budget = prepared.resolved.spec.budget_usd
            spec_name = path.name
            cells = estimate.cells or 1
            prompt_per_cell = round(estimate.prompt_tokens / cells)
            completion_per_cell = round(estimate.completion_tokens / cells)
    headroom = (
        spec_budget - estimate.cost_usd
        if estimate is not None and spec_budget is not None
        else None
    )
    budget_percent = (
        min(100, round(estimate.cost_usd / spec_budget * 100))
        if estimate is not None and spec_budget
        else None
    )
    return {
        "specs": [option.name for option in options],
        "spec_options": options,
        "specs_root": str(specs_directory(settings)),
        "selected": spec_name or selected,
        "spec_name": spec_name,
        "budget_override": budget_override,
        "estimate": estimate,
        "estimate_error": estimate_error,
        "spec_budget_usd": spec_budget,
        "headroom_usd": headroom,
        "over_budget": bool(headroom is not None and headroom < 0),
        "budget_percent": budget_percent,
        "prompt_tokens_per_cell": prompt_per_cell,
        "completion_tokens_per_cell": completion_per_cell,
        "equivalent_command": f"evalh run {spec_name}" if spec_name else "",
        "error": error,
        "running_run_id": running_run_id,
    }


def _option_for(spec: SpecRef, repository: Repository) -> SpecOption:
    """Validate one spec file, reading a broken or non-UTF-8 file as invalid."""
    try:
        text = spec.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return SpecOption(spec.name, False, f"the file could not be read: {exc}")
    check = check_document(text, repository)
    error = check.errors[0] if check.errors else None
    reason = f"{error.field}: {error.reason}" if error is not None else ""
    return SpecOption(spec.name, check.valid, reason)


def _default_spec(options: list[SpecOption]) -> str:
    """Prefer a valid smoke spec, then the first valid spec, else nothing."""
    valid = [option for option in options if option.valid]
    for option in valid:
        if option.name in PREFERRED_SPEC_NAMES:
            return option.name
    return valid[0].name if valid else ""


__all__ = ["SpecOption", "launch_page_context", "spec_options"]
