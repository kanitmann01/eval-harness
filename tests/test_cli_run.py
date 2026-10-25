"""End-to-end CLI tests: run, dry run, artifacts, determinism, gates, budget."""

from __future__ import annotations

import json
from pathlib import Path

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.cli import main
from tests.helpers import (
    STUB,
    STUB_FAILING,
    STUB_SLOW,
    base_spec,
    open_repository,
    run_id_from,
    write_spec,
)


def _cells_by_candidate(repository, run_id):
    return {cell.candidate: cell for cell in repository.list_cells(run_id)}


def test_run_smoke_end_to_end(evalh_home, workdir, capsys):
    spec = write_spec(workdir / "spec.yaml", base_spec())

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "candidate" in captured.out
    assert "stub-concise" in captured.out
    assert "status=ok" in captured.out
    assert captured.err == ""
    run_id = run_id_from(captured.out)

    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        assert run is not None
        assert run.status == "ok"
        assert run.seed == 7
        assert run.n_cells == 2 and run.n_ok == 2
        assert run.cost_usd > 0
        assert run.finished_at is not None
        assert run.harness_sha.startswith("evalharness/")
        assert run.spec_snapshot["name"] == "test-spec"
        assert run.prices_snapshot["stub-1"]["input"] == 0.15

        spec_row = repository.get_spec(run.spec_id)
        assert spec_row is not None
        assert "spec_version: 1" in spec_row["yaml"]

        cells = repository.list_cells(run_id)
        assert len(cells) == 2
        for cell in cells:
            assert cell.status == "ok"
            assert cell.answer == "42"
            assert cell.prompt_tokens == 12
            assert cell.completion_tokens == 8
            assert cell.usage_source == "provider"
            assert cell.cost_usd > 0
            assert cell.artifact_dir is not None
            assert cell.artifact_dir.startswith(
                str(evalh_home / "artifacts" / run_id)
            )
            artifact_dir = Path(cell.artifact_dir)
            assert artifact_dir.is_dir()
            assert (artifact_dir / "cell.json").is_file()
            assert (artifact_dir / "answer.txt").read_text(encoding="utf-8") == "42"
            checks = repository.list_checks(run_id, cell.cell_id)
            assert len(checks) == 1
            assert checks[0].metric == "exact_match"
            assert checks[0].passed is True
            assert checks[0].evidence == {"expected": "42", "actual": "42"}

        by_candidate = _cells_by_candidate(repository, run_id)
        assert by_candidate["stub-concise"].variant["prompt_variant"] == "concise"
        assert by_candidate["stub-concise"].variant["prompt_override"] == (
            "Answer with digits only."
        )
        assert by_candidate["stub-concise"].variant["context"] == []
        assert by_candidate["stub-explained"].variant["prompt_variant"] == (
            "explained"
        )
        assert by_candidate["stub-explained"].variant["context"] == [
            {"chunk_id": "policy-1", "text": "Answer as a bare number."}
        ]

        aggregates = repository.list_aggregates(run_id)
        assert len(aggregates) == 2
        assert all(row.value == 1.0 and row.n == 1 for row in aggregates)
        assert repository.list_gates(run_id) == []

        events = repository.list_events(run_id)
        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        kinds = [event.kind for event in events]
        assert kinds[0] == "run_started"
        assert "cell_started" in kinds
        assert "cell_completed" in kinds
        assert kinds[-1] == "run_finished"
        assert repository.max_event_seq(run_id) == len(events)
        assert repository.max_event_seq("missing-run") == 0
        assert repository.get_run("missing-run") is None
        assert repository.get_spec("missing-spec") is None
    finally:
        database.dispose()

    second = main(["run", str(spec)])
    second_out = capsys.readouterr().out
    assert second == 0
    second_run_id = run_id_from(second_out)
    database, repository = open_repository()
    try:
        assert second_run_id != run_id
        assert {cell.cell_id for cell in repository.list_cells(run_id)} == {
            cell.cell_id for cell in repository.list_cells(second_run_id)
        }
    finally:
        database.dispose()


def test_dry_run_executes_nothing(evalh_home, workdir, capsys):
    spec = write_spec(workdir / "spec.yaml", base_spec())

    code = main(["run", str(spec), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 0
    assert "cells: 2" in captured.out
    assert "stub-concise: 1" in captured.out
    assert "stub-explained: 1" in captured.out
    assert "estimated cost: $" in captured.out
    assert "dry run: nothing executed" in captured.out
    assert not evalh_home.exists()


def test_validate_prints_one_line(evalh_home, workdir, capsys):
    spec = write_spec(workdir / "spec.yaml", base_spec())

    code = main(["validate", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "spec valid: test-spec (2 candidates, 1 metrics)"
    assert captured.err == ""


def test_latency_gate_with_concurrency_warns(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["concurrency"] = 2
    spec_data["metrics"].append(
        {"key": "latency", "adapter": spec_data["metrics"][0]["adapter"], "options": {}}
    )
    spec_data["gates"] = [{"metric": "latency", "min": 0.9}]
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["validate", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    assert "warning:" in captured.err
    assert "concurrency" in captured.err


def test_gate_failure_exits_one(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["options"] = {"answer": "wrong"}
    spec_data["gates"] = [{"metric": "exact_match", "min": 0.9}]
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 1
    assert "FAIL" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        gates = repository.list_gates(run_id)
        assert len(gates) == 1
        assert gates[0].passed is False
        assert gates[0].detail["value"] == 0.0
        assert repository.list_aggregates(run_id)[0].value == 0.0
    finally:
        database.dispose()


def test_budget_abort_exits_three(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["options"] = {
        "prompt_tokens": 2_000_000,
        "completion_tokens": 0,
    }
    spec_data["replicas"] = 2
    spec_data["budget_usd"] = 0.5
    spec_data["prices"] = {"stub-1": {"input": 1.0, "output": 0.0}}
    spec_data["gates"] = []
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 3
    assert "status=budget_exceeded" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        assert run is not None
        assert run.status == "budget_exceeded"
        statuses = sorted(cell.status for cell in repository.list_cells(run_id))
        assert statuses == ["cancelled", "ok"]
        kinds = [event.kind for event in repository.list_events(run_id)]
        assert "budget_exceeded" in kinds
    finally:
        database.dispose()


def test_concurrency_two_keeps_replica_semantics(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["adapter"] = STUB_SLOW
    spec_data["candidates"][0]["options"] = {"prompt": "concise", "context": "none"}
    spec_data["replicas"] = 2
    spec_data["concurrency"] = 2
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id)
        assert len(cells) == 2
        assert sorted(cell.replica for cell in cells) == [1, 2]
        assert all(cell.status == "ok" for cell in cells)
        assert len({cell.cell_id for cell in cells}) == 2
    finally:
        database.dispose()


def test_transport_failure_is_recorded_as_error(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["adapter"] = STUB_FAILING
    spec_data["candidates"][0]["options"] = {}
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    assert "status=partial" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cell = repository.list_cells(run_id)[0]
        assert cell.status == "error"
        assert cell.output["error_type"] == "TransportError"
        assert "stub transport failed" in cell.output["error"]
        assert repository.list_checks(run_id, cell.cell_id) == []
    finally:
        database.dispose()


def test_non_transport_failure_is_not_retried(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["options"] = {"fail": "error"}
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cell = repository.list_cells(run_id)[0]
        assert cell.status == "error"
        assert cell.output["error_type"] == "RuntimeError"
    finally:
        database.dispose()


def test_cli_db_and_artifacts_overrides(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec = write_spec(workdir / "spec.yaml", spec_data)
    database_path = workdir / "custom" / "harness.db"
    artifacts_path = workdir / "custom-artifacts"

    code = main(
        [
            "run",
            str(spec),
            "--db",
            f"sqlite:///{database_path.as_posix()}",
            "--artifacts",
            str(artifacts_path),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    run_id = run_id_from(captured.out)
    assert database_path.is_file()
    assert (artifacts_path / run_id).is_dir()
    assert not (evalh_home / "harness.db").exists()


def test_unwritable_home_is_a_spec_error(evalh_home, workdir, capsys):
    evalh_home.write_text("not a directory", encoding="utf-8")
    spec = write_spec(workdir / "spec.yaml", base_spec())

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "EVALH_HOME" in captured.err


def test_broken_sink_factory_is_a_spec_error(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["sink"] = {"adapter": "test:boom_sink", "options": {}}
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "sink.adapter" in captured.err


def test_secret_is_redacted_from_cells_artifacts_and_events(
    evalh_home, workdir, capsys, monkeypatch
):
    monkeypatch.setenv("EVALH_CREDENTIAL_STUB", "SECRET-CANARY")
    spec_data = base_spec()
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["options"] = {"answer": "SECRET-CANARY"}
    spec = write_spec(workdir / "canary.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cell = repository.list_cells(run_id)[0]
        assert cell.answer == "[redacted:stub]"
        assert "SECRET-CANARY" not in json.dumps(cell.output)
        artifact_dir = Path(cell.artifact_dir)
        assert "SECRET-CANARY" not in (artifact_dir / "cell.json").read_text(
            encoding="utf-8"
        )
        assert "SECRET-CANARY" not in (artifact_dir / "answer.txt").read_text(
            encoding="utf-8"
        )
        events = [
            event.model_dump(mode="json")
            for event in repository.list_events(run_id)
        ]
        assert "SECRET-CANARY" not in json.dumps(events)
    finally:
        database.dispose()


def test_help_and_missing_command(capsys):
    assert main(["--help"]) == 0
    assert "usage: evalh" in capsys.readouterr().out
    assert main([]) == 0
    assert "usage: evalh" in capsys.readouterr().out
    assert main(["not-a-command"]) == 2