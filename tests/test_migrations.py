"""Migration tests: an issue #2 database keeps working under issue #4."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.cli import main
from evalharness.core.models import Aggregate, RunRecord, utc_now
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
