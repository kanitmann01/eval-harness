"""CLI-level budget, cancel and resume behaviour."""

from __future__ import annotations

import re
import signal
import sqlite3
from pathlib import Path

import pytest

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.cli import main
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import (
    STUB,
    base_spec,
    open_repository,
    run_id_from,
    write_spec,
)


def _single_candidate(
    spec: dict,
    *,
    adapter: str = STUB,
    options: dict | None = None,
    replicas: int = 1,
    concurrency: int = 1,
) -> dict:
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["adapter"] = adapter
    spec["candidates"][0]["options"] = {} if options is None else options
    spec["replicas"] = replicas
    spec["concurrency"] = concurrency
    return spec


def _expensive_cell_spec() -> dict:
    """One cell already costs $2.00, so a $0.50 cap aborts after the first."""
    spec = _single_candidate(
        base_spec(),
        options={"prompt_tokens": 2_000_000, "completion_tokens": 0},
        replicas=2,
    )
    spec["budget_usd"] = 0.5
    spec["prices"] = {"stub-1": {"input": 1.0, "output": 0.0}}
    return spec


def _cell_count(database_path: Path) -> int:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute("select count(*) from cells").fetchone()
    finally:
        connection.close()
    return int(row[0])


def _aggregate_rows(repository: Repository, run_id: str) -> list[tuple]:
    return [
        (row.candidate, row.metric, row.tier, row.n, row.value)
        for row in repository.list_aggregates(run_id)
    ]


def test_estimate_above_cap_refuses_before_any_cell(evalh_home, workdir, capsys):
    spec_data = base_spec()
    spec_data["budget_usd"] = 0.0001  # below the fallback 500+200 median estimate
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "spec error: budget_usd=" in captured.err
    assert "exceeds the cap" in captured.err
    assert "--override-budget" in captured.err
    assert captured.out == ""
    database, repository = open_repository()
    try:
        assert repository.list_runs() == []
    finally:
        database.dispose()
    assert _cell_count(evalh_home / "harness.db") == 0


def test_override_budget_proceeds_and_records_the_override(
    evalh_home, workdir, capsys
):
    spec_data = base_spec()
    spec_data["budget_usd"] = 0.0001
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec), "--override-budget", "2.5"])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "status=ok" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        assert run is not None
        assert run.budget_usd == 2.5
        assert run.budget_override_usd == 2.5
        overridden = [
            event
            for event in repository.list_events(run_id)
            if event.kind == "budget_overridden"
        ]
        assert len(overridden) == 1
        payload = overridden[0].payload
        assert payload["spec_budget_usd"] == 0.0001
        assert payload["budget_usd"] == 2.5
        assert payload["estimate_usd"] > 0
        assert len(repository.list_cells(run_id)) == 2
    finally:
        database.dispose()


def test_launch_estimate_uses_medians_from_an_earlier_run(
    evalh_home, workdir, capsys
):
    spec_data = _single_candidate(
        base_spec(),
        options={"prompt_tokens": 0, "completion_tokens": 0},
    )
    spec_data["budget_usd"] = 0.0001  # below the fallback median estimate
    spec = write_spec(workdir / "spec.yaml", spec_data)

    first = main(["run", str(spec), "--override-budget", "1.0"])

    first_captured = capsys.readouterr()
    assert first == 0, first_captured.err
    assert _cell_count(evalh_home / "harness.db") == 1

    second = main(["run", str(spec)])

    second_captured = capsys.readouterr()
    assert second == 0, second_captured.err
    assert "status=ok" in second_captured.out
    run_id = run_id_from(second_captured.out)
    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id)
        assert len(cells) == 1
        cell = cells[0]
        assert cell.prompt_tokens == 0 and cell.completion_tokens == 0
        assert repository.get_run(run_id).budget_override_usd is None
    finally:
        database.dispose()


def test_launch_estimate_falls_back_when_an_earlier_run_has_no_ok_cell(
    evalh_home, workdir, capsys
):
    spec = write_spec(
        workdir / "spec.yaml",
        _single_candidate(base_spec(), replicas=2),
    )
    assert main(["run", str(spec), "--cancel-after", "0"]) == 0
    capsys.readouterr()

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "status=ok" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        assert len(repository.list_cells(run_id)) == 2
    finally:
        database.dispose()


def test_budget_abort_mid_run_keeps_completed_cells_and_exits_three(
    evalh_home, workdir, capsys
):
    spec = write_spec(workdir / "spec.yaml", _expensive_cell_spec())

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 3
    assert "candidate" in captured.out
    assert "status=budget_exceeded" in captured.out
    assert "reason: budget exceeded" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        assert run is not None
        assert run.status == "budget_exceeded"
        assert run.budget_usd == 0.5
        assert run.cost_usd > run.budget_usd
        assert run.finished_at is not None
        cells = repository.list_cells(run_id)
        assert sorted(cell.status for cell in cells) == ["cancelled", "ok"]
        cancelled = [cell for cell in cells if cell.status == "cancelled"]
        assert "budget exceeded" in cancelled[0].output["reason"]
        assert repository.list_checks(run_id, cancelled[0].cell_id) == []
        kinds = [event.kind for event in repository.list_events(run_id)]
        assert "budget_exceeded" in kinds
    finally:
        database.dispose()


def test_cancel_after_one_cancels_the_rest_and_exits_zero(
    evalh_home, workdir, capsys
):
    spec = write_spec(
        workdir / "spec.yaml",
        _single_candidate(base_spec(), replicas=3),
    )

    code = main(["run", str(spec), "--cancel-after", "1"])

    captured = capsys.readouterr()
    assert code == 0
    assert "candidate" in captured.out
    assert "status=cancelled" in captured.out
    assert "reason: cancelled: --cancel-after 1 reached" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        assert run is not None
        assert run.status == "cancelled"
        assert run.n_cells == 3 and run.n_ok == 1
        statuses = sorted(cell.status for cell in repository.list_cells(run_id))
        assert statuses == ["cancelled", "cancelled", "ok"]
        cancelled = [
            cell
            for cell in repository.list_cells(run_id)
            if cell.status == "cancelled"
        ]
        assert all(
            cell.output["reason"] == "--cancel-after 1 reached"
            for cell in cancelled
        )
        aggregates = repository.list_aggregates(run_id)
        overall = [row for row in aggregates if row.tier is None]
        assert len(overall) == 1
        assert overall[0].n == 1 and overall[0].value == 1.0
        tier_rows = [row for row in aggregates if row.tier == 1]
        assert len(tier_rows) == 1 and tier_rows[0].n == 1
        events = repository.list_events(run_id)
        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        kinds = [event.kind for event in events]
        assert "run_cancelled" in kinds
        assert kinds[-1] == "run_finished"
    finally:
        database.dispose()


def test_cancel_before_any_cell_with_a_failing_gate_exits_one(
    evalh_home, workdir, capsys
):
    spec_data = _single_candidate(base_spec(), replicas=2)
    spec_data["gates"] = [{"metric": "exact_match", "min": 0.5}]
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec), "--cancel-after", "0"])

    captured = capsys.readouterr()
    assert code == 1
    assert "status=cancelled" in captured.out
    assert "reason: cancelled: --cancel-after 0 reached" in captured.out
    assert "FAIL" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id)
        assert len(cells) == 2
        assert all(cell.status == "cancelled" for cell in cells)
        aggregates = repository.list_aggregates(run_id)
        assert {row.tier for row in aggregates} == {None, 1}
        assert all(row.n == 0 and row.value is None for row in aggregates)
        gates = repository.list_gates(run_id)
        assert len(gates) == 1 and gates[0].passed is False
    finally:
        database.dispose()


def test_budget_decides_the_exit_code_when_cancel_also_tripped(
    evalh_home, workdir, capsys
):
    spec = write_spec(workdir / "spec.yaml", _expensive_cell_spec())

    code = main(["run", str(spec), "--cancel-after", "1"])

    captured = capsys.readouterr()
    assert code == 3
    assert "status=budget_exceeded" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        assert repository.get_run(run_id).status == "budget_exceeded"
    finally:
        database.dispose()


def test_sigint_cancels_between_cells_and_restores_the_handler(
    evalh_home, workdir, capsys
):
    spec = write_spec(
        workdir / "spec.yaml",
        _single_candidate(
            base_spec(),
            adapter="test:interrupting",
            replicas=2,
        ),
    )
    previous_handler = signal.getsignal(signal.SIGINT)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    assert "status=cancelled" in captured.out
    assert "reason: cancelled: interrupted (SIGINT)" in captured.out
    assert signal.getsignal(signal.SIGINT) is previous_handler
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        statuses = sorted(cell.status for cell in repository.list_cells(run_id))
        assert statuses == ["cancelled", "ok"]
        kinds = [event.kind for event in repository.list_events(run_id)]
        assert "run_cancelled" in kinds
    finally:
        database.dispose()


def test_resume_skips_ok_cells_and_matches_an_uninterrupted_run(
    evalh_home, workdir, capsys
):
    spec_data = _single_candidate(base_spec(), replicas=3)
    spec = write_spec(workdir / "spec.yaml", spec_data)

    first = main(["run", str(spec), "--cancel-after", "1"])

    first_captured = capsys.readouterr()
    assert first == 0
    run_id = run_id_from(first_captured.out)
    database, repository = open_repository()
    try:
        before = {
            cell.cell_id: cell.model_dump()
            for cell in repository.list_cells(run_id)
        }
        ok_ids = {
            cell.cell_id
            for cell in repository.list_cells(run_id)
            if cell.status == "ok"
        }
        assert len(ok_ids) == 1
    finally:
        database.dispose()

    second = main(["run", str(spec), "--resume", run_id])

    second_captured = capsys.readouterr()
    assert second == 0, second_captured.err
    assert f"run {run_id} status=ok" in second_captured.out
    assert re.search("stub-concise\\s+3\\s+3\\s+0", second_captured.out)
    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id)
        assert len(cells) == 3
        assert all(cell.status == "ok" for cell in cells)
        skipped = next(cell for cell in cells if cell.cell_id in ok_ids)
        assert skipped.model_dump() == before[skipped.cell_id]
        events = repository.list_events(run_id)
        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        kinds = [event.kind for event in events]
        assert "run_resumed" in kinds
        assert kinds.index("run_resumed") > 0
        resumed_event = next(
            event for event in events if event.kind == "run_resumed"
        )
        assert resumed_event.payload == {
            "n_cells": 3,
            "n_skipped": 1,
            "n_remaining": 2,
        }
        assert kinds.count("cell_started") == 3
        assert kinds[-1] == "run_finished"
        resumed_run = repository.get_run(run_id)
        assert resumed_run.status == "ok"
        assert resumed_run.n_cells == 3 and resumed_run.n_ok == 3
        resumed_aggregates = _aggregate_rows(repository, run_id)
    finally:
        database.dispose()

    fresh_url = f"sqlite:///{(workdir / 'fresh.db').as_posix()}"
    fresh = main(
        [
            "run",
            str(spec),
            "--db",
            fresh_url,
            "--artifacts",
            str(workdir / "fresh-artifacts"),
        ]
    )
    fresh_captured = capsys.readouterr()
    assert fresh == 0, fresh_captured.err
    fresh_run_id = run_id_from(fresh_captured.out)
    fresh_database = Database(fresh_url)
    fresh_database.create_all()
    try:
        fresh_aggregates = _aggregate_rows(
            Repository(fresh_database), fresh_run_id
        )
    finally:
        fresh_database.dispose()
    assert resumed_aggregates == fresh_aggregates


def test_resume_refuses_a_finished_run(evalh_home, workdir, capsys):
    spec = write_spec(
        workdir / "spec.yaml",
        _single_candidate(base_spec(), replicas=2),
    )
    assert main(["run", str(spec)]) == 0
    run_id = run_id_from(capsys.readouterr().out)

    code = main(["run", str(spec), "--resume", run_id])

    captured = capsys.readouterr()
    assert code == 2
    assert "spec error: --resume=" in captured.err
    assert "already finished with status=ok" in captured.err
    database, repository = open_repository()
    try:
        assert repository.get_run(run_id).status == "ok"
        assert len(repository.list_cells(run_id)) == 2
    finally:
        database.dispose()


def test_resume_refuses_a_changed_seed(evalh_home, workdir, capsys):
    spec_data = _single_candidate(base_spec(), replicas=2)
    spec = write_spec(workdir / "spec.yaml", spec_data)
    assert main(["run", str(spec), "--cancel-after", "1"]) == 0
    run_id = run_id_from(capsys.readouterr().out)

    spec_data["seed"] = 99
    write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec), "--resume", run_id])

    captured = capsys.readouterr()
    assert code == 2
    assert "spec error: seed=" in captured.err
    assert "does not match the seed 7" in captured.err


def test_resume_refuses_a_changed_spec(evalh_home, workdir, capsys):
    spec_data = _single_candidate(base_spec(), replicas=2)
    spec = write_spec(workdir / "spec.yaml", spec_data)
    assert main(["run", str(spec), "--cancel-after", "1"]) == 0
    run_id = run_id_from(capsys.readouterr().out)

    spec_data["description"] = "edited after the run"
    write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec), "--resume", run_id])

    captured = capsys.readouterr()
    assert code == 2
    assert "spec error: spec=" in captured.err
    assert "sha does not match the snapshot sha" in captured.err


def test_resume_unknown_run_is_a_spec_error(evalh_home, workdir, capsys):
    spec = write_spec(workdir / "spec.yaml", _single_candidate(base_spec()))

    code = main(["run", str(spec), "--resume", "deadbeef"])

    captured = capsys.readouterr()
    assert code == 2
    assert "spec error: --resume='deadbeef'" in captured.err
    assert "no run with this id" in captured.err


def test_resume_after_budget_abort_continues_only_with_a_raised_cap(
    evalh_home, workdir, capsys
):
    spec = write_spec(workdir / "spec.yaml", _expensive_cell_spec())
    assert main(["run", str(spec)]) == 3
    run_id = run_id_from(capsys.readouterr().out)

    blocked = main(["run", str(spec), "--resume", run_id])

    blocked_captured = capsys.readouterr()
    assert blocked == 3
    assert "reason: budget exceeded" in blocked_captured.out
    database, repository = open_repository()
    try:
        assert repository.get_run(run_id).status == "budget_exceeded"
        assert sorted(
            cell.status for cell in repository.list_cells(run_id)
        ) == ["cancelled", "ok"]
    finally:
        database.dispose()

    resumed = main(
        ["run", str(spec), "--resume", run_id, "--override-budget", "10"]
    )

    resumed_captured = capsys.readouterr()
    assert resumed == 0, resumed_captured.err
    assert f"run {run_id} status=ok" in resumed_captured.out
    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        assert run.status == "ok"
        assert run.budget_usd == 10.0
        assert run.budget_override_usd == 10.0
        assert run.n_ok == 2
        assert run.cost_usd == pytest.approx(4.0)
        overridden = [
            event
            for event in repository.list_events(run_id)
            if event.kind == "budget_overridden"
        ]
        assert len(overridden) == 1
        assert overridden[0].payload["estimate_usd"] is None
        assert overridden[0].payload["budget_usd"] == 10.0
    finally:
        database.dispose()


def test_dry_run_over_budget_prints_the_refusal_line(
    evalh_home, workdir, capsys
):
    spec_data = base_spec()
    spec_data["budget_usd"] = 0.0001
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 0
    assert "over budget:" in captured.out
    assert "--override-budget" in captured.out
    assert "dry run: nothing executed" in captured.out
    assert not evalh_home.exists()


def test_cancel_after_rejects_a_negative_count(evalh_home, workdir, capsys):
    spec = write_spec(workdir / "spec.yaml", base_spec())

    code = main(["run", str(spec), "--cancel-after", "-1"])

    captured = capsys.readouterr()
    assert code == 2
    assert "must be zero or greater" in captured.err
    assert captured.out == ""
