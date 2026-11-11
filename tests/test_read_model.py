"""Behaviour tests for the one run read model.

Each console surface is driven over a repository that records the read calls it
makes, so a surface that re-reads a table shows up as a second call. One CLI
test listens to SQLite to prove the write side no longer queries the checks
table cell by cell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.cli import main
from evalharness.core.config import Settings
from evalharness.core.events import Event
from evalharness.core.models import (
    Aggregate,
    CellRecord,
    Check,
    GateResult,
    RunRecord,
)
from evalharness.read_model import RunRead, load_run_read, run_diff_data
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository, RunSummary
from tests.helpers import base_spec, run_id_from, write_spec

FINISHED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
"""A fixed finish time; the read model is clock-free."""


class RecordingRepository(Repository):
    """A repository that records the name of every read method called."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.calls: list[str] = []

    def clear(self) -> None:
        """Forget the calls recorded so far."""
        self.calls.clear()

    def count(self, name: str) -> int:
        """Return how many times one read method was called."""
        return self.calls.count(name)

    def get_run(self, run_id: str) -> RunRecord | None:
        """Record and perform one run read."""
        self.calls.append("get_run")
        return super().get_run(run_id)

    def list_cells(self, run_id: str) -> list[CellRecord]:
        """Record and perform one cell list read."""
        self.calls.append("list_cells")
        return super().list_cells(run_id)

    def list_checks(self, run_id: str, cell_id: str) -> list[Check]:
        """Record and perform one per-cell check read."""
        self.calls.append("list_checks")
        return super().list_checks(run_id, cell_id)

    def list_checks_for_run(self, run_id: str) -> dict[str, list[Check]]:
        """Record and perform one run-wide check read."""
        self.calls.append("list_checks_for_run")
        return super().list_checks_for_run(run_id)

    def list_aggregates(self, run_id: str) -> list[Aggregate]:
        """Record and perform one aggregate read."""
        self.calls.append("list_aggregates")
        return super().list_aggregates(run_id)

    def list_gates(self, run_id: str) -> list[GateResult]:
        """Record and perform one gate read."""
        self.calls.append("list_gates")
        return super().list_gates(run_id)

    def list_events(self, run_id: str) -> list[Event]:
        """Record and perform one event read."""
        self.calls.append("list_events")
        return super().list_events(run_id)

    def list_run_summaries(
        self,
        candidate: str | None = None,
        metric: str | None = None,
    ) -> list[RunSummary]:
        """Record and perform one run list read."""
        self.calls.append("list_run_summaries")
        return super().list_run_summaries(candidate, metric)


@dataclass
class ReadConsole:
    """An application built on a recording repository over seeded CLI runs."""

    repository: RecordingRepository
    database: Database
    app: Any
    runs: dict[str, str]


def _client(app: Any) -> httpx.AsyncClient:
    """Build an in-process HTTP client for one application."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console.test"
    )


def _seed(capsys: pytest.CaptureFixture[str], path: Path) -> str:
    """Run one spec through the CLI and return the run id it printed."""
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return run_id_from(captured.out)


@pytest.fixture
def read_console(
    evalh_home: Path,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[ReadConsole]:
    """Seed two passing runs, then serve them from a recording repository."""
    first = base_spec()
    first["name"] = "read-alpha"
    first_id = _seed(capsys, write_spec(workdir / "alpha.yaml", first))

    second = base_spec()
    second["name"] = "read-beta"
    second["seed"] = 8
    second_id = _seed(capsys, write_spec(workdir / "beta.yaml", second))

    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = RecordingRepository(database)
    app = create_app(settings=settings, repository=repository)
    yield ReadConsole(
        repository=repository,
        database=database,
        app=app,
        runs={"alpha": first_id, "beta": second_id},
    )
    database.dispose()


async def test_run_json_reads_each_run_table_once(read_console: ReadConsole) -> None:
    """The JSON run detail loads its five tables once and never per cell."""
    read_console.repository.clear()
    async with _client(read_console.app) as client:
        response = await client.get(
            f"/api/runs/{read_console.runs['alpha']}"
        )
    assert response.status_code == 200
    repository = read_console.repository
    assert repository.count("get_run") == 1
    assert repository.count("list_cells") == 1
    assert repository.count("list_checks_for_run") == 1
    assert repository.count("list_aggregates") == 1
    assert repository.count("list_gates") == 1
    assert repository.count("list_checks") == 0
    assert repository.count("list_events") == 0


async def test_run_page_reads_each_run_table_once(read_console: ReadConsole) -> None:
    """The run page loads its rows once, including the event stream."""
    read_console.repository.clear()
    async with _client(read_console.app) as client:
        response = await client.get(f"/runs/{read_console.runs['alpha']}")
    assert response.status_code == 200
    repository = read_console.repository
    assert repository.count("get_run") == 1
    assert repository.count("list_cells") == 1
    assert repository.count("list_checks_for_run") == 1
    assert repository.count("list_aggregates") == 1
    assert repository.count("list_gates") == 1
    assert repository.count("list_events") == 1
    assert repository.count("list_checks") == 0


async def test_cell_json_and_selected_page_avoid_per_cell_checks(
    read_console: ReadConsole,
) -> None:
    """The cell JSON and one selected cell read checks once for the whole run."""
    run_id = read_console.runs["alpha"]
    async with _client(read_console.app) as client:
        read_console.repository.clear()
        cells_response = await client.get(f"/api/runs/{run_id}/cells")
        assert cells_response.status_code == 200
        cells = cells_response.json()["cells"]
        repository = read_console.repository
        assert repository.count("list_checks_for_run") == 1
        assert repository.count("list_checks") == 0

        read_console.repository.clear()
        page = await client.get(f"/runs/{run_id}", params={"cell": cells[0]["cell_id"]})
    assert page.status_code == 200
    assert cells[0]["cell_id"] in page.text
    assert read_console.repository.count("list_checks_for_run") == 1
    assert read_console.repository.count("list_checks") == 0


async def test_decision_surfaces_use_the_run_read(
    read_console: ReadConsole,
) -> None:
    """The decision page and JSON read the run once, never per cell."""
    run_id = read_console.runs["alpha"]
    async with _client(read_console.app) as client:
        for path in (f"/runs/{run_id}/decision", f"/api/runs/{run_id}/decision"):
            read_console.repository.clear()
            response = await client.get(path)
            assert response.status_code == 200, path
            repository = read_console.repository
            assert repository.count("get_run") == 1, path
            assert repository.count("list_checks_for_run") == 1, path
            assert repository.count("list_checks") == 0, path


async def test_calibration_surfaces_use_the_run_read(
    read_console: ReadConsole,
) -> None:
    """The calibration page and JSON read the run once, never per cell."""
    run_id = read_console.runs["alpha"]
    async with _client(read_console.app) as client:
        for path in (
            f"/runs/{run_id}/calibration",
            f"/api/runs/{run_id}/calibration",
        ):
            read_console.repository.clear()
            response = await client.get(path)
            assert response.status_code == 200, path
            repository = read_console.repository
            assert repository.count("get_run") == 1, path
            assert repository.count("list_checks_for_run") == 1, path
            assert repository.count("list_checks") == 0, path


async def test_compare_reads_each_run_once(read_console: ReadConsole) -> None:
    """One compare request reads each side once, in the page and in the JSON."""
    alpha = read_console.runs["alpha"]
    beta = read_console.runs["beta"]
    async with _client(read_console.app) as client:
        for path in (
            f"/compare?a={alpha}&b={beta}",
            f"/api/compare?a={alpha}&b={beta}",
        ):
            read_console.repository.clear()
            response = await client.get(path)
            assert response.status_code == 200, path
            repository = read_console.repository
            assert repository.count("get_run") == 2, path
            assert repository.count("list_cells") == 2, path
            assert repository.count("list_checks_for_run") == 2, path
            assert repository.count("list_gates") == 2, path
            assert repository.count("list_checks") == 0, path


async def test_compare_same_run_reads_it_once(read_console: ReadConsole) -> None:
    """Candidate mode compares two candidates of one run with one read."""
    alpha = read_console.runs["alpha"]
    read_console.repository.clear()
    async with _client(read_console.app) as client:
        response = await client.get(
            "/compare",
            params={
                "run": alpha,
                "candidate_a": "stub-concise",
                "candidate_b": "stub-explained",
            },
        )
    assert response.status_code == 200
    repository = read_console.repository
    assert repository.count("get_run") == 1
    assert repository.count("list_cells") == 1
    assert repository.count("list_checks_for_run") == 1
    assert repository.count("list_checks") == 0


async def test_run_page_and_json_agree_on_duration_and_pass_rate(
    read_console: ReadConsole,
) -> None:
    """The page and the JSON detail report the same duration and pass rate."""
    run_id = read_console.runs["alpha"]
    async with _client(read_console.app) as client:
        detail = (await client.get(f"/api/runs/{run_id}")).json()
        page = await client.get(f"/runs/{run_id}")
    record = Repository(read_console.database).get_run(run_id)
    assert record is not None
    expected = (record.finished_at - record.started_at).total_seconds()
    assert detail["duration_s"] == pytest.approx(expected)
    assert f"{detail['duration_s']:.1f}s" in page.text
    assert detail["pass_rate"] is not None
    assert f"{detail['pass_rate'] * 100:.1f}%" in page.text


async def test_unknown_run_is_one_read_and_no_rows(read_console: ReadConsole) -> None:
    """An unknown run stops at the run read, for both JSON and the page."""
    async with _client(read_console.app) as client:
        read_console.repository.clear()
        json_response = await client.get("/api/runs/missing")
        assert json_response.status_code == 404
        assert read_console.repository.calls == ["get_run"]

        read_console.repository.clear()
        page = await client.get("/runs/missing")
    assert page.status_code == 404
    assert read_console.repository.calls == ["get_run"]


def _read(run: RunRecord, checks: list[Check]) -> RunRead:
    """Build one loaded run for the pure read-model tests."""
    return RunRead(
        run=run,
        cells=[],
        checks_by_cell={"cell-1": checks} if checks else {},
        aggregates=[],
        gates=[],
        events=[],
    )


def _judged_snapshot(temperature: float) -> dict[str, Any]:
    """Return a spec snapshot that declares one scripted judge."""
    return {
        "metrics": [
            {"key": "citation_support", "options": {"judge": "strict"}},
        ],
        "judges": [
            {
                "name": "strict",
                "adapter": "judge:scripted",
                "model": "judge-1",
                "options": {"temperature": temperature},
            }
        ],
    }


def test_run_read_derives_pass_rate_duration_and_checks() -> None:
    """The read model derives counts, rate, duration and one cell's checks."""
    checks = [
        Check(metric="exact_match", passed=True),
        Check(metric="contains", passed=False),
    ]
    finished = RunRecord(
        run_id="run-a",
        status="completed",
        started_at=FINISHED,
        finished_at=FINISHED,
    )
    read = _read(finished, checks)
    assert read.checks == checks
    assert read.checks_passed == 1
    assert read.checks_total == 2
    assert read.pass_rate == 0.5
    assert read.duration_s == 0.0
    assert read.checks_for("cell-1") == checks
    assert read.checks_for("other-cell") == []

    running = RunRecord(run_id="run-b", status="running", started_at=FINISHED)
    empty = _read(running, [])
    assert empty.pass_rate is None
    assert empty.duration_s is None


def test_load_run_read_reads_events_only_when_asked(
    read_console: ReadConsole,
) -> None:
    """The loader loads one run's rows exactly once, events on request."""
    repository = read_console.repository
    run_id = read_console.runs["alpha"]
    repository.clear()
    read = load_run_read(repository, run_id)
    assert read is not None
    assert read.events == []
    assert repository.count("list_events") == 0
    assert repository.count("list_checks_for_run") == 1
    assert repository.count("list_checks") == 0

    repository.clear()
    with_events = load_run_read(repository, run_id, with_events=True)
    assert with_events is not None
    assert with_events.events
    assert repository.count("list_events") == 1

    repository.clear()
    assert load_run_read(repository, "missing") is None
    assert repository.calls == ["get_run"]


def test_run_diff_data_maps_both_sides_and_judge_drift() -> None:
    """The compare diff uses the loaded sides and flags a changed judge config."""
    baseline = _read(
        RunRecord(
            run_id="run-a",
            spec_name="judged-spec",
            spec_snapshot=_judged_snapshot(0.1),
            prices_snapshot={},
            status="completed",
            started_at=FINISHED,
            finished_at=FINISHED,
            operator="Ken",
        ),
        [],
    )
    current = _read(
        RunRecord(
            run_id="run-b",
            spec_name="judged-spec",
            spec_snapshot=_judged_snapshot(0.2),
            prices_snapshot={},
            status="completed",
            started_at=FINISHED,
            finished_at=FINISHED,
        ),
        [],
    )

    data = run_diff_data(current, baseline)

    assert set(data) == {
        "current",
        "against",
        "diff",
        "drift",
        "diff_url",
        "swap_url",
    }
    assert data["current"]["run_id"] == "run-b"
    assert data["against"]["run_id"] == "run-a"
    assert data["current"]["receipt_url"] == "/runs/run-b/receipt"
    assert data["against"]["operator_recorded"] is True
    assert data["diff_url"] == "/runs/run-b/diff?against=run-a"
    assert data["swap_url"] == "/runs/run-a/diff?against=run-b"
    assert set(data["diff"]) == {"headline", "total", "counts", "changes"}
    assert len(data["drift"]) == 1
    drift = data["drift"][0]
    assert set(drift) == {"kind", "judge", "metrics", "hashes", "runs", "reason"}
    assert drift["kind"] == "across_runs"
    assert drift["judge"] == "strict"
    assert drift["metrics"] == ["citation_support"]
    assert len(drift["hashes"]) == 2
    assert drift["runs"] == ["run-a", "run-b"]
    assert "not like-for-like" in drift["reason"]


def test_a_fresh_run_never_selects_checks_per_cell(
    evalh_home: Path,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The write side consumes the executor result, not a checks re-query."""
    statements: list[str] = []

    def before_cursor_execute(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", before_cursor_execute)
    try:
        code = main(["run", str(write_spec(workdir / "fresh.yaml", base_spec()))])
        captured = capsys.readouterr()
    finally:
        event.remove(Engine, "before_cursor_execute", before_cursor_execute)
    assert code == 0, captured.err
    check_selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
        and " FROM checks" in statement
    ]
    assert check_selects == [], (
        "a fresh run must not re-read the checks table; it uses the "
        f"executor result. Offending statements: {check_selects}"
    )
