"""Shared judge flow for the provider-shaped adapters.

A provider judge asks one model for a structured verdict, then maps the reply
and the reported usage onto the core `Verdict`. The provider's JSON lives in
`adapters.provider_wire`; the auth, retry and error policy is the same one the
provider candidates use.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.adapters.provider_wire import (
    DEFAULT_MAX_TOKENS,
    ProviderDialect,
    judge_prompt,
    parse_verdict,
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
    validate_response,
)
from evalharness.core.models import JudgeRequest, Verdict
from evalharness.core.protocols import Capabilities

ATTEMPT_BUDGET = 3
"""One first attempt plus two retries for a typed transport failure."""


class ProviderJudge:
    """A judge that asks one provider model for a structured verdict."""

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
        instructions: str = "",
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
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
            requires_model=True,
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
        self._instructions = instructions
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def verdict(self, request: JudgeRequest) -> Verdict:
        """Judge one answer and report the provider's tokens and usage source."""
        secret = request.credential.secret if request.credential is not None else ""
        headers = auth_headers(
            self._auth,
            secret,
            label=f"judge {self.name!r}",
            header_name=self._header_name,
            extra=self._dialect.extra_headers,
        )
        system, user = judge_prompt(request, self._instructions)
        payload = self._dialect.payload(
            model=request.model,
            system=system,
            prompt=user,
            temperature=self._temperature,
            seed=0,
            max_tokens=self._max_tokens,
        )
        response = await post_json(
            self._url, payload, headers, timeout_s=self._timeout_s
        )
        parsed = validate_response(
            self._dialect.response_model,
            decode_json(response),
            self._url,
        )
        provider_response = self._dialect.read(parsed)
        passed, reason, score = parse_verdict(provider_response.text)
        usage = map_usage(
            prompt_tokens=provider_response.prompt_tokens,
            completion_tokens=provider_response.completion_tokens,
            prompt_text=f"{system}\n\n{user}",
            completion_text=provider_response.text,
        )
        return Verdict(
            passed=passed,
            score=score,
            reason=reason,
            evidence={
                "provider": self._dialect.provider,
                "model": request.model,
                "verdict": provider_response.text,
            },
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            model=provider_response.model or request.model,
            usage_source=usage.source,
        )


def judge_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """Return the shared constructor arguments for one provider judge."""
    return {
        "base_url": str(options.get("base_url", "")),
        "path": str(options.get("path") or ""),
        "timeout_s": float(options.get("timeout_s", 30.0)),
        "auth": str(options.get("auth", BEARER)),
        "header_name": str(options.get("header_name", "")),
        "instructions": str(options.get("instructions", "")),
        "temperature": float(options.get("temperature", 0.0)),
        "max_tokens": int(options.get("max_tokens", DEFAULT_MAX_TOKENS)),
    }
