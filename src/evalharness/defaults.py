"""Default registry paths the spec falls back to when it names none.

This module sits outside both `core` and `adapters` on purpose: core resolves
adapters by name from the registry and must never spell a concrete adapter
path itself, which `tests/test_architecture.py` enforces.
"""

from __future__ import annotations

DEFAULT_SINK_ADAPTER = "evalharness.adapters.sinks.json_sink:build"
"""The artifact sink used when a spec does not override it."""
