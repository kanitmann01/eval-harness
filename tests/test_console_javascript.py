"""Contract tests for the console script.

The console works with JavaScript disabled, and the script is not executed in the
test suite. These tests read the source and pin the markup contract it builds for
the live event log.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "evalharness" / "api" / "static"
SCRIPT = STATIC / "console.js"
EDITOR_SCRIPT = STATIC / "spec_editor.js"


def _script() -> str:
    """Return the console script text."""
    return SCRIPT.read_text(encoding="utf-8")


def test_run_finished_names_the_cells_instead_of_repeating_the_chip() -> None:
    script = _script()
    assert '"run finished · "' in script
    assert 'noun(payload.n_cells || total, "cell")' in script
    assert ' + " passed"' in script
    assert "cells passed" not in script
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


def test_copy_announces_the_object_and_value() -> None:
    script = _script()
    assert "button.dataset.copyLabel" in script
    assert '"Copied " + object + " " + value' in script
    assert 'button.textContent.trim().replace(/^Copy\\s+/i, "")' in script


def test_help_tip_toggles_and_dismisses_on_escape_or_outside_click() -> None:
    script = _script()
    assert "data-help-tip" in script
    assert 'event.key !== "Escape"' in script
    assert '"aria-expanded"' in script
    assert "pointerdown" in script
    assert 'classList.add("is-dismissed")' in script


def test_money_is_formatted_server_side_not_by_either_script() -> None:
    console_script = _script()
    editor_script = EDITOR_SCRIPT.read_text(encoding="utf-8")
    assert "toFixed" not in console_script
    assert "toFixed" not in editor_script
    assert "estimate.cost_text" in editor_script
    assert "estimate.cost_exact" in editor_script
    assert '"budget exceeded · the run stopped at its cap"' in console_script


def test_filter_submit_shows_skeleton_and_sets_aria_busy() -> None:
    script = _script()
    assert "data-loading-form" in script
    assert "data-skeleton-rows" in script
    assert "data-loaded-rows" in script
    assert 'target.setAttribute("aria-busy", "true")' in script
    assert 'window.addEventListener("pageshow"' in script
