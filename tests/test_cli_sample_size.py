"""The sample-size command plans a two-proportion test from the shell."""

from __future__ import annotations

import json

import pytest

from evalharness.cli import main
from evalharness.core.stats import required_sample_size


def test_sample_size_prints_the_plan_per_side_and_in_total(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["sample-size", "--baseline", "0.70", "--effect", "0.10"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "n per side: 294" in captured.out
    assert "total n: 588" in captured.out
    assert "two-sided alpha 0.05" in captured.out
    assert "power 0.80" in captured.out
    assert "baseline 0.70" in captured.out
    assert "effect +0.10" in captured.out


def test_sample_size_accepts_power_and_alpha_overrides(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "sample-size",
                "--baseline",
                "0.70",
                "--effect",
                "0.10",
                "--power",
                "0.90",
                "--alpha",
                "0.01",
            ]
        )
        == 0
    )
    expected = required_sample_size(0.70, 0.10, power=0.90, alpha=0.01)
    captured = capsys.readouterr()
    assert f"n per side: {expected}" in captured.out
    assert f"total n: {2 * expected}" in captured.out
    assert "power 0.90" in captured.out
    assert "two-sided alpha 0.01" in captured.out


def test_sample_size_json_is_machine_readable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "sample-size",
                "--baseline",
                "0.70",
                "--effect",
                "0.10",
                "--json",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload == {
        "alpha": 0.05,
        "baseline": 0.70,
        "effect": 0.10,
        "n_per_side": 294,
        "power": 0.80,
        "total": 588,
    }


def test_sample_size_rejects_an_impossible_baseline_with_an_actionable_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["sample-size", "--baseline", "1.20", "--effect", "0.10"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sample-size error:" in captured.err
    assert "baseline" in captured.err
    assert "between 0 and 1" in captured.err


def test_sample_size_rejects_a_zero_effect(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sample-size", "--baseline", "0.70", "--effect", "0"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "effect must not be zero" in captured.err


def test_sample_size_help_and_top_level_help_list_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["sample-size", "--help"]) == 0
    captured = capsys.readouterr()
    for flag in ("--baseline", "--effect", "--power", "--alpha", "--json"):
        assert flag in captured.out

    assert main(["--help"]) == 0
    top_level = capsys.readouterr()
    assert "sample-size" in top_level.out
    assert "two-proportion" in top_level.out
