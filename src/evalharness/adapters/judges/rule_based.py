"""Deterministic rule-based judge: expected answer as a substring.

This is the judge the stub pack and the conformance suite use. It has no
model, no credential and no network, so a verdict is reproducible and
cheap; a real judge adapter can be swapped in without touching the scorer.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.core.models import JudgeRequest, Verdict
from evalharness.core.protocols import Capabilities


class RuleBasedJudge:
    """Pass when the expected answer appears in the candidate's answer."""

    capabilities = Capabilities()

    def __init__(self, name: str, expected_key: str = "answer") -> None:
        self.name = name
        self._expected_key = expected_key

    async def verdict(self, request: JudgeRequest) -> Verdict:
        """Compare the answer with the expected value, case-insensitively."""
        expected = str((request.expected or {}).get(self._expected_key, "")).strip()
        answer = request.answer.strip()
        passed = bool(expected) and expected.casefold() in answer.casefold()
        return Verdict(
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=(
                f"answer contains {expected!r}"
                if passed
                else f"answer does not contain {expected!r}"
            ),
            evidence={
                "rule": "expected-substring",
                "expected": expected,
                "answer": answer,
            },
        )


def build(identity: str, options: Mapping[str, Any]) -> RuleBasedJudge:
    """Build a deterministic judge named by the spec's judge name."""
    return RuleBasedJudge(
        identity,
        expected_key=str(options.get("expected_key", "answer")),
    )
