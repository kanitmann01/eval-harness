"""Every clause fails on a purpose-broken adapter, and passes on the stubs.

The broken adapters live in this module on purpose: they are external
behaviour, resolved through the production registry, and never monkeypatch
the suite or the runner.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

import pytest

from evalharness.adapters.judges.rule_based import build as build_rule_judge
from evalharness.conformance import CASES, KINDS, run_suite
from evalharness.conformance.suite import ConformanceError, cases_for, clause
from evalharness.core.context import RunContext
from evalharness.core.models import (
    CandidateOutput,
    Case,
    CellRecord,
    Check,
    JudgeRequest,
    Verdict,
)
from evalharness.core.protocols import Capabilities
from evalharness.errors import TransportError

WRONG = "conformance-wrong-answer"
STUB = "evalharness.adapters.candidates.stub:build"


class _Candidate:
    """Base broken candidate; honours both conformance fault hooks."""

    capabilities = Capabilities()

    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        self.name = name
        self.options = dict(options)

    def output(self, case: Case, ctx: RunContext) -> Any:
        return CandidateOutput(
            answer=str(self.options.get("answer", "42")),
            prompt_tokens=1,
            completion_tokens=1,
            tool_calls=0,
            latency_ms=1,
            model=ctx.model,
            usage_source="provider",
        )

    async def run(self, case: Case, ctx: RunContext) -> Any:
        if self.options.get("fail") == "transport":
            raise TransportError("conformance transport failure")
        return self.output(case, ctx)


class DictCandidate(_Candidate):
    def output(self, case: Case, ctx: RunContext) -> Any:
        return {"answer": "42"}


class NegativeUsageCandidate(_Candidate):
    def output(self, case: Case, ctx: RunContext) -> Any:
        return CandidateOutput(answer="42", prompt_tokens=-1)


class DictCapabilitiesCandidate(_Candidate):
    capabilities = {"supports_prompt_override": True}


class BadUsageSourceCandidate(_Candidate):
    def output(self, case: Case, ctx: RunContext) -> Any:
        return CandidateOutput.model_construct(
            answer="42",
            prompt_tokens=1,
            completion_tokens=1,
            tool_calls=0,
            latency_ms=1,
            model=ctx.model,
            usage_source="guessed",
            artifacts={},
        )


class OverrideAcceptingCandidate(_Candidate):
    capabilities = Capabilities(supports_prompt_override=False)

    def output(self, case: Case, ctx: RunContext) -> Any:
        answer = "overridden" if ctx.prompt_override else "42"
        return CandidateOutput(answer=answer, prompt_tokens=1, completion_tokens=1)


class WrongErrorTypeCandidate(_Candidate):
    async def run(self, case: Case, ctx: RunContext) -> Any:
        if self.options.get("fail") == "transport":
            raise RuntimeError("not a transport error")
        return self.output(case, ctx)


class SilentTransportCandidate(_Candidate):
    async def run(self, case: Case, ctx: RunContext) -> Any:
        return self.output(case, ctx)


class WrongRaisesCandidate(_Candidate):
    async def run(self, case: Case, ctx: RunContext) -> Any:
        if self.options.get("answer") == WRONG:
            raise TransportError("a wrong answer was raised")
        return await super().run(case, ctx)


class WrongRaisesOtherCandidate(_Candidate):
    async def run(self, case: Case, ctx: RunContext) -> Any:
        if self.options.get("answer") == WRONG:
            raise RuntimeError("a wrong answer crashed")
        return await super().run(case, ctx)


class _Judge:
    """Base broken judge."""

    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        self.name = name
        self.options = dict(options)

    async def verdict(self, request: JudgeRequest) -> Any:
        return Verdict(passed=True, reason="ok", evidence={"answer": request.answer})


class DictVerdictJudge(_Judge):
    async def verdict(self, request: JudgeRequest) -> Any:
        return {"passed": True}


class IntPassedJudge(_Judge):
    async def verdict(self, request: JudgeRequest) -> Any:
        return Verdict.model_construct(passed=1, reason="ok", evidence={})


class EmptyReasonJudge(_Judge):
    async def verdict(self, request: JudgeRequest) -> Any:
        return Verdict(passed=True, reason="   ", evidence={})


class BadEvidenceJudge(_Judge):
    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        super().__init__(name, options)
        self._marker = object()

    async def verdict(self, request: JudgeRequest) -> Any:
        return Verdict.model_construct(
            passed=True,
            reason="ok",
            evidence={"marker": self._marker},
        )


class TogglingJudge(_Judge):
    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        super().__init__(name, options)
        self._calls = 0

    async def verdict(self, request: JudgeRequest) -> Any:
        self._calls += 1
        return Verdict(passed=self._calls % 2 == 1, reason="ok", evidence={})


class RaisingJudge(_Judge):
    async def verdict(self, request: JudgeRequest) -> Any:
        raise RuntimeError("judge exploded")


class _Metric:
    """Base broken metric."""

    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        self.key = name
        self.options = dict(options)

    def check(self, cell: CellRecord, ctx: Any) -> Any:
        return [
            Check(
                metric=self.key,
                passed=cell.answer == "42",
                evidence={"answer": cell.answer},
            )
        ]


class DictChecksMetric(_Metric):
    def check(self, cell: CellRecord, ctx: Any) -> Any:
        return {"metric": self.key}


class EmptyChecksMetric(_Metric):
    def check(self, cell: CellRecord, ctx: Any) -> Any:
        return []


class WrongKeyMetric(_Metric):
    def check(self, cell: CellRecord, ctx: Any) -> Any:
        return [Check(metric="not-this-key", passed=True)]


class BadEvidenceMetric(_Metric):
    def check(self, cell: CellRecord, ctx: Any) -> Any:
        return [
            Check.model_construct(
                metric=self.key,
                passed=True,
                evidence={"marker": object()},
            )
        ]


class MutatingMetric(_Metric):
    def check(self, cell: CellRecord, ctx: Any) -> Any:
        cell.answer = "mutated"
        return [Check(metric=self.key, passed=False)]


class _Source:
    """Base broken source."""

    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        self.name = name
        self.options = dict(options)

    def load(self, spec: Any, ctx: RunContext) -> Any:
        return [
            Case(case_id="case-a", prompt="first"),
            Case(case_id="case-b", prompt="second"),
        ]


class EmptySource(_Source):
    def load(self, spec: Any, ctx: RunContext) -> Any:
        return []


class DictSource(_Source):
    def load(self, spec: Any, ctx: RunContext) -> Any:
        return [{"case_id": "case-a"}]


class DuplicateSource(_Source):
    def load(self, spec: Any, ctx: RunContext) -> Any:
        return [
            Case(case_id="same", prompt="first"),
            Case(case_id="same", prompt="second"),
        ]


class _Sink:
    """Base broken sink that writes inside its artifacts root."""

    def __init__(self, name: str, options: Mapping[str, Any]) -> None:
        self.root = Path(str(options.get("artifacts_dir", ".")))

    def open_run(self, run: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def record_cell(self, cell: CellRecord, checks: list[Check]) -> None:
        (self.root / "cell.json").write_text("{}", encoding="utf-8")

    def close_run(self, run: Any) -> None:
        (self.root / "run.json").write_text("{}", encoding="utf-8")


class EmptySink(_Sink):
    def open_run(self, run: Any) -> None:
        return None

    def record_cell(self, cell: CellRecord, checks: list[Check]) -> None:
        return None

    def close_run(self, run: Any) -> None:
        return None


class EscapeSink(_Sink):
    def record_cell(self, cell: CellRecord, checks: list[Check]) -> None:
        super().record_cell(cell, checks)
        (self.root.parent / "escape.txt").write_text("{}", encoding="utf-8")


def _factory(cls: type) -> Any:
    def build(identity: str, options: Mapping[str, Any]) -> Any:
        return cls(identity, options)

    return build


build_dict_candidate = _factory(DictCandidate)
build_negative_usage_candidate = _factory(NegativeUsageCandidate)
build_dict_capabilities_candidate = _factory(DictCapabilitiesCandidate)
build_bad_usage_source_candidate = _factory(BadUsageSourceCandidate)
build_override_accepting_candidate = _factory(OverrideAcceptingCandidate)
build_wrong_error_type_candidate = _factory(WrongErrorTypeCandidate)
build_silent_transport_candidate = _factory(SilentTransportCandidate)
build_wrong_raises_candidate = _factory(WrongRaisesCandidate)
build_wrong_raises_other_candidate = _factory(WrongRaisesOtherCandidate)
build_dict_verdict_judge = _factory(DictVerdictJudge)
build_int_passed_judge = _factory(IntPassedJudge)
build_empty_reason_judge = _factory(EmptyReasonJudge)
build_bad_evidence_judge = _factory(BadEvidenceJudge)
build_toggling_judge = _factory(TogglingJudge)
build_raising_judge = _factory(RaisingJudge)
build_dict_checks_metric = _factory(DictChecksMetric)
build_empty_checks_metric = _factory(EmptyChecksMetric)
build_wrong_key_metric = _factory(WrongKeyMetric)
build_bad_evidence_metric = _factory(BadEvidenceMetric)
build_mutating_metric = _factory(MutatingMetric)
build_empty_source = _factory(EmptySource)
build_dict_source = _factory(DictSource)
build_duplicate_source = _factory(DuplicateSource)
build_empty_sink = _factory(EmptySink)
build_escape_sink = _factory(EscapeSink)


BROKEN_CASES = [
    ("build_dict_candidate", "candidate", "candidate/1"),
    ("build_negative_usage_candidate", "candidate", "candidate/2"),
    ("build_dict_capabilities_candidate", "candidate", "candidate/3"),
    ("build_bad_usage_source_candidate", "candidate", "candidate/4"),
    ("build_override_accepting_candidate", "candidate", "candidate/5"),
    ("build_wrong_error_type_candidate", "candidate", "candidate/6"),
    ("build_silent_transport_candidate", "candidate", "candidate/6"),
    ("build_wrong_raises_candidate", "candidate", "candidate/7"),
    ("build_wrong_raises_other_candidate", "candidate", "candidate/7"),
    ("build_dict_verdict_judge", "judge", "judge/1"),
    ("build_raising_judge", "judge", "judge/1"),
    ("build_int_passed_judge", "judge", "judge/2"),
    ("build_empty_reason_judge", "judge", "judge/3"),
    ("build_bad_evidence_judge", "judge", "judge/4"),
    ("build_toggling_judge", "judge", "judge/5"),
    ("build_dict_checks_metric", "metric", "metric/1"),
    ("build_empty_checks_metric", "metric", "metric/2"),
    ("build_wrong_key_metric", "metric", "metric/3"),
    ("build_bad_evidence_metric", "metric", "metric/4"),
    ("build_mutating_metric", "metric", "metric/5"),
    ("build_empty_source", "source", "source/1"),
    ("build_dict_source", "source", "source/2"),
    ("build_duplicate_source", "source", "source/3"),
    ("build_empty_sink", "sink", "sink/1"),
    ("build_escape_sink", "sink", "sink/2"),
]


@pytest.mark.parametrize(("factory", "kind", "expected"), BROKEN_CASES)
def test_clause_fails_on_a_broken_adapter(evalh_home, factory, kind, expected):
    report = run_suite(f"tests.test_conformance:{factory}", kind)

    assert report.passed is False
    results = {result.clause: result for result in report.results}
    assert expected in results
    assert results[expected].ok is False
    assert results[expected].detail.startswith("expected ")


def test_every_kind_has_a_public_clause_list():
    assert set(CASES) == set(KINDS)
    assert cases_for("candidate") == CASES["candidate"]


def test_unexpected_adapter_crash_is_reported_as_the_clause(evalh_home):
    report = run_suite("tests.test_conformance:build_raising_judge", "judge")

    result = next(item for item in report.results if item.clause == "judge/1")
    assert result.ok is False
    assert "RuntimeError" in result.detail


def test_stub_candidate_passes_every_candidate_clause(evalh_home):
    report = run_suite(STUB, "candidate")

    assert report.passed is True
    assert [result.clause for result in report.results] == [
        f"candidate/{number}" for number in range(1, 8)
    ]
    assert report.clauses == report.results
    report.raise_for_failure()


def test_report_raises_an_error_that_names_the_clause(evalh_home):
    report = run_suite(
        "tests.test_conformance:build_dict_capabilities_candidate",
        "candidate",
    )

    with pytest.raises(ConformanceError) as raised:
        report.raise_for_failure()
    assert "candidate/3" in str(raised.value)
    assert "capabilities declared" in str(raised.value)
    assert "expected a Capabilities instance" in str(raised.value)


def test_options_override_kind_defaults(evalh_home, workdir):
    report = run_suite(
        STUB,
        "candidate",
        options={"prompt_tokens": 3},
        workdir=workdir,
    )

    assert report.passed is True


def test_clause_helper_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown conformance kind"):
        clause("bogus", 1)


def test_run_suite_rejects_unknown_kind(evalh_home):
    with pytest.raises(ValueError, match="unknown conformance kind"):
        run_suite(STUB, "bogus")


def test_rule_based_judge_is_deterministic():
    judge = build_rule_judge("judge", {})
    request = JudgeRequest(
        case_id="case",
        prompt="What is 6 times 7?",
        answer="The answer is 42",
        expected={"answer": "42"},
    )

    first = asyncio.run(judge.verdict(request))
    second = asyncio.run(judge.verdict(request))
    assert first.passed is True
    assert first == second
    assert first.evidence["rule"] == "expected-substring"

    wrong = request.model_copy(update={"answer": "nope"})
    assert asyncio.run(judge.verdict(wrong)).passed is False
