"""SQLAlchemy 2.0 tables for the harness database.

Only harness data lives here; no foreign key crosses into a candidate's own
database. JSON columns stay portable between SQLite and PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every harness table."""


class SpecRow(Base):
    """One stored spec snapshot."""

    __tablename__ = "eval_specs"

    spec_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    yaml: Mapped[str] = mapped_column(Text, nullable=False)
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RunRow(Base):
    """One run and the snapshots it was launched from."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    spec_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_specs.spec_id"), nullable=False
    )
    spec_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    prices_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    harness_sha: Mapped[str] = mapped_column(String(120), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_cells: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CellRow(Base):
    """One executed candidate x case x replica."""

    __tablename__ = "cells"

    # Cell ids are deterministic across runs, so identity is (run_id, cell_id).
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id"), primary_key=True
    )
    cell_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    candidate: Mapped[str] = mapped_column(String(200), nullable=False)
    case_id: Mapped[str] = mapped_column(String(200), nullable=False)
    replica: Mapped[int] = mapped_column(Integer, nullable=False)
    variant: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    wall_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    usage_source: Mapped[str] = mapped_column(String(20), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output: Mapped[dict] = mapped_column(JSON, nullable=False)
    artifact_dir: Mapped[str | None] = mapped_column(Text, nullable=True)


class CheckRow(Base):
    """One metric verdict for one cell."""

    __tablename__ = "checks"

    # Checks are per run as well, because a cell id repeats across runs.
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cell_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    metric: Mapped[str] = mapped_column(String(200), primary_key=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    judge_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AggregateRow(Base):
    """One candidate's value for one metric in one run, overall or per tier.

    The surrogate id exists because the overall row carries a NULL tier and a
    primary key cannot hold a NULL portably; a run's rows are replaced as a
    group, so uniqueness is a repository guarantee, not a constraint.
    """

    __tablename__ = "aggregates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id"), nullable=False
    )
    candidate: Mapped[str] = mapped_column(String(200), nullable=False)
    metric: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    pass_at_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    pass_k: Mapped[float | None] = mapped_column(Float, nullable=True)


class GateRow(Base):
    """One gate outcome for one run."""

    __tablename__ = "gates"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    metric: Mapped[str] = mapped_column(String(200), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False)


class EventRow(Base):
    """One append-only run event, ordered by per-run sequence."""

    __tablename__ = "events"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class CredentialRow(Base):
    """One encrypted credential; the vault implementation arrives in issue #6."""

    __tablename__ = "credentials"

    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_ok_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
