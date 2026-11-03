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

PROVIDER_OPENAI_CANDIDATE = "evalharness.adapters.candidates.openai_compat:build"
PROVIDER_ANTHROPIC_CANDIDATE = "evalharness.adapters.candidates.anthropic:build"
PROVIDER_OPENAI_JUDGE = "evalharness.adapters.judges.openai_compat:build"
PROVIDER_ANTHROPIC_JUDGE = "evalharness.adapters.judges.anthropic:build"

PROVIDER_ADAPTERS: frozenset[str] = frozenset(
    {
        PROVIDER_OPENAI_CANDIDATE,
        PROVIDER_ANTHROPIC_CANDIDATE,
        PROVIDER_OPENAI_JUDGE,
        PROVIDER_ANTHROPIC_JUDGE,
    }
)
"""The four provider adapters whose conformance rows need a live provider stub."""

PROVIDER_CONFORMANCE_ENV = "EVALH_PROVIDER_CONFORMANCE_BASE_URL"
"""Environment variable that names the provider stub for those four rows."""

DEFAULT_PROVIDER_CONFORMANCE_BASE_URL = "http://127.0.0.1:8770"
"""The reference provider stub's default loopback address."""

PROVIDER_CONFORMANCE_OPTIONS: dict[str, object] = {
    "auth": "none",
    "base_url": DEFAULT_PROVIDER_CONFORMANCE_BASE_URL,
}
"""Mutable suite options shared by the four provider adapters.

The default points at `examples/provider_server.py`, which answers both the
chat-completions and the messages path, so `evalh conformance --all` works
after starting that process. `EVALH_PROVIDER_CONFORMANCE_BASE_URL` replaces
`base_url` when CI starts the stub elsewhere; tests replace it with an
ephemeral stub before `run_all()`. Nothing here is a mock: without a live
server the provider clauses fail. `auth: none` is what lets the suite run
without a stored credential; the auth shapes are covered by the tests.
"""


MANIFEST: tuple[ManifestEntry, ...] = (
    ManifestEntry("evalharness.adapters.sources.inline:build", "source"),
    ManifestEntry("evalharness.adapters.sources.repo:build", "source"),
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
    ManifestEntry(
        PROVIDER_OPENAI_CANDIDATE, "candidate", PROVIDER_CONFORMANCE_OPTIONS
    ),
    ManifestEntry(
        PROVIDER_ANTHROPIC_CANDIDATE, "candidate", PROVIDER_CONFORMANCE_OPTIONS
    ),
    ManifestEntry("evalharness.adapters.judges.rule_based:build", "judge"),
    ManifestEntry("evalharness.adapters.judges.scripted:build", "judge"),
    ManifestEntry(PROVIDER_OPENAI_JUDGE, "judge", PROVIDER_CONFORMANCE_OPTIONS),
    ManifestEntry(PROVIDER_ANTHROPIC_JUDGE, "judge", PROVIDER_CONFORMANCE_OPTIONS),
    ManifestEntry("evalharness.adapters.metrics.exact_match:build", "metric"),
    ManifestEntry("evalharness.adapters.metrics.contains:build", "metric"),
    ManifestEntry(
        "evalharness.adapters.metrics.citation_support:build",
        "metric",
        {"judge": "conformance-judge"},
    ),
    ManifestEntry(
        "evalharness.adapters.metrics.judge_pass:build",
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
        variable = HTTP_CONFORMANCE_ENV
        default = DEFAULT_HTTP_CONFORMANCE_BASE_URL
    elif entry.adapter in PROVIDER_ADAPTERS:
        variable = PROVIDER_CONFORMANCE_ENV
        default = DEFAULT_PROVIDER_CONFORMANCE_BASE_URL
    else:
        return options
    source = os.environ if env is None else env
    override = source.get(variable)
    current = options.get("base_url")
    if override and current in (None, "", default):
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
