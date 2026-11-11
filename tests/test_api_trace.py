"""HTTP seam tests for the trace page and its replay panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from evalharness.api.app import create_app
from evalharness.cli import main
from tests.helpers import base_spec, run_id_from, write_spec


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console.test"
    )


@pytest.fixture
def traced_run(
    evalh_home: Path,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[tuple[Any, str]]:
    spec = base_spec()
    spec["name"] = "trace-spec"
    spec["candidates"][0]["options"]["answer"] = "42"
    spec["candidates"][1]["options"]["answer"] = "42 because 6 times 7 is 42"
    path = write_spec(workdir / "trace.yaml", spec)
    assert main(["run", str(path)]) == 0
    captured = capsys.readouterr()
    run_id = run_id_from(captured.out)
    yield create_app(), run_id


async def test_trace_shows_every_candidate_and_effective_prompt(
    traced_run: tuple[Any, str],
) -> None:
    app, run_id = traced_run
    async with _client(app) as client:
        response = await client.get(f"/runs/{run_id}/trace")
    assert response.status_code == 200
    body = response.text
    assert "Trace" in body
    assert "stub-concise" in body
    assert "stub-explained" in body
    assert "Effective prompt" in body
    assert "Answer with digits only." in body
    assert "Answer with a short clause." in body
    assert "policy-1" in body
    assert "exact_match" in body
    assert "Word diff" in body
    assert "differs" in body
    assert "replay-panel" not in body


async def test_trace_replay_panel_carries_the_recorded_events(
    traced_run: tuple[Any, str],
) -> None:
    app, run_id = traced_run
    async with _client(app) as client:
        response = await client.get(f"/runs/{run_id}/trace?replay=1")
    assert response.status_code == 200
    body = response.text
    assert 'id="replay-panel"' in body
    assert "recorded run" in body
    assert "timings simulated" in body
    assert '"kind": "run_started"' in body
    assert '"kind": "run_finished"' in body
    assert "replay.js" not in body


async def test_unknown_run_is_the_console_404(traced_run: tuple[Any, str]) -> None:
    app, _ = traced_run
    async with _client(app) as client:
        response = await client.get("/runs/no-such-run/trace")
    assert response.status_code == 404
    assert "does not exist" in response.text
