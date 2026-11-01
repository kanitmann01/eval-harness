"""HTTP seam tests for the per-run decision page and JSON surface.

The console fixture, `_seed` and `_client` are reused from the console tests.
The dominance case is seeded through the repository because a deterministic
stub cannot produce a wide pass-rate gap and a cost difference at once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.core.models import CellRecord, Check, RunRecord
from tests.helpers import base_spec, write_spec
from tests.test_api_console import (  # noqa: F401 - console is a fixture
    Console,
    _client,
    _seed,
    console,
)


def _create_run(console: Console, run_id: str, metrics: list[str]) -> None:
    """Create one completed run row with no cells."""
    console.repository.create_run(
        RunRecord(
            run_id=run_id,
            spec_name="seeded-spec",
            spec_snapshot={"metrics": [{"key": key} for key in metrics]},
            status="completed",
        )
    )


def _record_cell(
    console: Console,
    run_id: str,
    candidate: str,
    case_id: str,
    *,
    passed: int,
    total: int,
    cost_usd: float,
    wall_ms: int,
    status: str = "ok",
) -> None:
    """Record one cell and its checks."""
    cell = CellRecord(
        cell_id=f"{candidate}-{case_id}",
        run_id=run_id,
        candidate=candidate,
        case_id=case_id,
        replica=0,
        status=status,
        wall_ms=wall_ms,
        cost_usd=cost_usd,
    )
    console.repository.record_cell(cell)
    console.repository.record_checks(
        run_id,
        cell.cell_id,
        [
            Check(metric=f"check-{index}", passed=index < passed)
            for index in range(total)
        ],
    )


def _seed_dominance_run(console: Console) -> str:
    """Seed one run where fast dominates slow on all three measures."""
    run_id = "dominance-run"
    _create_run(console, run_id, ["exact_match"])
    _record_cell(
        console,
        run_id,
        "fast",
        "case-1",
        passed=5,
        total=5,
        cost_usd=1.0,
        wall_ms=100,
    )
    _record_cell(
        console,
        run_id,
        "fast",
        "case-2",
        passed=5,
        total=5,
        cost_usd=1.0,
        wall_ms=100,
    )
    _record_cell(
        console,
        run_id,
        "slow",
        "case-1",
        passed=1,
        total=5,
        cost_usd=0.5,
        wall_ms=100,
    )
    _record_cell(
        console,
        run_id,
        "slow",
        "case-2",
        passed=1,
        total=5,
        cost_usd=0.5,
        wall_ms=200,
    )
    return run_id


async def test_decision_page_ranks_candidates_and_plots_the_frontier(
    console: Console,
) -> None:
    run_id = console.runs["alpha"]
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}/decision")
        assert response.status_code == 200
        text = response.text
        assert "<h1>alpha-spec</h1>" in text
        assert "Primary metrics: exact_match" in text
        assert 'id="decision-table"' in text
        assert text.count("data-candidate=") == 2
        assert "stub-concise" in text and "cited-stub" in text
        assert "100.0%" in text
        assert "34.2% to 100.0%" in text
        assert "95% interval" in text
        assert "Cost per correct" in text
        assert "p50" in text and "p95" in text
        assert "On the frontier" in text
        assert 'aria-labelledby="pareto-title pareto-desc"' in text
        assert '<title id="pareto-title">' in text
        assert '<desc id="pareto-desc">' in text
        assert "Pass rate" in text
        assert "Cost per correct check (lower is better)" in text
        assert text.count("<circle") == 2
        assert "The ranked table above is the accessible table" in text
        assert f"/api/runs/{run_id}/decision" in text
        assert f"/compare?a={run_id}" in text

        payload = (await client.get(f"/api/runs/{run_id}/decision")).json()
        assert payload["run_id"] == run_id
        assert payload["primary_metrics"] == ["exact_match"]
        assert set(payload["pareto_frontier"]) == {"stub-concise", "cited-stub"}
        assert [item["candidate"] for item in payload["candidates"]] == [
            "cited-stub",
            "stub-concise",
        ]
        first = payload["candidates"][0]
        assert first["rank"] == 1
        assert first["n_checks"] == 2
        assert first["passed"] == 2
        assert first["pass_rate"] == 1.0
        assert first["ci_low"] < 0.52
        assert first["ci_high"] == 1.0
        assert first["n_cells"] == 2
        assert first["n_ok"] == 2
        assert first["n_errored"] == 0
        assert first["cost_usd"] > 0
        assert first["cost_per_correct"] > 0
        assert first["p50_latency_ms"] is not None
        assert first["p95_latency_ms"] is not None
        assert first["dominates"] == []
        assert first["dominated_by"] == []
        assert first["on_pareto_frontier"] is True


async def test_wrong_answers_have_no_cost_per_correct_and_no_chart(
    console: Console,
) -> None:
    run_id = console.runs["beta"]
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}/decision")
        assert response.status_code == 200
        text = response.text
        assert "0.0%" in text
        assert "No correct check" in text
        assert "No candidate has a defined pass rate and cost per correct check" in text
        assert "<circle" not in text
        assert "No cells recorded" not in text

        payload = (await client.get(f"/api/runs/{run_id}/decision")).json()
        candidate = payload["candidates"][0]
        assert candidate["candidate"] == "wrong-stub"
        assert candidate["pass_rate"] == 0.0
        assert candidate["cost_per_correct"] is None
        assert candidate["on_pareto_frontier"] is False
        assert payload["pareto_frontier"] == []


async def test_errored_cells_show_an_undefined_pass_rate(console: Console) -> None:
    run_id = console.runs["gamma"]
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}/decision")
        assert response.status_code == 200
        text = response.text
        assert "Pass rate undefined" in text
        assert "Latency undefined" in text
        assert "No checks recorded" in text
        assert "1 errored" in text

        payload = (await client.get(f"/api/runs/{run_id}/decision")).json()
        candidate = payload["candidates"][0]
        assert candidate["n_checks"] == 0
        assert candidate["pass_rate"] is None
        assert candidate["p50_latency_ms"] is None
        assert candidate["n_errored"] == 1
        assert candidate["on_pareto_frontier"] is False


async def test_a_candidate_without_latency_is_not_on_the_frontier(
    console: Console,
) -> None:
    run_id = "partial-latency-run"
    _create_run(console, run_id, ["exact_match"])
    _record_cell(
        console,
        run_id,
        "mixed",
        "case-1",
        passed=1,
        total=1,
        cost_usd=1.0,
        wall_ms=0,
        status="error",
    )
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}/decision")
        assert response.status_code == 200
        assert "Latency undefined" in response.text

        payload = (await client.get(f"/api/runs/{run_id}/decision")).json()
        candidate = payload["candidates"][0]
        assert candidate["pass_rate"] == 1.0
        assert candidate["cost_per_correct"] == 1.0
        assert candidate["p50_latency_ms"] is None
        assert candidate["p95_latency_ms"] is None
        assert candidate["on_pareto_frontier"] is False
        assert payload["pareto_frontier"] == []


async def test_decision_page_and_api_404_for_an_unknown_run(console: Console) -> None:
    async with _client(console.app) as client:
        page = await client.get("/runs/nope/decision")
        assert page.status_code == 404
        assert "run nope does not exist" in page.text
        payload = await client.get("/api/runs/nope/decision")
        assert payload.status_code == 404
        assert payload.json()["detail"] == "run nope does not exist"


async def test_empty_run_renders_an_empty_state(console: Console) -> None:
    run_id = "empty-run"
    _create_run(console, run_id, ["exact_match"])
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}/decision")
        assert response.status_code == 200
        text = response.text
        assert "No cells recorded" in text
        assert 'id="decision-table"' not in text
        assert "Primary metrics: exact_match" in text

        payload = (await client.get(f"/api/runs/{run_id}/decision")).json()
        assert payload["candidates"] == []
        assert payload["pareto_frontier"] == []
        assert payload["primary_metrics"] == ["exact_match"]


@pytest.fixture
def delta_run(
    console: Console,
    capsys: pytest.CaptureFixture[str],
    workdir: Path,
) -> str:
    """Seed a one-candidate run through the real CLI."""
    spec = base_spec()
    spec["name"] = "delta-spec"
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["name"] = "only-stub"
    return _seed(capsys, write_spec(workdir / "delta.yaml", spec))


async def test_single_candidate_gets_a_rank_and_a_chart(
    console: Console,
    delta_run: str,
) -> None:
    run_id = delta_run
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}/decision")
        assert response.status_code == 200
        text = response.text
        assert text.count("data-candidate=") == 1
        assert "On the frontier" in text
        assert text.count("<circle") == 1
        assert "The ranked table above is the accessible table" in text

        payload = (await client.get(f"/api/runs/{run_id}/decision")).json()
        assert payload["pareto_frontier"] == ["only-stub"]
        assert payload["candidates"][0]["rank"] == 1
        assert payload["candidates"][0]["on_pareto_frontier"] is True


async def test_dominance_is_reported_on_the_page_and_in_json(
    console: Console,
) -> None:
    run_id = _seed_dominance_run(console)
    async with _client(console.app) as client:
        response = await client.get(f"/runs/{run_id}/decision")
        assert response.status_code == 200
        text = response.text
        assert "Dominates slow" in text
        assert "Dominated by fast" in text
        assert text.count("<circle") == 1
        assert "fast" in text and "slow" in text

        payload = (await client.get(f"/api/runs/{run_id}/decision")).json()
        by_name = {item["candidate"]: item for item in payload["candidates"]}
        assert payload["pareto_frontier"] == ["fast"]
        assert by_name["fast"]["rank"] == 1
        assert by_name["fast"]["dominates"] == ["slow"]
        assert by_name["fast"]["dominated_by"] == []
        assert by_name["fast"]["on_pareto_frontier"] is True
        assert by_name["slow"]["rank"] == 2
        assert by_name["slow"]["dominates"] == []
        assert by_name["slow"]["dominated_by"] == ["fast"]
        assert by_name["slow"]["on_pareto_frontier"] is False
        assert by_name["slow"]["cost_per_correct"] == 0.5
