"""Migration tests: an issue #2 database keeps working under issue #4."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.cli import main
from evalharness.core.cost import provider_cost_rows
from evalharness.core.models import (
    Aggregate,
    CellRecord,
    GateResult,
    ProviderCost,
    RunRecord,
    utc_now,
)
from evalharness.errors import SchemaMigrationError
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import base_spec, open_repository, run_id_from, write_spec

# The aggregates DDL issue #2 shipped: no tier, no surrogate id, NOT NULL value.
_ISSUE_2_AGGREGATES = (
    "CREATE TABLE aggregates ("
    "run_id VARCHAR(36) NOT NULL, candidate VARCHAR(200) NOT NULL, "
    "metric VARCHAR(200) NOT NULL, n INTEGER NOT NULL, value FLOAT NOT NULL, "
    "ci_low FLOAT, ci_high FLOAT, pass_at_k FLOAT, pass_k FLOAT, "
    "PRIMARY KEY (run_id, candidate, metric), "
    "FOREIGN KEY(run_id) REFERENCES runs (run_id))"
)


def _database_url(workdir: Path, name: str = "old.db") -> str:
    return "sqlite:///" + (workdir / name).as_posix()


def _issue_2_database(url: str) -> None:
    """Store one run and one aggregate in an issue #2 shaped database."""
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.save_spec("spec-old", "old-spec", "yaml", "sha", utc_now())
    repository.create_run(
        RunRecord(
            run_id="run-old",
            spec_id="spec-old",
            status="ok",
            started_at=utc_now(),
        )
    )
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE aggregates")
        connection.exec_driver_sql(_ISSUE_2_AGGREGATES)
        connection.exec_driver_sql(
            "INSERT INTO aggregates (run_id, candidate, metric, n, value) "
            "VALUES ('run-old', 'cand', 'exact_match', 3, 0.5)"
        )
        # Issue #2 had no version marker, so the runner must see version zero.
        connection.exec_driver_sql("DROP TABLE schema_version")
    database.dispose()


def test_issue_2_database_keeps_its_run_readable(
    evalh_home, workdir, monkeypatch
):
    url = _database_url(workdir)
    monkeypatch.setenv("EVALH_DATABASE_URL", url)
    _issue_2_database(url)

    database, repository = open_repository()
    try:
        old_run = repository.get_run("run-old")
        assert old_run is not None
        assert old_run.status == "ok"
        old_rows = repository.list_aggregates("run-old")
        assert [(row.tier, row.n, row.value) for row in old_rows] == [
            (None, 3, 0.5)
        ]
        # The migrated table now accepts what issue #4 needs: an undefined
        # value and a per-tier row.
        repository.record_aggregates(
            [
                Aggregate(
                    run_id="run-old", candidate="cand", metric="exact_match", n=0
                ),
                Aggregate(
                    run_id="run-old",
                    candidate="cand",
                    metric="exact_match",
                    tier=2,
                    n=1,
                    value=1.0,
                ),
            ]
        )
        written = repository.list_aggregates("run-old")
        assert [(row.tier, row.n, row.value) for row in written] == [
            (None, 0, None),
            (2, 1, 1.0),
        ]
    finally:
        database.dispose()


def test_a_new_run_writes_into_a_migrated_database(
    evalh_home, workdir, capsys, monkeypatch
):
    url = _database_url(workdir)
    monkeypatch.setenv("EVALH_DATABASE_URL", url)
    _issue_2_database(url)
    spec = write_spec(workdir / "spec.yaml", base_spec())

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        assert repository.get_run("run-old") is not None
        new_rows = repository.list_aggregates(run_id)
        assert {row.tier for row in new_rows} == {None, 1}
        assert all(row.value == 1.0 for row in new_rows)
    finally:
        database.dispose()


def test_migration_adds_a_missing_nullable_column(evalh_home, workdir):
    url = _database_url(workdir)
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.save_spec("spec-old", "old-spec", "yaml", "sha", utc_now())
    repository.create_run(
        RunRecord(
            run_id="run-old",
            spec_id="spec-old",
            status="ok",
            started_at=utc_now(),
        )
    )
    repository.record_aggregates(
        [
            Aggregate(
                run_id="run-old",
                candidate="cand",
                metric="exact_match",
                n=1,
                value=1.0,
            )
        ]
    )
    with database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE aggregates DROP COLUMN tier")
        connection.exec_driver_sql("DROP TABLE schema_version")
    database.dispose()

    reopened = Database(url)
    reopened.create_all()
    repository = Repository(reopened)
    try:
        assert repository.list_aggregates("run-old")[0].tier is None
        repository.record_aggregates(
            [
                Aggregate(
                    run_id="run-old",
                    candidate="cand",
                    metric="exact_match",
                    tier=2,
                    n=1,
                    value=0.0,
                )
            ]
        )
        assert repository.list_aggregates("run-old")[0].tier == 2
    finally:
        reopened.dispose()


def test_migration_adds_the_budget_override_to_a_version_1_database(
    evalh_home, workdir
):
    url = _database_url(workdir, "version-1.db")
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.save_spec("spec-old", "old-spec", "yaml", "sha", utc_now())
    repository.create_run(
        RunRecord(
            run_id="run-old",
            spec_id="spec-old",
            status="cancelled",
            started_at=utc_now(),
        )
    )
    with database.engine.begin() as connection:
        # Issue #4 shipped schema version 1 without the column issue #3 needs.
        connection.exec_driver_sql(
            "ALTER TABLE runs DROP COLUMN budget_override_usd"
        )
        connection.execute(text("UPDATE schema_version SET version = 1"))
    database.dispose()

    reopened = Database(url)
    reopened.create_all()
    repository = Repository(reopened)
    try:
        run = repository.get_run("run-old")
        assert run is not None
        assert run.budget_override_usd is None
        repository.update_run(
            run.model_copy(update={"budget_override_usd": 2.5})
        )
        assert repository.get_run("run-old").budget_override_usd == 2.5
    finally:
        reopened.dispose()


def test_migration_adds_gate_arithmetic_columns(evalh_home, workdir):
    url = _database_url(workdir)
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.save_spec("spec-old", "old-spec", "yaml", "sha", utc_now())
    repository.create_run(
        RunRecord(
            run_id="run-old",
            spec_id="spec-old",
            status="ok",
            started_at=utc_now(),
        )
    )
    repository.record_gates(
        "run-old",
        [
            GateResult(
                name="exact_match",
                metric="exact_match",
                passed=False,
                detail={"reason": "old shape"},
            )
        ],
    )
    with database.engine.begin() as connection:
        for column in ("candidate", "comparison", "threshold", "observed", "n"):
            connection.exec_driver_sql(f"ALTER TABLE gates DROP COLUMN {column}")
        connection.exec_driver_sql("DROP TABLE schema_version")
    database.dispose()

    reopened = Database(url)
    reopened.create_all()
    repository = Repository(reopened)
    try:
        old_gate = repository.list_gates("run-old")[0]
        assert old_gate.passed is False
        assert old_gate.detail["reason"] == "old shape"
        assert old_gate.comparison == ""
        assert old_gate.observed is None
        assert old_gate.n == 0
        repository.record_gates(
            "run-old",
            [
                GateResult(
                    name="exact_match",
                    metric="exact_match",
                    passed=True,
                    comparison=">=",
                    threshold=0.9,
                    observed=1.0,
                    n=2,
                )
            ],
        )
        gate = repository.list_gates("run-old")[0]
        assert (gate.comparison, gate.threshold) == (">=", 0.9)
        assert (gate.observed, gate.n) == (1.0, 2)
        with reopened.engine.begin() as connection:
            version = connection.exec_driver_sql(
                "SELECT MAX(version) FROM schema_version"
            ).scalar()
        assert version == 6
    finally:
        reopened.dispose()


def test_migration_refuses_a_missing_not_null_column(evalh_home, workdir):
    url = _database_url(workdir)
    database = Database(url)
    database.create_all()
    with database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN n_ok")
        connection.exec_driver_sql("DROP TABLE schema_version")
    database.dispose()

    with pytest.raises(SchemaMigrationError) as raised:
        Database(url)

    assert "runs.n_ok" in str(raised.value)
    assert "NOT NULL" in str(raised.value)
    assert "Fix:" in str(raised.value)


def test_migration_adds_the_cost_split_to_a_version_3_database(evalh_home, workdir):
    url = _database_url(workdir, "version-3.db")
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.save_spec("spec-old", "old-spec", "yaml", "sha", utc_now())
    repository.create_run(
        RunRecord(
            run_id="run-old",
            spec_id="spec-old",
            status="ok",
            started_at=utc_now(),
        )
    )
    repository.record_cell(
        CellRecord(
            cell_id="cell-old",
            run_id="run-old",
            candidate="cand",
            case_id="case",
            replica=1,
            cost_usd=0.5,
        )
    )
    with database.engine.begin() as connection:
        for column in (
            "answer_cost_usd",
            "judge_cost_usd",
            "tool_cost_usd",
            "judge_prompt_tokens",
            "judge_completion_tokens",
        ):
            connection.exec_driver_sql(f"ALTER TABLE cells DROP COLUMN {column}")
        connection.execute(text("UPDATE schema_version SET version = 3"))
    database.dispose()

    reopened = Database(url)
    repository = Repository(reopened)
    try:
        old = repository.list_cells("run-old")[0]
        assert old.answer_cost_usd == 0.0
        assert old.judge_cost_usd == 0.0
        assert old.tool_cost_usd == 0.0
        assert old.judge_prompt_tokens == 0
        assert old.judge_completion_tokens == 0

        repository.record_cell(
            old.model_copy(
                update={
                    "answer_cost_usd": 0.25,
                    "judge_cost_usd": 0.5,
                    "tool_cost_usd": 0.75,
                    "judge_prompt_tokens": 11,
                    "judge_completion_tokens": 3,
                }
            )
        )
        written = repository.list_cells("run-old")[0]
        assert written.answer_cost_usd == 0.25
        assert written.judge_cost_usd == 0.5
        assert written.tool_cost_usd == 0.75
        assert written.judge_prompt_tokens == 11
        assert written.judge_completion_tokens == 3
    finally:
        reopened.dispose()

def test_migration_adds_credentials_disabled(evalh_home, workdir):
    url = _database_url(workdir, "version-4.db")
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.add_credential(
        "old", "openai", b"ciphertext", created_at=utc_now()
    )
    with database.engine.begin() as connection:
        # Issue #9 shipped the credentials table without issue #13's column.
        connection.exec_driver_sql("ALTER TABLE credentials DROP COLUMN disabled")
        connection.execute(text("UPDATE schema_version SET version = 4"))
    database.dispose()

    reopened = Database(url)
    repository = Repository(reopened)
    try:
        old = repository.list_credentials()[0]
        assert old.disabled is False
        assert repository.set_credential_disabled("old", True).disabled is True
        assert repository.set_credential_disabled("old", False).disabled is False
        with reopened.engine.begin() as connection:
            version = connection.exec_driver_sql(
                "SELECT MAX(version) FROM schema_version"
            ).scalar()
        assert version == 6
    finally:
        reopened.dispose()


def test_migration_adds_provider_attribution_to_a_version_4_database(
    evalh_home, workdir
):
    url = _database_url(workdir, "version-4-provider.db")
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.save_spec("spec-old", "old-spec", "yaml", "sha", utc_now())
    repository.create_run(
        RunRecord(
            run_id="run-old",
            spec_id="spec-old",
            status="ok",
            started_at=utc_now(),
        )
    )
    repository.record_cell(
        CellRecord(
            cell_id="cell-old",
            run_id="run-old",
            candidate="cand",
            case_id="case",
            replica=1,
            judge_cost_usd=0.25,
            judge_prompt_tokens=4,
            judge_completion_tokens=2,
        )
    )
    with database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE cells DROP COLUMN provider")
        connection.exec_driver_sql(
            "ALTER TABLE cells DROP COLUMN judge_provider_usage"
        )
        connection.exec_driver_sql("DROP TABLE provider_costs")
        connection.execute(text("UPDATE schema_version SET version = 4"))
    database.dispose()

    reopened = Database(url)
    reopened.create_all()
    repository = Repository(reopened)
    try:
        old = repository.list_cells("run-old")[0]
        assert old.provider == ""
        assert old.judge_provider_usage == {}
        # The legacy cell still attributes its judge cost to its own provider.
        costs = provider_cost_rows("run-old", [old])
        assert [(row.provider, row.judge_cost_usd) for row in costs] == [
            ("", 0.25)
        ]

        repository.record_provider_costs(
            "run-old",
            [
                ProviderCost(
                    run_id="run-old",
                    provider="openai-compatible",
                    n_cells=1,
                    prompt_tokens=10,
                    completion_tokens=5,
                    judge_prompt_tokens=4,
                    judge_completion_tokens=2,
                    answer_cost_usd=0.5,
                    judge_cost_usd=0.25,
                    tool_cost_usd=0.0,
                    cost_usd=0.75,
                )
            ],
        )
        written = repository.list_provider_costs("run-old")
        assert [(row.provider, row.cost_usd) for row in written] == [
            ("openai-compatible", 0.75)
        ]
        with reopened.engine.begin() as connection:
            version = connection.execute(
                text("SELECT MAX(version) FROM schema_version")
            ).scalar()
        assert version == 6
    finally:
        reopened.dispose()
