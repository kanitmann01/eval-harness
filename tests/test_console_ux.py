"""Contract tests for the console microcopy, money and time formatters.

These are pure unit tests over the display helpers plus static-text checks for
the shared stylesheet, script and templates. Browser behaviour is covered by the
HTTP console tests; this file keeps the formatting contract cheap to pin.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Template

from evalharness.api.templating import format_time, format_usd

TEMPLATES = Path(__file__).parents[1] / "src" / "evalharness" / "api" / "templates"
STATIC = Path(__file__).parents[1] / "src" / "evalharness" / "api" / "static"
PROGRESS_SCALE = re.compile(r'style="transform: scaleX\(\{\{ (.+?) \}\}\)"')


def test_usd_keeps_three_significant_digits_below_a_cent() -> None:
    """A real sub-cent cost stays readable without scientific notation."""
    assert format_usd(0.000066) == "$0.0000660"
    assert format_usd(0.00069) == "$0.000690"
    assert format_usd(0.000026) == "$0.0000260"
    assert format_usd(0.0000066) == "$0.00000660"


def test_usd_rounds_to_cents_at_or_above_a_cent() -> None:
    """Ordinary money uses two decimals once it reaches a cent."""
    assert format_usd(0.01) == "$0.01"
    assert format_usd(1.234) == "$1.23"
    assert format_usd(1234.5) == "$1,234.50"
    assert format_usd(0) == "$0.00"
    assert format_usd(None) == "-"


def test_timestamp_is_explicitly_utc() -> None:
    """Every stored timestamp renders in UTC and says so."""
    assert format_time(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02 03:04:05 UTC"
    assert format_time(None) == "-"


def _rendered_progress_scale(percent: int) -> str:
    """Return the transform the run page renders for one completion percentage."""
    source = (TEMPLATES / "run_detail.html").read_text(encoding="utf-8")
    expression = PROGRESS_SCALE.search(source)
    assert expression is not None, "run_detail.html no longer renders a scaleX fill"
    template = Template("scaleX({{ " + expression.group(1) + " }})")
    return template.render(progress=SimpleNamespace(percent=percent))


def test_run_detail_server_renders_the_progress_scale() -> None:
    """The first paint already has the right fill, before any SSE event."""
    assert _rendered_progress_scale(0) == "scaleX(0.0)"
    assert _rendered_progress_scale(67) == "scaleX(0.67)"
    assert _rendered_progress_scale(100) == "scaleX(1.0)"
    template = (TEMPLATES / "run_detail.html").read_text(encoding="utf-8")
    assert 'style="width: {{ progress.percent }}%"' not in template


def test_every_in_page_table_region_ships_skeleton_rows() -> None:
    """The runs filter is the only fetched table swap, and it has skeletons."""
    table_regions = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        if "data-loading-form" in text:
            assert "data-loading-region" in text, path.name
        if "data-loading-region" in text and "<table" in text:
            table_regions[path.name] = text
    assert set(table_regions) == {"runs.html"}
    for name, text in table_regions.items():
        assert "data-loading-form" in text, name
        # The macro renders data-skeleton-rows; the served page is pinned in
        # tests/test_api_console.py.
        assert "skeleton_rows(" in text, name


def test_spec_editor_marks_the_form_busy_while_it_validates_in_page() -> None:
    """The spec editor swaps panels from a fetch, so it uses aria-busy, not rows."""
    script = (STATIC / "spec_editor.js").read_text(encoding="utf-8")
    assert 'form.setAttribute("aria-busy", "true")' in script
    assert 'form.removeAttribute("aria-busy")' in script
    assert "data-skeleton-rows" not in script
