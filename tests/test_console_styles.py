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
    ".help-tip,",
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


MOTION_DECLARATION = re.compile(
    r"(?<![-\w])((?:animation|transition)(?:-[a-z]+)?)\s*:"
)
MOTION_GROUP = {
    "animation": "animation",
    "animation-name": "animation",
    "animation-duration": "animation",
    "animation-timing-function": "animation",
    "animation-iteration-count": "animation",
    "animation-direction": "animation",
    "animation-fill-mode": "animation",
    "animation-play-state": "animation",
    "transition": "transition",
    "transition-property": "transition",
    "transition-duration": "transition",
    "transition-timing-function": "transition",
}


def _motion_sites(css: str) -> dict[str, set[str]]:
    """Return selectors outside keyframes that declare animation or transition.

    Delays are ignored: a delay alone cannot animate, and the shorthand or
    name that does is the site the reduced-motion block must neutralise.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    sites: dict[str, set[str]] = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = re.sub(r"\s+", " ", match.group(1)).strip()
        if selector.startswith("@"):
            continue
        properties = {
            MOTION_GROUP[found.group(1)]
            for found in MOTION_DECLARATION.finditer(match.group(2))
            if found.group(1) in MOTION_GROUP
        }
        if not properties:
            continue
        for part in selector.split(","):
            name = re.sub(r"\s+", " ", part).strip()
            sites.setdefault(name, set()).update(properties)
    return sites


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


def test_print_omits_actions_help_and_placeholders() -> None:
    # Printed run detail is the report: interactive affordances and legend
    # keys add ink without adding evidence.
    block = _block(_css(), "@media print")
    for selector in (
        ".page-actions,",
        ".copy-button,",
        ".help,",
        ".legend,",
        ".panel-actions,",
        ".event-history,",
        ".skeleton-rows,",
    ):
        assert selector in block, f"{selector} missing from print rules"
    assert ".heat-cell.empty," in block
    assert ".heat-cell.pending { visibility: hidden; }" in block


def test_skip_link_reveals_with_a_transform_instead_of_top() -> None:
    css = _css()
    link = _block(css, ".skip-link {")
    assert "top: var(--space-3);" in link
    assert "transform: translateY(" in link
    assert "transition: transform 0.15s ease;" in link
    assert "transition: top" not in css
    assert "transform: translateY(0);" in _block(css, ".skip-link:focus")


def test_progress_fill_scales_the_layer_and_keeps_the_colours() -> None:
    css = _css()
    fill = _block(css, ".progress-fill {")
    # An inline span ignores width, height and transform, so the block layer is
    # what makes the track paint at all.
    assert "display: block;" in fill
    assert "width: 100%;" in fill
    assert "transform: scaleX(0);" in fill
    assert "transform-origin: left center;" in fill
    assert "transition: transform 0.35s ease;" in fill
    assert "transition: width" not in css
    running = _block(css, ".progress-track.is-running .progress-fill {")
    assert (
        "background: linear-gradient(90deg, var(--accent), var(--accent-hover));"
        in running
    )
    partial = _block(css, ".progress-track.is-partial .progress-fill {")
    assert "background: var(--warn-ink);" in partial


def test_reduced_motion_uses_targeted_static_states() -> None:
    css = _css()
    block = _block(css, "@media (prefers-reduced-motion: reduce)")
    assert "0.01ms" not in block
    assert "*::before" not in block
    live = _block(block, ".live-dot")
    assert "animation: none;" in live
    assert "box-shadow: 0 0 0 3px var(--accent-soft);" in live
    skeleton = _block(block, ".skeleton")
    assert "animation: none;" in skeleton
    assert "background: var(--surface-3);" in skeleton
    assert "transition: none;" in _block(block, ".skip-link,")
    assert "transition-duration" not in block


def test_reduced_motion_block_covers_every_motion_site() -> None:
    """A new animation cannot ship without a static state for reduced motion."""
    css = _css()
    marker = "@media (prefers-reduced-motion: reduce)"
    reduced_body = _block(css, marker)
    # Scan the whole sheet except the reduced block, not just its prefix, so a
    # motion declaration appended after the block cannot escape the guard.
    base = _motion_sites(css.replace(reduced_body, "", 1))
    reduced = _motion_sites(reduced_body)
    known = {".skip-link", ".progress-fill", ".live-dot", ".skeleton"}
    assert known <= set(base), "the motion scan missed a known site"
    for selector, properties in base.items():
        assert selector in reduced, f"{selector} still moves under reduced motion"
        missing = properties - reduced[selector]
        assert not missing, f"{selector} still animates {missing}"


def test_graph_variants_keep_their_own_box() -> None:
    css = _css()
    # The 96 by 32 thumbnail was illegible at table density; deliberately
    # enlarged to 144 by 48 with stronger wires in the same viewBox.
    mini = _block(css, ".spec-graph--mini {")
    assert "width: 144px;" in mini
    assert "height: 48px;" in mini
    assert ".spec-graph--pair" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in _block(
        css, ".graph-pair {"
    )
    phone = _block(css, "@media (max-width: 768px)")
    assert ".col-graph, .run-graph { display: none; }" in phone
