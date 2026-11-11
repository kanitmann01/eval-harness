"""Contract tests for the first-visit tour script.

The tour is a progressive enhancement: with JavaScript disabled the markup stays
hidden and the console works. These tests read the source and pin the preference
key, the dismissal paths and the keyboard map.
"""

from __future__ import annotations

from pathlib import Path

SCRIPT = (
    Path(__file__).parents[1]
    / "src"
    / "evalharness"
    / "api"
    / "static"
    / "tour.js"
)


def _script() -> str:
    """Return the tour script text."""
    return SCRIPT.read_text(encoding="utf-8")


def test_seen_state_is_a_local_preference_not_a_server_record() -> None:
    script = _script()
    assert 'const STORAGE_KEY = "evalh:console-tour:v1";' in script
    assert 'window.localStorage.getItem(STORAGE_KEY) === "1"' in script
    assert 'window.localStorage.setItem(STORAGE_KEY, "1")' in script
    assert "fetch(" not in script
    assert "document.cookie" not in script


def test_a_second_visit_removes_the_tour_before_showing_it() -> None:
    script = _script()
    assert script.index("if (alreadySeen)") < script.index("show(0)")
    assert "root.remove();" in script
    assert "root.hidden = false;" in script
    assert "root.hidden = true;" in script


def test_the_five_markup_steps_are_the_only_content_source() -> None:
    script = _script()
    assert 'root.querySelectorAll(".tour-step")' in script
    assert "steps.length" in script
    assert "tour-progress" in script
    assert '" / "' in script
    assert "target.classList.add" in script


def test_escape_skips_and_arrow_keys_walk_the_steps() -> None:
    script = _script()
    assert 'event.key === "Escape"' in script
    assert 'event.key === "ArrowRight"' in script
    assert 'event.key === "ArrowLeft"' in script
    assert "finish();" in script
    assert "show(index + 1);" in script
    assert "show(index - 1);" in script
    assert 'next.textContent = index === steps.length - 1 ? "Done" : "Next";' in script


def test_dismissal_removes_scroll_and_resize_listeners() -> None:
    script = _script()
    assert 'removeEventListener("keydown", onKey)' in script
    assert 'removeEventListener("resize", reposition)' in script
    assert 'removeEventListener("scroll", reposition, true)' in script
    assert "previousFocus.focus();" in script
