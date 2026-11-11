"""The public view-model surface of the comparison wizard and edit form.

The comparison routes import every helper they need from here. The
implementation is split along the wizard's existing seam so each module has one
responsibility: comparison_draft owns the draft document the hidden form field
carries (parse, merge, serialize, action verbs), and comparison_rows builds the
render model (row models, field errors, choices, estimate rail, page assembly).

__all__ is the stable contract. Every name in it was public in this module
before the split; routes and tests keep importing from here unchanged.
"""

from __future__ import annotations

from evalharness.api.comparison_draft import (
    DEFAULT_MODEL,
    EXACT_MATCH,
    INLINE_SOURCE,
    JUDGE_PASS,
    REMOVABLE_KINDS,
    SCRIPTED_JUDGE,
    STUB_CANDIDATE,
    default_draft,
    draft_text,
    merge_step,
    parse_action,
    parse_draft,
    remove_row,
)
from evalharness.api.comparison_rows import (
    STEPS,
    CaseRow,
    CandidateRow,
    Choice,
    ContextRow,
    CredentialRow,
    EstimateRail,
    GateRow,
    JudgeRow,
    MetricRow,
    PriceRow,
    PromptRow,
    StepDefinition,
    WizardChoices,
    WizardError,
    WizardPage,
    WizardValues,
    build_choices,
    build_page,
    collect_edit_errors,
    collect_errors,
    estimate_rail,
    field_key,
    read_values,
    route_errors,
)

__all__ = [
    "DEFAULT_MODEL",
    "EXACT_MATCH",
    "INLINE_SOURCE",
    "JUDGE_PASS",
    "REMOVABLE_KINDS",
    "SCRIPTED_JUDGE",
    "STEPS",
    "STUB_CANDIDATE",
    "CaseRow",
    "CandidateRow",
    "Choice",
    "ContextRow",
    "CredentialRow",
    "EstimateRail",
    "GateRow",
    "JudgeRow",
    "MetricRow",
    "PriceRow",
    "PromptRow",
    "StepDefinition",
    "WizardChoices",
    "WizardError",
    "WizardPage",
    "WizardValues",
    "build_choices",
    "build_page",
    "collect_edit_errors",
    "collect_errors",
    "default_draft",
    "draft_text",
    "estimate_rail",
    "field_key",
    "merge_step",
    "parse_action",
    "parse_draft",
    "read_values",
    "remove_row",
    "route_errors",
]
