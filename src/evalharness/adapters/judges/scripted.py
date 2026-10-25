"""Deterministic rubric/claim judge with explicit, estimated usage.

The judge reads its claims from its own options first, then the case rubric,
then the case's expected data. Every claim must appear in the answer for a
pass. Tokens are counted from the text it read and the claims it wrote, or
taken verbatim from options, so a run can price the judge from the spec's
price table without any network or model call.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.core.models import JudgeRequest, Verdict
from evalharness.core.protocols import Capabilities

DEFAULT_MODEL = "scripted-judge"
"""The model a scripted judge reports when options.model is not set."""


class ScriptedJudge:
    """Judge an answer against a deterministic set of claims."""

    capabilities = Capabilities()

    def __init__(
        self,
        name: str,
        *,
        model: str = DEFAULT_MODEL,
        claims: list[str] | None = None,
        instructions: str = "",
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        self.name = name
        self._model = model
        self._claims = list(claims or [])
        self._instructions = instructions
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    async def verdict(self, request: JudgeRequest) -> Verdict:
        """Check every claim against the answer and return the evidence."""
        claims = self._claims_for(request)
        checked = [
            {
                "claim": claim,
                "supported": claim.casefold() in request.answer.casefold(),
            }
            for claim in claims
        ]
        passed = bool(checked) and all(item["supported"] for item in checked)
        return Verdict(
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=_reason(checked),
            evidence={
                "rule": "claim-support",
                "claims": checked,
                "instructions": self._instructions,
                "answer": request.answer,
            },
            prompt_tokens=self._prompt_usage(request),
            completion_tokens=self._completion_usage(checked),
            model=self._model,
            usage_source="estimated",
        )

    def _claims_for(self, request: JudgeRequest) -> list[str]:
        """Return this judge's claims, falling back to the rubric and expected."""
        if self._claims:
            return self._claims
        if request.rubric:
            rubric_claims = [
                line.strip() for line in request.rubric.splitlines() if line.strip()
            ]
            if rubric_claims:
                return rubric_claims
        expected = request.expected or {}
        expected_claims = expected.get("claims")
        if isinstance(expected_claims, list):
            return [str(claim) for claim in expected_claims]
        expected_answer = expected.get("answer")
        if expected_answer is not None and str(expected_answer).strip():
            return [str(expected_answer).strip()]
        return []

    def _prompt_usage(self, request: JudgeRequest) -> int:
        """Count the words the judge read, or use the declared override."""
        if self._prompt_tokens is not None:
            return self._prompt_tokens
        text = " ".join(
            [
                request.prompt,
                self._instructions,
                request.rubric or "",
                request.answer,
            ]
        )
        return max(1, len(text.split()))

    def _completion_usage(self, checked: list[dict[str, Any]]) -> int:
        """Count the words in the verdict, or use the declared override."""
        if self._completion_tokens is not None:
            return self._completion_tokens
        return max(1, len(checked) + 1)


def _reason(checked: list[dict[str, Any]]) -> str:
    """Explain a pass, a partial fail, or an empty claim set."""
    if not checked:
        return "no claims were available to check"
    failed = sum(1 for item in checked if not item["supported"])
    if failed:
        return f"{failed} of {len(checked)} claim(s) are not supported by the answer"
    return f"all {len(checked)} claim(s) are supported by the answer"


def _optional_int(value: Any) -> int | None:
    """Parse an optional token override, keeping None distinct from zero."""
    return None if value is None else int(value)


def build(identity: str, options: Mapping[str, Any]) -> ScriptedJudge:
    """Build a deterministic claim judge from the spec's judge options."""
    claims_option = options.get("claims") or []
    if not isinstance(claims_option, list):
        raise ValueError("options.claims must be a list of claim strings")
    return ScriptedJudge(
        identity,
        model=str(options.get("model", DEFAULT_MODEL)),
        claims=[str(claim) for claim in claims_option],
        instructions=str(options.get("instructions", "")),
        prompt_tokens=_optional_int(options.get("prompt_tokens")),
        completion_tokens=_optional_int(options.get("completion_tokens")),
    )
