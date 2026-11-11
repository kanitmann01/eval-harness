"""Public-boundary tests for the comparison page model.

Each test builds the three inputs the route hands the page model (the draft,
the validator result and the stored credentials) and checks the render model
the templates consume.
"""

from __future__ import annotations

from dataclasses import replace

from evalharness.api.comparison_draft import (
    DEFAULT_MODEL,
    EXACT_MATCH,
    INLINE_SOURCE,
    STUB_CANDIDATE,
    default_draft,
)
from evalharness.api.comparison_rows import (
    STEPS,
    CaseRow,
    Choice,
    build_choices,
    build_page,
    collect_edit_errors,
    collect_errors,
    estimate_rail,
    field_key,
    read_values,
    route_errors,
)
from evalharness.api.credential_views import CredentialChoice
from evalharness.api.graph_views import SpecGraph
from evalharness.api.spec_views import SpecCheck, SpecEstimate
from evalharness.errors import SpecError


def _estimate() -> SpecEstimate:
    """Return an estimate whose medians and cost the rail can show."""
    return SpecEstimate(
        cells=12,
        cells_per_candidate={"baseline": 12},
        cost_usd=1.5,
        prompt_tokens=120,
        completion_tokens=60,
        median_prompt_tokens=20,
        median_completion_tokens=10,
        observed=True,
    )


def _check(
    *,
    errors: tuple[SpecError, ...] = (),
    warnings: tuple[str, ...] = (),
    estimate: SpecEstimate | None = None,
) -> SpecCheck:
    """Return a validation result carrying only what the page model reads."""
    return SpecCheck(
        resolved=None, errors=errors, warnings=warnings, cases=0, estimate=estimate
    )


def test_read_values_renders_the_default_draft_as_rows() -> None:
    """Step 1 sees one case, one candidate, one metric and one price."""
    values = read_values(default_draft(), 1)
    assert values.name == "comparison"
    assert values.source_adapter == INLINE_SOURCE
    assert values.cases == (CaseRow(0, "case-1", "", "", "", ""),)
    assert values.candidates[0].name == "baseline"
    assert values.candidates[0].adapter == STUB_CANDIDATE
    assert values.candidates[0].model == DEFAULT_MODEL
    assert values.candidates[0].credential == "none"
    assert values.metrics[0].key == "exact_match"
    assert values.metrics[0].adapter == EXACT_MATCH
    assert values.prices[0].model == DEFAULT_MODEL
    assert (values.prices[0].input, values.prices[0].output) == ("0.15", "0.6")
    assert (values.replicas, values.concurrency) == ("1", "1")
    assert values.budget == ""


def test_read_values_adds_the_blank_row_the_remove_action_asked_for() -> None:
    """An Add posting appends exactly one empty render row for that kind."""
    values = read_values(default_draft(), 1, ("case",))
    assert [case.index for case in values.cases] == [0, 1]
    assert values.cases[1] == CaseRow(1, "", "", "", "", "")


def test_build_choices_offers_the_sentinels_and_the_vault() -> None:
    """The selects always offer none first and name the stored credentials."""
    values = read_values(default_draft(), 2)
    choices = build_choices(
        values, [CredentialChoice("openai-main", "openai", "ready", True)]
    )
    assert choices.credentials == (
        Choice("none", "none (no credential)"),
        Choice("openai-main", "openai-main (ready)"),
    )
    assert choices.prompts[0] == Choice("none", "none (case prompt)")
    assert choices.contexts[0] == Choice("none", "none")
    assert choices.providers[0] == Choice("none", "none")
    assert choices.metric_keys[0] == Choice("", "Choose a metric")
    assert Choice("exact_match", "exact_match") in choices.metric_keys
    assert all(choice.value for choice in choices.source_adapters)


def test_estimate_rail_turns_the_check_into_budget_numbers() -> None:
    """Cells, tokens, cost, headroom and the budget line come from the check."""
    rail = estimate_rail(_check(estimate=_estimate()), {"budget_usd": 2.0})
    assert rail is not None
    assert (rail.cells, rail.tokens_per_cell, rail.cost_usd) == (12, 30, 1.5)
    assert (rail.budget_usd, rail.headroom_usd) == (2.0, 0.5)
    assert rail.over_budget is False
    assert rail.budget_percent == 75
    assert rail.observed is True


def test_estimate_rail_clamps_over_budget_and_handles_no_budget() -> None:
    """Over budget clamps the bar at 100; no budget means no headroom."""
    over = estimate_rail(
        _check(estimate=replace(_estimate(), cost_usd=2.5)), {"budget_usd": 2.0}
    )
    assert over is not None
    assert over.over_budget is True
    assert over.headroom_usd == -0.5
    assert over.budget_percent == 100

    unbounded = estimate_rail(_check(estimate=_estimate()), {})
    assert unbounded is not None
    assert (unbounded.budget_usd, unbounded.headroom_usd) == (None, None)
    assert unbounded.over_budget is False
    assert unbounded.budget_percent == 0

    assert estimate_rail(_check(), {"budget_usd": 2.0}) is None


def test_field_key_maps_validator_paths_to_form_fields() -> None:
    """Validator paths and posted row names land on the same form ids."""
    document = default_draft()
    assert field_key("name", document) == "name"
    assert field_key("source.adapter", document) == "source_adapter"
    assert field_key("budget_usd", document) == "budget_usd"
    assert field_key("candidates[0].name", document) == "candidate_0_name"
    assert field_key("candidates[1].options.prompt", document) == "candidate_1_prompt"
    assert field_key("metrics[0].key", document) == "metric_0_key"
    assert field_key("metrics[0].adapter", document) == "metric_0_adapter"
    assert field_key("gates[0].min", document) == "gate_0_threshold"
    assert field_key("prices.stub-1.output", document) == "price_0_output"
    assert field_key("judges[0].model", document) is None
    assert field_key("mystery", document) is None


def test_route_errors_name_the_row_each_step_owns() -> None:
    """Step 1 checks cases, step 3 checks candidates; both name the row."""
    document = default_draft()
    document["name"] = ""
    document["source"]["options"]["cases"] = []
    identity = route_errors(document, 1, (), {})
    assert [(error.field, error.reason) for error in identity] == [
        ("name", "the name is required")
    ]

    document = default_draft()
    document["source"]["options"]["cases"] = [{"case_id": "", "prompt": ""}]
    case_fields = {error.field for error in route_errors(document, 1, (), {})}
    assert case_fields == {"case_0_case_id", "case_0_prompt", "case_0_tier"}

    document = default_draft()
    document["candidates"] = [
        {
            "name": "",
            "adapter": STUB_CANDIDATE,
            "model": "",
            "credential": "vault-key",
            "options": {"prompt": "missing"},
        }
    ]
    candidate_fields = {error.field for error in route_errors(document, 3, (), {})}
    assert candidate_fields == {
        "candidate_0_name",
        "candidate_0_model",
        "candidate_0_credential",
        "candidate_0_prompt",
    }


def test_route_errors_reports_a_duplicate_row_name_as_a_general_error() -> None:
    """Two rows with one name have no field to decorate, so they stay general."""
    document = default_draft()
    document["prompts"] = {"dup": "x"}
    errors = route_errors(
        document, 2, (), {"prompt_0_name": "dup", "prompt_1_name": "dup"}
    )
    duplicates = [
        error for error in errors if error.reason == "the name is declared twice"
    ]
    assert len(duplicates) == 1
    assert duplicates[0].field == ""
    assert duplicates[0].label == "Prompts"


def test_collect_errors_layers_the_validator_over_the_rows() -> None:
    """Mapped validator errors decorate fields; unmapped ones stay general."""
    document = default_draft()
    document["name"] = ""
    check = _check(
        errors=(
            SpecError(
                "name", "", "the name is required", "give it a slug such as math-check"
            ),
            SpecError(
                "source.adapter",
                "nope",
                "the adapter is not a registered source",
                "pick one of the listed sources",
            ),
            SpecError("mystery", 1, "the value is wrong", "change it"),
        )
    )
    errors, general = collect_errors(check, document, 1, (), {})
    assert errors["name"].label == "Name"
    assert errors["name"].reason == "the name is required"
    assert errors["source_adapter"].label == "source.adapter"
    assert errors["source_adapter"].value == "nope"
    assert [(error.label, error.reason) for error in general] == [
        ("mystery", "the value is wrong")
    ]


def test_collect_edit_errors_adds_the_later_steps_on_one_page() -> None:
    """The single editing page shows step 2 and step 3 errors beside step 1."""
    document = default_draft()
    document["name"] = ""
    document["prompts"] = {"": "orphan text"}
    document["candidates"] = [{"name": "", "adapter": STUB_CANDIDATE, "model": ""}]
    errors, general = collect_edit_errors(_check(), document, (), {})
    assert {"name", "prompt_0_name", "candidate_0_name", "candidate_0_model"} <= set(
        errors
    )
    assert general == ()


def test_build_page_assembles_the_step_from_its_parts() -> None:
    """The page carries the step strip, the rows, the rail and the graph."""
    document = default_draft()
    draft = "name: comparison\ncandidates: []\n"
    page = build_page(
        1, document, draft, _check(estimate=_estimate(), warnings=("heads up",)), ()
    )
    assert page.step is STEPS[0]
    assert page.steps == STEPS
    assert page.previous_path == STEPS[0].path
    assert page.draft == draft
    assert page.values.name == "comparison"
    assert page.choices.providers[0] == Choice("none", "none")
    assert page.estimate is not None
    assert page.estimate.cells == 12
    assert page.errors == {}
    assert page.general_errors == ()
    assert page.warnings == ()
    assert isinstance(page.graph, SpecGraph)

    third = build_page(3, document, draft, _check(), ())
    assert third.step is STEPS[2]
    assert third.previous_path == STEPS[1].path
