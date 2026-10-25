"""Judge-backed metrics: cost split, config hashes, evidence and conformance."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.adapters.judges.scripted import build as build_scripted
from evalharness.adapters.metrics.citation_support import build as build_citation
from evalharness.cli import main
from evalharness.conformance import run_suite
from evalharness.core.models import (
    Case,
    CellRecord,
    Check,
    JudgeRequest,
    JudgeSession,
    ScoreContext,
    Verdict,
)
from tests.helpers import (
    CITATION_SUPPORT,
    EXACT_MATCH,
    SCRIPTED_JUDGE,
    base_spec,
    open_repository,
    run_id_from,
    write_spec,
)

STRONG_PRICE = {"input": 2.0, "output": 8.0}
CHEAP_PRICE = {"input": 0.1, "output": 0.4}
OTHER_PRICE = {"input": 3.0, "output": 9.0}


def _judge_spec(
    *,
    model: str = "scripted-strong",
    instructions: str = "Require the claim.",
    extra_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a one-candidate spec with one judge-backed citation metric."""
    spec = base_spec()
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["options"] = {
        "answer": "The answer is 42.",
        "citations": ["6 times 7 is 42."],
    }
    spec["metrics"] = [
        {
            "key": "citation_support",
            "adapter": CITATION_SUPPORT,
            "options": {"judge": "judge-a"},
        }
    ]
    options: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "prompt_tokens": 120,
        "completion_tokens": 12,
    }
    options.update(extra_options or {})
    spec["judges"] = [
        {
            "name": "judge-a",
            "adapter": SCRIPTED_JUDGE,
            "model": model,
            "options": options,
        }
    ]
    spec["prices"] = {
        "stub-1": {"input": 0.15, "output": 0.60},
        "scripted-strong": STRONG_PRICE,
        "scripted-cheap": CHEAP_PRICE,
        "scripted-other": OTHER_PRICE,
    }
    return spec


def _run_spec(evalh_home, workdir, capsys, name: str, spec: dict[str, Any]):
    """Run one spec through the CLI and return its run id and captured output."""
    capsys.readouterr()
    path = write_spec(workdir / f"{name}.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return run_id_from(captured.out), captured


def _checks_by_metric(repository, run_id: str) -> dict[str, Check]:
    """Read every check for a run, keyed by metric, collecting verdict reasons."""
    found: dict[str, Check] = {}
    for cell in repository.list_cells(run_id):
        for check in repository.list_checks(run_id, cell.cell_id):
            found[check.metric] = check
    return found


def _single_cell(repository, run_id: str) -> CellRecord:
    """Read the one cell a one-candidate, one-case spec produced."""
    cells = repository.list_cells(run_id)
    assert len(cells) == 1
    return cells[0]


def test_judge_cost_and_tokens_are_split_from_answer_cost(evalh_home, workdir, capsys):
    spec = _judge_spec()
    spec["metrics"].append(
        {"key": "exact_match", "adapter": EXACT_MATCH, "options": {}}
    )
    run_id, captured = _run_spec(evalh_home, workdir, capsys, "split", spec)

    assert "cost: answer $" in captured.out
    assert "judge $0.000336" in captured.out
    assert "tools $0.000000" in captured.out

    database, repository = open_repository()
    try:
        cell = _single_cell(repository, run_id)
        assert cell.answer_cost_usd == pytest.approx(0.0000066)
        assert cell.judge_prompt_tokens == 120
        assert cell.judge_completion_tokens == 12
        assert cell.judge_cost_usd == pytest.approx(0.000336)
        assert cell.tool_cost_usd == 0.0
        assert cell.cost_usd == pytest.approx(
            cell.answer_cost_usd + cell.judge_cost_usd + cell.tool_cost_usd
        )
        assert cell.cost_usd == pytest.approx(0.0003426)
        run = repository.get_run(run_id)
        assert run is not None
        assert run.cost_usd == pytest.approx(cell.cost_usd)

        checks = _checks_by_metric(repository, run_id)
        citation = checks["citation_support"]
        assert citation.passed is True
        assert citation.judge_config_hash is not None
        assert len(citation.judge_config_hash) == 64
        assert int(citation.judge_config_hash, 16) >= 0
        assert citation.evidence["citations"][0]["citation"] == "6 times 7 is 42."
        assert citation.evidence["citations"][0]["passed"] is True
        assert checks["exact_match"].judge_config_hash is None
    finally:
        database.dispose()


def test_two_judges_give_two_hashes_and_two_reasons(evalh_home, workdir, capsys):
    spec = _judge_spec()
    spec["judges"] = [
        {
            "name": "strict",
            "adapter": SCRIPTED_JUDGE,
            "model": "scripted-strong",
            "options": {
                "model": "scripted-strong",
                "claims": ["42", "6 times 7"],
                "prompt_tokens": 120,
                "completion_tokens": 12,
            },
        },
        {
            "name": "cheap",
            "adapter": SCRIPTED_JUDGE,
            "model": "scripted-cheap",
            "options": {
                "model": "scripted-cheap",
                "claims": ["the moon is cheese"],
                "prompt_tokens": 40,
                "completion_tokens": 6,
            },
        },
    ]
    spec["metrics"] = [
        {
            "key": "citation_strict",
            "adapter": CITATION_SUPPORT,
            "options": {"judge": "strict"},
        },
        {
            "key": "citation_cheap",
            "adapter": CITATION_SUPPORT,
            "options": {"judge": "cheap"},
        },
    ]
    run_id, _ = _run_spec(evalh_home, workdir, capsys, "two-judges", spec)

    database, repository = open_repository()
    try:
        checks = _checks_by_metric(repository, run_id)
        strict = checks["citation_strict"]
        cheap = checks["citation_cheap"]
        assert strict.passed is True
        assert cheap.passed is False
        assert strict.judge_config_hash != cheap.judge_config_hash
        assert strict.judge_config_hash is not None
        assert cheap.judge_config_hash is not None
        strict_reason = strict.evidence["citations"][0]["reason"]
        cheap_reason = cheap.evidence["citations"][0]["reason"]
        assert "supported" in strict_reason
        assert "not supported" in cheap_reason
        assert strict_reason != cheap_reason

        cell = _single_cell(repository, run_id)
        assert cell.judge_prompt_tokens == 160
        assert cell.judge_completion_tokens == 18
        expected_judge = (120 * 2.0 + 12 * 8.0) / 1_000_000 + (
            40 * 0.1 + 6 * 0.4
        ) / 1_000_000
        assert cell.judge_cost_usd == pytest.approx(expected_judge)
        assert cell.cost_usd == pytest.approx(
            cell.answer_cost_usd + cell.judge_cost_usd + cell.tool_cost_usd
        )
    finally:
        database.dispose()


def test_judge_hash_is_stable_and_changes_with_model_and_options(
    evalh_home, workdir, capsys
):
    first_id, _ = _run_spec(evalh_home, workdir, capsys, "hash-a", _judge_spec())
    repeated_id, _ = _run_spec(
        evalh_home, workdir, capsys, "hash-a-repeat", _judge_spec()
    )
    other_model_id, _ = _run_spec(
        evalh_home, workdir, capsys, "hash-b", _judge_spec(model="scripted-other")
    )
    other_options_id, _ = _run_spec(
        evalh_home,
        workdir,
        capsys,
        "hash-c",
        _judge_spec(instructions="Different instructions."),
    )

    database, repository = open_repository()
    try:
        first = _checks_by_metric(repository, first_id)["citation_support"]
        repeated = _checks_by_metric(repository, repeated_id)["citation_support"]
        other_model = _checks_by_metric(repository, other_model_id)["citation_support"]
        other_options = _checks_by_metric(repository, other_options_id)[
            "citation_support"
        ]
    finally:
        database.dispose()

    assert first.judge_config_hash == repeated.judge_config_hash
    assert first.judge_config_hash != other_model.judge_config_hash
    assert first.judge_config_hash != other_options.judge_config_hash


def test_deterministic_run_has_no_judge_cost_or_hash(evalh_home, workdir, capsys):
    spec = base_spec()
    run_id, captured = _run_spec(evalh_home, workdir, capsys, "deterministic", spec)

    assert "judge $0.000000" in captured.out
    database, repository = open_repository()
    try:
        for cell in repository.list_cells(run_id):
            assert cell.judge_cost_usd == 0.0
            assert cell.judge_prompt_tokens == 0
            assert cell.judge_completion_tokens == 0
            assert cell.cost_usd == pytest.approx(
                cell.answer_cost_usd + cell.judge_cost_usd + cell.tool_cost_usd
            )
            for check in repository.list_checks(run_id, cell.cell_id):
                assert check.judge_config_hash is None
    finally:
        database.dispose()


def test_judge_without_a_model_costs_zero_but_records_tokens(
    evalh_home, workdir, capsys
):
    spec = _judge_spec()
    spec["judges"][0].pop("model")
    run_id, _ = _run_spec(evalh_home, workdir, capsys, "no-model", spec)

    database, repository = open_repository()
    try:
        cell = _single_cell(repository, run_id)
        assert cell.judge_prompt_tokens == 120
        assert cell.judge_completion_tokens == 12
        assert cell.judge_cost_usd == 0.0
        assert cell.cost_usd == pytest.approx(cell.answer_cost_usd + cell.tool_cost_usd)
        check = _checks_by_metric(repository, run_id)["citation_support"]
        assert check.judge_config_hash is not None
    finally:
        database.dispose()


def test_citation_support_fails_an_unsupported_citation_with_evidence(
    evalh_home, workdir, capsys
):
    spec = _judge_spec()
    spec["candidates"][0]["options"]["citations"] = ["The moon is made of cheese."]
    run_id, _ = _run_spec(evalh_home, workdir, capsys, "unsupported", spec)

    database, repository = open_repository()
    try:
        check = _checks_by_metric(repository, run_id)["citation_support"]
        assert check.passed is False
        assert "do not support" in check.reason
        assert check.judge_config_hash is not None
        citation = check.evidence["citations"][0]
        assert citation["citation"] == "The moon is made of cheese."
        assert citation["passed"] is False
        assert citation["reason"]
    finally:
        database.dispose()


def test_citation_support_without_a_judge_is_a_spec_error(evalh_home, workdir, capsys):
    spec = _judge_spec()
    spec["metrics"][0]["options"] = {}
    path = write_spec(workdir / "no-judge.yaml", spec)

    code = main(["validate", str(path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "metrics[0].adapter" in captured.err
    assert "options.judge" in captured.err
    assert "Fix:" in captured.err


def test_citation_metric_without_citations_fails_with_a_reason():
    metric = build_citation("citations", {"judge": "judge-a"})
    cell = CellRecord(
        cell_id="cell",
        run_id="run",
        candidate="candidate",
        case_id="case",
        replica=1,
        answer="42",
        output={"citations": "not-a-list"},
    )
    session = JudgeSession(
        name="judge-a",
        judge=_passing_judge(),
        model="scripted",
        config_hash="hash",
    )
    context = ScoreContext(
        run_id="run",
        candidate="candidate",
        cases={"case": Case(case_id="case", prompt="question")},
        judges={"judge-a": session},
    )

    checks = asyncio.run(metric.check(cell, context))

    assert len(checks) == 1
    assert checks[0].passed is False
    assert checks[0].reason == "the answer cites no sources"
    assert checks[0].evidence["citations"] == []


def test_scripted_judge_reads_claims_from_rubric_and_expected_data():
    judge = build_scripted("judge", {"model": "scripted"})

    rubric_request = JudgeRequest(
        case_id="case",
        prompt="question",
        answer="alpha beta",
        rubric="alpha\nbeta",
    )
    rubric_verdict = asyncio.run(judge.verdict(rubric_request))
    assert rubric_verdict.passed is True
    assert rubric_verdict.reason == "all 2 claim(s) are supported by the answer"
    assert rubric_verdict.prompt_tokens > 0
    assert rubric_verdict.completion_tokens == 3
    assert rubric_verdict.model == "scripted"
    assert rubric_verdict.usage_source == "estimated"

    claims_request = JudgeRequest(
        case_id="case",
        prompt="question",
        answer="alpha",
        expected={"claims": ["alpha", "beta"]},
    )
    claims_verdict = asyncio.run(judge.verdict(claims_request))
    assert claims_verdict.passed is False
    assert "1 of 2 claim(s)" in claims_verdict.reason

    empty_request = JudgeRequest(case_id="case", prompt="question", answer="alpha")
    empty_verdict = asyncio.run(judge.verdict(empty_request))
    assert empty_verdict.passed is False
    assert empty_verdict.reason == "no claims were available to check"
    assert empty_verdict.evidence["claims"] == []


def test_scripted_judge_honours_explicit_claims_and_usage_overrides():
    judge = build_scripted(
        "judge",
        {
            "claims": ["42"],
            "instructions": "Answer with 42.",
            "prompt_tokens": 7,
            "completion_tokens": 3,
        },
    )
    request = JudgeRequest(case_id="case", prompt="question", answer="42")
    verdict = asyncio.run(judge.verdict(request))

    assert verdict.passed is True
    assert verdict.prompt_tokens == 7
    assert verdict.completion_tokens == 3
    assert verdict.evidence["instructions"] == "Answer with 42."


def test_scripted_judge_rejects_non_list_claims():
    with pytest.raises(ValueError, match="options.claims"):
        build_scripted("judge", {"claims": "42"})


class _PassingJudge:
    """A minimal judge whose only job is to fill a session in a unit test."""

    async def verdict(self, request: JudgeRequest) -> Verdict:
        return Verdict(passed=True, reason="ok")


def _passing_judge() -> _PassingJudge:
    return _PassingJudge()


@pytest.mark.parametrize(
    ("adapter", "kind", "options"),
    [
        (SCRIPTED_JUDGE, "judge", {"model": "scripted"}),
        (CITATION_SUPPORT, "metric", {"judge": "conformance-judge"}),
    ],
)
def test_new_adapters_pass_the_public_suite(evalh_home, adapter, kind, options):
    report = run_suite(adapter, kind, options=options)

    assert report.passed, [
        (result.clause, result.detail) for result in report.results if not result.ok
    ]
