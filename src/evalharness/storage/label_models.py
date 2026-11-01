"""SQLAlchemy table for the human labels behind judge calibration."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from evalharness.storage.models import Base


class HumanLabelRow(Base):
    """One human verdict on one stored check, keyed by run, cell and metric.

    The composite primary key is the upsert target: reviewing the same check
    again replaces the stored verdict instead of adding a second opinion, so
    the agreement rate never counts one check twice.
    """

    __tablename__ = "human_labels"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cell_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    metric: Mapped[str] = mapped_column(String(200), primary_key=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    operator: Mapped[str] = mapped_column(
        String(200), nullable=False, default="not recorded"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
