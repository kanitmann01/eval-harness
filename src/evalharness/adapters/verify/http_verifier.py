"""One HTTP probe against a provider endpoint; no retries, short timeout."""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from evalharness.adapters.provider_wire import DEFAULT_ANTHROPIC_VERSION
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
    "openai": (BEARER, "Authorization"),
    "anthropic": (HEADER, "x-api-key"),
    "azure-openai": (HEADER, "api-key"),
    "opencode": (BEARER, "Authorization"),
    "none": (NONE, ""),
}
"""The auth shapes the verifier knows; an unlisted provider is bearer."""

PROVIDER_EXTRA_HEADERS: dict[str, dict[str, str]] = {
    "anthropic": {"anthropic-version": DEFAULT_ANTHROPIC_VERSION},
}
"""Headers a provider requires on every request, beyond its auth header."""

KNOWN_PROVIDERS: tuple[str, ...] = tuple(PROVIDER_AUTH)
"""The provider names the console offers, in the table's order."""


def provider_choices() -> list[tuple[str, str]]:
    """Return (provider, auth label) pairs for the console's provider select.

    The form reads this list instead of hard-coding providers, so a new row in
    PROVIDER_AUTH reaches the browser without a second edit.
    """
    choices: list[tuple[str, str]] = []
    for provider, (shape, header) in PROVIDER_AUTH.items():
        if shape == BEARER:
            choices.append((provider, "Bearer token"))
        elif shape == NONE:
            choices.append((provider, "no auth header"))
        else:
            choices.append((provider, header))
    return choices


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
        headers = {header: secret}
    else:
        headers = {"Authorization": f"Bearer {secret}"}
    headers.update(PROVIDER_EXTRA_HEADERS.get(provider, {}))
    return headers


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
