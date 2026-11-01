"""Calibration console tests: real CLI-seeded runs driven over HTTP.

Every run is produced by the CLI in a disposable home, then the page and JSON
surfaces are read through an in-process ASGI client. The labels written by the
forms are verified through the storage API the console itself uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
from sqlalchemy import text

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.cli import main
from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.labels import LabelStore
from evalharness.storage.repository import Repository
from tests.helpers import base_spec, run_id_from, write_spec

METRIC = "exact_match"


@dataclass
class CalibrationConsole:
    """An application built on a database seeded through the CLI."""

    settings: Settings
    database: Database
    repository: Repository
    app: Any
    runs: dict[str, str]


def _seed(capsys: pytest.CaptureFixture[str], path: Path, expected: int = 0) -> str:
    """Run the CLI against path and return the run id it printed."""
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == expected, captured.err
    return run_id_from(captured.out)


def _client(app: Any) -> httpx.AsyncClient:
    """Build an in-process HTTP client for one application."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console.test"
    )


@pytest.fixture
def console(
    evalh_home: Path,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[CalibrationConsole]:
    """Seed one all-pass run and one wrong-answer run."""
    monkeypatch.delenv("EVALH_OPERATOR", raising=False)
    alpha = base_spec()
    alpha["name"] = "calibration-alpha"
    alpha_id = _seed(capsys, write_spec(workdir / "alpha.yaml", alpha))

    beta = base_spec()
    beta["name"] = "calibration-beta"
    beta["candidates"] = [beta["candidates"][0]]
    beta["candidates"][0]["name"] = "wrong-stub"
    beta["candidates"][0]["options"] = {"answer": "wrong"}
    beta_id = _seed(capsys, write_spec(workdir / "beta.yaml", beta))

    settings = Settings.from_env()
    # Build the app the way serve does: it opens its own database and creates
    # the missing tables, so the label routes read the same database the
    # fixture asserts on.
    app = create_app(settings=settings)
    database = app.state.database
    repository = app.state.repository
    try:
        yield CalibrationConsole(
            settings=settings,
            database=database,
            repository=repository,
            app=app,
            runs={"alpha": alpha_id, "beta": beta_id},
        )
    finally:
        database.dispose()


def _store(console: CalibrationConsole) -> LabelStore:
    """Return a label store over the fixture database."""
    return LabelStore(console.database)


def _cell(console: CalibrationConsole, run_id: str) -> str:
    """Return the first stored cell id of one run."""
    return console.repository.list_cells(run_id)[0].cell_id


def _label_form(
    cell_id: str,
    metric: str = METRIC,
    verdict: str = "pass",
    note: str = "",
) -> dict[str, str]:
    """Return the form fields the save control submits."""
    return {
        "cell_id": cell_id,
        "metric": metric,
        "verdict": verdict,
        "note": note,
    }


async def test_page_renders_empty_state_and_review_rows(console) -> None:
    alpha = console.runs["alpha"]
    cells = console.repository.list_cells(alpha)

    async with _client(console.app) as client:
        response = await client.get(f"/runs/{alpha}/calibration")

    assert response.status_code == 200
    text = response.text
    assert "Calibration" in text
    assert "0 labelled" in text
    assert f"{len(cells)} unlabelled" in text
    assert "No human verdicts recorded yet." in text
    assert "exact_match" in text
    assert "Save human verdict" in text
    assert "Clear human verdict" in text
    assert "No disagreements." in text


async def test_save_agreeing_verdict_redirects_and_updates_the_page(console) -> None:
    alpha = console.runs["alpha"]
    cell_id = _cell(console, alpha)

    async with _client(console.app) as client:
        response = await client.post(
            f"/runs/{alpha}/calibration/labels",
            data=_label_form(cell_id, note="looks right"),
        )
        page = await client.get(f"/runs/{alpha}/calibration")

    assert response.status_code == 303
    assert response.headers["location"] == f"/runs/{alpha}/calibration"
    stored = _store(console).get(alpha, cell_id, METRIC)
    assert stored is not None
    assert stored.passed is True
    assert stored.note == "looks right"
    assert stored.operator == "not recorded"
    text = page.text
    assert "1 labelled" in text
    assert "100.0%" in text
    assert "1 unlabelled" in text
    assert "looks right" in text
    assert "not recorded" in text
    assert "No disagreements." in text
    assert "Last human label" in text


async def test_save_disagreeing_verdict_renders_the_disagreement_panel(console) -> None:
    beta = console.runs["beta"]
    cell_id = _cell(console, beta)

    async with _client(console.app) as client:
        response = await client.post(
            f"/runs/{beta}/calibration/labels",
            data=_label_form(cell_id, note="I checked this by hand"),
        )
        page = await client.get(f"/runs/{beta}/calibration")

    assert response.status_code == 303
    text = page.text
    assert "1 disagreement" in text
    assert "I checked this by hand" in text
    assert "Open cell" in text
    assert f"/runs/{beta}/cells/{cell_id}" in text
    assert f"{len(console.repository.list_cells(beta))} labelled" in text
    assert "0.0%" in text
    assert "No disagreements." not in text


async def test_updating_the_same_label_keeps_one_row(console) -> None:
    alpha = console.runs["alpha"]
    cell_id = _cell(console, alpha)
    async with _client(console.app) as client:
        await client.post(
            f"/runs/{alpha}/calibration/labels",
            data=_label_form(cell_id, verdict="fail", note="first pass"),
        )
        await client.post(
            f"/runs/{alpha}/calibration/labels",
            data=_label_form(cell_id, verdict="pass", note="updated after review"),
        )
        page = await client.get(f"/runs/{alpha}/calibration")

    labels = _store(console).list_for_run(alpha)
    assert len(labels) == 1
    assert labels[0].passed is True
    assert labels[0].note == "updated after review"
    assert "updated after review" in page.text
    assert "100.0%" in page.text


async def test_clear_verdict_removes_the_label_and_is_idempotent(console) -> None:
    alpha = console.runs["alpha"]
    cell_id = _cell(console, alpha)
    target = f"/runs/{alpha}/calibration/labels/clear"
    async with _client(console.app) as client:
        await client.post(
            f"/runs/{alpha}/calibration/labels",
            data=_label_form(cell_id),
        )
        first = await client.post(
            target, data={"cell_id": cell_id, "metric": METRIC}
        )
        again = await client.post(
            target, data={"cell_id": cell_id, "metric": METRIC}
        )
        page = await client.get(f"/runs/{alpha}/calibration")

    assert first.status_code == 303
    assert first.headers["location"] == f"/runs/{alpha}/calibration"
    assert again.status_code == 303
    assert _store(console).list_for_run(alpha) == []
    assert "No human verdicts recorded yet." in page.text


async def test_json_shape_reports_agreement_and_review_rows(console) -> None:
    beta = console.runs["beta"]
    cell_id = _cell(console, beta)
    async with _client(console.app) as client:
        await client.post(
            f"/runs/{beta}/calibration/labels",
            data=_label_form(cell_id, verdict="pass", note="human says pass"),
        )
        response = await client.get(f"/api/runs/{beta}/calibration")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == beta
    assert body["labelled"] == 1
    assert body["unlabelled"] == 0
    assert body["agreements"] == 0
    assert body["agreement_rate"] == 0.0
    assert body["interval"]["low"] == body["ci_low"]
    assert body["interval"]["high"] == body["ci_high"]
    assert isinstance(body["last_validated_at"], str)
    assert isinstance(body["judges"], list)
    judge = body["judges"][0]
    assert judge["name"] is None
    assert judge["model"] is None
    assert judge["config_hash"] is None
    assert judge["labelled"] == 1
    assert judge["agreements"] == 0
    assert judge["agreement_rate"] == 0.0
    assert isinstance(judge["last_validated_at"], str)
    assert len(body["disagreements"]) == 1
    disagreement = body["disagreements"][0]
    assert disagreement["case_id"] == "case-1"
    assert disagreement["candidate"] == "wrong-stub"
    assert disagreement["metric"] == METRIC
    assert disagreement["judge_passed"] is False
    assert disagreement["human_passed"] is True
    assert disagreement["human_note"] == "human says pass"
    assert disagreement["cell_url"] == f"/runs/{beta}/cells/{cell_id}"
    assert body["checks"][0]["cell_id"] == cell_id


async def test_json_empty_state_has_null_rate_and_interval(console) -> None:
    alpha = console.runs["alpha"]
    async with _client(console.app) as client:
        response = await client.get(f"/api/runs/{alpha}/calibration")

    assert response.status_code == 200
    body = response.json()
    assert body["labelled"] == 0
    assert body["unlabelled"] == 2
    assert body["agreements"] == 0
    assert body["agreement_rate"] is None
    assert body["ci_low"] is None
    assert body["ci_high"] is None
    assert body["interval"] == {"low": None, "high": None}
    assert body["last_validated_at"] is None
    assert body["disagreements"] == []
    assert all(row["human_passed"] is None for row in body["checks"])


async def test_unknown_run_is_a_not_found_page_and_json_error(console) -> None:
    async with _client(console.app) as client:
        page = await client.get("/runs/nope/calibration")
        body = await client.get("/api/runs/nope/calibration")
        posted = await client.post(
            "/runs/nope/calibration/labels",
            data=_label_form("cell-1"),
        )
        cleared = await client.post(
            "/runs/nope/calibration/labels/clear",
            data={"cell_id": "cell-1", "metric": METRIC},
        )

    assert page.status_code == 404
    assert "run nope does not exist" in page.text
    assert body.status_code == 404
    assert body.json() == {"detail": "run nope does not exist"}
    assert posted.status_code == 404
    assert "run nope does not exist" in posted.text
    assert cleared.status_code == 404
    assert "run nope does not exist" in cleared.text


async def test_invalid_cell_metric_and_verdict_are_actionable_400s(console) -> None:
    alpha = console.runs["alpha"]
    beta = console.runs["beta"]
    alpha_cell = _cell(console, alpha)
    beta_cell = _cell(console, beta)
    async with _client(console.app) as client:
        wrong_run = await client.post(
            f"/runs/{beta}/calibration/labels",
            data=_label_form(alpha_cell),
        )
        wrong_metric = await client.post(
            f"/runs/{alpha}/calibration/labels",
            data=_label_form(alpha_cell, metric="missing_metric"),
        )
        wrong_verdict = await client.post(
            f"/runs/{alpha}/calibration/labels",
            data=_label_form(alpha_cell, verdict="maybe"),
        )
        wrong_clear = await client.post(
            f"/runs/{beta}/calibration/labels/clear",
            data={"cell_id": alpha_cell, "metric": METRIC},
        )

    assert wrong_run.status_code == 400
    assert "cell_id" in wrong_run.text
    assert "is not part of run" in wrong_run.text
    assert "Fix:" in wrong_run.text
    assert wrong_metric.status_code == 400
    assert "metric" in wrong_metric.text
    assert "has no stored check" in wrong_metric.text
    assert wrong_verdict.status_code == 400
    assert "verdict" in wrong_verdict.text
    assert "must be pass or fail" in wrong_verdict.text
    assert wrong_clear.status_code == 400
    assert beta_cell not in (alpha_cell,)
    assert _store(console).list_for_run(alpha) == []
    assert _store(console).list_for_run(beta) == []


async def test_reopening_an_old_database_adds_the_label_table(console) -> None:
    alpha = console.runs["alpha"]
    cell_id = _cell(console, alpha)
    with console.database.engine.begin() as connection:
        connection.execute(text("DROP TABLE human_labels"))

    reopened = create_app(settings=console.settings)
    try:
        async with _client(reopened) as client:
            page = await client.get(f"/runs/{alpha}/calibration")
            posted = await client.post(
                f"/runs/{alpha}/calibration/labels",
                data=_label_form(cell_id, note="written after the upgrade"),
            )
    finally:
        reopened.state.database.dispose()

    assert page.status_code == 200
    assert posted.status_code == 303
    stored = _store(console).get(alpha, cell_id, METRIC)
    assert stored is not None
    assert stored.note == "written after the upgrade"


async def test_operator_comes_from_the_environment_stripped_or_not_recorded(
    console, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = console.runs["alpha"]
    beta = console.runs["beta"]
    alpha_cell = _cell(console, alpha)
    beta_cell = _cell(console, beta)
    async with _client(console.app) as client:
        monkeypatch.setenv("EVALH_OPERATOR", "  alice  ")
        named = await client.post(
            f"/runs/{alpha}/calibration/labels",
            data=_label_form(alpha_cell, note="named"),
        )
        named_page = await client.get(f"/runs/{alpha}/calibration")
        monkeypatch.delenv("EVALH_OPERATOR")
        anonymous = await client.post(
            f"/runs/{beta}/calibration/labels",
            data=_label_form(beta_cell, note="anonymous"),
        )

    assert named.status_code == 303
    assert anonymous.status_code == 303
    assert _store(console).get(alpha, alpha_cell, METRIC).operator == "alice"
    assert (
        _store(console).get(beta, beta_cell, METRIC).operator == "not recorded"
    )
    assert "alice" in named_page.text
