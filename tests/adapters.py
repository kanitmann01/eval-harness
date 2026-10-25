"""Test-only adapters registered through the production registry."""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.core.context import RunContext
from evalharness.core.models import (
    CandidateOutput,
    Case,
    Check,
    JudgeRequest,
    Verdict,
)
from evalharness.core.protocols import Capabilities
from evalharness.core.registry import register
from evalharness.errors import TransportError


class FlakyCandidate:
    """Fail the first transport attempt, then answer correctly."""

    capabilities = Capabilities()

    def __init__(self, name: str) -> None:
        self.name = name
        self.attempts = 0

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        self.attempts += 1
        if self.attempts == 1:
            raise TransportError("flaky transport")
        return CandidateOutput(
            answer="42",
            prompt_tokens=10,
            completion_tokens=5,
            model=ctx.model,
            usage_source="provider",
            artifacts={"attempts": self.attempts},
        )


class WrongCandidate:
    """Return a wrong answer once, proving a wrong answer is not retried."""

    capabilities = Capabilities()

    def __init__(self, name: str) -> None:
        self.name = name
        self.attempts = 0

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        self.attempts += 1
        return CandidateOutput(
            answer="wrong",
            prompt_tokens=10,
            completion_tokens=5,
            model=ctx.model,
            usage_source="provider",
            artifacts={"attempts": self.attempts},
        )


class ExplodingCandidate:
    """Raise a non-transport error; the executor must record, not retry."""

    capabilities = Capabilities()

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        raise ValueError("candidate exploded")


class NoCapabilitiesCandidate:
    """Return an object without the Candidate capabilities declaration."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        return CandidateOutput(answer="42")


class NoContextCandidate:
    """Refuse context injection."""

    capabilities = Capabilities(supports_context_injection=False)

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        return CandidateOutput(answer="42", model=ctx.model)


class ReplicaCandidate:
    """Answer correctly on even replicas and wrongly on odd ones.

    Mixed per-replica outcomes are what separate pass@k from pass^k.
    """

    capabilities = Capabilities()

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        return CandidateOutput(
            answer="42" if ctx.replica % 2 == 0 else "wrong",
            prompt_tokens=10,
            completion_tokens=5,
            model=ctx.model,
            usage_source="provider",
        )


class StubJudge:
    """A judge that always passes, used to exercise judge validation."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def verdict(self, request: JudgeRequest) -> Verdict:
        return Verdict(passed=True, reason="stub judge")


class WrongKeyMetric:
    """A metric whose key does not match the spec key."""

    def __init__(self, name: str) -> None:
        self.key = "not-" + name

    def check(self, cell, ctx) -> list[Check]:
        return []


class FailingSource:
    """A source that raises while loading."""

    def load(self, spec, ctx):
        raise RuntimeError("source exploded")


class EmptySource:
    """A source that returns no cases."""

    def load(self, spec, ctx):
        return []


def _flaky(identity: str, options: Mapping[str, Any]) -> FlakyCandidate:
    return FlakyCandidate(identity)


def _wrong(identity: str, options: Mapping[str, Any]) -> WrongCandidate:
    return WrongCandidate(identity)


def _exploding(identity: str, options: Mapping[str, Any]) -> ExplodingCandidate:
    return ExplodingCandidate(identity)


def _no_context(identity: str, options: Mapping[str, Any]) -> NoContextCandidate:
    return NoContextCandidate(identity)


def _no_capabilities(
    identity: str, options: Mapping[str, Any]
) -> NoCapabilitiesCandidate:
    return NoCapabilitiesCandidate(identity)


def _replica_mix(identity: str, options: Mapping[str, Any]) -> ReplicaCandidate:
    return ReplicaCandidate(identity)


def _judge(identity: str, options: Mapping[str, Any]) -> StubJudge:
    return StubJudge(identity)


def _wrong_key(identity: str, options: Mapping[str, Any]) -> WrongKeyMetric:
    return WrongKeyMetric(identity)


def _failing_source(identity: str, options: Mapping[str, Any]) -> FailingSource:
    return FailingSource()


def _empty_source(identity: str, options: Mapping[str, Any]) -> EmptySource:
    return EmptySource()


def _boom(identity: str, options: Mapping[str, Any]) -> Any:
    raise RuntimeError("factory exploded")


register("test:flaky", _flaky)
register("test:wrong", _wrong)
register("test:exploding", _exploding)
register("test:no_context", _no_context)
register("test:no_capabilities", _no_capabilities)
register("test:replica_mix", _replica_mix)
register("test:judge", _judge)
register("test:wrong_key_metric", _wrong_key)
register("test:failing_source", _failing_source)
register("test:empty_source", _empty_source)
register("test:boom_candidate", _boom)
register("test:boom_sink", _boom)
