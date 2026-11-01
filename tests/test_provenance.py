"""Behaviour tests for judge pins, drift and the provenance pages."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from evalharness.core.config import Settings
from evalharness.core.judge_pins import judge_drift, pins_for_run
from evalharness.core.judges import judge_config_hash
from evalharness.core.models import Check, JudgeSpec, RunRecord
from evalharness.storage.db import Database
from evalharness.storage.migrations import SCHEMA_VERSION
from evalharness.storage.models import RunRow
from evalharness.storage.repository import Repository
from tests.helpers import base_spec, write_spec
from tests.test_api_console import _seed

FINISHED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
STARTED = datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc)


def _run(
    spec: dict[str, Any],
    run_id: str = "run-1",
    *,
    finished: datetime | None = FINISHED,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        spec_name="spec",
        spec_snapshot=spec,
        started_at=STARTED,
        finished_at=finished,
    )


def _judged_spec(temperature: float = 0.2) -> dict[str, Any]:
    return {
        "metrics": [
            {"key": "citation_support", "options": {"judge": "strict"}},
            {"key": "exact_match", "options": {}},
        ],
        "judges": [
            {
                "name": "strict",
                "adapter": "judge:scripted",
                "model": "judge-1",
                "options": {"temperature": temperature, "instructions": "strict"},
            }
        ],
    }


def test_pins_fall_back_to_the_spec_and_carry_the_config_hash() -> None:
    spec = _judged_spec()

    pins = pins_for_run(_run(spec), [])

    assert len(pins) == 1
    pin = pins[0]
    assert pin.metric == "citation_support"
    assert pin.judge == "strict"
    assert pin.adapter == "judge:scripted"
    assert pin.model == "judge-1"
    assert pin.temperature == 0.2
    assert pin.options_digest
    assert pin.source == "spec"
    assert pin.last_used_at == FINISHED
    assert pin.config_hash == judge_config_hash(
        JudgeSpec(
            name="strict",
            adapter="judge:scripted",
            model="judge-1",
            options={"temperature": 0.2, "instructions": "strict"},
        )
    )


def test_stored_hashes_are_authoritative_and_flag_within_run_drift() -> None:
    checks = [
        Check(metric="citation_support", passed=True, judge_config_hash="hash-a"),
        Check(metric="citation_support", passed=True, judge_config_hash="hash-b"),
    ]

    pins = pins_for_run(_run(_judged_spec()), checks)

    assert [(pin.config_hash, pin.source) for pin in pins] == [
        ("hash-a", "checks"),
        ("hash-b", "checks"),
    ]
    drifts = judge_drift(_run(_judged_spec()), checks)
    assert len(drifts) == 1
    drift = drifts[0]
    assert drift.kind == "within_run"
    assert drift.judge == "strict"
    assert drift.metrics == ("citation_support",)
    assert drift.hashes == ("hash-a", "hash-b")
    assert drift.runs == ("run-1",)
    assert "not like-for-like" in drift.reason


def test_stored_hashes_without_a_declaration_still_pin_and_drift() -> None:
    checks = [
        Check(metric="m", passed=True, judge_config_hash="hash-a"),
        Check(metric="m", passed=True, judge_config_hash="hash-b"),
    ]

    pins = pins_for_run(_run({}), checks)
    assert len(pins) == 2
    assert {pin.judge for pin in pins} == {None}
    assert {pin.source for pin in pins} == {"checks"}

    drifts = judge_drift(_run({}), checks)
    assert len(drifts) == 1
    assert drifts[0].judge == "unknown judge"
    assert "not like-for-like" in drifts[0].reason


def test_compared_runs_with_different_judge_hashes_are_not_like_for_like() -> None:
    run_a = _run(_judged_spec(0.1), "run-a")
    run_b = _run(_judged_spec(0.2), "run-b")

    drifts = judge_drift(run_a, [], run_b, [])

    assert len(drifts) == 1
    drift = drifts[0]
    assert drift.kind == "across_runs"
    assert drift.judge == "strict"
    assert drift.metrics == ("citation_support",)
    assert len(drift.hashes) == 2
    assert drift.runs == ("run-a", "run-b")
    assert "not like-for-like" in drift.reason

    same = judge_drift(run_a, [], run_a, [])
    assert same == ()

    unjudged = _run({"metrics": [{"key": "exact_match", "options": {}}]}, "run-c")
    assert judge_drift(run_a, [], unjudged, []) == ()


def test_a_version_6_database_gains_the_operator_column(
    evalh_home: Any, workdir: Any
) -> None:
    url = "sqlite:///" + (workdir / "version-6.db").as_posix()
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.save_spec(
        "spec-old", "old-spec", "yaml", "sha", datetime.now(timezone.utc)
    )
    repository.create_run(
        RunRecord(
            run_id="run-old",
            spec_id="spec-old",
            status="ok",
            started_at=datetime.now(timezone.utc),
        )
    )
    with database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN operator")
        connection.execute(text("UPDATE schema_version SET version = 6"))
    database.dispose()

    reopened = Database(url)
    repository = Repository(reopened)
    try:
        old = repository.get_run("run-old")
        assert old is not None
        assert old.operator == ""
        repository.update_run(old.model_copy(update={"operator": "Ken"}))
        assert repository.get_run("run-old").operator == "Ken"
        with reopened.engine.begin() as connection:
            version = connection.exec_driver_sql(
                "SELECT MAX(version) FROM schema_version"
            ).scalar()
        assert version == SCHEMA_VERSION
    finally:
        reopened.dispose()


def _read_run(run_id: str) -> tuple[Repository, Database, RunRecord]:
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    run = repository.get_run(run_id)
    assert run is not None
    return repository, database, run


def test_operator_is_captured_and_trimmed_from_the_environment(
    evalh_home: Any,
    workdir: Any,
    capsys: Any,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EVALH_OPERATOR", "  Ken / FDE  ")
    spec = base_spec()
    spec["name"] = "operator-spec"
    run_id = _seed(capsys, write_spec(workdir / "operator.yaml", spec))

    repository, database, run = _read_run(run_id)
    try:
        assert run.operator == "Ken / FDE"
        assert repository.get_run(run_id).operator == "Ken / FDE"
    finally:
        database.dispose()


def test_operator_reads_as_empty_when_unset_or_null(
    evalh_home: Any,
    workdir: Any,
    capsys: Any,
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("EVALH_OPERATOR", raising=False)
    spec = base_spec()
    spec["name"] = "no-operator-spec"
    run_id = _seed(capsys, write_spec(workdir / "no-operator.yaml", spec))

    repository, database, run = _read_run(run_id)
    try:
        assert run.operator == ""
        assert repository.get_run(run_id).operator == ""
        with database.session() as session:
            row = session.get(RunRow, run_id)
            assert row is not None
            row.operator = None
        assert repository.get_run(run_id).operator == ""
    finally:
        database.dispose()