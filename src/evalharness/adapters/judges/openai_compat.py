"""The OpenAI-compatible judge: POST {base_url}/chat/completions.

The judge asks its declared model for the one JSON verdict shape documented
in `docs/providers.md`, reads `choices[0].message.content` and reports
`usage.prompt_tokens` / `usage.completion_tokens` on the verdict. The
declaration's `model` is passed to the request by `JudgeSession`.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.adapters.judges._provider import ProviderJudge, judge_options
from evalharness.adapters.provider_wire import OPENAI


def build(identity: str, options: Mapping[str, Any]) -> ProviderJudge:
    """Build the OpenAI-compatible judge from its spec options."""
    return ProviderJudge(identity, OPENAI, **judge_options(options))
