"""Contract tests for the console stylesheet.

The stylesheet ships as static text, so these tests read it directly instead of
starting a browser. Each test pins one rule that regressed or was documented in
docs/design-system.md.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "evalharness" / "api" / "static"
CSS = STATIC / "console.css"
TOUCH_TARGET_SELECTORS = (
    ".btn,",
    ".button-link,",
    ".btn-sm,",
    ".icon-btn,",
    ".copy-button,",
    ".site-nav a,",
    ".step-link,",
    ".heat-cell a {",
)


def _css() -> str:
    """Return the console stylesheet text."""
    return CSS.read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    """Return the body of the first rule or at-rule that starts with selector."""
    start = css.index(selector)
    opening = css.index("{", start)
    depth = 1
    for position in range(opening + 1, len(css)):
        if css[position] == "{":
            depth += 1
        elif css[position] == "}":
            depth -= 1
            if depth == 0:
                return css[opening + 1 : position]
    raise AssertionError(f"unbalanced braces after {selector}")


def _token(block: str, name: str) -> str:
    """Return the hex value declared for one custom property in a block."""
    match = re.search(rf"{re.escape(name)}\s*:\s*(#[0-9a-fA-F]{{6}})", block)
    assert match is not None, f"{name} is not declared"
    return match.group(1)


def _luminance(colour: str) -> float:
    """Return the relative luminance of a #rrggbb colour."""
    channels = []
    for offset in (1, 3, 5):
        value = int(colour[offset : offset + 2], 16) / 255
        if value <= 0.03928:
            channels.append(value / 12.92)
        else:
            channels.append(((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(foreground: str, background: str) -> float:
    """Return the WCAG contrast ratio between two #rrggbb colours."""
    lighter, darker = sorted(
        (_luminance(foreground), _luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_on_accent_muted_token_is_gone() -> None:
    assert "--on-accent-muted" not in _css()


def test_faint_is_declared_once_in_each_scheme() -> None:
    css = _css()
    light = _block(css, ":root {")
    dark = _block(css, "@media (prefers-color-scheme: dark)")
    assert light.count("--faint:") == 1
    assert dark.count("--faint:") == 1
    assert _token(light, "--faint") == "#66706a"
    assert _token(dark, "--faint") == "#84918a"


def test_faint_clears_aa_on_both_surfaces_in_both_schemes() -> None:
    css = _css()
    for marker in (":root {", "@media (prefers-color-scheme: dark)"):
        block = _block(css, marker)
        faint = _token(block, "--faint")
        for surface in ("--surface", "--surface-2"):
            background = _token(block, surface)
            ratio = _contrast(faint, background)
            assert ratio >= 4.5, f"{marker} {faint} on {background} is {ratio:.2f}"


def test_table_wrapper_scrolls_on_both_axes_so_the_header_can_pin() -> None:
    css = _css()
    wrapper = _block(css, ".table-wrap {")
    assert "overflow: auto;" in wrapper
    assert "max-height: 70vh;" in wrapper
    head = _block(css, "thead th {")
    assert "position: sticky;" in head
    assert "top: 0;" in head


def test_heat_rate_is_hidden_until_the_phone_breakpoint() -> None:
    css = _css()
    assert "display: none;" in _block(css, ".heat-rate {")
    marker = "@media (max-width: 700px)"
    hidden = css.index(".heat-gap, .heat-summary { display: none; }")
    phone = _block(css[css.rindex(marker, 0, hidden) :], marker)
    assert ".heat-rate { display: block; }" in phone


def test_every_touch_target_reaches_44px_on_coarse_pointers_and_phones() -> None:
    css = _css()
    assert css.count("min-height: 44px") == 2
    for marker in ("@media (pointer: coarse)", "@media (max-width: 768px)"):
        block = _block(css, marker)
        assert "min-width: 44px;" in block
        assert "min-height: 44px;" in block
        for selector in TOUCH_TARGET_SELECTORS:
            assert selector in block, f"{selector} missing from {marker}"


def test_compare_metrics_columns_are_fixed_by_a_colgroup() -> None:
    css = _css()
    assert "th:nth-child(7)" not in css
    assert "table-layout: fixed;" in _block(css, ".compare-metrics {")
    for name, width in (
        ("col-metric", "13%"),
        ("col-a", "15%"),
        ("col-b", "15%"),
        ("col-delta", "13%"),
        ("col-verdict", "20%"),
        ("col-cost", "11%"),
        ("col-judge", "13%"),
    ):
        rule = f".compare-metrics col.{name} {{ width: {width}; }}"
        assert rule in css, rule


def test_secondary_buttons_keep_their_border() -> None:
    # button.btn outranked .btn-secondary, so every secondary <button> lost its
    # border and the add-case affordance rendered as plain text.
    css = _css()
    assert "button.btn" not in css
    assert "border-style: dashed;" in _block(css, ".btn-add {")


def test_rail_graph_headings_fit_the_320_unit_columns() -> None:
    css = _css()
    rule = ".spec-graph--rail .sg-col-label { font-size: 9px;"
    assert rule in css


def test_compare_cost_cell_truncates_inside_its_column() -> None:
    # The cost sub is longer than the 11% column, so it spilled into JUDGE.
    css = _css()
    cell = _block(css, ".compare-metrics .cost-cell {")
    assert "overflow: hidden;" in cell
    for part in (".cost-cell .metric-value", ".cost-cell .metric-sub"):
        rule = _block(css, f".compare-metrics {part}")
        assert "text-overflow: ellipsis;" in rule
        assert "white-space: nowrap;" in rule


def test_graph_variants_keep_their_own_box() -> None:
    css = _css()
    mini = _block(css, ".spec-graph--mini {")
    assert "width: 96px;" in mini
    assert "height: 32px;" in mini
    assert ".spec-graph--pair" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in _block(
        css, ".graph-pair {"
    )
    phone = _block(css, "@media (max-width: 768px)")
    assert ".col-graph, .run-graph { display: none; }" in phone
