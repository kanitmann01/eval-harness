"""Architecture guard: core may never import a concrete adapter."""

from __future__ import annotations

from pathlib import Path


def test_core_never_imports_adapters():
    core = Path(__file__).parents[1] / "src" / "evalharness" / "core"
    offenders = []
    for path in core.glob("*.py"):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            if "evalharness.adapters" in stripped:
                offenders.append(f"{path.name}:{number}: {stripped}")
    assert offenders == []
