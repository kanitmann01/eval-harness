"""The OpenAI-compatible candidate: POST {base_url}/chat/completions.

The request carries the model, the messages, the temperature and the run seed;
the response is read from `choices[0].message.content` with
`usage.prompt_tokens` and `usage.completion_tokens`. Auth is a bearer token by
default; `auth: none` is the explicit escape hatch for a stub endpoint.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.adapters.candidates._provider import (
    ProviderCandidate,
    candidate_options,
)
from evalharness.adapters.provider_wire import OPENAI

WRONG_ANSWER = "conformance-wrong-answer"
"""The deliberately wrong answer the conformance suite asks this adapter to return."""

CONFORMANCE_HOOKS: dict[str, dict[str, Any]] = {
    "transport": {"fail": "transport"},
    "wrong": {"answer": WRONG_ANSWER},
}
"""Fault-injection options the public conformance suite applies to this adapter."""


def build(identity: str, options: Mapping[str, Any]) -> ProviderCandidate:
    """Build the OpenAI-compatible candidate from its spec options."""
    return ProviderCandidate(identity, OPENAI, **candidate_options(options))


build.CONFORMANCE_HOOKS = CONFORMANCE_HOOKS  # type: ignore[attr-defined]
