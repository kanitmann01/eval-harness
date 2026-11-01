"""The CLI help must describe the console as it is: an editor and a launcher."""

from __future__ import annotations

import pytest

from evalharness.cli import main


def _help(capsys: pytest.CaptureFixture[str], argv: list[str]) -> str:
    """Run the CLI for help, assert success, and return unwrapped stdout."""
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return " ".join(captured.out.split())


def test_serve_help_describes_editing_specs_launching_runs_and_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help(capsys, ["serve", "--help"])
    assert "read-only" not in text
    assert "console" in text
    for phrase in ("edit specs", "launch and cancel runs", "provider credentials"):
        assert phrase in text
    for flag in ("--host", "--port", "--db", "--artifacts"):
        assert flag in text


def test_the_top_level_help_names_the_console(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help(capsys, ["--help"])
    assert "read-only" not in text
    assert "evaluation specs" in text
    assert "edits specs" in text
    assert "launches and cancels runs" in text
    assert "manages credentials" in text


def test_running_without_a_command_prints_the_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help(capsys, [])
    assert "console" in text
    assert "read-only" not in text
