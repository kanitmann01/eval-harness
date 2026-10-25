"""Every first-party adapter with its protocol kind: the conformance CI gate.

The manifest is deliberately data, not imports: adding an adapter means
adding a row, and a test compares this list with the factories discovered
under evalharness.adapters so an adapter cannot ship without a suite run.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterator


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One first-party adapter: dotted factory path, contract, suite options."""

    adapter: str
    kind: str
    options: dict[str, object] | None = None


MANIFEST: tuple[ManifestEntry, ...] = (
    ManifestEntry("evalharness.adapters.sources.inline:build", "source"),
    ManifestEntry("evalharness.adapters.candidates.stub:build", "candidate"),
    ManifestEntry("evalharness.adapters.candidates.stub:build_slow", "candidate"),
    ManifestEntry(
        "evalharness.adapters.candidates.stub:build_failing",
        "candidate",
        {"fail": ""},
    ),
    ManifestEntry(
        "evalharness.adapters.candidates.stub:build_fixed_prompt", "candidate"
    ),
    ManifestEntry("evalharness.adapters.judges.rule_based:build", "judge"),
    ManifestEntry("evalharness.adapters.judges.scripted:build", "judge"),
    ManifestEntry("evalharness.adapters.metrics.exact_match:build", "metric"),
    ManifestEntry("evalharness.adapters.metrics.contains:build", "metric"),
    ManifestEntry(
        "evalharness.adapters.metrics.citation_support:build",
        "metric",
        {"judge": "conformance-judge"},
    ),
    ManifestEntry("evalharness.adapters.sinks.json_sink:build", "sink"),
)
"""Every first-party adapter the public suite must cover."""

_PACKAGE_KINDS: dict[str, str] = {
    "candidates": "candidate",
    "judges": "judge",
    "metrics": "metric",
    "sources": "source",
    "sinks": "sink",
}
"""Adapter subpackage -> the protocol kind every factory there implements."""


def _adapter_modules() -> Iterator[tuple[str, ModuleType]]:
    root = Path(__file__).parent
    for package, kind in sorted(_PACKAGE_KINDS.items()):
        for path in sorted((root / package).glob("*.py")):
            if path.stem == "__init__":
                continue
            module_name = f"{__package__}.{package}.{path.stem}"
            yield kind, importlib.import_module(module_name)


def first_party_factories() -> set[tuple[str, str]]:
    """Discover every public build* factory under the adapters package."""
    found: set[tuple[str, str]] = set()
    for kind, module in _adapter_modules():
        for name, value in vars(module).items():
            if name.startswith("build") and inspect.isfunction(value):
                found.add((f"{module.__name__}:{name}", kind))
    return found
