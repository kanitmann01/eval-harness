"""One HTTP probe against a provider endpoint; no retries, short timeout."""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from evalharness.core.protocols import VerificationResult

BEARER = "bearer"
"""The default auth shape: Authorization Bearer <secret>."""

HEADER = "header"
"""A provider that puts the secret in its own header."""

NONE = "none"
"""A provider that needs no credential in the probe."""

DEFAULT_TIMEOUT_S = 5.0
"""A verification probe must fail fast, so it never retries."""

PROVIDER_AUTH: dict[str, tuple[str, str]] = {
    "anthropic": (HEADER, "x-api-key"),
    "azure-openai": (HEADER, "api-key"),
    "none": (NONE, ""),
}
"""Providers whose auth is not a bearer token; bearer is the default."""


class HttpVerifier:
    """Verify a credential with exactly one GET against a provider base URL."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base_url = base_url
        self._timeout_s = timeout_s

    async def verify(self, provider: str, secret: str) -> VerificationResult:
        """Return the provider's verdict and message verbatim."""
        headers = _auth_headers(provider, secret)
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(self._base_url, headers=headers)
        except httpx.HTTPError as exc:
            return VerificationResult(
                ok=False,
                message=f"verification request failed: {exc}",
            )
        message = _provider_message(response)
        if response.is_success:
            return VerificationResult(
                ok=True,
                message=message or "credential accepted",
            )
        return VerificationResult(
            ok=False,
            message=message or response.reason_phrase,
        )


def _auth_headers(provider: str, secret: str) -> dict[str, str]:
    shape, header = PROVIDER_AUTH.get(provider, (BEARER, "Authorization"))
    if shape == NONE:
        return {}
    if shape == HEADER:
        return {header: secret}
    return {"Authorization": f"Bearer {secret}"}


def _provider_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    message = payload.get("message")
    return message if isinstance(message, str) else ""


def build(identity: str, options: Mapping[str, Any]) -> HttpVerifier:
    """Build an HTTP verifier from base_url and timeout_s options."""
    return HttpVerifier(
        str(options.get("base_url", "")),
        timeout_s=float(options.get("timeout_s", DEFAULT_TIMEOUT_S)),
    )
