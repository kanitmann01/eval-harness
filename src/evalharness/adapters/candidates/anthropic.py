"""The Anthropic-style candidate: POST {base_url}/v1/messages.

The request carries the model, a completion cap, the messages, the system
prompt and the temperature; the response is read from `content[0].text` with
`usage.input_tokens` and `usage.output_tokens`. Auth is the `x-api-key`
header plus `anthropic-version`; `auth: none` is the explicit escape hatch for
a stub endpoint.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.adapters.candidates._provider import (
    ProviderCandidate,
    candidate_options,
)
from evalharness.adapters.provider_wire import ANTHROPIC

WRONG_ANSWER = "conformance-wrong-answer"
"""The deliberately wrong answer the conformance suite asks this adapter to return."""

CONFORMANCE_HOOKS: dict[str, dict[str, Any]] = {
    "transport": {"fail": "transport"},
    "wrong": {"answer": WRONG_ANSWER},
}
"""Fault-injection options the public conformance suite applies to this adapter."""


def build(identity: str, options: Mapping[str, Any]) -> ProviderCandidate:
    """Build the Anthropic-style candidate from its spec options."""
    return ProviderCandidate(identity, ANTHROPIC, **candidate_options(options))


build.CONFORMANCE_HOOKS = CONFORMANCE_HOOKS  # type: ignore[attr-defined]
