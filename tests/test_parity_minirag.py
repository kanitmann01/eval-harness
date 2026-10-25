"""Parity with the recorded minirag pipeline, run through the real CLI.

The fixture in `examples/parity/fixture/` is the committed provenance of the
three-arm scoreboard the RAG repository used to produce. This test rebuilds
that matrix as a spec (three candidates, eight cases with their tiers, one
decision metric), runs it through the CLI against the replay server, and
compares the observed scoreboard with the committed one. Any drift fails.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

from evalharness.cli import main
from tests.helpers import (
    CONTAINS,
    INLINE_SOURCE,
    open_repository,
    run_id_from,
    write_spec,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "examples" / "parity" / "fixture"
SERVER = REPO / "examples" / "parity" / "minirag_replay_server.py"
HTTP_CANDIDATE = "evalharness.adapters.candidates.http:build"

EXPECTED_SHA256 = "3ef4c300ac2232d4f2d4d5724d8e4113603d934a5ee7a78159130b8d670f2a81"
"""The pinned sha256 of the committed `expected.json`, recorded in the fixture README."""

ARMS = ("bare", "map", "map_rules")
"""The three recorded context arms, in scoreboard order."""

JUDGE_CALLS = 4
"""The recorded scoreboard folds one judge call per tier-2 case into its totals."""


def _fixture(name: str):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def _load_server() -> ModuleType:
    """Import the replay server by path, the way the other example tests do."""
    spec = importlib.util.spec_from_file_location("minirag_replay_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def replay_server():
    """Serve the recorded pipeline on a fresh loopback port for one test."""
    module = _load_server()
    server = module.create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _decision_token(task: dict) -> str:
    """The token the recorded decision rule required in the answer."""
    if int(task["tier"]) == 1:
        return str(task["expected"]["target"])
    return str(task["expected"]["chunk_ids"][0])


def _cases(tasks: list[dict]) -> list[dict]:
    """The eight recorded tasks as cases, tiers included."""
    return [
        {
            "case_id": str(task["id"]),
            "prompt": str(task["prompt"]),
            "expected": {"answer": _decision_token(task)},
            "tier": int(task["tier"]),
        }
        for task in tasks
    ]


def _map_chunk(tasks: list[dict]) -> dict:
    """Compose the map excerpt the fixture's tier-1 tasks were answered from."""
    lines = []
    for task in tasks:
        edge = task["origin"]["edge"]
        lines.append(
            "\n".join(
                [
                    f"source: {edge['source']}",
                    f"relation: {edge['relation']}",
                    f"file: {edge['source_file']}",
                ]
            )
        )
    return {"chunk_id": "map-all", "text": "\n\n".join(lines)}


def _rule_chunks(tasks: list[dict]) -> list[dict]:
    """The recorded tier-2 evidence chunks, one per rule, in stable order."""
    chunks: dict[str, dict] = {}
    for task in tasks:
        for chunk in (task.get("origin") or {}).get("chunks") or []:
            chunks.setdefault(
                str(chunk["chunk_id"]),
                {"chunk_id": str(chunk["chunk_id"]), "text": str(chunk["text"])},
            )
    return [chunks[chunk_id] for chunk_id in sorted(chunks)]


def _spec(base_url: str, tasks: list[dict], answers: dict, expected: dict) -> dict:
    """Build the three-arm parity spec against one replay server."""
    map_chunk = _map_chunk(tasks)
    rules = _rule_chunks(tasks)
    common = {"base_url": base_url, "path": "/run", "auth": "none", "timeout_s": 10}
    return {
        "spec_version": 1,
        "name": "minirag-parity",
        "description": "Replay the recorded minirag three-arm scoreboard.",
        "seed": int(answers["seed"]),
        "source": {"adapter": INLINE_SOURCE, "options": {"cases": _cases(tasks)}},
        "contexts": {
            "map": [map_chunk],
            "map_rules": [map_chunk, *rules],
        },
        "candidates": [
            {
                "name": "bare",
                "adapter": HTTP_CANDIDATE,
                "model": str(expected["model"]),
                "options": dict(common),
            },
            {
                "name": "map",
                "adapter": HTTP_CANDIDATE,
                "model": str(expected["model"]),
                "options": {**common, "context": "map"},
            },
            {
                "name": "map_rules",
                "adapter": HTTP_CANDIDATE,
                "model": str(expected["model"]),
                "options": {**common, "context": "map_rules"},
            },
        ],
        "replicas": 1,
        "concurrency": 1,
        "budget_usd": 1.0,
        "metrics": [
            {"key": "decision", "adapter": CONTAINS, "options": {}},
        ],
        "judges": [],
        "gates": [],
        "prices": {
            "fixture-model": {
                "input": float(expected["prices"]["fixture-model"]["input_usd_per_million"]),
                "output": float(
                    expected["prices"]["fixture-model"]["output_usd_per_million"]
                ),
                "tool": float(expected["prices"]["fixture-model"]["tool_usd"]),
            }
        },
    }


def test_the_expected_scoreboard_is_unchanged():
    payload = (FIXTURE / "expected.json").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256


def test_the_replay_server_reports_health(replay_server):
    with urllib.request.urlopen(f"{replay_server}/health", timeout=5) as response:
        assert response.status == 200
        assert json.loads(response.read()) == {
            "status": "ok",
            "contract": "candidate-v1",
        }


def test_the_recorded_three_arm_scoreboard_replays_exactly(
    evalh_home, workdir, replay_server, capsys
):
    tasks = _fixture("tasks.json")
    answers = _fixture("answers.json")
    expected = _fixture("expected.json")
    spec = write_spec(workdir / "parity.yaml", _spec(replay_server, tasks, answers, expected))

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id)
        aggregates = repository.list_aggregates(run_id)
        checks = {
            cell.cell_id: repository.list_checks(run_id, cell.cell_id) for cell in cells
        }
    finally:
        database.dispose()

    assert len(cells) == 24
    rows = {row["context"]: row for row in expected["scoreboard"]}
    tiers_by_case = {str(task["id"]): int(task["tier"]) for task in tasks}
    by_candidate: dict[str, list] = {arm: [] for arm in ARMS}
    for cell in cells:
        by_candidate[cell.candidate].append(cell)

    for arm in ARMS:
        row = rows[arm]
        owned = by_candidate[arm]
        assert len(owned) == row["tasks_total"], arm
        assert all(cell.status == "ok" for cell in owned), arm
        assert sum(cell.tool_calls for cell in owned) == row["tool_calls"], arm

        passes: dict[int, list[bool]] = {1: [], 2: []}
        for cell in owned:
            verdicts = checks[cell.cell_id]
            assert [check.metric for check in verdicts] == ["decision"]
            passes[tiers_by_case[cell.case_id]].append(
                all(check.passed for check in verdicts)
            )
        for tier in (1, 2):
            want = row["passed_by_tier"][str(tier)]
            assert len(passes[tier]) == want["total"], (arm, tier)
            assert sum(passes[tier]) == want["passed"], (arm, tier)

        scored = {
            aggregate.tier: aggregate
            for aggregate in aggregates
            if aggregate.candidate == arm
        }
        assert scored[None].n == row["tasks_total"], arm
        assert scored[None].value == sum(passes[1] + passes[2]) / row["tasks_total"]
        for tier in (1, 2):
            want = row["passed_by_tier"][str(tier)]
            assert scored[tier].n == want["total"], (arm, tier)
            assert scored[tier].value == want["passed"] / want["total"], (arm, tier)

        prompt = sum(cell.prompt_tokens for cell in owned)
        completion = sum(cell.completion_tokens for cell in owned)
        # The committed scoreboard folded four judge calls into these totals;
        # the harness scores the same decisions deterministically, without a
        # judge, so its answer tokens are the recorded totals minus that block.
        assert prompt + JUDGE_CALLS * answers["judge"]["prompt_tokens"] == row[
            "prompt_tokens"
        ], arm
        assert completion + JUDGE_CALLS * answers["judge"]["completion_tokens"] == row[
            "completion_tokens"
        ], arm

        price = expected["prices"][expected["model"]]
        table_cost = (
            prompt * price["input_usd_per_million"] / 1_000_000
            + completion * price["output_usd_per_million"] / 1_000_000
            + sum(cell.tool_calls for cell in owned) * price["tool_usd"]
        )
        observed_cost = sum(cell.cost_usd for cell in owned)
        assert observed_cost == pytest.approx(table_cost, abs=1e-12), arm
        assert abs(observed_cost - row["cost_usd"]) <= 0.01, arm
