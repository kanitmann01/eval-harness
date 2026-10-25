"""Every first-party adapter with its protocol kind: the conformance CI gate.

The manifest is deliberately data, not imports: adding an adapter means
adding a row, and a test compares this list with the factories discovered
under evalharness.adapters so an adapter cannot ship without a suite run.
"""

from __future__ import annotations

import importlib
import inspect
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterator, Mapping


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One first-party adapter: dotted factory path, contract, suite options."""

    adapter: str
    kind: str
    options: dict[str, object] | None = None


HTTP_CANDIDATE = "evalharness.adapters.candidates.http:build"
"""The HTTP candidate whose conformance row needs a live endpoint."""

HTTP_CONFORMANCE_ENV = "EVALH_HTTP_CONFORMANCE_BASE_URL"
"""Environment variable that names the live endpoint for the HTTP row.

CI sets it to the reference server it starts. Leaving it unset keeps the
manifest's default, which is the local demo address.
"""

DEFAULT_HTTP_CONFORMANCE_BASE_URL = "http://127.0.0.1:8765"
"""The reference demo server's default loopback address."""

HTTP_CONFORMANCE_OPTIONS: dict[str, object] = {
    "auth": "none",
    "base_url": DEFAULT_HTTP_CONFORMANCE_BASE_URL,
}
"""Mutable suite options for the HTTP candidate.

The default points at the reference demo server
(`examples/http_candidate_server.py`), so `evalh conformance --all` works after
starting that process. `EVALH_HTTP_CONFORMANCE_BASE_URL` replaces `base_url`
when CI starts a server somewhere else. Tests replace `base_url` with an
ephemeral stub server before `run_all()`; the suite's existing options
mechanism carries it to every clause. Nothing here is a mock: without a live
server the HTTP clauses fail.
"""


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
    ManifestEntry(HTTP_CANDIDATE, "candidate", HTTP_CONFORMANCE_OPTIONS),
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


def conformance_options(
    entry: ManifestEntry,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return one entry's suite options with the environment default applied.

    The environment names the HTTP endpoint when the row still carries the
    repository default. Explicit row options win, so a test that starts its own
    stub (or an operator pointing at another host) is never overridden.

    Parameters
    ----------
    entry:
        The manifest row about to run.
    env:
        Environment mapping to read; defaults to `os.environ`.
    """
    options = dict(entry.options or {})
    if entry.adapter == HTTP_CANDIDATE:
        source = os.environ if env is None else env
        override = source.get(HTTP_CONFORMANCE_ENV)
        current = options.get("base_url")
        if override and current in (None, "", DEFAULT_HTTP_CONFORMANCE_BASE_URL):
            options["base_url"] = override
    return options


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
