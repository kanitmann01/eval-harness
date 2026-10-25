"""The shared HTTP transport every provider-shaped adapter uses.

An OpenAI-compatible endpoint and an Anthropic-style endpoint differ in their
paths, request bodies and auth headers, but not in how a failure is classified:
429 and 5xx are retryable, a 4xx rejection is not, and a 2xx body that does not
match the declared shape is a rejection carrying the provider's message. This
module holds that one policy, the documented token-estimation fallback and the
prompt text rule, so the candidate and judge adapters share it instead of
copying it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from evalharness.core.context import RunContext
from evalharness.core.models import Case
from evalharness.errors import HarnessError, TransportError

BEARER = "bearer"
"""The default auth shape: `Authorization: Bearer <secret>`."""

HEADER = "header"
"""The secret goes in a named header, for example `x-api-key`."""

NONE = "none"
"""The provider needs no key: no auth header and no credential required."""

AUTH_SHAPES: tuple[str, ...] = (BEARER, HEADER, NONE)
"""Every auth shape a provider adapter accepts."""

CHARS_PER_TOKEN = 4
"""The documented fallback when a provider does not report token usage."""

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class ProviderError(HarnessError):
    """A provider answered in a way that must not be retried."""


@dataclass(frozen=True, slots=True)
class MappedUsage:
    """Token counts and whether they came from the provider or an estimate."""

    prompt_tokens: int
    completion_tokens: int
    source: Literal["provider", "estimated"]


def estimate_tokens(text: str) -> int:
    """Estimate tokens as `ceil(characters / 4)`, the published fallback rule."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def map_usage(
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    prompt_text: str,
    completion_text: str,
) -> MappedUsage:
    """Use reported counts when both are present, else estimate the missing ones.

    A response that reports one count keeps it and estimates only the other,
    but the source is still `estimated`, so a cost figure never hides which
    numbers the provider billed and which one the harness guessed.
    """
    if prompt_tokens is not None and completion_tokens is not None:
        return MappedUsage(prompt_tokens, completion_tokens, "provider")
    return MappedUsage(
        estimate_tokens(prompt_text) if prompt_tokens is None else prompt_tokens,
        (
            estimate_tokens(completion_text)
            if completion_tokens is None
            else completion_tokens
        ),
        "estimated",
    )


def prompt_text(case: Case, ctx: RunContext) -> str:
    """Return the prompt a provider sees: the override, plus every injected chunk."""
    text = ctx.prompt_override if ctx.prompt_override is not None else case.prompt
    if not ctx.chunks:
        return text
    return "\n\n".join([text, *(chunk.text for chunk in ctx.chunks)])


def auth_headers(
    auth: str,
    secret: str,
    *,
    label: str,
    header_name: str = "",
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the request headers for one auth shape.

    `label` names the adapter in the missing-credential error, so a cell error
    says which candidate or judge could not resolve its secret.
    """
    headers = {"Content-Type": "application/json"}
    headers.update(extra or {})
    if auth == NONE:
        return headers
    if not secret:
        raise ProviderError(
            f"{label} requires a credential resolved from the vault, but the run "
            "context carried none; store it with 'evalh credentials add' or set "
            "options.auth: none when the provider needs no key"
        )
    if auth == HEADER:
        headers[header_name] = secret
    else:
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def provider_message(response: httpx.Response) -> str:
    """Return the provider's error text verbatim, or an empty string.

    The order is: JSON `error` when it is a string, JSON `error.message`,
    JSON `message`, the plain body, then nothing (the caller falls back to the
    HTTP reason phrase).
    """
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        error = error.get("message")
    if isinstance(error, str):
        return error
    message = payload.get("message")
    return message if isinstance(message, str) else ""


async def post_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    timeout_s: float,
    error_type: type[ProviderError] = ProviderError,
) -> httpx.Response:
    """POST one JSON request and classify the outcome.

    A connect error, timeout, 429 or 5xx raises `TransportError`, which the
    executor retries to the adapter's declared attempt budget. Every other
    non-2xx response raises `error_type`, carrying the provider's message, so
    it becomes one cell error instead of a retry or a crash.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(url, json=dict(payload), headers=dict(headers))
    except httpx.HTTPError as exc:
        raise TransportError(f"request to {url} failed: {exc}") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise TransportError(
            f"{url} returned HTTP {response.status_code}: "
            f"{provider_message(response)}"
        )
    if not response.is_success:
        raise error_type(
            f"{url} returned HTTP {response.status_code}: "
            f"{provider_message(response) or response.reason_phrase}"
        )
    return response


def decode_json(response: httpx.Response) -> Any:
    """Decode a 2xx body, raising `ProviderError` when it is not JSON."""
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            f"{response.url} returned a body that is not JSON: "
            f"{response.text[:200]!r}"
        ) from exc


def validate_response(
    model: type[ResponseModel],
    payload: Any,
    url: str,
) -> ResponseModel:
    """Validate a decoded body against one response model, naming the field.

    A 2xx body that does not match the provider's shape is a rejection, not a
    retry: asking the same question again would return the same bad shape.
    """
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error.get("loc", ())) or "response"
        raise ProviderError(
            f"{url} returned a response that does not match the provider "
            f"contract at {field}: {error.get('msg', 'invalid value')}"
        ) from exc
