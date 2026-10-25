"""Architecture guard: core may never name a concrete adapter.

The scan looks at every line, not only imports, because a hardcoded adapter
path in a string would bind core just as tightly as an import would. The one
default the spec falls back to lives in `evalharness/defaults.py`, outside
core.
"""

from __future__ import annotations

from pathlib import Path

CORE = Path(__file__).parents[1] / "src" / "evalharness" / "core"


def test_core_never_names_an_adapter():
    offenders = []
    for path in CORE.glob("*.py"):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "evalharness.adapters" in line:
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []
