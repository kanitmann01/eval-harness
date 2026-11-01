"""Human calibration labels: the record and the store the console uses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from evalharness.core.models import utc_now
from evalharness.storage.db import Database
from evalharness.storage.label_models import HumanLabelRow


@dataclass(frozen=True, slots=True)
class HumanLabel:
    """One human verdict on a stored check.

    operator is the stripped EVALH_OPERATOR value at save time, or
    "not recorded" when the environment does not name anyone.
    """

    run_id: str
    cell_id: str
    metric: str
    passed: bool
    note: str = ""
    operator: str = "not recorded"
    created_at: datetime = field(default_factory=utc_now)


class LabelStore:
    """Read and write human labels in the harness database."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, run_id: str, cell_id: str, metric: str) -> HumanLabel | None:
        """Return the stored label for one check, or None when it is absent."""
        with self._database.session() as session:
            row = session.get(HumanLabelRow, (run_id, cell_id, metric))
            return None if row is None else _label_record(row)

    def list_for_run(self, run_id: str) -> list[HumanLabel]:
        """Return every label for a run, ordered by cell and metric."""
        with self._database.session() as session:
            rows = session.scalars(
                select(HumanLabelRow)
                .where(HumanLabelRow.run_id == run_id)
                .order_by(HumanLabelRow.cell_id, HumanLabelRow.metric)
            ).all()
            return [_label_record(row) for row in rows]

    def upsert(self, label: HumanLabel) -> HumanLabel:
        """Insert or replace one label and return it."""
        with self._database.session() as session:
            row = session.get(
                HumanLabelRow, (label.run_id, label.cell_id, label.metric)
            )
            if row is None:
                row = HumanLabelRow(
                    run_id=label.run_id,
                    cell_id=label.cell_id,
                    metric=label.metric,
                )
                session.add(row)
            row.passed = label.passed
            row.note = label.note
            row.operator = label.operator
            row.created_at = label.created_at
        return label

    def delete(self, run_id: str, cell_id: str, metric: str) -> bool:
        """Delete one label and report whether a row was removed."""
        with self._database.session() as session:
            row = session.get(HumanLabelRow, (run_id, cell_id, metric))
            if row is None:
                return False
            session.delete(row)
            return True


def _label_record(row: HumanLabelRow) -> HumanLabel:
    """Turn one SQLAlchemy row into the public label record."""
    return HumanLabel(
        run_id=row.run_id,
        cell_id=row.cell_id,
        metric=row.metric,
        passed=row.passed,
        note=row.note,
        operator=row.operator,
        created_at=_utc(row.created_at),
    )


def _utc(value: datetime) -> datetime:
    """Return a database timestamp with an explicit UTC timezone.

    SQLite drops the offset on the way in, so a naive value read back is UTC,
    not local time.
    """
    return (
        value.astimezone(timezone.utc)
        if value.tzinfo is not None
        else value.replace(tzinfo=timezone.utc)
    )
