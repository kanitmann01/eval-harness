"""Contract tests for the console script.

The console works with JavaScript disabled, and the script is not executed in the
test suite. These tests read the source and pin the markup contract it builds for
the live event log.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "evalharness" / "api" / "static"
SCRIPT = STATIC / "console.js"


def _script() -> str:
    """Return the console script text."""
    return SCRIPT.read_text(encoding="utf-8")


def test_run_finished_names_the_cells_instead_of_repeating_the_chip() -> None:
    script = _script()
    assert '"run finished · "' in script
    assert "cells passed" in script
    assert 'payload.status || "done"' not in script


def test_one_chip_per_event_row() -> None:
    script = _script()
    assert script.count('return ["status-') == 3
    assert '"status-cancelled"' not in script
    assert '"status-budget_exceeded"' not in script
    assert 'if (kind === "cell_completed")' in script
    assert 'if (kind === "cell_failed")' in script
    assert 'if (kind === "run_finished")' in script


def test_cell_failed_does_not_repeat_the_error_type() -> None:
    script = _script()
    assert '"errored · "' not in script
    assert "payload.error_type" in script or "error_type" in script
