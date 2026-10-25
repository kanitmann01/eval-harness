"""The smoke script as a subprocess: a dead URL and a real in-process server.

This file starts uvicorn on an ephemeral loopback port, points the script at it,
and lets the script launch one no-credential run through the API. No Docker and
no paid key is involved; the separate packaging test asserts the image wiring.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
import uvicorn

from evalharness.api.app import create_app

REPO = Path(__file__).resolve().parents[1]
SMOKE = REPO / "scripts" / "smoke.py"


def _free_port() -> int:
    """Bind an ephemeral loopback port and release it for the next listener."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def running_harness(
    evalh_home: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Serve the app on a real loopback port with the bundled no-key specs."""
    specs = workdir / "specs"
    specs.mkdir()
    for bundled in ("smoke.yaml", "stub-pack.yaml"):
        shutil.copy(REPO / "examples" / "specs" / bundled, specs / bundled)
    monkeypatch.setenv("EVALH_SPECS_DIR", str(specs))
    app = create_app()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() >= deadline:
            raise AssertionError("the test server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        if app.state.database is not None:
            app.state.database.dispose()


def test_smoke_fails_with_an_actionable_message_against_a_dead_url() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SMOKE),
            f"http://127.0.0.1:{_free_port()}",
            "--timeout",
            "5",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 1
    assert "smoke: FAIL" in completed.stderr
    assert "cannot reach the harness" in completed.stderr
    assert "evalh serve" in completed.stderr


@pytest.mark.parametrize(
    ("spec", "expected_status"),
    [("smoke.yaml", "ok"), ("stub-pack.yaml", "partial")],
)
def test_smoke_passes_against_an_in_process_harness(
    running_harness: str, spec: str, expected_status: str
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SMOKE),
            running_harness,
            "--spec",
            spec,
            "--timeout",
            "60",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ),
    )
    assert completed.returncode == 0, completed.stderr
    assert "smoke: ok" in completed.stdout
    assert "health: 200" in completed.stdout
    assert "database=ok" in completed.stdout
    assert f"status={expected_status}" in completed.stdout
    assert "checks=" in completed.stdout
    assert "/runs/" in completed.stdout
