"""Calibration core behaviour: agreement arithmetic over stored records.

The tests hand-build a run, its cells, checks and human labels, then read the
public report. No database and no private helper is involved except in the
label-store round trip, which drives the storage API the console uses.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect

from evalharness.core.calibration import calibration_report
from evalharness.core.models import CellRecord, Check, RunRecord
from evalharness.core.stats import wilson_interval
from evalharness.storage.db import Database
from evalharness.storage.labels import HumanLabel, LabelStore
from evalharness.storage.models import Base

HASH_A = "a" * 64
HASH_B = "b" * 64
T1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc)


def _run(snapshot: dict | None = None) -> RunRecord:
    """Return a run with an optional judge snapshot."""
    return RunRecord(run_id="run-1", spec_snapshot=snapshot or {})


def _cell(cell_id: str, candidate: str, case_id: str) -> CellRecord:
    """Return one completed cell for a report."""
    return CellRecord(
        cell_id=cell_id,
        run_id="run-1",
        candidate=candidate,
        case_id=case_id,
        replica=1,
    )


def _check(
    metric: str = "exact_match",
    passed: bool = True,
    reason: str = "",
    config_hash: str | None = None,
) -> Check:
    """Return one stored metric check."""
    return Check(
        metric=metric,
        passed=passed,
        reason=reason,
        judge_config_hash=config_hash,
    )


def _label(
    cell_id: str,
    metric: str = "exact_match",
    passed: bool = True,
    *,
    note: str = "",
    operator: str = "alice",
    created_at: datetime = T1,
) -> HumanLabel:
    """Return one human label for the hand-built run."""
    return HumanLabel(
        run_id="run-1",
        cell_id=cell_id,
        metric=metric,
        passed=passed,
        note=note,
        operator=operator,
        created_at=created_at,
    )


def _judge_snapshot() -> dict:
    """Return the snapshot shape the executor stores for a judged metric."""
    return {
        "metrics": [
            {
                "key": "citation_support",
                "adapter": "evalharness.adapters.metrics.citation_support:build",
                "options": {"judge": "judge-a"},
            },
            {
                "key": "judge_pass",
                "adapter": "evalharness.adapters.metrics.judge_pass:build",
                "options": {"judge": "judge-b"},
            },
        ],
        "judges": [
            {
                "name": "judge-a",
                "adapter": "evalharness.adapters.judges.scripted:build",
                "model": "model-a",
                "options": {},
            },
            {
                "name": "judge-b",
                "adapter": "evalharness.adapters.judges.scripted:build",
                "model": "model-b",
                "options": {},
            },
        ],
    }


def test_report_counts_labelled_agreements_unlabelled_and_the_interval() -> None:
    cells = [
        _cell("cell-1", "cand-a", "case-1"),
        _cell("cell-2", "cand-b", "case-2"),
    ]
    checks = {
        "cell-1": [_check(passed=True)],
        "cell-2": [_check(passed=True), _check(metric="contains", passed=False)],
    }
    labels = [
        _label("cell-1", passed=True, created_at=T1),
        _label("cell-2", passed=False, created_at=T2),
    ]

    report = calibration_report(_run(), cells, checks, labels)

    assert report.run_id == "run-1"
    assert report.labelled == 2
    assert report.unlabelled == 1
    assert report.agreements == 1
    assert report.agreement_rate == 0.5
    low, high = wilson_interval(1, 2)
    assert report.interval_low == pytest.approx(low)
    assert report.interval_high == pytest.approx(high)
    assert report.last_validated_at == T2


def test_disagreement_rows_carry_both_verdicts_and_the_cell_link() -> None:
    cells = [_cell("cell-1", "cand-a", "case-1")]
    checks = {"cell-1": [_check(passed=True, reason="the answer is 42")]}
    labels = [
        _label("cell-1", passed=False, note="the unit is missing", operator="bob")
    ]

    report = calibration_report(_run(), cells, checks, labels)

    assert report.agreements == 0
    assert report.agreement_rate == 0.0
    assert len(report.disagreements) == 1
    row = report.disagreements[0]
    assert row.cell_id == "cell-1"
    assert row.case_id == "case-1"
    assert row.candidate == "cand-a"
    assert row.metric == "exact_match"
    assert row.passed is True
    assert row.reason == "the answer is 42"
    assert row.human_passed is False
    assert row.human_note == "the unit is missing"
    assert row.operator == "bob"
    assert row.agreement is False
    assert row.cell_url == "/runs/run-1/cells/cell-1"


def test_per_judge_breakdown_uses_snapshot_names_models_and_hashes() -> None:
    cells = [
        _cell("cell-1", "cand-a", "case-1"),
        _cell("cell-2", "cand-b", "case-2"),
    ]
    checks = {
        "cell-1": [
            _check(metric="citation_support", passed=True, config_hash=HASH_A),
            _check(metric="judge_pass", passed=False, config_hash=HASH_B),
        ],
        "cell-2": [
            _check(metric="citation_support", passed=False, config_hash=HASH_A),
            _check(metric="exact_match", passed=True),
        ],
    }
    labels = [
        _label("cell-1", "citation_support", passed=True, created_at=T1),
        _label("cell-2", "citation_support", passed=True, created_at=T3),
        _label("cell-1", "judge_pass", passed=False, created_at=T2),
    ]

    report = calibration_report(_run(_judge_snapshot()), cells, checks, labels)

    by_name = {judge.name: judge for judge in report.judges}
    judge_a = by_name["judge-a"]
    assert judge_a.model == "model-a"
    assert judge_a.config_hash == HASH_A
    assert judge_a.labelled == 2
    assert judge_a.agreements == 1
    assert judge_a.agreement_rate == 0.5
    assert judge_a.last_validated_at == T3
    judge_b = by_name["judge-b"]
    assert (judge_b.model, judge_b.labelled, judge_b.agreements) == (
        "model-b",
        1,
        1,
    )
    assert judge_b.last_validated_at == T2
    deterministic = [judge for judge in report.judges if judge.config_hash is None]
    assert len(deterministic) == 1
    assert deterministic[0].name is None
    assert deterministic[0].model is None
    assert deterministic[0].labelled == 0
    assert deterministic[0].agreement_rate is None


def test_judge_identity_handles_undeclared_metrics_and_orphan_judges() -> None:
    snapshot = {
        "metrics": [
            {"key": "undecided_judge", "options": {}},
            {"key": "numeric_judge", "options": {"judge": 7}},
            {"key": "orphan_judge", "options": {"judge": "judge-z"}},
        ],
        "judges": [],
    }
    cells = [_cell("cell-1", "cand-a", "case-1")]
    checks = {
        "cell-1": [
            _check(metric="undecided_judge", config_hash=HASH_A),
            _check(metric="numeric_judge", config_hash=HASH_B),
            _check(metric="orphan_judge", config_hash=HASH_A),
            _check(metric="missing_metric", config_hash=HASH_B),
        ]
    }

    report = calibration_report(_run(snapshot), cells, checks, [])

    rows = {row.metric: row for row in report.checks}

    def identity(metric: str) -> tuple[str | None, str | None]:
        row = rows[metric]
        return row.judge_name, row.judge_model

    assert identity("undecided_judge") == (None, None)
    assert identity("numeric_judge") == (None, None)
    assert identity("orphan_judge") == ("judge-z", None)
    assert identity("missing_metric") == (None, None)
    assert len(report.judges) == 3


def test_zero_labels_is_reported_without_a_rate_or_an_error() -> None:
    cells = [_cell("cell-1", "cand-a", "case-1")]
    checks = {
        "cell-1": [
            _check(metric="citation_support", passed=True, config_hash=HASH_A)
        ]
    }

    report = calibration_report(_run(_judge_snapshot()), cells, checks, [])

    assert report.labelled == 0
    assert report.unlabelled == 1
    assert report.agreements == 0
    assert report.agreement_rate is None
    assert report.interval_low is None
    assert report.interval_high is None
    assert report.last_validated_at is None
    assert report.disagreements == ()
    assert len(report.judges) == 1
    assert report.judges[0].labelled == 0
    assert report.judges[0].agreement_rate is None
    assert report.judges[0].last_validated_at is None


def test_stale_labels_without_a_stored_check_are_ignored() -> None:
    cells = [_cell("cell-1", "cand-a", "case-1")]
    checks = {"cell-1": [_check(passed=True)]}
    labels = [
        _label("missing-cell", passed=False, created_at=T1),
        _label("cell-1", "missing-metric", passed=False, created_at=T2),
    ]

    report = calibration_report(_run(), cells, checks, labels)

    assert report.labelled == 0
    assert report.unlabelled == 1
    assert report.last_validated_at is None
    assert report.checks[0].label is None
    assert report.disagreements == ()


def test_empty_run_reports_no_rate_and_no_disagreements() -> None:
    report = calibration_report(_run(), [], {}, [])

    assert report.labelled == 0
    assert report.unlabelled == 0
    assert report.agreement_rate is None
    assert report.interval_low is None
    assert report.interval_high is None
    assert report.last_validated_at is None
    assert report.judges == ()
    assert report.disagreements == ()
    assert report.checks == ()


def test_an_existing_database_gains_the_human_label_table(workdir) -> None:
    path = workdir / "existing.db"
    url = "sqlite:///" + path.as_posix()
    old_engine = create_engine(url)
    try:
        old_tables = [
            table
            for table in Base.metadata.sorted_tables
            if table.name != "human_labels"
        ]
        Base.metadata.create_all(old_engine, tables=old_tables)
    finally:
        old_engine.dispose()
    check_engine = create_engine(url)
    try:
        assert "human_labels" not in inspect(check_engine).get_table_names()
    finally:
        check_engine.dispose()

    database = Database(url)
    database.create_all()
    try:
        assert "human_labels" in inspect(database.engine).get_table_names()
        store = LabelStore(database)
        label = HumanLabel(
            run_id="run-1",
            cell_id="cell-1",
            metric="exact_match",
            passed=True,
        )
        assert store.upsert(label) == label
        assert store.get("run-1", "cell-1", "exact_match") == label
    finally:
        database.dispose()


def test_label_store_round_trips_upserts_lists_and_deletes(workdir) -> None:
    database = Database("sqlite:///" + (workdir / "labels.db").as_posix())
    database.create_all()
    try:
        store = LabelStore(database)
        assert store.get("run-1", "cell-1", "exact_match") is None
        first = HumanLabel(
            run_id="run-1",
            cell_id="cell-2",
            metric="exact_match",
            passed=True,
            note="first",
            operator="alice",
            created_at=T1,
        )
        second = HumanLabel(
            run_id="run-1",
            cell_id="cell-1",
            metric="contains",
            passed=False,
            note="second",
            operator="bob",
            created_at=T2,
        )
        store.upsert(first)
        store.upsert(second)
        stored = store.get("run-1", "cell-2", "exact_match")
        assert stored == first
        assert [item.cell_id for item in store.list_for_run("run-1")] == [
            "cell-1",
            "cell-2",
        ]
        assert store.list_for_run("run-2") == []

        updated = HumanLabel(
            run_id="run-1",
            cell_id="cell-2",
            metric="exact_match",
            passed=False,
            note="changed",
            operator="carol",
            created_at=T3,
        )
        store.upsert(updated)
        assert store.get("run-1", "cell-2", "exact_match") == updated
        assert len(store.list_for_run("run-1")) == 2

        assert store.delete("run-1", "cell-2", "exact_match") is True
        assert store.delete("run-1", "cell-2", "exact_match") is False
        assert store.get("run-1", "cell-2", "exact_match") is None
    finally:
        database.dispose()
