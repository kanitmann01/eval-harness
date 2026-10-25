"""The HTTP candidate: POST a case to another agent and map its answer back.

The wire contract is published in `contract/candidate-v1.schema.json` and
explained for the endpoint's author in `docs/http-candidate.md`. The adapter is
built from spec options only; the credential arrives on `RunContext`, never
from settings.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evalharness.adapters.transport import map_usage, post_json
from evalharness.core.context import RunContext
from evalharness.core.models import CandidateOutput, Case
from evalharness.core.protocols import Capabilities
from evalharness.errors import HarnessError, TransportError

BEARER = "bearer"
"""The default auth shape: `Authorization: Bearer <secret>`."""

HEADER = "header"
"""The secret goes in the header named by `header_name`."""

NONE = "none"
"""The endpoint owns its key: no auth header and no credential required."""

AUTH_SHAPES: tuple[str, ...] = (BEARER, HEADER, NONE)
"""Every auth shape the adapter accepts."""

DEFAULT_PATH = "/run"
"""The path appended to `base_url` when the spec names none."""

DEFAULT_TIMEOUT_S = 30.0
"""A request timeout that is generous for a slow agent but never hangs a run."""

ATTEMPT_BUDGET = 3
"""One first attempt plus two retries for a typed transport failure."""

WRONG_ANSWER = "conformance-wrong-answer"
"""The deliberately wrong answer the conformance suite asks this adapter to return."""

CONFORMANCE_HOOKS: dict[str, dict[str, Any]] = {
    "transport": {"fail": "transport"},
    "wrong": {"answer": WRONG_ANSWER},
}
"""Fault-injection options the public conformance suite applies to this adapter."""


class HttpCandidateError(HarnessError):
    """A candidate endpoint answered in a way that is not safe to retry."""


class _Usage(BaseModel):
    """The usage object a response may carry; both counts are optional."""

    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class _RunResponse(BaseModel):
    """The v1 response body with append-compatible extra fields ignored."""

    model_config = ConfigDict(extra="ignore")

    answer: str
    citations: list[str] = Field(default_factory=list)
    usage: _Usage | None = None
    tool_calls: int = 0
    artifacts: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None


class HttpCandidate:
    """A candidate whose agent listens at an HTTP endpoint."""

    def __init__(
        self,
        name: str,
        *,
        base_url: str,
        path: str = DEFAULT_PATH,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        auth: str = BEARER,
        header_name: str = "",
        answer: str | None = None,
        fail: str = "",
    ) -> None:
        if not base_url:
            raise ValueError(
                "options.base_url is required; point it at the endpoint's base URL, "
                "for example http://127.0.0.1:8765"
            )
        if auth not in AUTH_SHAPES:
            raise ValueError(
                f"options.auth must be one of {', '.join(AUTH_SHAPES)}, got {auth!r}"
            )
        if auth == HEADER and not header_name:
            raise ValueError(
                "options.header_name is required when options.auth is 'header'; "
                "name the header that carries the credential"
            )
        self.name = name
        self.capabilities = Capabilities(
            requires_credential=auth != NONE,
            max_attempts=ATTEMPT_BUDGET,
        )
        self._url = base_url.rstrip("/") + (
            path if path.startswith("/") else "/" + path
        )
        self._timeout_s = timeout_s
        self._auth = auth
        self._header_name = header_name
        self._answer = answer
        self._fail = fail

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        """Execute one case and map the response into a `CandidateOutput`."""
        if self._fail == "transport":
            raise TransportError(
                f"http candidate {self.name!r} transport failure (conformance hook)"
            )
        if self._answer is not None:
            return CandidateOutput(
                answer=self._answer,
                model=ctx.model,
                usage_source="estimated",
                artifacts={"source": "conformance-hook"},
            )
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_headers(ctx))
        payload = {
            "case_id": case.case_id,
            "prompt": case.prompt,
            "context": [
                {"chunk_id": chunk.chunk_id, "text": chunk.text}
                for chunk in ctx.chunks
            ],
            "prompt_override": ctx.prompt_override,
            "seed": ctx.seed,
        }
        started = time.perf_counter()
        response = await self._post(payload, headers)
        latency_ms = int((time.perf_counter() - started) * 1000)
        parsed = _parse_response(response)
        prompt_text = (
            ctx.prompt_override
            if ctx.prompt_override is not None
            else case.prompt
        )
        prompt_chars = prompt_text + "".join(chunk.text for chunk in ctx.chunks)
        usage = map_usage(
            prompt_tokens=(
                parsed.usage.prompt_tokens if parsed.usage is not None else None
            ),
            completion_tokens=(
                parsed.usage.completion_tokens if parsed.usage is not None else None
            ),
            prompt_text=prompt_chars,
            completion_text=parsed.answer,
        )
        return CandidateOutput(
            answer=parsed.answer,
            tool_calls=parsed.tool_calls,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            citations=list(parsed.citations),
            latency_ms=latency_ms,
            model=parsed.model or ctx.model,
            usage_source=usage.source,
            artifacts=dict(parsed.artifacts),
        )

    def _auth_headers(self, ctx: RunContext) -> dict[str, str]:
        """Return the harness-owned auth header, empty for an endpoint-owned key."""
        if self._auth == NONE:
            return {}
        handle = ctx.credential
        secret = handle.secret if handle is not None else ""
        if not secret:
            raise HttpCandidateError(
                f"candidate {self.name!r} requires a credential resolved from the "
                "vault, but RunContext carried none; store it with "
                "'evalh credentials add' or set options.auth: none when the "
                "endpoint holds its own key"
            )
        if self._auth == HEADER:
            return {self._header_name: secret}
        return {"Authorization": f"Bearer {secret}"}

    async def _post(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        """POST one request, sharing the provider retry and error policy."""
        return await post_json(
            self._url,
            payload,
            headers,
            timeout_s=self._timeout_s,
            error_type=HttpCandidateError,
        )


def _parse_response(response: httpx.Response) -> _RunResponse:
    """Decode a 2xx body and validate it against the v1 response contract."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise HttpCandidateError(
            f"{response.url} returned a body that is not JSON: "
            f"{response.text[:200]!r}"
        ) from exc
    try:
        return _RunResponse.model_validate(payload)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error.get("loc", ())) or "response"
        raise HttpCandidateError(
            f"{response.url} returned a response that does not match "
            f"candidate-v1 at {field}: {error.get('msg', 'invalid value')}"
        ) from exc


def build(identity: str, options: Mapping[str, Any]) -> HttpCandidate:
    """Build the HTTP candidate from its spec options."""
    return HttpCandidate(
        identity,
        base_url=str(options.get("base_url", "")),
        path=str(options.get("path", DEFAULT_PATH)),
        timeout_s=float(options.get("timeout_s", DEFAULT_TIMEOUT_S)),
        auth=str(options.get("auth", BEARER)),
        header_name=str(options.get("header_name", "")),
        answer=(
            str(options["answer"]) if options.get("answer") is not None else None
        ),
        fail=str(options.get("fail", "")),
    )


build.CONFORMANCE_HOOKS = CONFORMANCE_HOOKS  # type: ignore[attr-defined]
