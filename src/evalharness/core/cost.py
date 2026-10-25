"""Price tables, usage to USD, cost splits and pre-run estimates."""

from __future__ import annotations

import statistics
from typing import Iterable

from pydantic import BaseModel

from evalharness.core.models import CandidateOutput, CellRecord, Price, Spec

__all__ = [
    "DEFAULT_MEDIAN_COMPLETION_TOKENS",
    "DEFAULT_MEDIAN_PROMPT_TOKENS",
    "CostEstimate",
    "CostSplit",
    "Price",
    "estimate_cost",
    "observed_medians",
    "split_cost",
    "usage_cost",
]

DEFAULT_MEDIAN_PROMPT_TOKENS = 500
"""Calibration fallback until a comparable run supplies real medians."""

DEFAULT_MEDIAN_COMPLETION_TOKENS = 200
"""Calibration fallback until a comparable run supplies real medians."""


class CostSplit(BaseModel):
    """One cell's cost split into answer, judge and tool components."""

    answer_usd: float
    judge_usd: float
    tool_usd: float
    total_usd: float


class CostEstimate(BaseModel):
    """A pre-run cost estimate built from median token counts."""

    cells: int
    cells_per_candidate: dict[str, int]
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


def usage_cost(
    price: Price,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    tool_calls: int = 0,
) -> float:
    """Convert token and tool usage into USD at the given price."""
    return price.usage_usd(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_calls=tool_calls,
    )


def split_cost(
    price: Price,
    output: CandidateOutput,
    *,
    judge_usd: float = 0.0,
) -> CostSplit:
    """Split one candidate output's cost into answer, judge and tool parts."""
    answer_usd = usage_cost(
        price,
        prompt_tokens=output.prompt_tokens,
        completion_tokens=output.completion_tokens,
    )
    tool_usd = usage_cost(price, tool_calls=output.tool_calls)
    return CostSplit(
        answer_usd=answer_usd,
        judge_usd=judge_usd,
        tool_usd=tool_usd,
        total_usd=answer_usd + judge_usd + tool_usd,
    )


def observed_medians(cells: Iterable[CellRecord]) -> tuple[int, int] | None:
    """Return median (prompt, completion) tokens from ok cells, or None.

    The CLI feeds an earlier run of the same spec into the next launch estimate,
    so the budget check reflects measured usage instead of the fallback.
    """
    measured = [cell for cell in cells if cell.status == "ok"]
    if not measured:
        return None
    prompt = int(statistics.median(cell.prompt_tokens for cell in measured))
    completion = int(statistics.median(cell.completion_tokens for cell in measured))
    return prompt, completion


def estimate_cost(
    spec: Spec,
    prices: dict[str, Price],
    cells_per_candidate: dict[str, int],
    *,
    median_prompt_tokens: int = DEFAULT_MEDIAN_PROMPT_TOKENS,
    median_completion_tokens: int = DEFAULT_MEDIAN_COMPLETION_TOKENS,
) -> CostEstimate:
    """Estimate run cost from per-candidate cell counts and median tokens."""
    total_cells = sum(cells_per_candidate.values())
    prompt_tokens = total_cells * median_prompt_tokens
    completion_tokens = total_cells * median_completion_tokens
    total = 0.0
    for candidate in spec.candidates:
        price = prices[candidate.model]
        total += cells_per_candidate.get(candidate.name, 0) * usage_cost(
            price,
            prompt_tokens=median_prompt_tokens,
            completion_tokens=median_completion_tokens,
        )
    return CostEstimate(
        cells=total_cells,
        cells_per_candidate=dict(cells_per_candidate),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=total,
    )
