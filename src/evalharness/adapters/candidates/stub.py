"""Scripted stub candidates used by tests, examples and the walking skeleton.

The factories cover the four stub shapes the ticket names: scripted output
(build), slow (build_slow), always-failing transport (build_failing) and one
that refuses prompt overrides (build_fixed_prompt).
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from evalharness.core.context import RunContext
from evalharness.core.models import CandidateOutput, Case
from evalharness.core.protocols import Capabilities
from evalharness.errors import TransportError


class StubCandidate:
    """A candidate that returns scripted text with synthetic usage."""

    def __init__(
        self,
        name: str,
        *,
        answer: str = "42",
        answers: Mapping[str, str] | None = None,
        prompt_tokens: int = 12,
        completion_tokens: int = 8,
        latency_ms: int = 5,
        tool_calls: int = 0,
        citations: list[str] | None = None,
        delay_ms: int = 0,
        fail: str = "",
        usage_source: str = "provider",
        capabilities: Capabilities | None = None,
    ) -> None:
        self.name = name
        self.capabilities = capabilities or Capabilities()
        self._answer = answer
        self._answers = dict(answers or {})
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._latency_ms = latency_ms
        self._tool_calls = tool_calls
        self._citations = list(citations or [])
        self._delay_ms = delay_ms
        self._fail = fail
        self._usage_source = usage_source

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        """Return the scripted answer, or raise the configured failure."""
        if self._delay_ms:
            await asyncio.sleep(self._delay_ms / 1000)
        if self._fail == "transport":
            raise TransportError("stub transport failed")
        if self._fail == "error":
            raise RuntimeError("stub candidate failed")
        return CandidateOutput(
            answer=self._answers.get(case.case_id, self._answer),
            tool_calls=self._tool_calls,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            citations=self._citations,
            latency_ms=self._latency_ms,
            model=ctx.model or self.name,
            usage_source=self._usage_source,  # type: ignore[arg-type]
            artifacts={"received": ctx.artifact_metadata(), "attempts": 1},
        )


def _build(
    identity: str,
    options: Mapping[str, Any],
    *,
    delay_ms: int = 0,
    fail: str = "",
    capabilities: Capabilities | None = None,
) -> StubCandidate:
    return StubCandidate(
        identity,
        answer=str(options.get("answer", "42")),
        answers={
            str(key): str(value)
            for key, value in dict(options.get("answers") or {}).items()
        },
        prompt_tokens=int(options.get("prompt_tokens", 12)),
        completion_tokens=int(options.get("completion_tokens", 8)),
        latency_ms=int(options.get("latency_ms", 5)),
        tool_calls=int(options.get("tool_calls", 0)),
        citations=[
            str(citation)
            for citation in list(options.get("citations") or [])
        ],
        delay_ms=int(options.get("delay_ms", delay_ms)),
        fail=str(options.get("fail", fail)),
        usage_source=str(options.get("usage_source", "provider")),
        capabilities=capabilities,
    )


def build(identity: str, options: Mapping[str, Any]) -> StubCandidate:
    """Build a scripted stub candidate."""
    return _build(identity, options)


def build_slow(identity: str, options: Mapping[str, Any]) -> StubCandidate:
    """Build a stub that sleeps before answering, for concurrency tests."""
    return _build(identity, options, delay_ms=25)


def build_failing(identity: str, options: Mapping[str, Any]) -> StubCandidate:
    """Build a stub whose transport always fails, for retry-exhaustion tests."""
    return _build(identity, options, fail="transport")


def build_fixed_prompt(identity: str, options: Mapping[str, Any]) -> StubCandidate:
    """Build a stub that refuses prompt overrides."""
    return _build(
        identity,
        options,
        capabilities=Capabilities(supports_prompt_override=False),
    )


CONFORMANCE_HOOKS: dict[str, dict[str, Any]] = {
    "transport": {"fail": "transport"},
    "wrong": {"answer": "__conformance_wrong_answer__"},
}
"""Fault-injection options the public conformance suite applies to a stub."""

for _factory in (build, build_slow, build_failing, build_fixed_prompt):
    _factory.CONFORMANCE_HOOKS = CONFORMANCE_HOOKS
del _factory
