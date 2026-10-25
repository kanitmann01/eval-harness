"""The Anthropic-style judge: POST {base_url}/v1/messages.

The judge asks its declared model for the one JSON verdict shape documented
in `docs/providers.md`, reads `content[0].text` and reports
`usage.input_tokens` / `usage.output_tokens` on the verdict. Auth is the
`x-api-key` header plus `anthropic-version`.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.adapters.judges._provider import ProviderJudge, judge_options
from evalharness.adapters.provider_wire import ANTHROPIC


def build(identity: str, options: Mapping[str, Any]) -> ProviderJudge:
    """Build the Anthropic-style judge from its spec options."""
    return ProviderJudge(identity, ANTHROPIC, **judge_options(options))
