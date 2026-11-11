"""The shipped default price table and provider-aware price lookup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field

from evalharness.core.models import Price, Spec

DEFAULT_PRICES_PATH = Path(__file__).resolve().parents[1] / "config" / "prices.yaml"
"""The packaged default table; `config/prices.yaml` inside the distribution."""


class PriceTable(BaseModel):
    """Resolved USD per million tokens, keyed by provider then model.

    An empty provider key holds a price that is not provider-specific, which
    is what a deterministic judge or a custom endpoint's model uses. The table
    is snapshotted on each run, so a later edit to the shipped file cannot
    change what an old run cost.
    """

    model_config = ConfigDict(extra="forbid")

    providers: dict[str, dict[str, Price]] = Field(default_factory=dict)

    def for_(self, provider: str, model: str) -> Price | None:
        """Return one provider's price for one model, or None when unset."""
        return self.providers.get(provider, {}).get(model)

    def snapshot(self) -> dict[str, Any]:
        """Return the JSON-ready snapshot stored on a run.

        The shape is versioned so a snapshot written before providers existed
        still reads back: version 2 nests the table by provider, and the
        compare view flattens it to `provider/model` keys for display.
        """
        return {
            "version": 2,
            "providers": {
                provider: {
                    model: price.model_dump()
                    for model, price in sorted(models.items())
                }
                for provider, models in sorted(self.providers.items())
            },
        }


def load_default_prices(path: str | Path | None = None) -> PriceTable:
    """Read the shipped provider price table, or one path a test names."""
    source = Path(path) if path is not None else DEFAULT_PRICES_PATH
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return PriceTable(providers=data)


def resolve_price(
    spec: Spec,
    provider: str,
    model: str,
    defaults: PriceTable,
) -> Price | None:
    """Resolve one model's price, most specific declaration first.

    A spec's `provider_prices` entry wins over its model-only `prices` entry,
    which wins over the shipped table for that provider. The order lets a spec
    price the same model differently at two providers without losing the
    model-only syntax every existing spec uses.
    """
    provider_specific = spec.provider_prices.get(provider, {}).get(model)
    if provider_specific is not None:
        return provider_specific
    declared = spec.prices.get(model)
    if declared is not None:
        return declared
    return defaults.for_(provider, model)


def resolved_price_table(spec: Spec, defaults: PriceTable) -> PriceTable:
    """Merge the shipped table with the spec's overrides, most specific last.

    The result is what the run snapshots: every default entry with the spec's
    model-only prices applied to every bucket that holds that model, then the
    provider-specific entries that win. A model-only override with no matching
    default lands in the provider-agnostic `""` bucket.
    """
    table: dict[str, dict[str, Price]] = {
        provider: dict(models) for provider, models in defaults.providers.items()
    }
    for model, price in spec.prices.items():
        applied = False
        for models in table.values():
            if model in models:
                models[model] = price
                applied = True
        if not applied:
            table.setdefault("", {})[model] = price
    for provider, models in spec.provider_prices.items():
        table.setdefault(provider, {}).update(models)
    return PriceTable(providers=table)


def flatten_price_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, dict[str, float]]:
    """Return a run's price snapshot as display rates, legacy or version 2.

    A version 2 snapshot nests the table by provider and is flattened to
    `provider/model` keys; a provider-agnostic entry keeps the bare model key,
    so the display stays readable. A snapshot written before issue #17 is
    already flat and is read as it stands. An empty mapping is allowed so a run
    recorded without prices still reads back as zero rates.
    """
    data = dict(snapshot or {})
    if data.get("version") == 2:
        providers = data.get("providers") or {}
        return {
            (f"{provider}/{model}" if provider else str(model)): {
                str(key): float(value) for key, value in dict(price).items()
            }
            for provider, models in providers.items()
            for model, price in models.items()
        }
    return {
        str(model): {str(key): float(value) for key, value in dict(price).items()}
        for model, price in data.items()
    }


def price_table_identity(
    snapshot: Mapping[str, Any],
) -> tuple[str, dict[str, dict[str, float]]]:
    """Return the flattened rates and the sha256 identity a receipt records.

    The identity is the SHA-256 of the canonical JSON flattening, and it is the
    key both the compare page and the run receipt quote. Every stored receipt
    hash was computed with exactly this expression, so it must not change
    without migrating the runs that already recorded one.
    """
    prices = flatten_price_snapshot(snapshot)
    identity = hashlib.sha256(
        json.dumps(prices, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return identity, prices
