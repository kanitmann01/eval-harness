"""Conformance command surface: exit codes, clause lines and the no-key demo."""

from __future__ import annotations

from pathlib import Path

from evalharness.cli import main
from tests.helpers import STUB

BROKEN_CAPABILITIES = "tests.test_conformance:build_dict_capabilities_candidate"


def test_conformance_adapter_passes(evalh_home, capsys):
    code = main(["conformance", "--adapter", STUB, "--kind", "candidate"])

    captured = capsys.readouterr()
    assert code == 0
    assert "candidate/1: ok" in captured.out
    assert "conformance passed:" in captured.out
    assert captured.err == ""


def test_conformance_adapter_fails_with_exit_one(evalh_home, capsys):
    code = main(
        ["conformance", "--adapter", BROKEN_CAPABILITIES, "--kind", "candidate"]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "candidate/3: FAIL" in captured.out
    assert "expected a Capabilities instance" in captured.out
    assert "conformance failed:" in captured.err


def test_conformance_all_runs_the_manifest(
    evalh_home, http_conformance_stub, provider_conformance_stub, capsys
):
    code = main(["conformance", "--all"])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert (
        "adapter evalharness.adapters.candidates.stub:build (candidate)"
        in captured.out
    )
    assert "adapter evalharness.adapters.judges.rule_based:build (judge)" in (
        captured.out
    )
    assert "conformance passed:" in captured.out


def test_conformance_unknown_adapter_exits_two(evalh_home, capsys):
    code = main(["conformance", "--adapter", "nope:missing", "--kind", "judge"])

    captured = capsys.readouterr()
    assert code == 2
    assert "conformance error:" in captured.err


def test_conformance_adapter_requires_kind(evalh_home, capsys):
    code = main(["conformance", "--adapter", STUB])

    captured = capsys.readouterr()
    assert code == 2
    assert "--adapter requires --kind" in captured.err


def test_conformance_kind_requires_adapter(evalh_home, capsys):
    code = main(["conformance", "--all", "--kind", "candidate"])

    captured = capsys.readouterr()
    assert code == 2
    assert "--kind only applies with --adapter" in captured.err


def test_conformance_requires_a_selection(capsys):
    assert main(["conformance"]) == 2


def test_smoke_demo_still_runs_without_credentials(evalh_home, capsys):
    spec = Path(__file__).parents[1] / "examples" / "specs" / "smoke.yaml"

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "status=ok" in captured.out


def test_stub_pack_demo_runs_without_credentials(evalh_home, capsys):
    spec = Path(__file__).parents[1] / "examples" / "specs" / "stub-pack.yaml"

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "scripted" in captured.out
    assert "always-failing" in captured.out
    assert "status=partial" in captured.out
