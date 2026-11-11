"""Public-boundary tests for the comparison draft module.

The wizard's hidden YAML document is this module's whole contract: these tests
build the default draft, serialize and parse it, merge one step's posted fields
into it, and remove one row, without going through the HTTP layer.
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


def _step_one_fields() -> dict[str, str]:
    """Return the posted fields of a valid step 1 form."""
    return {
        "name": "math-check",
        "description": "Six times seven.",
        "seed": "7",
        "source_adapter": INLINE_SOURCE,
        "case_0_case_id": "case-1",
        "case_0_prompt": "What is 6 times 7?",
        "case_0_expected": "42",
        "case_0_rubric": "",
        "case_0_tier": "1",
        "case_1_case_id": "",
        "case_1_prompt": "",
        "case_1_expected": "",
        "case_1_rubric": "",
        "case_1_tier": "",
    }


def _step_two_fields() -> dict[str, str]:
    """Return the posted fields of a valid step 2 form."""
    return {
        "prompt_0_name": "concise",
        "prompt_0_text": "Be brief.",
        "prompt_1_name": "",
        "prompt_1_text": "",
        "context_0_name": "policy",
        "context_0_text": "Refunds within 30 days.\n\nNo cash refunds.",
        "context_1_name": "",
        "context_1_text": "",
        "credential_0_name": "",
        "credential_0_provider": "none",
    }


def _step_three_fields() -> dict[str, str]:
    """Return the posted fields of a valid step 3 form."""
    return {
        "candidate_0_name": "stub-concise",
        "candidate_0_adapter": STUB_CANDIDATE,
        "candidate_0_model": "stub-1",
        "candidate_0_credential": "none",
        "candidate_0_prompt": "concise",
        "candidate_0_context": "none",
        "candidate_1_name": "stub-policy",
        "candidate_1_adapter": STUB_CANDIDATE,
        "candidate_1_model": "stub-1",
        "candidate_1_credential": "none",
        "candidate_1_prompt": "none",
        "candidate_1_context": "policy",
        "candidate_2_name": "",
        "candidate_2_adapter": STUB_CANDIDATE,
        "candidate_2_model": "",
        "candidate_2_credential": "none",
        "candidate_2_prompt": "none",
        "candidate_2_context": "none",
        "replicas": "1",
        "concurrency": "2",
    }


def _step_four_fields() -> dict[str, str]:
    """Return the posted fields of a valid step 4 form."""
    return {
        "budget_usd": "2.00",
        "metric_0_key": "exact_match",
        "metric_0_adapter": EXACT_MATCH,
        "metric_1_key": "",
        "metric_1_adapter": EXACT_MATCH,
        "judge_0_metric": "quality",
        "judge_0_model": "",
        "judge_0_rubric": "Mentions 42\nIs brief",
        "judge_1_metric": "",
        "judge_1_model": "",
        "judge_1_rubric": "",
        "gate_0_metric": "quality",
        "gate_0_op": "at least",
        "gate_0_threshold": "1.0",
        "gate_1_metric": "exact_match",
        "gate_1_op": "at most",
        "gate_1_threshold": "1.0",
        "gate_2_metric": "",
        "gate_2_op": "at least",
        "gate_2_threshold": "",
        "price_0_model": "stub-1",
        "price_0_input": "0.15",
        "price_0_output": "0.60",
        "price_1_model": "",
        "price_1_input": "",
        "price_1_output": "",
    }


def test_the_default_draft_round_trips_through_the_hidden_field() -> None:
    """The document survives the YAML text the form carries between steps."""
    document = default_draft()
    text = draft_text(document)
    parsed, error = parse_draft(text)
    assert error == ""
    assert parsed == document
    assert "spec_version: 1" in text
    assert "candidates:" in text


def test_parse_draft_explains_every_way_the_text_can_be_wrong() -> None:
    """Empty text starts from nothing; bad YAML and a list are reported."""
    assert parse_draft("   ") == (None, "")
    document, error = parse_draft("name: [")
    assert document is None
    assert error.startswith("the draft did not parse as YAML")
    document, error = parse_draft("- one\n- two")
    assert document is None
    assert error == "the draft must be a YAML mapping at the top level"


def test_parse_action_names_the_verb_the_row_kind_and_the_index() -> None:
    """Continue, add and remove buttons carry their intent in the value."""
    assert parse_action("continue") == ("continue", "", 0)
    assert parse_action("add:case") == ("add", "case", 0)
    assert parse_action("remove:case:1") == ("remove", "case", 1)
    assert parse_action("goto:3") == ("goto", "3", 3)
    assert parse_action("") == ("continue", "", 0)


def test_step_one_merges_the_identity_and_the_cases() -> None:
    """Only the non-blank case rows survive, typed as the spec expects."""
    merged = merge_step(default_draft(), 1, _step_one_fields())
    assert merged["name"] == "math-check"
    assert merged["description"] == "Six times seven."
    assert merged["seed"] == 7
    assert merged["source"]["adapter"] == INLINE_SOURCE
    assert merged["source"]["options"]["cases"] == [
        {
            "case_id": "case-1",
            "prompt": "What is 6 times 7?",
            "expected": {"answer": "42"},
            "rubric": None,
            "tier": 1,
        }
    ]
    assert merged["spec_version"] == 1
    assert merged["provider_prices"] == {}


def test_step_two_merges_prompts_contexts_and_credentials() -> None:
    """Each context is split into one chunk per blank-line separated block."""
    merged = merge_step(default_draft(), 2, _step_two_fields())
    assert merged["prompts"] == {"concise": "Be brief."}
    assert merged["contexts"] == {
        "policy": [
            {"chunk_id": "policy-1", "text": "Refunds within 30 days."},
            {"chunk_id": "policy-2", "text": "No cash refunds."},
        ]
    }
    assert merged["credentials"] == []


def test_step_three_merges_the_candidates_and_the_scale() -> None:
    """Candidate options only carry a prompt or a context that was chosen."""
    merged = merge_step(default_draft(), 3, _step_three_fields())
    assert [candidate["name"] for candidate in merged["candidates"]] == [
        "stub-concise",
        "stub-policy",
    ]
    assert merged["candidates"][0]["adapter"] == STUB_CANDIDATE
    assert merged["candidates"][0]["model"] == DEFAULT_MODEL
    assert merged["candidates"][0]["credential"] is None
    assert merged["candidates"][0]["options"] == {"prompt": "concise"}
    assert merged["candidates"][1]["options"] == {"context": "policy"}
    assert (merged["replicas"], merged["concurrency"]) == (1, 2)


def test_step_four_merges_the_budget_metrics_judges_gates_and_prices() -> None:
    """The judge adds a judge metric, and each gate becomes a min or a max."""
    merged = merge_step(default_draft(), 4, _step_four_fields())
    assert merged["budget_usd"] == 2.0
    assert merged["metrics"] == [
        {"key": "exact_match", "adapter": EXACT_MATCH, "options": {}},
        {"key": "quality", "adapter": JUDGE_PASS, "options": {"judge": "quality-judge"}},
    ]
    assert merged["judges"] == [
        {
            "name": "quality-judge",
            "adapter": SCRIPTED_JUDGE,
            "model": None,
            "options": {"claims": ["Mentions 42", "Is brief"]},
        }
    ]
    assert merged["gates"] == [
        {"metric": "quality", "min": 1.0},
        {"metric": "exact_match", "max": 1.0},
    ]
    assert merged["prices"] == {"stub-1": {"input": 0.15, "output": 0.6}}


def test_merge_step_never_mutates_the_carried_draft() -> None:
    """The route can keep the parsed document and merge a copy of it."""
    original = default_draft()
    merge_step(original, 4, _step_four_fields())
    assert original == default_draft()


def test_remove_row_drops_list_and_mapping_rows_and_ignores_bad_indices() -> None:
    """Cases and candidates are lists; prompts are a mapping."""
    document = default_draft()
    document["source"]["options"]["cases"] = [{"case_id": "a"}, {"case_id": "b"}]
    document["prompts"] = {"first": "one", "second": "two"}
    document["candidates"] = [{"name": "a"}, {"name": "b"}, {"name": "c"}]

    remove_row(document, "case", 0)
    remove_row(document, "prompt", 1)
    remove_row(document, "candidate", 9)

    assert [case["case_id"] for case in document["source"]["options"]["cases"]] == ["b"]
    assert document["prompts"] == {"first": "one"}
    assert len(document["candidates"]) == 3


def test_the_removable_kinds_are_the_rows_each_step_owns() -> None:
    """Every kind a Remove button can name has one row path."""
    assert REMOVABLE_KINDS == (
        "case",
        "prompt",
        "context",
        "credential",
        "candidate",
        "metric",
        "judge",
        "gate",
        "price",
    )
