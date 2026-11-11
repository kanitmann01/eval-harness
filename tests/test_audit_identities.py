"""Tests for the shared audit identities: price, cost total and credential rule."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from evalharness.core.credential_names import (
    UNKNOWN_CREDENTIAL_FIX,
    unknown_credential,
)
from evalharness.core.cost import cost_total
from evalharness.core.models import CellRecord, RunRecord, utc_now
from evalharness.core.prices import flatten_price_snapshot, price_table_identity
from evalharness.errors import SpecError
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository

_LEGACY_SNAPSHOT = {"stub-1": {"input": 0.15, "output": 0.6}}
_CURRENT_SNAPSHOT = {
    "version": 2,
    "providers": {
        "": {"stub-1": {"input": 0.15, "output": 0.6}},
        "openai-compatible": {"gpt-4o-mini": {"input": 0.15, "output": 0.6}},
    },
}
_LEGACY_RATES = {"stub-1": {"input": 0.15, "output": 0.6}}
_CURRENT_RATES = {
    "stub-1": {"input": 0.15, "output": 0.6},
    "openai-compatible/gpt-4o-mini": {"input": 0.15, "output": 0.6},
}
# These are the fingerprints the pre-refactor copies produced. A stored receipt
# quotes the same sha256, so these literals guard the audit trail against drift.
_LEGACY_FINGERPRINT = "f8fa5a3959fb38bdae7c17a4f5a7942ac6f547a97a141cf0f37d64049771a35d"
_CURRENT_FINGERPRINT = (
    "cb2767c0f8b89a70528ed65c64fd1dc02410727e5a477c3bcb809a04dd057b59"
)


def _database_url(workdir: Path) -> str:
    return "sqlite:///" + (workdir / "audit.db").as_posix()


def _create_run(database: Database, run_id: str) -> Repository:
    repository = Repository(database)
    repository.save_spec("spec-audit", "audit", "yaml", "sha", utc_now())
    repository.create_run(
        RunRecord(
            run_id=run_id,
            spec_id="spec-audit",
            status="ok",
            started_at=utc_now(),
        )
    )
    return repository


def _seed_cell(database: Database) -> Repository:
    repository = _create_run(database, "run-audit")
    repository.record_cell(
        CellRecord(
            cell_id="cell-audit",
            run_id="run-audit",
            candidate="cand",
            case_id="case",
            replica=1,
            cost_usd=0.6,
            answer_cost_usd=0.1,
            judge_cost_usd=0.2,
            tool_cost_usd=0.3,
        )
    )
    return repository


def test_price_table_identity_keeps_stored_receipt_fingerprints() -> None:
    assert price_table_identity(_LEGACY_SNAPSHOT) == (
        _LEGACY_FINGERPRINT,
        _LEGACY_RATES,
    )
    assert price_table_identity(_CURRENT_SNAPSHOT) == (
        _CURRENT_FINGERPRINT,
        _CURRENT_RATES,
    )


def test_flatten_price_snapshot_accepts_an_empty_mapping() -> None:
    assert flatten_price_snapshot({}) == {}
    assert flatten_price_snapshot({"version": 2, "providers": {}}) == {}


def test_cost_total_sums_the_three_parts() -> None:
    assert cost_total(0.1, 0.2, 0.3) == pytest.approx(0.6)
    assert cost_total(answer_usd=0.5) == pytest.approx(0.5)
    assert cost_total() == 0.0


def test_cost_total_counts_a_missing_split_as_zero() -> None:
    assert cost_total(None, 0.2, None) == pytest.approx(0.2)
    assert cost_total(None, None, None) == 0.0


def test_a_null_split_column_reads_as_zero_and_keeps_the_total(workdir: Path) -> None:
    database = Database(_database_url(workdir))
    database.create_all()
    repository = _seed_cell(database)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE cells SET judge_cost_usd = NULL "
                    "WHERE cell_id = 'cell-audit'"
                )
            )
        cell = repository.list_cells("run-audit")[0]
        assert cell.judge_cost_usd == 0.0
        assert cell.cost_usd == pytest.approx(0.4)
        assert cell.cost_usd == pytest.approx(
            cell.answer_cost_usd + cell.judge_cost_usd + cell.tool_cost_usd
        )
    finally:
        database.dispose()


def test_a_pre_split_row_reads_as_a_zero_split_sum(workdir: Path) -> None:
    database = Database(_database_url(workdir))
    database.create_all()
    repository = _seed_cell(database)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE cells SET answer_cost_usd = NULL, "
                    "judge_cost_usd = NULL, tool_cost_usd = NULL "
                    "WHERE cell_id = 'cell-audit'"
                )
            )
            stored = connection.execute(
                text("SELECT cost_usd FROM cells WHERE cell_id = 'cell-audit'")
            ).scalar_one()
        assert stored == pytest.approx(0.6)
        cell = repository.list_cells("run-audit")[0]
        assert cell.answer_cost_usd == 0.0
        assert cell.judge_cost_usd == 0.0
        assert cell.tool_cost_usd == 0.0
        assert cell.cost_usd == 0.0
    finally:
        database.dispose()


def test_record_cell_round_trips_a_total_without_a_split(workdir: Path) -> None:
    database = Database(_database_url(workdir))
    database.create_all()
    try:
        repository = _create_run(database, "run-total")
        repository.record_cell(
            CellRecord(
                cell_id="cell-total",
                run_id="run-total",
                candidate="cand",
                case_id="case",
                replica=1,
                cost_usd=1.0,
            )
        )
        cell = repository.list_cells("run-total")[0]
        assert cell.cost_usd == pytest.approx(1.0)
        assert cell.answer_cost_usd == pytest.approx(1.0)
        assert cell.judge_cost_usd == 0.0
        assert cell.tool_cost_usd == 0.0
        assert cell.cost_usd == pytest.approx(
            cell.answer_cost_usd + cell.judge_cost_usd + cell.tool_cost_usd
        )
    finally:
        database.dispose()


def test_record_cell_stores_the_split_total_not_the_caller_total(
    workdir: Path,
) -> None:
    database = Database(_database_url(workdir))
    database.create_all()
    try:
        repository = _create_run(database, "run-split")
        repository.record_cell(
            CellRecord(
                cell_id="cell-split",
                run_id="run-split",
                candidate="cand",
                case_id="case",
                replica=1,
                cost_usd=9.0,
                answer_cost_usd=0.25,
                judge_cost_usd=0.5,
                tool_cost_usd=0.75,
            )
        )
        cell = repository.list_cells("run-split")[0]
        assert cell.cost_usd == pytest.approx(1.5)
        assert cell.answer_cost_usd == pytest.approx(0.25)
        assert cell.judge_cost_usd == pytest.approx(0.5)
        assert cell.tool_cost_usd == pytest.approx(0.75)
    finally:
        database.dispose()


def test_unknown_credential_names_the_field_value_and_available_names() -> None:
    error = unknown_credential("missing", available=["beta", "alpha"])
    assert isinstance(error, SpecError)
    assert error.field == "credentials.missing"
    assert error.value == "missing"
    assert error.reason == "unknown credential"
    assert error.fix == (
        f"use one of: alpha, beta, or {UNKNOWN_CREDENTIAL_FIX}"
    )
    assert "'evalh credentials add'" in str(error)
    assert "'evalh credentials list'" in str(error)


def test_unknown_credential_keeps_the_callers_field_and_handles_no_choices() -> None:
    error = unknown_credential(
        "missing", field="candidates[0].credential", available=[]
    )
    assert error.field == "candidates[0].credential"
    assert error.fix == f"use one of: none declared, or {UNKNOWN_CREDENTIAL_FIX}"


def test_unknown_credential_defaults_to_the_credentials_field() -> None:
    error = unknown_credential("missing")
    assert error.field == "credentials.missing"
    assert error.fix == UNKNOWN_CREDENTIAL_FIX
