"""Provider wire shapes: request bodies, response models and verdict parsing.

An OpenAI-compatible endpoint and an Anthropic-style endpoint share one
transport policy (`adapters.transport`) but not their JSON. This module keeps
both dialects in one place, so a provider's candidate and its judge use the
same payload builder and the same response model and cannot drift apart.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from evalharness.core.models import JudgeRequest

DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
"""The Messages API version header the Anthropic dialect sends."""

DEFAULT_MAX_TOKENS = 1024
"""Anthropic requires a completion cap; candidates and judges share the default."""

VERDICT_INSTRUCTIONS = (
    "You are grading one answer. Reply with exactly one JSON object: "
    '{"passed": true|false, "reason": "<one sentence>", '
    '"score": <0..1 or null>}. '
    "Use the rubric and the expected data when they are present; otherwise "
    "judge whether the answer is correct and complete."
)
"""The system prompt that declares the one verdict shape a judge must return."""

_PASS = re.compile(r"\bPASS\b")
_FAIL = re.compile(r"\bFAIL\b")


class _OpenAiMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str = ""


class _OpenAiChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _OpenAiMessage


class OpenAiUsage(BaseModel):
    """The token counts an OpenAI-compatible response may report."""

    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class OpenAiResponse(BaseModel):
    """A `/chat/completions` response, ignoring fields the harness does not need."""

    model_config = ConfigDict(extra="ignore")

    choices: list[_OpenAiChoice] = Field(min_length=1)
    usage: OpenAiUsage | None = None
    model: str | None = None


class AnthropicContentBlock(BaseModel):
    """One block of an Anthropic-style response `content` array."""

    model_config = ConfigDict(extra="ignore")

    type: str = "text"
    text: str = ""


class AnthropicUsage(BaseModel):
    """The token counts an Anthropic-style response may report."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int | None = None
    output_tokens: int | None = None


class AnthropicResponse(BaseModel):
    """A `/v1/messages` response, ignoring fields the harness does not need."""

    model_config = ConfigDict(extra="ignore")

    content: list[AnthropicContentBlock] = Field(min_length=1)
    usage: AnthropicUsage | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedProviderResponse:
    """The text, model and reported usage one provider response carried."""

    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass(frozen=True, slots=True)
class ProviderDialect:
    """One provider's path, request body, response model and static headers."""

    provider: str
    path: str
    response_model: type[BaseModel]
    extra_headers: Mapping[str, str]
    build_payload: Callable[..., dict[str, Any]]
    read_response: Callable[[Any], ParsedProviderResponse]

    def payload(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
        seed: int,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Build the request body for one provider call."""
        return self.build_payload(
            model=model,
            system=system,
            prompt=prompt,
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
        )

    def read(self, parsed: Any) -> ParsedProviderResponse:
        """Read the answer text, model and reported usage out of a response."""
        return self.read_response(parsed)


def _openai_payload(
    *,
    model: str,
    system: str,
    prompt: str,
    temperature: float,
    seed: int,
    max_tokens: int,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "seed": seed,
    }


def _openai_read(parsed: OpenAiResponse) -> ParsedProviderResponse:
    usage = parsed.usage
    return ParsedProviderResponse(
        text=parsed.choices[0].message.content,
        model=parsed.model or "",
        prompt_tokens=usage.prompt_tokens if usage is not None else None,
        completion_tokens=usage.completion_tokens if usage is not None else None,
    )


def _anthropic_payload(
    *,
    model: str,
    system: str,
    prompt: str,
    temperature: float,
    seed: int,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "system": system,
        "temperature": temperature,
    }


def _anthropic_read(parsed: AnthropicResponse) -> ParsedProviderResponse:
    usage = parsed.usage
    return ParsedProviderResponse(
        text=parsed.content[0].text,
        model=parsed.model or "",
        prompt_tokens=usage.input_tokens if usage is not None else None,
        completion_tokens=usage.output_tokens if usage is not None else None,
    )


OPENAI = ProviderDialect(
    provider="openai-compatible",
    path="/chat/completions",
    response_model=OpenAiResponse,
    extra_headers={},
    build_payload=_openai_payload,
    read_response=_openai_read,
)
"""The OpenAI-compatible dialect: bearer auth, `choices[0].message.content`."""

ANTHROPIC = ProviderDialect(
    provider="anthropic",
    path="/v1/messages",
    response_model=AnthropicResponse,
    extra_headers={"anthropic-version": DEFAULT_ANTHROPIC_VERSION},
    build_payload=_anthropic_payload,
    read_response=_anthropic_read,
)
"""The Anthropic-style dialect: `x-api-key` plus a version header."""


def judge_prompt(
    request: JudgeRequest,
    instructions: str,
) -> tuple[str, str]:
    """Build the system and user text for one judged answer.

    The declaration's `options.instructions` are appended to the verdict
    instruction, so a judge's per-declaration guidance lives in the request
    rather than in a branch in the adapter.
    """
    system = VERDICT_INSTRUCTIONS
    if instructions:
        system = f"{system}\n\n{instructions}"
    parts: list[str] = []
    if request.rubric:
        parts.append(f"Rubric:\n{request.rubric}")
    if request.expected:
        parts.append(
            "Expected:\n" + json.dumps(request.expected, sort_keys=True, default=str)
        )
    if request.evidence:
        parts.append(
            "Evidence:\n" + json.dumps(request.evidence, sort_keys=True, default=str)
        )
    parts.append(f"Question:\n{request.prompt}")
    parts.append(f"Answer:\n{request.answer}")
    return system, "\n\n".join(parts)


def parse_verdict(content: str) -> tuple[bool, str, float | None]:
    """Parse a judge's reply into a pass, a reason and an optional score.

    A JSON object with a boolean `passed` is the documented shape. A reply
    without one falls back to a whole-word PASS or FAIL marker, and any other
    prose is a fail with the model's text as the reason: an unparseable reply
    must never silently pass a candidate. FAIL is checked first because a
    reply that mentions both is not a pass.
    """
    text = content.strip()
    data = _json_verdict(text)
    if isinstance(data, dict) and isinstance(data.get("passed"), bool):
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = text
        score = data.get("score")
        numeric = (
            float(score)
            if isinstance(score, (int, float)) and not isinstance(score, bool)
            else None
        )
        return data["passed"], reason, numeric
    upper = text.upper()
    if _FAIL.search(upper):
        return False, text or "the judge returned no verdict", None
    if _PASS.search(upper):
        return True, text, None
    return False, text or "the judge returned no verdict", None


def _json_verdict(text: str) -> Any:
    """Return the JSON object in a reply, tolerating a Markdown code fence."""
    candidate = text
    if candidate.startswith("```"):
        candidate = "\n".join(
            line
            for line in candidate.splitlines()
            if not line.strip().startswith("```")
        ).strip()
    try:
        return json.loads(candidate)
    except ValueError:
        return None
