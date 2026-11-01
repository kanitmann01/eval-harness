"""Repository docs hold together: no conflict markers, real screenshots, a demo story.

These are content contracts, not style rules: the README, the operator docs and
the changelog must describe the console that actually ships, and every image the
README points at must exist in the tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import evalharness

REPO = Path(__file__).resolve().parents[1]
DOC_FILES = [REPO / "README.md", REPO / "CHANGELOG.md", *sorted((REPO / "docs").glob("*.md"))]

IMAGE_LINK = re.compile(r"!\[[^\]]*\]\((?P<target>[^)\s]+)\)")
CHANGELOG_HEADING = re.compile(
    r"^## \[(?P<version>\d+\.\d+\.\d+)\] - (?P<date>\d{4}-\d{2}-\d{2})",
    re.MULTILINE,
)


def _text(path: Path) -> str:
    """Read one markdown file as UTF-8."""
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", DOC_FILES, ids=lambda p: p.name)
def test_docs_have_no_merge_conflict_markers(path: Path) -> None:
    """No committed conflict marker survives in the README, docs or changelog."""
    text = _text(path)
    assert "<<<<<<< " not in text, f"{path.name} still contains a start-of-conflict marker"
    assert ">>>>>>> " not in text, f"{path.name} still contains an end-of-conflict marker"
    assert "\n=======\n" not in text, f"{path.name} still contains a separator marker"


def test_readme_documents_console_v2_routes_and_screens() -> None:
    """The front door names console v2, the wizard, the edit form and every route."""
    readme = _text(REPO / "README.md")
    assert "Console v2" in readme
    for phrase in ("wizard", "Comparisons", "Compare", "Launch", "Credentials", "Decision", "Receipt", "Calibration"):
        assert phrase.lower() in readme.lower(), f"README does not mention {phrase}"
    for route in (
        "/specs",
        "/specs/{name}/edit",
        "/comparisons/new",
        "/comparisons/{name}/edit",
        "/launch",
        "/compare",
        "/runs/{id}/decision",
        "/runs/{id}/receipt",
        "/runs/{id}/diff",
        "/runs/{id}/calibration",
        "/credentials",
    ):
        assert route in readme, f"README does not document the route {route}"


def test_readme_names_the_four_offline_specs() -> None:
    """The demo table names all four no-credential specs the repository ships."""
    readme = _text(REPO / "README.md")
    for name in ("smoke.yaml", "stub-pack.yaml", "gated.yaml", "gated-failing.yaml"):
        assert (REPO / "examples" / "specs" / name).is_file()
        assert name in readme, f"README does not name {name}"
    assert "gated-failing.yaml" in readme and "1" in readme


def test_readme_offline_setup_names_pythonpath_and_master_key() -> None:
    """The offline path is copy-paste complete: PYTHONPATH, home, specs and the key."""
    readme = _text(REPO / "README.md")
    assert "PYTHONPATH" in readme
    assert "EVALH_MASTER_KEY" in readme
    assert "EVALH_HOME" in readme
    assert "scripts/demo.py" in readme


def test_readme_screenshot_links_resolve() -> None:
    """Every screenshot the README shows exists under docs/evidence."""
    readme = _text(REPO / "README.md")
    targets = [match.group("target") for match in IMAGE_LINK.finditer(readme)]
    assert targets, "README shows no screenshots"
    for target in targets:
        assert not target.startswith(("http://", "https://")), target
        assert (REPO / target).is_file(), f"README links a missing image: {target}"


def test_changelog_version_and_date_match_the_package() -> None:
    """The newest changelog entry is the released version and its release date."""
    changelog = _text(REPO / "CHANGELOG.md")
    match = CHANGELOG_HEADING.search(changelog)
    assert match is not None, "CHANGELOG.md has no '## [x.y.z] - YYYY-MM-DD' heading"
    assert match.group("version") == evalharness.__version__
    assert match.group("date") == "2026-11-01"


def test_changelog_consolidates_console_v2() -> None:
    """The 0.2.0 entry records the console, the timestamps and the operator path."""
    changelog = _text(REPO / "CHANGELOG.md")
    lowered = changelog.lower()
    for phrase in (
        "wizard",
        "edit form",
        "comparisons",
        "compare",
        "credentials",
        "utc",
        "favicon",
        "error page",
        "cost",
    ):
        assert phrase in lowered, f"CHANGELOG.md does not mention {phrase}"


def test_changelog_consolidates_the_trust_release() -> None:
    """The 0.3.0 entry records decision, receipt, diff, calibration and operator."""
    changelog = _text(REPO / "CHANGELOG.md").lower()
    for phrase in (
        "decision",
        "what-would-change-my-mind",
        "sample-size",
        "spec diff",
        "receipt",
        "calibration",
        "operator",
    ):
        assert phrase in changelog, f"CHANGELOG.md does not mention {phrase}"


def test_console_docs_page_map_covers_console_v2() -> None:
    """The page map lists the v2 screens, not only the original six pages."""
    console = _text(REPO / "docs" / "console.md")
    for route in (
        "/comparisons/new",
        "/comparisons/{name}/edit",
        "/specs/{name}/edit",
        "/launch",
        "/compare",
        "/runs/{run_id}/decision",
        "/runs/{run_id}/receipt",
        "/runs/{run_id}/diff",
        "/runs/{run_id}/calibration",
        "/credentials",
    ):
        assert route in console, f"docs/console.md does not document {route}"
    assert "UTC" in console
    for error_page in ("404", "409", "503"):
        assert error_page in console


def test_design_system_documents_timestamps_money_and_help() -> None:
    """The design system owns the UTC rule, the USD formatter and inline help."""
    design = _text(REPO / "docs" / "design-system.md")
    assert "UTC" in design
    assert "format_usd" in design
    assert "field-hint" in design
    assert "skeleton-row" in design


def test_ux_flow_marks_console_v2_as_shipped() -> None:
    """The screen flow reads as the shipped product, not an approval draft."""
    flow = _text(REPO / "docs" / "ux-flow.md")
    head = "\n".join(flow.splitlines()[:12])
    assert "shipped" in head.lower()
    assert "phase 0, for approval" not in head.lower()
    assert "/compare?a=" in flow
