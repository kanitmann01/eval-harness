"""HTTP API seam tests for launching and cancelling a run from the console.

Every test drives the ASGI application in process, seeds specs through the
filesystem, and reads back through the same repository the console serves.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import (
    STUB_SLOW,
    base_spec,
    write_spec,
)
from tests.test_api_console import Console, _client, _seed, _two_cases

SLOW_DELAY_MS = 400
"""Long enough that a cancel issued after the first cell lands between cells."""


def _console_spec() -> dict[str, Any]:
    """Return one candidate over two cases, cheap enough to run in a test."""
    spec = base_spec()
    spec["name"] = "console-spec"
    spec["source"]["options"]["cases"] = _two_cases()
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["options"] = {}
    spec["gates"] = [{"metric": "exact_match", "min": 0.5}]
    return spec


def _slow_spec() -> dict[str, Any]:
    """Return three sequential, slow cells so a cancel has cells left to stop."""
    spec = _console_spec()
    spec["name"] = "slow-spec"
    spec["candidates"][0]["adapter"] = STUB_SLOW
    spec["candidates"][0]["options"] = {"delay_ms": SLOW_DELAY_MS}
    spec["source"]["options"]["cases"] = _two_cases()[:1]
    spec["replicas"] = 3
    spec["concurrency"] = 1
    spec["gates"] = []
    return spec


@pytest.fixture
def console(
    evalh_home: Path,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Console]:
    """Seed one CLI run and a specs directory with a fast and a slow spec."""
    specs = workdir / "specs"
    specs.mkdir()
    monkeypatch.setenv("EVALH_SPECS_DIR", str(specs))
    write_spec(specs / "slow.yaml", _slow_spec())
    run_id = _seed(capsys, write_spec(specs / "console.yaml", _console_spec()))
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    app = create_app(settings=settings, repository=repository)
    yield Console(
        settings=settings,
        database=database,
        repository=repository,
        app=app,
        runs={"console": run_id},
    )
    database.dispose()


def _specs_dir() -> Path:
    """Return the specs directory the fixture pointed the console at."""
    return Path(os.environ["EVALH_SPECS_DIR"])


async def _wait_for_status(
    repository: Repository,
    run_id: str,
    statuses: set[str],
    timeout: float = 30.0,
) -> None:
    """Wait until the run reaches one of the statuses the test expects."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = repository.get_run(run_id)
        if run is not None and run.status in statuses:
            return
        await asyncio.sleep(0.02)
    run = repository.get_run(run_id)
    raise AssertionError(f"run never reached {statuses}; last status {run}")


async def _wait_finished(
    console: Console, run_id: str, statuses: set[str]
) -> None:
    """Wait for the final status and for the launch lock to be released."""
    await _wait_for_status(console.repository, run_id, statuses)
    assert await _wait_for_idle(console.app)


async def _run_shutdown_handlers(app: Any) -> None:
    """Invoke every shutdown handler the application registered."""
    for handler in app.router.on_shutdown:
        result = handler()
        if inspect.isawaitable(result):
            await result


async def _wait_for_first_cell(repository: Repository, run_id: str) -> None:
    """Wait until the run has recorded its first cell."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if repository.list_cells(run_id):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the run never recorded a cell")


async def _wait_for_idle(app: Any, timeout: float = 30.0) -> bool:
    """Wait until this console's launch lock is free again."""
    registry = app.state.launches
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if registry.active_run_id() is None:
            return True
        await asyncio.sleep(0.02)
    return False


async def test_launch_page_lists_specs_and_shows_the_estimate(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        page = await client.get("/launch")
        assert page.status_code == 200
        text = page.text
        assert "Launch a run" in text
        assert ">Launch</a>" in text
        assert "console.yaml" in text and "slow.yaml" in text
        assert "Budget override (USD)" in text
        assert "Show estimate" in text
        assert str(_specs_dir()) in text

        estimated = await client.get("/launch", params={"spec": "console.yaml"})
        assert estimated.status_code == 200
        assert "Estimate:" in estimated.text
        assert "2 cells" in estimated.text
        assert "prompt" in estimated.text
        assert "completion tokens per cell" in estimated.text
        assert "$" in estimated.text
        assert 'value="console.yaml"' in estimated.text

        unknown = await client.get("/launch", params={"spec": "missing.yaml"})
        assert unknown.status_code == 200
        assert "The estimate is unavailable" in unknown.text
        assert "no spec named" in unknown.text

        outside = await client.get("/launch", params={"spec": "../outside.yaml"})
        assert outside.status_code == 200
        assert "outside the specs directory" in outside.text


async def test_api_launch_lands_on_the_run_and_streams_to_a_finished_record(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        response = await client.post("/api/runs", json={"spec": "console.yaml"})
        assert response.status_code == 202
        body = response.json()
        run_id = body["run_id"]
        assert body["url"] == f"/runs/{run_id}"
        assert len(run_id) == 32

        detail = await client.get(f"/runs/{run_id}")
        assert detail.status_code == 200
        assert "console-spec" in detail.text

        stream = await client.get(f"/runs/{run_id}/events")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: run_started" in stream.text
        assert stream.text.count("event: cell_completed") == 2
        assert stream.text.count("event: run_finished") == 1
        assert "event: run_cancelled" not in stream.text

        finished = (await client.get(f"/api/runs/{run_id}")).json()
        assert finished["status"] == "ok"
        assert finished["n_cells"] == 2 and finished["n_ok"] == 2
        assert finished["gates"][0]["passed"] is True
        assert finished["aggregates"][0]["n"] == 2

        assert await _wait_for_idle(console.app)
        after = await client.get("/launch")
        assert "already running" not in after.text


async def test_second_launch_is_refused_and_names_the_running_run(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        first = await client.post("/api/runs", json={"spec": "slow.yaml"})
        assert first.status_code == 202
        run_id = first.json()["run_id"]

        second = await client.post("/api/runs", json={"spec": "console.yaml"})
        assert second.status_code == 409
        detail = second.json()["detail"]
        assert run_id in detail
        assert f"POST /api/runs/{run_id}/cancel" in detail

        page = await client.get("/launch")
        assert run_id in page.text
        assert "already running" in page.text

        form = await client.post("/launch", content=b"spec=console.yaml")
        assert form.status_code == 409
        assert run_id in form.text

        run_page = await client.get(f"/runs/{run_id}")
        assert "Cancel run" in run_page.text
        assert f"/runs/{run_id}/cancel\"" in run_page.text

        cancelled = await client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json() == {"run_id": run_id, "cancelling": True}
        await _wait_finished(console, run_id, {"cancelled"})
        assert await _wait_for_idle(console.app)

        again = await client.post("/api/runs", json={"spec": "console.yaml"})
        assert again.status_code == 202
        await _wait_finished(console, again.json()["run_id"], {"ok"})


async def test_cancel_stops_between_cells_and_keeps_partial_results(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        response = await client.post("/api/runs", json={"spec": "slow.yaml"})
        run_id = response.json()["run_id"]
        await _wait_for_first_cell(console.repository, run_id)

        cancel = await client.post(f"/api/runs/{run_id}/cancel")
        assert cancel.status_code == 200
        await _wait_finished(console, run_id, {"cancelled"})

        run = console.repository.get_run(run_id)
        assert run is not None
        assert run.status == "cancelled"
        assert run.finished_at is not None
        cells = console.repository.list_cells(run_id)
        assert len(cells) == 3
        statuses = [cell.status for cell in cells]
        assert "ok" in statuses and "cancelled" in statuses
        assert run.n_ok == statuses.count("ok")
        for cell in cells:
            if cell.status == "ok":
                assert console.repository.list_checks(run_id, cell.cell_id)
            else:
                assert cell.output["reason"] == "cancelled from the console"
                assert console.repository.list_checks(run_id, cell.cell_id) == []

        overall = [
            row
            for row in console.repository.list_aggregates(run_id)
            if row.tier is None
        ]
        assert len(overall) == 1
        assert overall[0].n == run.n_ok and overall[0].value == 1.0

        stream = await client.get(f"/runs/{run_id}/events")
        assert "event: run_cancelled" in stream.text
        frames = [part for part in stream.text.split("\n\n") if part.strip()]
        assert "event: run_finished" in frames[-1]

        repeated = await client.post(f"/api/runs/{run_id}/cancel")
        assert repeated.status_code == 409
        await _wait_for_idle(console.app)


async def test_console_and_cli_launches_produce_equivalent_run_records(
    console: Console,
) -> None:
    cli_run_id = console.runs["console"]
    async with _client(console.app) as client:
        response = await client.post("/api/runs", json={"spec": "console.yaml"})
        assert response.status_code == 202
        console_run_id = response.json()["run_id"]
        await _wait_finished(console, console_run_id, {"ok"})
    assert _record(console.repository, cli_run_id) == _record(
        console.repository, console_run_id
    )


def _record(repository: Repository, run_id: str) -> dict[str, Any]:
    """Return the parts of a run record two launches must agree on."""
    run = repository.get_run(run_id)
    assert run is not None
    stored = repository.get_spec(run.spec_id)
    assert stored is not None
    return {
        "status": run.status,
        "seed": run.seed,
        "harness_sha": run.harness_sha,
        "spec_sha": stored["sha"],
        "spec_snapshot": run.spec_snapshot,
        "prices_snapshot": run.prices_snapshot,
        "n_cells": run.n_cells,
        "n_ok": run.n_ok,
        "cost_usd": run.cost_usd,
        "budget_usd": run.budget_usd,
        "budget_override_usd": run.budget_override_usd,
        "cells": [
            (
                cell.cell_id,
                cell.candidate,
                cell.case_id,
                cell.replica,
                cell.status,
                cell.answer,
                cell.prompt_tokens,
                cell.completion_tokens,
                cell.tool_calls,
                cell.cost_usd,
                cell.usage_source,
                cell.output,
            )
            for cell in repository.list_cells(run_id)
        ],
        "checks": {
            cell_id: [
                (check.metric, check.passed, check.score, check.reason)
                for check in checks
            ]
            for cell_id, checks in repository.list_checks_for_run(run_id).items()
        },
        "aggregates": [
            (
                row.candidate,
                row.metric,
                row.tier,
                row.n,
                row.value,
                row.ci_low,
                row.ci_high,
                row.pass_at_k,
                row.pass_k,
            )
            for row in repository.list_aggregates(run_id)
        ],
        "gates": [
            (
                gate.name,
                gate.metric,
                gate.passed,
                gate.comparison,
                gate.threshold,
                gate.observed,
                gate.n,
            )
            for gate in repository.list_gates(run_id)
        ],
    }


async def test_a_bare_name_resolves_yaml_then_yml(console: Console) -> None:
    write_spec(_specs_dir() / "other.yml", _console_spec())
    async with _client(console.app) as client:
        response = await client.post("/api/runs", json={"spec": "other"})
        assert response.status_code == 202
        await _wait_finished(console, response.json()["run_id"], {"ok"})
        again = await client.post("/api/runs", json={"spec": "other.yml"})
        assert again.status_code == 202
        await _wait_finished(console, again.json()["run_id"], {"ok"})


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({}, "choose a spec"),
        ({"spec": ""}, "choose a spec"),
        ({"spec": "missing.yaml"}, "no spec named"),
        ({"spec": "../outside.yaml"}, "outside the specs directory"),
        ({"spec": "console.yaml", "budget_override_usd": "0.5"}, "must be a number"),
        ({"spec": "console.yaml", "budget_override_usd": True}, "must be a number"),
        (
            {"spec": "console.yaml", "budget_override_usd": {"usd": 1}},
            "must be a number",
        ),
        ({"spec": "console.yaml", "budget_override_usd": -1}, "positive number"),
        ({"spec": "console.yaml", "budget_override_usd": 0}, "positive number"),
    ],
)
async def test_a_refused_launch_is_400_with_a_clear_message(
    console: Console, body: dict[str, Any], fragment: str
) -> None:
    async with _client(console.app) as client:
        response = await client.post("/api/runs", json=body)
        assert response.status_code == 400
        assert fragment in response.json()["detail"]


async def test_an_absolute_path_outside_the_specs_directory_is_400(
    console: Console, workdir: Path
) -> None:
    outside = write_spec(workdir / "elsewhere.yaml", _console_spec())
    async with _client(console.app) as client:
        response = await client.post("/api/runs", json={"spec": str(outside)})
        assert response.status_code == 400
        assert "outside the specs directory" in response.json()["detail"]


async def test_the_form_launch_reports_a_bad_budget_and_redirects_on_success(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        bad = await client.post(
            "/launch", content=b"spec=console.yaml&budget_override_usd=abc"
        )
        assert bad.status_code == 400
        assert "budget_override_usd must be a number" in bad.text
        assert 'value="abc"' in bad.text

        infinite = await client.post(
            "/launch", content=b"spec=console.yaml&budget_override_usd=inf"
        )
        assert infinite.status_code == 400
        assert "positive number" in infinite.text

        missing = await client.post("/launch", content=b"spec=nope.yaml")
        assert missing.status_code == 400
        assert "no spec named" in missing.text

        launched = await client.post(
            "/launch",
            content=b"spec=console.yaml&budget_override_usd=2",
            follow_redirects=False,
        )
        assert launched.status_code == 303
        location = launched.headers["location"]
        assert "/runs/" in location
        run_id = location.rstrip("/").rsplit("/", 1)[-1]
        await _wait_finished(console, run_id, {"ok"})
        run = console.repository.get_run(run_id)
        assert run.budget_override_usd == 2
        assert run.budget_usd == 2

        landed = await client.get(location)
        assert landed.status_code == 200
        assert "console-spec" in landed.text


async def test_a_launch_that_fails_while_starting_is_400_and_releases_the_lock(
    console: Console,
) -> None:
    broken = _console_spec()
    broken["sink"] = {"adapter": "test:boom_sink", "options": {}}
    write_spec(_specs_dir() / "broken.yaml", broken)
    async with _client(console.app) as client:
        response = await client.post("/api/runs", json={"spec": "broken.yaml"})
        assert response.status_code == 400
        assert "sink factory" in response.json()["detail"]
        assert await _wait_for_idle(console.app)
        assert console.repository.list_runs(), "only the seeded run remains"

        second = await client.post("/api/runs", json={"spec": "console.yaml"})
        assert second.status_code == 202
        await _wait_finished(console, second.json()["run_id"], {"ok"})


async def test_an_over_budget_launch_is_400_and_records_nothing(
    console: Console,
) -> None:
    tight = _console_spec()
    tight["budget_usd"] = 0.0001
    write_spec(_specs_dir() / "tight.yaml", tight)
    before = len(console.repository.list_runs())
    async with _client(console.app) as client:
        refused = await client.post("/api/runs", json={"spec": "tight.yaml"})
        assert refused.status_code == 400
        assert "exceeds the cap" in refused.json()["detail"]
        assert len(console.repository.list_runs()) == before

        overridden = await client.post(
            "/api/runs",
            json={"spec": "tight.yaml", "budget_override_usd": 5},
        )
        assert overridden.status_code == 202
        run_id = overridden.json()["run_id"]
        await _wait_finished(console, run_id, {"ok"})
        run = console.repository.get_run(run_id)
        assert run.budget_override_usd == 5
        assert run.budget_usd == 5


async def test_cancel_refuses_a_run_this_console_is_not_driving(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        idle = await client.post(f"/api/runs/{console.runs['console']}/cancel")
        assert idle.status_code == 409
        assert "no run is in flight" in idle.json()["detail"]

        started = await client.post("/api/runs", json={"spec": "slow.yaml"})
        run_id = started.json()["run_id"]
        other = await client.post("/api/runs/not-a-run/cancel")
        assert other.status_code == 409
        assert run_id in other.json()["detail"]
        assert f"POST /api/runs/{run_id}/cancel" in other.json()["detail"]

        refused = await client.post(
            f"/runs/{console.runs['console']}/cancel", follow_redirects=False
        )
        assert refused.status_code == 409
        assert "not being driven by this console" in refused.text
        assert f"/runs/{console.runs['console']}" in refused.text

        requested = await client.post(
            f"/runs/{run_id}/cancel", follow_redirects=False
        )
        assert requested.status_code == 303
        assert requested.headers["location"].endswith(f"/runs/{run_id}")
        await _wait_finished(console, run_id, {"cancelled"})


async def test_another_console_sees_the_run_in_flight_but_cannot_cancel_it(
    console: Console,
) -> None:
    other = create_app(settings=console.settings, repository=console.repository)
    async with _client(console.app) as client, _client(other) as other_client:
        started = await client.post("/api/runs", json={"spec": "slow.yaml"})
        run_id = started.json()["run_id"]
        await _wait_for_first_cell(console.repository, run_id)

        page = await other_client.get(f"/runs/{run_id}")
        assert page.status_code == 200
        assert "Cancel run" in page.text
        refused = await other_client.post(f"/api/runs/{run_id}/cancel")
        assert refused.status_code == 409
        assert run_id in refused.json()["detail"]

        await client.post(f"/api/runs/{run_id}/cancel")
        await _wait_finished(console, run_id, {"cancelled"})


async def test_run_pages_render_the_read_only_content_and_404s(
    console: Console,
) -> None:
    run_id = console.runs["console"]
    async with _client(console.app) as client:
        page = await client.get(f"/runs/{run_id}")
        assert page.status_code == 200
        assert "Candidate by case" in page.text
        assert "data-cell-id=" in page.text
        assert "Cancel run" not in page.text

        cell = console.repository.list_cells(run_id)[0]
        by_cell = await client.get(f"/runs/{run_id}/cells/{cell.cell_id}")
        assert by_cell.status_code == 200
        assert cell.cell_id in by_cell.text

        assert (await client.get("/runs/not-a-run")).status_code == 404
        missing = await client.get(f"/runs/{run_id}/cells/not-a-cell")
        assert missing.status_code == 404
        assert "is not part of run" in missing.text


async def test_shutdown_cancels_and_joins_a_running_launch(
    console: Console,
) -> None:
    assert console.app.router.on_shutdown, "the launch module registers a handler"
    async with _client(console.app) as client:
        await client.get("/launch")
        await _run_shutdown_handlers(console.app)

        started = await client.post("/api/runs", json={"spec": "slow.yaml"})
        run_id = started.json()["run_id"]
        await _wait_for_first_cell(console.repository, run_id)
        await _run_shutdown_handlers(console.app)

    run = console.repository.get_run(run_id)
    assert run is not None
    assert run.status == "cancelled"
    assert console.app.state.launches.active_run_id() is None
    cancelled = [
        cell
        for cell in console.repository.list_cells(run_id)
        if cell.status == "cancelled"
    ]
    assert cancelled
    assert {cell.output["reason"] for cell in cancelled} == {
        "console shutting down"
    }


async def test_the_launch_page_falls_back_to_a_text_input_without_specs(
    evalh_home: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVALH_SPECS_DIR", str(workdir / "no-specs-here"))
    app = create_app()
    try:
        async with _client(app) as client:
            page = await client.get("/launch")
            assert page.status_code == 200
            assert "No specs were found" in page.text
            assert 'type="text"' in page.text
            assert "0 specs" in page.text
            assert "EVALH_SPECS_DIR" in page.text
    finally:
        app.state.database.dispose()


async def test_the_specs_directory_defaults_next_to_the_database(
    evalh_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVALH_SPECS_DIR", raising=False)
    specs = evalh_home / "specs"
    specs.mkdir(parents=True)
    write_spec(specs / "default.yaml", _console_spec())
    settings = Settings.from_env()
    assert settings.specs_dir == specs
    app = create_app(settings=settings)
    try:
        async with _client(app) as client:
            page = await client.get("/launch")
            assert page.status_code == 200
            assert "default.yaml" in page.text
            assert str(specs.resolve()) in page.text
    finally:
        app.state.database.dispose()
