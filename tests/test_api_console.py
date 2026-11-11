"""HTTP API seam tests for the console.

Every test seeds a real run through `evalharness.cli.main` and reads it
through `httpx.AsyncClient` against the ASGI application, never a server.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.api.views import (
    context_summary,
    heatmap,
    progress_summary,
    run_verdict,
)
from evalharness.cli import main
from evalharness.core.config import Settings
from evalharness.core.models import CellRecord, Check, GateResult, RunRecord
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import (
    CONTAINS,
    STUB_FAILING,
    STUB_SLOW,
    base_spec,
    run_id_from,
    write_spec,
)


@dataclass
class Console:
    """An application built on a disposable database seeded through the CLI."""

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


def _two_cases() -> list[dict[str, Any]]:
    """Return two small cases for a two-column heatmap."""
    return [
        {
            "case_id": "case-1",
            "prompt": "What is 6 times 7?",
            "expected": {"answer": "42"},
            "tier": 1,
        },
        {
            "case_id": "case-2",
            "prompt": "What is 7 times 6?",
            "expected": {"answer": "42"},
            "tier": 2,
        },
    ]


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
) -> Iterator[Console]:
    """Seed three runs: a passing run, a wrong-answer run and an error run."""
    alpha = base_spec()
    alpha["name"] = "alpha-spec"
    alpha["source"]["options"]["cases"] = _two_cases()
    alpha["candidates"][1]["name"] = "cited-stub"
    alpha["candidates"][1]["adapter"] = "test:cited"
    alpha["gates"] = [{"metric": "exact_match", "min": 0.5}]
    alpha_id = _seed(capsys, write_spec(workdir / "alpha.yaml", alpha))

    beta = base_spec()
    beta["name"] = "beta-spec"
    beta["candidates"] = [beta["candidates"][0]]
    beta["candidates"][0]["name"] = "wrong-stub"
    beta["candidates"][0]["options"] = {"answer": "wrong"}
    beta["gates"] = [{"metric": "exact_match", "min": 0.9}]
    beta_id = _seed(capsys, write_spec(workdir / "beta.yaml", beta), expected=1)

    gamma = base_spec()
    gamma["name"] = "gamma-spec"
    gamma["candidates"] = [gamma["candidates"][0]]
    gamma["candidates"][0]["name"] = "broken-stub"
    gamma["candidates"][0]["adapter"] = STUB_FAILING
    gamma["candidates"][0]["options"] = {}
    gamma["metrics"] = [{"key": "contains", "adapter": CONTAINS, "options": {}}]
    gamma["gates"] = [{"metric": "contains", "min": 0.9}]
    gamma_id = _seed(capsys, write_spec(workdir / "gamma.yaml", gamma), expected=1)

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
        runs={"alpha": alpha_id, "beta": beta_id, "gamma": gamma_id},
    )
    database.dispose()


def test_context_summary_reports_the_largest_group_for_one_digest() -> None:
    first = CellRecord(
        cell_id="one",
        run_id="run",
        candidate="c",
        case_id="case-a",
        replica=1,
        variant={
            "context_name": "graph",
            "context_digest": "digest",
            "context": [{"chunk_id": "a", "text": "one"}],
        },
    )
    second = first.model_copy(
        update={
            "cell_id": "two",
            "case_id": "case-b",
            "variant": {
                "context_name": "graph",
                "context_digest": "digest",
                "context": [
                    {"chunk_id": "a", "text": "one"},
                    {"chunk_id": "b", "text": "two"},
                ],
            },
        }
    )

    summary = context_summary([first, second])

    assert len(summary) == 1
    assert summary[0]["chunk_count"] == 2
    assert [item["chunk_id"] for item in summary[0]["chunks"]] == ["a"]


async def test_run_list_shows_status_spec_cost_pass_rate_and_duration(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        response = await client.get("/")
        assert response.status_code == 200
        text = response.text
        for name in ("alpha-spec", "beta-spec", "gamma-spec"):
            assert name in text
        assert "status-ok" in text
        assert "status-partial" in text
        assert "100.0%" in text
        assert "0.0%" in text
        assert "$" in text
        assert "s</td>" in text
        assert text.count('class="run-row"') == 3
        assert text.count('class="spec-graph spec-graph--mini"') == 3
        assert text.count('aria-hidden="true"') >= 3
        assert "2 need attention" in text
        assert "Check pass rate" in text
        assert 'class="help-tip"' in text
        assert 'role="tooltip"' in text
        gamma = _table_row(text, "gamma-spec")
        assert "1 not ok" in gamma

        rows = (await client.get("/api/runs")).json()
        assert len(rows) == 3
        by_name = {row["spec_name"]: row for row in rows}
        assert by_name["alpha-spec"]["status"] == "ok"
        assert by_name["alpha-spec"]["pass_rate"] == 1.0
        assert by_name["alpha-spec"]["n_cells"] == 4
        assert by_name["alpha-spec"]["n_ok"] == 4
        assert by_name["alpha-spec"]["cost_usd"] > 0
        assert by_name["alpha-spec"]["duration_s"] is not None
        assert by_name["beta-spec"]["pass_rate"] == 0.0
        assert by_name["gamma-spec"]["pass_rate"] is None
        assert by_name["gamma-spec"]["status"] == "partial"


async def test_run_list_ships_a_skeleton_loading_state(console: Console) -> None:
    async with _client(console.app) as client:
        text = (await client.get("/")).text
        assert "data-loading-form" in text
        assert 'data-loading-target="#runs-table"' in text
        assert 'id="runs-table"' in text
        assert 'aria-busy="false"' in text
        assert "data-loading-status" in text
        assert "data-skeleton-rows" in text
        assert text.count('class="skeleton-row"') == 3


async def test_candidate_and_metric_filters_narrow_the_list(console: Console) -> None:
    async with _client(console.app) as client:
        by_candidate = await client.get("/", params={"candidate": "stub-concise"})
        assert by_candidate.status_code == 200
        assert "alpha-spec" in by_candidate.text
        assert "beta-spec" not in by_candidate.text
        assert "gamma-spec" not in by_candidate.text
        assert by_candidate.text.count('class="run-row"') == 1

        by_metric = await client.get("/", params={"metric": "contains"})
        assert "gamma-spec" in by_metric.text
        assert "alpha-spec" not in by_metric.text
        assert "beta-spec" not in by_metric.text

        empty = await client.get("/", params={"candidate": "nobody"})
        assert "No runs match these filters" in empty.text

        rows = (
            await client.get("/api/runs", params={"candidate": "stub-concise"})
        ).json()
        assert [row["spec_name"] for row in rows] == ["alpha-spec"]
        rows = (await client.get("/api/runs", params={"metric": "contains"})).json()
        assert [row["spec_name"] for row in rows] == ["gamma-spec"]


async def test_heatmap_has_one_link_per_planned_cell(console: Console) -> None:
    run_id = console.runs["alpha"]
    cells = console.repository.list_cells(run_id)
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        text = response.text
        assert text.count("data-cell-id=") == len(cells) == 4
        for cell in cells:
            assert f'data-cell-id="{cell.cell_id}"' in text
        assert "Candidate by case" in text
        assert "Spec graph" in text
        assert 'id="graph-title"' in text
        assert 'class="spec-graph-figure"' in text
        assert 'class="spec-graph-caption"' in text
        assert "over 2 cases produce 4 cells" in text
        assert 'class="heat-rate"' in text
        assert "95% interval" in text
        assert "gate-pass" in text
        assert "exact_match" in text
        assert "All 4 checks passed across 4 cells." in text
        assert "Check pass rate" in text
        assert "0 errored of 4 recorded cells" in text
        assert 'class="help-tip"' in text
        assert 'role="tooltip"' in text
        assert text.count(f'data-copy="{run_id}"') == 1
        assert ">Copy run ID</span>" in text
        assert 'aria-valuemax="4"' in text and 'aria-valuenow="4"' in text
        assert "2/2 checks" in text
        assert f"/compare?a={run_id}" in text

        detail = (await client.get(f"/api/runs/{run_id}")).json()
        assert detail["spec_name"] == "alpha-spec"
        assert detail["pass_rate"] == 1.0
        assert len(detail["cells"]) == 4
        assert detail["gates"][0]["passed"] is True
        assert detail["aggregates"][0]["ci_low"] is not None
        assert all("checks" in cell for cell in detail["cells"])

        cells_json = (await client.get(f"/api/runs/{run_id}/cells")).json()
        assert cells_json["run_id"] == run_id
        assert len(cells_json["cells"]) == 4


async def test_selecting_a_cell_shows_answer_citations_checks_and_usage(
    console: Console,
) -> None:
    run_id = console.runs["alpha"]
    cells = console.repository.list_cells(run_id)
    cited = next(cell for cell in cells if cell.candidate == "cited-stub")
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}", params={"cell": cited.cell_id})
        assert response.status_code == 200
        text = response.text
        assert cited.cell_id in text
        assert "42" in text
        assert "policy-1" in text and "policy-2" in text
        assert "exact_match" in text
        assert "expected" in text and "actual" in text
        assert "Prompt tokens" in text and ">12<" in text
        assert "Completion tokens" in text and ">8<" in text
        assert "Latency" in text and " ms" in text
        assert "provider" in text
        assert "$" in text
        assert "Raw artifact (cell.json)" in text

        by_route = await client.get(f"/runs/{run_id}/cells/{cited.cell_id}")
        assert by_route.status_code == 200
        assert "policy-1" in by_route.text

        plain = next(cell for cell in cells if cell.candidate == "stub-concise")
        no_citations = await client.get(
            f"/runs/{run_id}", params={"cell": plain.cell_id}
        )
        assert "No citations were returned" in no_citations.text


async def test_detail_pages_render_failed_and_errored_cells(console: Console) -> None:
    async with _client(console.app) as client:
        beta = await client.get(f"/runs/{console.runs['beta']}")
        assert beta.status_code == 200
        assert "heat-cell fail" in beta.text
        assert "gate-fail" in beta.text

        gamma_run = console.runs["gamma"]
        gamma = await client.get(f"/runs/{gamma_run}")
        assert gamma.status_code == 200
        assert "heat-cell error" in gamma.text
        assert "gate-fail" in gamma.text

        broken = console.repository.list_cells(gamma_run)[0]
        selected = await client.get(
            f"/runs/{gamma_run}", params={"cell": broken.cell_id}
        )
        assert "stub transport failed" in selected.text
        assert "No checks were recorded for this cell" in selected.text

        detail = (await client.get(f"/api/runs/{gamma_run}")).json()
        assert detail["pass_rate"] is None
        assert detail["cells"][0]["checks"] == []
        assert detail["cells"][0]["error"] == "stub transport failed"
        assert detail["aggregates"][0]["value"] is None


async def test_artifact_link_serves_the_stored_file_and_refuses_escapes(
    console: Console,
) -> None:
    run_id = console.runs["alpha"]
    cell = next(
        item
        for item in console.repository.list_cells(run_id)
        if item.status == "ok"
    )
    url = f"/runs/{run_id}/cells/{cell.cell_id}/artifact"
    async with _client(console.app) as client:
        artifact = await client.get(url)
        assert artifact.status_code == 200
        assert artifact.headers["content-type"].startswith("application/json")
        payload = artifact.json()
        assert payload["cell"]["cell_id"] == cell.cell_id
        assert payload["checks"][0]["metric"] == "exact_match"

        answer = await client.get(url, params={"file": "answer.txt"})
        assert answer.status_code == 200
        assert answer.text == "42"
        assert answer.headers["content-type"].startswith("text/plain")

        traversal = await client.get(url, params={"file": "../../run.json"})
        assert traversal.status_code == 404
        unknown_name = await client.get(url, params={"file": "cell.json.bak"})
        assert unknown_name.status_code == 404

        elsewhere = replace(
            console.settings,
            artifacts_dir=console.settings.artifacts_dir.parent / "elsewhere",
        )
        outside_app = create_app(settings=elsewhere, repository=console.repository)
        async with _client(outside_app) as outside:
            assert (await outside.get(url)).status_code == 404

        Path(cell.artifact_dir or "").joinpath("cell.json").unlink()
        missing = await client.get(url)
        assert missing.status_code == 404


def test_every_terminal_status_has_a_live_artifact_link(
    console: Console,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = base_spec()
    spec["name"] = "cancelled-spec"
    spec["candidates"] = [spec["candidates"][0]]
    spec["replicas"] = 2
    spec["concurrency"] = 1
    cancelled_spec = write_spec(workdir / "cancelled.yaml", spec)
    assert main(["run", str(cancelled_spec), "--cancel-after", "0"]) == 0
    cancelled_id = run_id_from(capsys.readouterr().out)

    async def read_links() -> None:
        async with _client(console.app) as client:
            for status, run_id in (
                ("ok", console.runs["alpha"]),
                ("error", console.runs["gamma"]),
                ("cancelled", cancelled_id),
            ):
                cell = next(
                    item
                    for item in console.repository.list_cells(run_id)
                    if item.status == status
                )
                artifact_dir = Path(cell.artifact_dir or "")
                assert artifact_dir == (
                    console.settings.artifacts_dir / run_id / cell.cell_id
                )
                assert artifact_dir.is_dir()
                assert (artifact_dir / "cell.json").is_file()
                assert (artifact_dir / "answer.txt").is_file()
                url = f"/runs/{run_id}/cells/{cell.cell_id}/artifact"
                artifact = await client.get(url)
                assert artifact.status_code == 200
                assert artifact.json()["cell"]["status"] == status
                page = await client.get(
                    f"/runs/{run_id}", params={"cell": cell.cell_id}
                )
                assert page.status_code == 200
                assert "Raw artifact (cell.json)" in page.text

    asyncio.run(read_links())


async def test_unknown_ids_are_404(console: Console) -> None:
    run_id = console.runs["alpha"]
    cell = console.repository.list_cells(run_id)[0]
    async with _client(console.app) as client:
        assert (await client.get("/runs/not-a-run")).status_code == 404
        assert (await client.get(f"/runs/{run_id}?cell=not-a-cell")).status_code == 404
        assert (
            await client.get(f"/runs/{run_id}/cells/not-a-cell")
        ).status_code == 404
        assert (await client.get("/api/runs/not-a-run")).status_code == 404
        assert (await client.get("/api/runs/not-a-run/cells")).status_code == 404
        assert (
            await client.get(f"/runs/not-a-run/cells/{cell.cell_id}/artifact")
        ).status_code == 404
        assert (
            await client.get(f"/runs/{run_id}/cells/not-a-cell/artifact")
        ).status_code == 404
        assert (await client.get("/runs/not-a-run/events")).status_code == 404


async def test_sse_replays_persisted_events_until_the_terminal_event(
    console: Console,
) -> None:
    run_id = console.runs["alpha"]
    events = console.repository.list_events(run_id)
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}/events")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = response.text
        assert text.count("event: ") == len(events)
        for seq in range(1, len(events) + 1):
            assert f"id: {seq}\nevent:" in text
        assert "event: run_started" in text
        assert "event: run_finished" in text
        assert "data: " in text

        api = await client.get(f"/api/runs/{run_id}/events")
        assert api.text == text

        after = await client.get(f"/runs/{run_id}/events", params={"after": 2})
        assert "id: 1\nevent:" not in after.text
        assert "id: 3\nevent:" in after.text

        header = await client.get(
            f"/runs/{run_id}/events", headers={"Last-Event-ID": "2"}
        )
        assert header.text == after.text

        invalid = await client.get(
            f"/runs/{run_id}/events", headers={"Last-Event-ID": "not-a-number"}
        )
        assert "id: 1\nevent:" in invalid.text


def test_live_run_streams_and_partial_heatmap_renders(
    evalh_home: Path,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec_data = base_spec()
    spec_data["name"] = "live-spec"
    spec_data["source"]["options"]["cases"] = _two_cases()
    for candidate in spec_data["candidates"]:
        candidate["adapter"] = STUB_SLOW
        candidate["options"] = {
            "prompt": "concise",
            "context": "none",
            "delay_ms": 250,
        }
    spec_data["concurrency"] = 1
    spec_data["gates"] = []
    spec = write_spec(workdir / "live.yaml", spec_data)

    env = dict(os.environ)
    src = str(Path(__file__).parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-m", "evalharness", "run", str(spec)],
        cwd=workdir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    try:
        run_id = _wait_for_partial_run(repository)
        app = create_app(settings=settings, repository=repository)

        async def read_live() -> None:
            async with _client(app) as client:
                detail = await client.get(f"/runs/{run_id}")
                assert detail.status_code == 200
                stream = await client.get(f"/runs/{run_id}/events")
                assert stream.status_code == 200
                assert "event: run_started" in stream.text
                assert "event: run_finished" in stream.text
                assert stream.text.count("event: ") == len(
                    repository.list_events(run_id)
                )

        asyncio.run(read_live())
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        assert "status=ok" in stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        database.dispose()


def _wait_for_partial_run(repository: Repository) -> str:
    """Wait until a run is mid-flight with at least one, not all, cells stored."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        summaries = repository.list_run_summaries()
        if summaries and summaries[0].status == "running":
            recorded = len(repository.list_cells(summaries[0].run_id))
            if 0 < recorded < summaries[0].n_cells:
                return summaries[0].run_id
        time.sleep(0.01)
    raise AssertionError("the CLI run never reached a partial state")


def test_empty_console_renders_an_empty_state(evalh_home: Path) -> None:
    app = create_app()

    async def read_empty() -> None:
        async with _client(app) as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert "No runs yet" in response.text
            assert (await client.get("/api/runs")).json() == []

    try:
        asyncio.run(read_empty())
    finally:
        app.state.database.dispose()


def test_request_logs_omit_query_strings(console: Console) -> None:
    async def read_with_secret() -> None:
        async with _client(console.app) as client:
            response = await client.get(
                "/", params={"candidate": "SECRET-CANARY"}
            )
            assert response.status_code == 200

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("evalharness.api")
    previous_propagate = logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        asyncio.run(read_with_secret())
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous_propagate
    assert "GET / -> 200" in stream.getvalue()
    assert "SECRET-CANARY" not in stream.getvalue()


def test_console_never_renders_a_known_secret(
    evalh_home: Path,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("SECRET-CANARY\n"))
    assert main(
        ["credentials", "add", "stub", "--provider", "none", "--secret-stdin"]
    ) == 0
    capsys.readouterr()
    spec_data = base_spec()
    spec_data["name"] = "secret-spec"
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["options"] = {"answer": "SECRET-CANARY"}
    spec = write_spec(workdir / "secret.yaml", spec_data)
    run_id = _seed(capsys, spec)

    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    app = create_app(settings=settings, repository=repository)

    async def read_every_surface() -> None:
        cell = repository.list_cells(run_id)[0]
        async with _client(app) as client:
            bodies = [
                (await client.get("/")).text,
                (await client.get(f"/runs/{run_id}")).text,
                (
                    await client.get(
                        f"/runs/{run_id}", params={"cell": cell.cell_id}
                    )
                ).text,
                (await client.get(f"/api/runs/{run_id}")).text,
                (await client.get(f"/api/runs/{run_id}/events")).text,
                (
                    await client.get(
                        f"/runs/{run_id}/cells/{cell.cell_id}/artifact"
                    )
                ).text,
                (
                    await client.get(
                        f"/runs/{run_id}/cells/{cell.cell_id}/artifact",
                        params={"file": "answer.txt"},
                    )
                ).text,
            ]
        for body in bodies:
            assert "SECRET-CANARY" not in body
        assert "[redacted:stub]" in bodies[2]

    try:
        asyncio.run(read_every_surface())
    finally:
        database.dispose()


def test_serve_cli_builds_the_app_and_calls_uvicorn(
    evalh_home: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, str, int]] = []

    def fake_run(
        app: Any, *, host: str, port: int, access_log: bool
    ) -> None:
        calls.append((app, host, port))
        assert access_log is False

    monkeypatch.setattr("evalharness.cli.uvicorn.run", fake_run)
    database_path = workdir / "serve" / "harness.db"
    artifacts = workdir / "serve-artifacts"

    code = main(
        [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--db",
            f"sqlite:///{database_path.as_posix()}",
            "--artifacts",
            str(artifacts),
        ]
    )

    assert code == 0
    app, host, port = calls[0]
    assert host == "127.0.0.1"
    assert port == 8123
    assert app.state.settings.database_url == f"sqlite:///{database_path.as_posix()}"
    assert app.state.settings.artifacts_dir == artifacts
    assert database_path.is_file()
    # Newer FastAPI keeps included routers nested, so read the public OpenAPI
    # paths rather than assuming a flat app.routes list.
    assert "/api/runs" in app.openapi()["paths"]

    assert main(["serve"]) == 0
    second, default_host, default_port = calls[1]
    assert default_host == "127.0.0.1"
    assert default_port == 8000
    assert second.state.settings.database_url == Settings.from_env().database_url


def _table_row(text: str, spec_name: str) -> str:
    """Return the HTML of the run-list row that names one spec."""
    start = text.index(spec_name)
    return text[start : text.index("</tr>", start)]


async def test_run_list_flags_a_failed_gate_next_to_the_status(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        text = (await client.get("/")).text
        alpha = _table_row(text, "alpha-spec")
        beta = _table_row(text, "beta-spec")
        assert "gate-chip" not in alpha
        assert "gate-chip" in beta
        assert "gate failed" in beta
        assert "Failed gate: exact_match" in beta
        assert "status-ok" in beta

        rows = (await client.get("/api/runs")).json()
        by_name = {row["spec_name"]: row for row in rows}
        assert by_name["alpha-spec"]["gate_failed"] is False
        assert by_name["alpha-spec"]["failed_gates"] == []
        assert by_name["beta-spec"]["gate_failed"] is True
        assert by_name["beta-spec"]["failed_gates"] == ["exact_match"]


async def test_run_detail_breadcrumb_uses_a_short_run_id(console: Console) -> None:
    run_id = console.runs["alpha"]
    async with _client(console.app) as client:
        text = (await client.get(f"/runs/{run_id}")).text
        crumbs = text.split('class="crumbs"', 1)[1].split("</nav>", 1)[0]
        assert f'title="{run_id}"' in crumbs
        assert f">{run_id[:8]}<" in crumbs
        assert f">{run_id}<" not in crumbs


async def test_run_detail_links_to_provenance_surfaces(console: Console) -> None:
    run_id = console.runs["alpha"]
    async with _client(console.app) as client:
        text = (await client.get(f"/runs/{run_id}")).text
        assert f"/runs/{run_id}/decision" in text
        assert f"/runs/{run_id}/receipt" in text
        assert f"/runs/{run_id}/calibration" in text


async def test_pages_declare_an_inline_favicon_and_an_accurate_badge(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        text = (await client.get("/")).text
        assert 'rel="icon"' in text
        assert "data:image/svg+xml" in text
        assert "%231f5e4b" in text
        assert "single user" in text
        assert "All times UTC" in text
        assert ">Specs</a>" in text
        assert ">Compare</a>" in text
        assert ">Launch</a>" in text
        assert "read-only results" not in text


async def test_run_list_formats_tiny_costs_without_a_fake_zero(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        text = (await client.get("/")).text
        alpha = _table_row(text, "alpha-spec")
        beta = _table_row(text, "beta-spec")
        gamma = _table_row(text, "gamma-spec")
        assert "$0.0000264" in alpha
        assert "$0.00000660" in beta
        assert "$0.00" in gamma
        assert "$0.000000" not in text

def _verdict_run(status: str, **overrides: Any) -> RunRecord:
    """Build a run record for the verdict view model tests."""
    values: dict[str, Any] = {
        "run_id": "run-1",
        "status": status,
        "n_cells": 1,
        "budget_usd": 1.0,
        "cost_usd": 0.5,
    }
    values.update(overrides)
    return RunRecord(**values)


def _verdict_cell(status: str = "ok") -> CellRecord:
    """Build one stored cell for the heatmap and progress tests."""
    return CellRecord(
        cell_id=f"cell-{status}",
        run_id="run-1",
        candidate="stub",
        case_id="case-1",
        replica=1,
        status=status,
    )


def test_verdict_view_covers_every_terminal_outcome() -> None:
    """The verdict view states each outcome in words, not colour alone."""
    passed = Check(metric="exact_match", passed=True)
    failed = Check(metric="exact_match", passed=False)

    running = run_verdict(
        _verdict_run("running", n_cells=4), [_verdict_cell()], [passed], []
    )
    assert (running.tone, running.label) == ("info", "Running")
    assert "In progress: 1 of 4 cells" in running.headline

    error = run_verdict(_verdict_run("error"), [], [], [])
    assert (error.tone, error.label) == ("bad", "Failed")
    assert "ended with an error" in error.headline

    budget = run_verdict(
        _verdict_run("budget_exceeded", cost_usd=2.0), [], [], []
    )
    assert (budget.tone, budget.label) == ("bad", "Budget exceeded")
    assert budget.budget_over is True
    assert "budget cap stopped the run" in budget.warning
    assert "Stopped at the budget cap" in budget.headline

    cancelled = run_verdict(
        _verdict_run("cancelled", n_cells=4), [_verdict_cell()], [], []
    )
    assert (cancelled.tone, cancelled.label) == ("neutral", "Cancelled")
    assert "Cancelled after 1 of 4 cells" in cancelled.headline
    assert "cancelled run keeps" in cancelled.warning

    empty = run_verdict(_verdict_run("ok", n_cells=0), [], [], [])
    assert (empty.tone, empty.label) == ("info", "No checks")
    assert empty.pass_rate is None
    assert empty.headline == "The run completed but recorded no checks."

    wrong = run_verdict(_verdict_run("ok"), [_verdict_cell()], [failed], [])
    assert (wrong.tone, wrong.label) == ("warn", "Checks failed")
    assert wrong.headline == "0 of 1 checks passed across 1 cells."

    partial = run_verdict(
        _verdict_run("partial", n_cells=2),
        [_verdict_cell("ok"), _verdict_cell("error")],
        [passed],
        [],
    )
    assert (partial.tone, partial.label) == ("warn", "Partial")
    assert "did not produce an answer" in partial.headline
    assert "1 cell errored" in partial.warning

    gate = GateResult(
        name="floor", metric="exact_match", passed=False, n=1, observed=0.0
    )
    gated = run_verdict(_verdict_run("ok"), [], [], [gate])
    assert (gated.tone, gated.label) == ("warn", "Gate failed")
    assert "Gate not cleared: exact_match" in gated.headline
    assert "Gate failed: exact_match" in gated.warning

    short = run_verdict(
        _verdict_run("ok", n_cells=4), [_verdict_cell()], [passed], []
    )
    assert short.headline == "1 of 1 checks passed across 1 of 4 cells."


def test_heatmap_marks_unplanned_positions_and_row_rates() -> None:
    """A candidate row can miss a case the other candidate recorded."""
    cells = [
        CellRecord(
            cell_id="a1",
            run_id="run-1",
            candidate="a",
            case_id="case-1",
            replica=1,
            status="ok",
        ),
        CellRecord(
            cell_id="b2",
            run_id="run-1",
            candidate="b",
            case_id="case-2",
            replica=1,
            status="ok",
        ),
    ]
    columns, rows = heatmap(
        cells, {"a1": [Check(metric="exact_match", passed=True)]}, "run-1", "a1"
    )
    assert len(columns) == 2
    assert rows[0].cells_recorded == 1
    assert rows[0].cells_planned == 2
    assert rows[0].cells[1] is None
    assert rows[0].check_rate == 1.0
    assert rows[0].cells[0] is not None and rows[0].cells[0].selected is True
    assert rows[1].check_rate is None


def test_progress_view_covers_empty_and_partial_runs() -> None:
    """An empty run has no percentage; a stopped run is partially recorded."""
    empty = progress_summary(_verdict_run("ok", n_cells=0), [])
    assert (empty.state, empty.percent) == ("empty", 0)
    assert empty.label == "no cells planned"

    partial = progress_summary(
        _verdict_run("cancelled", n_cells=4), [_verdict_cell()]
    )
    assert (partial.state, partial.percent) == ("partial", 25)
    assert partial.note == (
        "The run stopped before every planned cell was recorded."
    )