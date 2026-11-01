"""scripts/demo.py as a subprocess: --help and one full offline demo home.

The demo must never touch the network, never need a paid key and never print the
generated vault key. The full run copies the four bundled specs into a temporary
home, runs them through the real CLI, and finishes without starting a server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "scripts" / "demo.py"
SPECS = ("smoke.yaml", "stub-pack.yaml", "gated.yaml", "gated-failing.yaml")


def _clean_env() -> dict[str, str]:
    """A subprocess environment with no inherited harness settings."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("EVALH_")}
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _run(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    """Run the demo script in the repository with an isolated environment."""
    return subprocess.run(
        [sys.executable, str(DEMO), *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_env(),
    )


def test_demo_help_documents_every_flag() -> None:
    """--help works without a home, a key or a server and names the flags."""
    completed = _run("--help", timeout=30)
    assert completed.returncode == 0, completed.stderr
    for flag in ("--home", "--port", "--no-serve", "--no-run", "--reset"):
        assert flag in completed.stdout, f"--help does not document {flag}"


def test_demo_runs_all_four_specs_offline(tmp_path: Path) -> None:
    """One --no-serve run leaves specs, a key file, four run ids and no leaked key."""
    started = time.monotonic()
    home = tmp_path / "demo-home"
    completed = _run("--home", str(home), "--no-serve")
    assert completed.returncode == 0, completed.stderr
    output = completed.stdout + completed.stderr
    for name in SPECS:
        assert name in output, f"demo output does not mention {name}"
    assert "exit 1" in output, "demo does not report the expected gate failure"
    assert "http://127.0.0.1:8000/" in output
    assert "talk track" in output.lower()

    for name in SPECS:
        assert (home / "specs" / name).is_file()
    env_text = (home / "demo.env").read_text(encoding="utf-8")
    assert "EVALH_MASTER_KEY=" in env_text
    key = env_text.split("EVALH_MASTER_KEY=", 1)[1].splitlines()[0].strip()
    assert len(key) == 44
    assert key not in output, "the demo printed the generated vault key"
    assert (home / "harness.db").is_file()
    assert time.monotonic() - started < 90, "the offline demo run is too slow"


def test_demo_no_run_skips_execution(tmp_path: Path) -> None:
    """--no-run prepares the home but runs nothing and needs no run ids."""
    home = tmp_path / "demo-home"
    completed = _run("--home", str(home), "--no-run", "--no-serve")
    assert completed.returncode == 0, completed.stderr
    assert (home / "demo.env").is_file()
    for name in SPECS:
        assert (home / "specs" / name).is_file()
    assert "run id" not in completed.stdout.lower()
    assert not (home / "harness.db").exists()


def test_demo_reset_replaces_an_existing_home(tmp_path: Path) -> None:
    """--reset clears a stale demo home before the four specs are copied."""
    home = tmp_path / "demo-home"
    home.mkdir()
    stale = home / "stale.txt"
    stale.write_text("old home", encoding="utf-8")
    completed = _run("--home", str(home), "--no-run", "--no-serve", "--reset")
    assert completed.returncode == 0, completed.stderr
    assert not stale.exists()
    assert (home / "demo.env").is_file()
