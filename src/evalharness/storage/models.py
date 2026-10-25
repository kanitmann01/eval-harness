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
    budget_override_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    # The cost split columns are nullable so a database written before issue
    # #7 can gain them in place; older rows read back as zero.
    answer_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_completion_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    usage_source: Mapped[str] = mapped_column(String(20), nullable=False)
    # Issue #17 attributes cost per provider. Both columns are nullable so a
    # database written before provider support gains them in place; an old row
    # reads back as the provider-agnostic bucket with no judge split.
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    judge_provider_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output: Mapped[dict] = mapped_column(JSON, nullable=False)
    artifact_dir: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProviderCostRow(Base):
    """One provider's share of one run: cells, tokens and the cost split.

    A new table rather than columns on `runs`: a run has a variable number of
    providers, and a wide `provider_1_*`, `provider_2_*` shape would cap it.
    `cost_usd` is the sum of the three parts, so the rows always add up to the
    run's stored cost.
    """

    __tablename__ = "provider_costs"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(80), primary_key=True)
    n_cells: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    judge_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    judge_completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    answer_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    judge_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    tool_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)


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
    """One gate outcome for one run, with the arithmetic that decided it.

    The columns added in issue #8 are nullable on purpose: the migration
    runner can only add nullable columns to an existing database, and a gate
    stored before those columns existed still reads back with its detail
    blob. Every new write fills them.
    """

    __tablename__ = "gates"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.run_id"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    metric: Mapped[str] = mapped_column(String(200), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    candidate: Mapped[str | None] = mapped_column(String(200), nullable=True)
    comparison: Mapped[str | None] = mapped_column(String(4), nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed: Mapped[float | None] = mapped_column(Float, nullable=True)
    n: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    """One encrypted credential plus the metadata the console displays.

    disabled is NOT NULL with a Python-side default: a new row always writes
    it, and the issue #13 migration adds the column to an older database with
    a false server default so every stored row reads back as enabled.
    """

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
    disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
