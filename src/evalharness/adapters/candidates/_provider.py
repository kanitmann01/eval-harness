"""Shared candidate flow for the provider-shaped adapters.

The two provider families differ only in their JSON, which
`adapters.provider_wire` owns. Everything a candidate does with the response -
auth, the retryable/non-retryable split, the usage mapping and the output
record - is the same, so it lives here once.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from evalharness.adapters.provider_wire import (
    DEFAULT_MAX_TOKENS,
    ProviderDialect,
)
from evalharness.adapters.transport import (
    AUTH_SHAPES,
    BEARER,
    HEADER,
    NONE,
    auth_headers,
    decode_json,
    map_usage,
    post_json,
    prompt_text,
    validate_response,
)
from evalharness.core.context import RunContext
from evalharness.core.models import CandidateOutput, Case
from evalharness.core.protocols import Capabilities
from evalharness.errors import TransportError

ATTEMPT_BUDGET = 3
"""One first attempt plus two retries for a typed transport failure."""


class ProviderCandidate:
    """A candidate that posts one case to a provider's chat endpoint."""

    def __init__(
        self,
        name: str,
        dialect: ProviderDialect,
        *,
        base_url: str,
        path: str = "",
        timeout_s: float = 30.0,
        auth: str = BEARER,
        header_name: str = "",
        headers: Mapping[str, str] | None = None,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        answer: str | None = None,
        fail: str = "",
    ) -> None:
        if not base_url:
            raise ValueError(
                "options.base_url is required; point it at the provider's base "
                "URL, for example http://127.0.0.1:8770"
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
            provider=dialect.provider,
            requires_credential=auth != NONE,
            max_attempts=ATTEMPT_BUDGET,
        )
        self._dialect = dialect
        suffix = path or dialect.path
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        self._url = base_url.rstrip("/") + suffix
        self._timeout_s = timeout_s
        self._auth = auth
        self._header_name = header_name
        self._headers = dict(headers or {})
        self._system = system
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._answer = answer
        self._fail = fail

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        """Execute one case and map the provider response into an output."""
        if self._fail == "transport":
            raise TransportError(
                f"provider candidate {self.name!r} transport failure "
                "(conformance hook)"
            )
        if self._answer is not None:
            return CandidateOutput(
                answer=self._answer,
                model=ctx.model,
                usage_source="estimated",
                artifacts={"source": "conformance-hook"},
            )
        secret = ctx.credential.secret if ctx.credential is not None else ""
        extra_headers = dict(self._dialect.extra_headers)
        extra_headers.update(self._headers)
        headers = auth_headers(
            self._auth,
            secret,
            label=f"candidate {self.name!r}",
            header_name=self._header_name,
            extra=extra_headers,
        )
        prompt = prompt_text(case, ctx)
        payload = self._dialect.payload(
            model=ctx.model,
            system=self._system,
            prompt=prompt,
            temperature=self._temperature,
            seed=ctx.seed,
            max_tokens=self._max_tokens,
        )
        started = time.perf_counter()
        response = await post_json(
            self._url, payload, headers, timeout_s=self._timeout_s
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        parsed = validate_response(
            self._dialect.response_model,
            decode_json(response),
            self._url,
        )
        provider_response = self._dialect.read(parsed)
        usage = map_usage(
            prompt_tokens=provider_response.prompt_tokens,
            completion_tokens=provider_response.completion_tokens,
            prompt_text=(
                f"{self._system}\n{prompt}" if self._system else prompt
            ),
            completion_text=provider_response.text,
        )
        return CandidateOutput(
            answer=provider_response.text,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            latency_ms=latency_ms,
            model=provider_response.model or ctx.model,
            usage_source=usage.source,
            artifacts={"provider": self._dialect.provider},
        )


def candidate_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """Return the shared constructor arguments for one provider candidate."""
    return {
        "base_url": str(options.get("base_url", "")),
        "path": str(options.get("path") or ""),
        "timeout_s": float(options.get("timeout_s", 30.0)),
        "auth": str(options.get("auth", BEARER)),
        "header_name": str(options.get("header_name", "")),
        "headers": _headers(options.get("headers")),
        "system": str(options.get("system", "")),
        "temperature": float(options.get("temperature", 0.0)),
        "max_tokens": int(options.get("max_tokens", DEFAULT_MAX_TOKENS)),
        "answer": (
            str(options["answer"]) if options.get("answer") is not None else None
        ),
        "fail": str(options.get("fail", "")),
    }


def _headers(value: Any) -> dict[str, str]:
    """Validate the optional custom request headers from a spec."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(
            "options.headers must be a mapping of header names to string values"
        )
    headers: dict[str, str] = {}
    for name, header_value in value.items():
        if not isinstance(name, str) or not isinstance(header_value, str):
            raise ValueError(
                "options.headers must map string header names to string values"
            )
        headers[name] = header_value
    return headers
