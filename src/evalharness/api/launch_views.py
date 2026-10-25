"""Page shaping for the launch page.

The cancel action lives in the shared navigation partial (`site_nav.html`),
rendered from `base.html` on every page. It needs no page shaping here: the
run detail page already carries the `run` record, so the partial can show the
action only while that run is live without the read-only page being edited.
"""

from __future__ import annotations

from typing import Any

from evalharness.api.launch import (
    LaunchError,
    SpecRef,
    available_specs,
    resolve_spec,
    specs_directory,
)
from evalharness.core.config import Settings
from evalharness.core.cost import CostEstimate
from evalharness.errors import SpecError
from evalharness.service import load_run

ESTIMATE_RUN_ID = "estimate"
"""The placeholder id a page estimate loads cases under; no run is created."""


def launch_page_context(
    *,
    settings: Settings,
    requested: str,
    budget_override: str = "",
    error: str = "",
    running_run_id: str | None = None,
) -> dict[str, Any]:
    """Build the launch page: the picker, its estimate and any refusal.

    The estimate loads the spec exactly as a launch would, so an invalid spec
    is named before an operator spends a click on launching it.
    """
    specs: list[SpecRef] = available_specs(settings)
    selected = requested.strip() or (specs[0].name if specs else "")
    estimate: CostEstimate | None = None
    estimate_error = ""
    spec_budget: float | None = None
    spec_name = ""
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
    return {
        "specs": specs,
        "specs_root": str(specs_directory(settings)),
        "selected": selected,
        "spec_name": spec_name,
        "budget_override": budget_override,
        "estimate": estimate,
        "estimate_error": estimate_error,
        "spec_budget_usd": spec_budget,
        "error": error,
        "running_run_id": running_run_id,
    }


__all__ = ["launch_page_context"]
