"""The public adapter contract suite: clauses for the five protocols.

A clause is a stable, numbered promise. Its identifier is
``<kind>/<number>`` (for example ``candidate/3``), and numbers are
append-only: publishing a new promise takes the next number for that
contract, and an existing promise keeps its number forever so a failure in
CI points at the same sentence it always did.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from evalharness.core.context import RunContext
from evalharness.core.events import EventEmitter
from evalharness.core.models import (
    CandidateOutput,
    Case,
    CellRecord,
    Check,
    JudgeRequest,
    JudgeSession,
    RunRecord,
    ScoreContext,
    SourceSpec,
    Verdict,
    utc_now,
)
from evalharness.core.protocols import Capabilities
from evalharness.core.registry import AdapterFactory
from evalharness.errors import HarnessError, TransportError

CANDIDATE = "candidate"
"""The candidate protocol kind."""

JUDGE = "judge"
"""The judge protocol kind."""

METRIC = "metric"
"""The metric protocol kind."""

SOURCE = "source"
"""The source protocol kind."""

SINK = "sink"
"""The sink protocol kind."""

KINDS: tuple[str, ...] = (CANDIDATE, JUDGE, METRIC, SOURCE, SINK)
"""Every protocol the public suite covers."""

OVERRIDE_SENTINEL = "CONFORMANCE-PROMPT-OVERRIDE"
"""The prompt override the suite injects when it checks override refusal."""

WRONG_ANSWER = "conformance-wrong-answer"
"""The deliberately wrong answer the suite asks a candidate to return."""

RUN_ID = "conformance-run"
CASE_ID = "conformance-case"
CANDIDATE_NAME = "conformance-candidate"
METRIC_KEY = "conformance_metric"


class ConformanceError(HarnessError):
    """A contract clause failed; the message names the clause and expectation."""

    def __init__(self, clause: str, title: str, detail: str) -> None:
        self.clause = clause
        self.title = title
        self.detail = detail
        super().__init__(f"{clause} ({title}): {detail}")

    def __str__(self) -> str:
        return f"{self.clause} ({self.title}): {self.detail}"


def clause(kind: str, number: int) -> str:
    """Return the stable public identifier for one contract clause."""
    if kind not in KINDS:
        raise ValueError(
            f"unknown conformance kind {kind!r}; use one of: {', '.join(KINDS)}"
        )
    return f"{kind}/{number}"


@dataclass(frozen=True, slots=True)
class ClauseResult:
    """The outcome of one clause: its identifier, whether it held, and why not."""

    clause: str
    ok: bool
    detail: str = ""
    title: str = ""


@dataclass(frozen=True, slots=True)
class ConformanceCase:
    """One executable clause in a protocol's public contract."""

    clause: str
    kind: str
    title: str
    check: Callable[[ConformanceContext], None]


@dataclass(frozen=True, slots=True)
class ConformanceContext:
    """What one clause may build and where it may write while it runs."""

    adapter_path: str
    kind: str
    identity: str
    options: Mapping[str, Any]
    workdir: Path
    factory: AdapterFactory

    def build(self, extra_options: Mapping[str, Any] | None = None) -> Any:
        """Build a fresh adapter instance with clause-local options merged in."""
        options = dict(self.options)
        options.update(extra_options or {})
        return self.factory(self.identity, options)

    def hook_options(self, name: str, default: Mapping[str, Any]) -> dict[str, Any]:
        """Return the factory's declared fault-injection options for one hook."""
        hooks = getattr(self.factory, "CONFORMANCE_HOOKS", {})
        return dict(hooks.get(name, default))


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    """The whole suite's outcome for one adapter: every clause, and the total."""

    adapter: str
    kind: str
    passed: bool
    results: list[ClauseResult]

    @property
    def clauses(self) -> list[ClauseResult]:
        """Alias for results; reports read better when they say clauses."""
        return self.results

    def raise_for_failure(self) -> None:
        """Raise the first failed clause as a ConformanceError."""
        for result in self.results:
            if not result.ok:
                raise ConformanceError(result.clause, result.title, result.detail)


class _ClauseFailure(Exception):
    """Internal signal that one expectation did not hold."""

    def __init__(self, expected: str, got: str) -> None:
        self.expected = expected
        self.got = got
        super().__init__(expected, got)


def _expect(condition: bool, expected: str, got: str) -> None:
    if not condition:
        raise _ClauseFailure(expected, got)


def _json_failure(value: Any) -> str | None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        return str(exc)
    return None


def _case() -> Case:
    return Case(
        case_id=CASE_ID,
        prompt="What is 6 times 7?",
        expected={"answer": "42"},
    )


def _run_context(prompt_override: str | None = None) -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        seed=0,
        credential=None,
        events=EventEmitter(RUN_ID),
        prompt_override=prompt_override,
        candidate=CANDIDATE_NAME,
        case_id=CASE_ID,
        replica=1,
        model="conformance-model",
    )


class _ConformanceJudge:
    """A deterministic in-suite judge so a judge-backed metric can be scored."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def verdict(self, request: JudgeRequest) -> Verdict:
        """Pass when the judged text contains the conformance answer."""
        passed = "42" in request.answer
        return Verdict(
            passed=passed,
            reason=(
                "conformance judge found 42"
                if passed
                else "conformance judge missed 42"
            ),
            evidence={"answer": request.answer},
        )


def _score_context(target: ConformanceContext) -> ScoreContext:
    """Build a score context with a session for every judge the metric names."""
    names = {"conformance-judge"}
    named = target.options.get("judge")
    if isinstance(named, str) and named:
        names.add(named)
    judges = {
        name: JudgeSession(
            name=name,
            judge=_ConformanceJudge(name),
            model="conformance-judge",
            config_hash=f"conformance-{name}",
        )
        for name in sorted(names)
    }
    return ScoreContext(
        run_id=RUN_ID,
        candidate=CANDIDATE_NAME,
        cases={CASE_ID: _case()},
        judges=judges,
    )


def _cell() -> CellRecord:
    return CellRecord(
        cell_id="conformance-cell",
        run_id=RUN_ID,
        candidate=CANDIDATE_NAME,
        case_id=CASE_ID,
        replica=1,
        answer="42",
        output={"citations": ["42"]},
    )


def _output(target: ConformanceContext, prompt_override: str | None = None) -> Any:
    candidate = target.build()
    return asyncio.run(candidate.run(_case(), _run_context(prompt_override)))


def _candidate_output(target: ConformanceContext) -> None:
    output = _output(target)
    _expect(
        isinstance(output, CandidateOutput),
        "a CandidateOutput instance",
        type(output).__name__,
    )


def _candidate_usage(target: ConformanceContext) -> None:
    output = _output(target)
    _expect(
        isinstance(output, CandidateOutput),
        "a CandidateOutput instance",
        type(output).__name__,
    )
    for name in ("prompt_tokens", "completion_tokens", "tool_calls", "latency_ms"):
        value = getattr(output, name)
        _expect(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0,
            f"{name} to be a non-negative integer",
            repr(value),
        )


def _candidate_capabilities(target: ConformanceContext) -> None:
    capabilities = getattr(target.build(), "capabilities", None)
    _expect(
        isinstance(capabilities, Capabilities),
        "a Capabilities instance",
        type(capabilities).__name__,
    )


def _candidate_usage_source(target: ConformanceContext) -> None:
    output = _output(target)
    _expect(
        isinstance(output, CandidateOutput),
        "a CandidateOutput instance",
        type(output).__name__,
    )
    _expect(
        output.usage_source in ("provider", "estimated"),
        "usage_source to be 'provider' or 'estimated'",
        repr(output.usage_source),
    )


def _candidate_prompt_override(target: ConformanceContext) -> None:
    candidate = target.build()
    capabilities = getattr(candidate, "capabilities", None)
    _expect(
        isinstance(capabilities, Capabilities),
        "a Capabilities instance",
        type(capabilities).__name__,
    )
    if capabilities.supports_prompt_override:
        return
    plain = asyncio.run(candidate.run(_case(), _run_context()))
    overridden = asyncio.run(
        candidate.run(_case(), _run_context(OVERRIDE_SENTINEL))
    )
    _expect(
        overridden == plain,
        "the prompt override to be ignored",
        "the output changed when a prompt override was supplied",
    )


def _candidate_transport_error(target: ConformanceContext) -> None:
    options = target.hook_options("transport", {"fail": "transport"})
    candidate = target.build(options)
    try:
        asyncio.run(candidate.run(_case(), _run_context()))
    except TransportError:
        return
    except Exception as exc:
        _expect(
            False,
            "TransportError",
            f"{type(exc).__name__}: {exc}",
        )
    _expect(False, "TransportError", "no exception was raised")


def _candidate_wrong_answer(target: ConformanceContext) -> None:
    options = target.hook_options("wrong", {"answer": WRONG_ANSWER})
    candidate = target.build(options)
    try:
        output = asyncio.run(candidate.run(_case(), _run_context()))
    except TransportError as exc:
        _expect(
            False,
            "a wrong answer to be returned as output, not raised",
            f"TransportError: {exc}",
        )
    except Exception as exc:
        _expect(
            False,
            "a wrong answer to be returned as output, not raised",
            f"{type(exc).__name__}: {exc}",
        )
    _expect(
        isinstance(output, CandidateOutput),
        "a CandidateOutput instance",
        type(output).__name__,
    )


def _judge_request() -> JudgeRequest:
    return JudgeRequest(
        case_id=CASE_ID,
        prompt="What is 6 times 7?",
        answer="42",
        expected={"answer": "42"},
    )


def _verdict(target: ConformanceContext) -> Any:
    return asyncio.run(target.build().verdict(_judge_request()))


def _judge_verdict(target: ConformanceContext) -> None:
    verdict = _verdict(target)
    _expect(
        isinstance(verdict, Verdict),
        "a Verdict instance",
        type(verdict).__name__,
    )


def _judge_passed(target: ConformanceContext) -> None:
    verdict = _verdict(target)
    _expect(
        isinstance(verdict, Verdict),
        "a Verdict instance",
        type(verdict).__name__,
    )
    _expect(
        isinstance(verdict.passed, bool),
        "passed to be a bool",
        repr(verdict.passed),
    )


def _judge_reason(target: ConformanceContext) -> None:
    verdict = _verdict(target)
    _expect(
        isinstance(verdict, Verdict),
        "a Verdict instance",
        type(verdict).__name__,
    )
    _expect(
        isinstance(verdict.reason, str) and bool(verdict.reason.strip()),
        "a non-empty reason string",
        repr(verdict.reason),
    )


def _judge_evidence(target: ConformanceContext) -> None:
    verdict = _verdict(target)
    _expect(
        isinstance(verdict, Verdict),
        "a Verdict instance",
        type(verdict).__name__,
    )
    failure = _json_failure(verdict.evidence)
    _expect(
        isinstance(verdict.evidence, dict) and failure is None,
        "evidence to be a JSON-serialisable mapping",
        failure or type(verdict.evidence).__name__,
    )


def _judge_determinism(target: ConformanceContext) -> None:
    judge = target.build()
    first = asyncio.run(judge.verdict(_judge_request()))
    second = asyncio.run(judge.verdict(_judge_request()))
    _expect(
        first == second,
        "the same verdict for the same request",
        f"{first!r} then {second!r}",
    )


def _checks(target: ConformanceContext) -> Any:
    return asyncio.run(target.build().check(_cell(), _score_context(target)))


def _metric_checks(target: ConformanceContext) -> None:
    checks = _checks(target)
    _expect(
        isinstance(checks, list),
        "a list of Check instances",
        type(checks).__name__,
    )
    for check in checks:
        _expect(
            isinstance(check, Check),
            "a list of Check instances",
            type(check).__name__,
        )


def _metric_not_empty(target: ConformanceContext) -> None:
    checks = _checks(target)
    _expect(
        isinstance(checks, list),
        "a list of Check instances",
        type(checks).__name__,
    )
    _expect(len(checks) > 0, "at least one Check", "no checks were returned")


def _metric_key(target: ConformanceContext) -> None:
    checks = _checks(target)
    _expect(
        isinstance(checks, list),
        "a list of Check instances",
        type(checks).__name__,
    )
    for check in checks:
        _expect(
            isinstance(check, Check),
            "a list of Check instances",
            type(check).__name__,
        )
        _expect(
            check.metric == target.identity,
            f"metric {target.identity!r} on every check",
            repr(check.metric),
        )


def _metric_evidence(target: ConformanceContext) -> None:
    checks = _checks(target)
    _expect(
        isinstance(checks, list),
        "a list of Check instances",
        type(checks).__name__,
    )
    for check in checks:
        _expect(
            isinstance(check, Check),
            "a list of Check instances",
            type(check).__name__,
        )
        failure = _json_failure(check.evidence)
        _expect(
            isinstance(check.evidence, dict) and failure is None,
            "evidence to be a JSON-serialisable mapping",
            failure or type(check.evidence).__name__,
        )


def _metric_immutable(target: ConformanceContext) -> None:
    cell = _cell()
    before = cell.model_dump(mode="json")
    asyncio.run(target.build().check(cell, _score_context(target)))
    _expect(
        cell.model_dump(mode="json") == before,
        "the metric to leave the cell unchanged",
        "the cell changed during check",
    )


def _source_cases(target: ConformanceContext) -> list[Any]:
    source = target.build()
    spec = SourceSpec(adapter=target.adapter_path, options=dict(target.options))
    return list(source.load(spec, _run_context()))


def _source_not_empty(target: ConformanceContext) -> None:
    cases = _source_cases(target)
    _expect(len(cases) > 0, "at least one case", "the source returned no cases")


def _source_cases_are_cases(target: ConformanceContext) -> None:
    cases = _source_cases(target)
    for case in cases:
        _expect(
            isinstance(case, Case),
            "Case instances",
            type(case).__name__,
        )


def _source_unique_ids(target: ConformanceContext) -> None:
    cases = _source_cases(target)
    case_ids = [case.case_id for case in cases]
    _expect(
        len(case_ids) == len(set(case_ids)),
        "unique case ids",
        repr(case_ids),
    )


def _sink_root(target: ConformanceContext) -> Path:
    return target.workdir / "artifacts"


def _sink_roundtrip(target: ConformanceContext) -> Path:
    root = _sink_root(target)
    sink = target.build({"artifacts_dir": str(root)})
    run = RunRecord(run_id=RUN_ID, started_at=utc_now())
    cell = CellRecord(
        cell_id="conformance-cell",
        run_id=RUN_ID,
        candidate=CANDIDATE_NAME,
        case_id=CASE_ID,
        replica=1,
        answer="42",
    )
    sink.open_run(run)
    sink.record_cell(cell, [Check(metric=METRIC_KEY, passed=True)])
    sink.close_run(run)
    return root


def _sink_artifact_exists(target: ConformanceContext) -> None:
    root = _sink_roundtrip(target)
    written = [path for path in root.rglob("*") if path.is_file()]
    _expect(
        bool(written),
        "at least one artifact file under the artifacts root",
        "no artifact was written",
    )


def _sink_stays_inside(target: ConformanceContext) -> None:
    root = _sink_roundtrip(target)
    outside = [
        str(path)
        for path in target.workdir.rglob("*")
        if path.is_file() and not path.resolve().is_relative_to(root.resolve())
    ]
    _expect(
        not outside,
        "every artifact path to stay inside the artifacts root",
        f"wrote outside: {outside}",
    )


CANDIDATE_CASES: tuple[ConformanceCase, ...] = (
    ConformanceCase(
        clause(CANDIDATE, 1),
        CANDIDATE,
        "run returns a CandidateOutput",
        _candidate_output,
    ),
    ConformanceCase(
        clause(CANDIDATE, 2),
        CANDIDATE,
        "usage fields are non-negative integers",
        _candidate_usage,
    ),
    ConformanceCase(
        clause(CANDIDATE, 3),
        CANDIDATE,
        "capabilities declared",
        _candidate_capabilities,
    ),
    ConformanceCase(
        clause(CANDIDATE, 4),
        CANDIDATE,
        "usage source is provider or estimated",
        _candidate_usage_source,
    ),
    ConformanceCase(
        clause(CANDIDATE, 5),
        CANDIDATE,
        "prompt override is ignored when unsupported",
        _candidate_prompt_override,
    ),
    ConformanceCase(
        clause(CANDIDATE, 6),
        CANDIDATE,
        "a transport failure raises TransportError",
        _candidate_transport_error,
    ),
    ConformanceCase(
        clause(CANDIDATE, 7),
        CANDIDATE,
        "a wrong answer is an output, not an error",
        _candidate_wrong_answer,
    ),
)

JUDGE_CASES: tuple[ConformanceCase, ...] = (
    ConformanceCase(
        clause(JUDGE, 1),
        JUDGE,
        "verdict is a Verdict",
        _judge_verdict,
    ),
    ConformanceCase(
        clause(JUDGE, 2),
        JUDGE,
        "passed is a bool",
        _judge_passed,
    ),
    ConformanceCase(
        clause(JUDGE, 3),
        JUDGE,
        "reason is non-empty",
        _judge_reason,
    ),
    ConformanceCase(
        clause(JUDGE, 4),
        JUDGE,
        "evidence is JSON-serialisable",
        _judge_evidence,
    ),
    ConformanceCase(
        clause(JUDGE, 5),
        JUDGE,
        "the same request gives the same verdict",
        _judge_determinism,
    ),
)

METRIC_CASES: tuple[ConformanceCase, ...] = (
    ConformanceCase(
        clause(METRIC, 1),
        METRIC,
        "check returns Check instances",
        _metric_checks,
    ),
    ConformanceCase(
        clause(METRIC, 2),
        METRIC,
        "check returns at least one check",
        _metric_not_empty,
    ),
    ConformanceCase(
        clause(METRIC, 3),
        METRIC,
        "every check names this metric key",
        _metric_key,
    ),
    ConformanceCase(
        clause(METRIC, 4),
        METRIC,
        "evidence is JSON-serialisable",
        _metric_evidence,
    ),
    ConformanceCase(
        clause(METRIC, 5),
        METRIC,
        "check does not mutate the cell",
        _metric_immutable,
    ),
)

SOURCE_CASES: tuple[ConformanceCase, ...] = (
    ConformanceCase(
        clause(SOURCE, 1),
        SOURCE,
        "load returns at least one case",
        _source_not_empty,
    ),
    ConformanceCase(
        clause(SOURCE, 2),
        SOURCE,
        "load returns Case instances",
        _source_cases_are_cases,
    ),
    ConformanceCase(
        clause(SOURCE, 3),
        SOURCE,
        "case ids are unique",
        _source_unique_ids,
    ),
)

SINK_CASES: tuple[ConformanceCase, ...] = (
    ConformanceCase(
        clause(SINK, 1),
        SINK,
        "the run leaves an artifact on disk",
        _sink_artifact_exists,
    ),
    ConformanceCase(
        clause(SINK, 2),
        SINK,
        "every artifact stays inside the artifacts root",
        _sink_stays_inside,
    ),
)

CASES: dict[str, tuple[ConformanceCase, ...]] = {
    CANDIDATE: CANDIDATE_CASES,
    JUDGE: JUDGE_CASES,
    METRIC: METRIC_CASES,
    SOURCE: SOURCE_CASES,
    SINK: SINK_CASES,
}
"""The complete public clause list, keyed by protocol kind."""


def cases_for(kind: str) -> tuple[ConformanceCase, ...]:
    """Return the clauses for one protocol kind."""
    try:
        return CASES[kind]
    except KeyError as exc:
        raise ValueError(
            f"unknown conformance kind {kind!r}; use one of: {', '.join(KINDS)}"
        ) from exc


def run_case(case: ConformanceCase, context: ConformanceContext) -> ClauseResult:
    """Execute one clause and turn its outcome into a result."""
    try:
        case.check(context)
    except _ClauseFailure as failure:
        error = ConformanceError(
            case.clause,
            case.title,
            f"expected {failure.expected}, got {failure.got}",
        )
        return ClauseResult(case.clause, False, error.detail, case.title)
    except Exception as exc:  # an adapter crash is a clause failure, not a traceback
        error = ConformanceError(
            case.clause,
            case.title,
            f"expected the clause to run, got {type(exc).__name__}: {exc}",
        )
        return ClauseResult(case.clause, False, error.detail, case.title)
    return ClauseResult(case.clause, True, "", case.title)
