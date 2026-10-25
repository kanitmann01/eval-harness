"""Price tables, usage to USD, cost splits and pre-run estimates."""

from __future__ import annotations

import statistics
from typing import Iterable

from pydantic import BaseModel

from evalharness.core.models import (
    CandidateOutput,
    CellRecord,
    Price,
    ProviderCost,
    ProviderUsage,
    Spec,
)

__all__ = [
    "DEFAULT_MEDIAN_COMPLETION_TOKENS",
    "DEFAULT_MEDIAN_PROMPT_TOKENS",
    "CostEstimate",
    "CostSplit",
    "Price",
    "estimate_cost",
    "observed_medians",
    "provider_cost_rows",
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
    """Estimate run cost from per-candidate cell counts and median tokens.

    `prices` is keyed by candidate name, not by model, because two candidates
    may use the same model at two providers with two different prices.
    """
    total_cells = sum(cells_per_candidate.values())
    prompt_tokens = total_cells * median_prompt_tokens
    completion_tokens = total_cells * median_completion_tokens
    total = 0.0
    for candidate in spec.candidates:
        price = prices[candidate.name]
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


def provider_cost_rows(
    run_id: str,
    cells: Iterable[CellRecord],
) -> list[ProviderCost]:
    """Sum a run's cells into one row per provider, sorted by provider name.

    Answer and tool cost belong to the provider that ran the candidate; judge
    cost belongs to the provider that served the judge, which is why a cell
    stores the judge usage it triggered per provider. A cell recorded before
    that split existed falls back to attributing its judge cost to its own
    provider, so an old run still totals correctly. Every row's `cost_usd` is
    its three parts, so the rows sum to the run's stored cost.
    """
    rows: dict[str, ProviderCost] = {}

    def row_for(provider: str) -> ProviderCost:
        return rows.setdefault(
            provider, ProviderCost(run_id=run_id, provider=provider)
        )

    for cell in cells:
        row = row_for(cell.provider or "")
        row.n_cells += 1
        row.prompt_tokens += cell.prompt_tokens
        row.completion_tokens += cell.completion_tokens
        row.answer_cost_usd += cell.answer_cost_usd
        row.tool_cost_usd += cell.tool_cost_usd
        if cell.judge_provider_usage:
            for provider, usage in cell.judge_provider_usage.items():
                _add_judge_usage(row_for(provider), usage)
        else:
            row.judge_prompt_tokens += cell.judge_prompt_tokens
            row.judge_completion_tokens += cell.judge_completion_tokens
            row.judge_cost_usd += cell.judge_cost_usd
    for row in rows.values():
        row.cost_usd = row.answer_cost_usd + row.judge_cost_usd + row.tool_cost_usd
    return sorted(rows.values(), key=lambda item: item.provider)


def _add_judge_usage(row: ProviderCost, usage: ProviderUsage) -> None:
    """Add one cell's judge usage to its provider's row."""
    row.judge_prompt_tokens += usage.prompt_tokens
    row.judge_completion_tokens += usage.completion_tokens
    row.judge_cost_usd += usage.cost_usd
