"""The only code in the harness that talks SQL."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from evalharness.core.cost import cost_total
from evalharness.core.credential_names import unknown_credential
from evalharness.core.events import Event
from evalharness.core.models import (
    Aggregate,
    CellRecord,
    Check,
    CredentialRecord,
    EncryptedCredential,
    GateResult,
    ProviderCost,
    RunRecord,
    utc_now,
)
from evalharness.errors import SpecError
from evalharness.storage.db import Database
from evalharness.storage.models import (
    AggregateRow,
    CellRow,
    CheckRow,
    CredentialRow,
    EventRow,
    GateRow,
    ProviderCostRow,
    RunRow,
    SpecRow,
)

MAX_LAST_ERROR_CHARS = 500
"""Bound on a stored verification error; provider bodies can be whole pages."""


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One console run-list row: identity, outcome and computed aggregates."""

    run_id: str
    spec_name: str
    status: str
    cost_usd: float
    pass_rate: float | None
    duration_s: float | None
    n_cells: int
    n_ok: int
    started_at: datetime
    finished_at: datetime | None
    candidates: tuple[str, ...]
    metrics: tuple[str, ...]
    failed_gates: tuple[str, ...]
    spec_snapshot: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RunFilters:
    """The candidate and metric names available to the run-list filter."""

    candidates: tuple[str, ...]
    metrics: tuple[str, ...]


class Repository:
    """Read and write harness records; callers never see ORM rows."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @property
    def database(self) -> Database:
        """Return the database this repository reads and writes."""
        return self._database

    def ping(self) -> None:
        """Execute a trivial query, raising when the database does not answer.

        The health endpoint calls this instead of opening a second connection
        or parsing the engine URL, so the probe proves the same path the
        console uses and never echoes a password embedded in the URL.
        """
        with self._database.engine.connect() as connection:
            connection.execute(select(1)).scalar_one()

    def save_spec(
        self,
        spec_id: str,
        name: str,
        yaml_text: str,
        sha: str,
        created_at: Any,
    ) -> None:
        """Store one spec text snapshot."""
        with self._database.session() as session:
            session.merge(
                SpecRow(
                    spec_id=spec_id,
                    name=name,
                    yaml=yaml_text,
                    sha=sha,
                    created_at=created_at,
                )
            )

    def get_spec(self, spec_id: str) -> dict[str, Any] | None:
        """Read one stored spec snapshot as a plain mapping."""
        with self._database.session() as session:
            row = session.get(SpecRow, spec_id)
            if row is None:
                return None
            return {
                "spec_id": row.spec_id,
                "name": row.name,
                "yaml": row.yaml,
                "sha": row.sha,
                "created_at": row.created_at,
            }

    def create_run(self, run: RunRecord) -> None:
        """Insert a run before execution starts."""
        with self._database.session() as session:
            session.add(_run_row(run))

    def update_run(self, run: RunRecord) -> None:
        """Merge a run's current state into storage."""
        with self._database.session() as session:
            session.merge(_run_row(run))

    def get_run(self, run_id: str) -> RunRecord | None:
        """Read one run by id."""
        with self._database.session() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                return None
            return _run_record(row)

    def list_runs(self) -> list[RunRecord]:
        """Return every run, most recently started first."""
        with self._database.session() as session:
            rows = session.scalars(
                select(RunRow).order_by(RunRow.started_at.desc())
            ).all()
            return [_run_record(row) for row in rows]

    def latest_run_for_spec(self, spec_sha: str) -> RunRecord | None:
        """Return the newest run recorded from a spec snapshot with this sha."""
        with self._database.session() as session:
            row = session.scalars(
                select(RunRow)
                .join(SpecRow, RunRow.spec_id == SpecRow.spec_id)
                .where(SpecRow.sha == spec_sha)
                .order_by(RunRow.started_at.desc())
                .limit(1)
            ).first()
            if row is None:
                return None
            return _run_record(row)

    def list_run_summaries(
        self,
        candidate: str | None = None,
        metric: str | None = None,
    ) -> list[RunSummary]:
        """Return runs newest first, optionally narrowed by candidate or metric.

        The filters are existence tests over stored cells and aggregates, so a
        run whose metric has no samples still matches that metric.
        """
        with self._database.session() as session:
            statement = select(RunRow).order_by(
                RunRow.started_at.desc(), RunRow.run_id
            )
            if candidate:
                statement = statement.where(
                    select(CellRow.cell_id)
                    .where(
                        CellRow.run_id == RunRow.run_id,
                        CellRow.candidate == candidate,
                    )
                    .exists()
                )
            if metric:
                statement = statement.where(
                    select(AggregateRow.id)
                    .where(
                        AggregateRow.run_id == RunRow.run_id,
                        AggregateRow.metric == metric,
                    )
                    .exists()
                )
            rows = session.scalars(statement).all()
            totals = {
                run_id: (int(passed or 0), int(total or 0))
                for run_id, total, passed in session.execute(
                    select(
                        CheckRow.run_id,
                        func.count(CheckRow.metric),
                        func.sum(case((CheckRow.passed, 1), else_=0)),
                    ).group_by(CheckRow.run_id)
                ).all()
            }
            candidates = _group_names(
                session.execute(
                    select(CellRow.run_id, CellRow.candidate).distinct()
                ).all()
            )
            metrics = _group_names(
                session.execute(
                    select(CheckRow.run_id, CheckRow.metric).distinct()
                ).all()
            )
            for run_id, name in session.execute(
                select(AggregateRow.run_id, AggregateRow.metric).distinct()
            ).all():
                metrics.setdefault(run_id, set()).add(name)
            failed_gates = _group_names(
                session.execute(
                    select(GateRow.run_id, GateRow.metric).where(
                        GateRow.passed.is_(False)
                    )
                ).all()
            )
            return [
                _run_summary(
                    row,
                    totals.get(row.run_id, (0, 0)),
                    candidates.get(row.run_id, set()),
                    metrics.get(row.run_id, set()),
                    failed_gates.get(row.run_id, set()),
                )
                for row in rows
            ]

    def list_run_filters(self) -> RunFilters:
        """Return the candidate and metric names stored across every run."""
        with self._database.session() as session:
            candidates = session.scalars(
                select(CellRow.candidate).distinct().order_by(CellRow.candidate)
            ).all()
            metrics = set(
                session.scalars(select(CheckRow.metric).distinct()).all()
            )
            metrics.update(
                session.scalars(select(AggregateRow.metric).distinct()).all()
            )
        return RunFilters(
            candidates=tuple(candidates), metrics=tuple(sorted(metrics))
        )

    def get_cell(self, run_id: str, cell_id: str) -> CellRecord | None:
        """Read one cell by run and deterministic cell id."""
        with self._database.session() as session:
            row = session.get(CellRow, (run_id, cell_id))
            if row is None:
                return None
            return _cell_record(row)

    def artifact_path(self, run_id: str, cell_id: str) -> Path | None:
        """Return the artifact directory the database stored for one cell."""
        cell = self.get_cell(run_id, cell_id)
        if cell is None:
            return None
        return Path(cell.artifact_dir) if cell.artifact_dir else None

    def record_cell(self, cell: CellRecord) -> None:
        """Persist one cell as soon as it finishes."""
        with self._database.session() as session:
            session.merge(_cell_row(cell))

    def list_cells(self, run_id: str) -> list[CellRecord]:
        """Return every cell for a run in a stable order."""
        with self._database.session() as session:
            rows = session.scalars(
                select(CellRow)
                .where(CellRow.run_id == run_id)
                .order_by(CellRow.candidate, CellRow.case_id, CellRow.replica)
            ).all()
            return [_cell_record(row) for row in rows]

    def record_checks(self, run_id: str, cell_id: str, checks: list[Check]) -> None:
        """Replace a cell's checks, keeping resume idempotent."""
        with self._database.session() as session:
            session.execute(
                delete(CheckRow).where(
                    CheckRow.run_id == run_id, CheckRow.cell_id == cell_id
                )
            )
            for check in checks:
                session.add(_check_row(run_id, cell_id, check))

    def list_checks(self, run_id: str, cell_id: str) -> list[Check]:
        """Return one cell's checks ordered by metric."""
        with self._database.session() as session:
            rows = session.scalars(
                select(CheckRow)
                .where(CheckRow.run_id == run_id, CheckRow.cell_id == cell_id)
                .order_by(CheckRow.metric)
            ).all()
            return [_check_record(row) for row in rows]

    def list_checks_for_run(self, run_id: str) -> dict[str, list[Check]]:
        """Return every cell's checks for a run, keyed by cell id."""
        with self._database.session() as session:
            rows = session.scalars(
                select(CheckRow)
                .where(CheckRow.run_id == run_id)
                .order_by(CheckRow.cell_id, CheckRow.metric)
            ).all()
        checks: dict[str, list[Check]] = {}
        for row in rows:
            checks.setdefault(row.cell_id, []).append(_check_record(row))
        return checks

    def record_aggregates(self, aggregates: list[Aggregate]) -> None:
        """Replace a run's aggregates, keeping repeated scoring idempotent."""
        if not aggregates:
            return
        run_id = aggregates[0].run_id
        with self._database.session() as session:
            session.execute(delete(AggregateRow).where(AggregateRow.run_id == run_id))
            for aggregate in aggregates:
                session.add(_aggregate_row(aggregate))

    def list_aggregates(self, run_id: str) -> list[Aggregate]:
        """Return a run's aggregates: overall rows first, then each tier."""
        with self._database.session() as session:
            rows = session.scalars(
                select(AggregateRow)
                .where(AggregateRow.run_id == run_id)
                .order_by(AggregateRow.candidate, AggregateRow.metric)
            ).all()
            records = [_aggregate_record(row) for row in rows]
        # Sorting in Python keeps NULL ordering identical on SQLite and
        # PostgreSQL, which disagree on where NULLs fall by default, and puts
        # the overall row for a candidate and metric before its tier rows.
        return sorted(
            records,
            key=lambda aggregate: (
                aggregate.candidate,
                aggregate.metric,
                aggregate.tier is not None,
                aggregate.tier or 0,
            ),
        )

    def record_provider_costs(
        self, run_id: str, costs: list[ProviderCost]
    ) -> None:
        """Replace a run's per-provider rows, keeping repeated scoring idempotent."""
        with self._database.session() as session:
            session.execute(
                delete(ProviderCostRow).where(ProviderCostRow.run_id == run_id)
            )
            for cost in costs:
                session.add(_provider_cost_row(cost))

    def list_provider_costs(self, run_id: str) -> list[ProviderCost]:
        """Return a run's per-provider cost rows ordered by provider name."""
        with self._database.session() as session:
            rows = session.scalars(
                select(ProviderCostRow)
                .where(ProviderCostRow.run_id == run_id)
                .order_by(ProviderCostRow.provider)
            ).all()
            return [_provider_cost_record(row) for row in rows]

    def record_gates(self, run_id: str, gates: list[GateResult]) -> None:
        """Replace a run's gate outcomes."""
        with self._database.session() as session:
            session.execute(delete(GateRow).where(GateRow.run_id == run_id))
            for gate in gates:
                session.add(_gate_row(run_id, gate))

    def list_gates(self, run_id: str) -> list[GateResult]:
        """Return a run's gate outcomes ordered by name."""
        with self._database.session() as session:
            rows = session.scalars(
                select(GateRow)
                .where(GateRow.run_id == run_id)
                .order_by(GateRow.name)
            ).all()
            return [
                GateResult(
                    name=row.name,
                    metric=row.metric,
                    passed=row.passed,
                    candidate=row.candidate,
                    comparison=row.comparison or "",
                    threshold=row.threshold,
                    observed=row.observed,
                    n=row.n or 0,
                    detail=dict(row.detail),
                )
                for row in rows
            ]

    def append_event(self, event: Event) -> None:
        """Append one event to the run's stream."""
        with self._database.session() as session:
            session.add(
                EventRow(
                    run_id=event.run_id,
                    seq=event.seq,
                    ts=event.ts,
                    kind=event.kind,
                    payload=dict(event.payload),
                )
            )

    def list_events(self, run_id: str) -> list[Event]:
        """Return a run's events in sequence order."""
        return self.list_events_after(run_id, 0)

    def list_events_after(self, run_id: str, seq: int) -> list[Event]:
        """Return a run's events whose sequence number is greater than seq."""
        with self._database.session() as session:
            rows = session.scalars(
                select(EventRow)
                .where(EventRow.run_id == run_id, EventRow.seq > seq)
                .order_by(EventRow.seq)
            ).all()
            return [_event_record(row) for row in rows]

    def max_event_seq(self, run_id: str) -> int:
        """Return the last event sequence for a run, or zero when empty."""
        with self._database.session() as session:
            value = session.scalar(
                select(func.max(EventRow.seq)).where(EventRow.run_id == run_id)
            )
            return int(value or 0)

    def add_credential(
        self,
        name: str,
        provider: str,
        ciphertext: bytes,
        created_at: datetime | None = None,
    ) -> CredentialRecord:
        """Store one encrypted credential; a duplicate name is a spec error."""
        with self._database.session() as session:
            if session.get(CredentialRow, name) is not None:
                raise SpecError(
                    f"credentials.{name}",
                    name,
                    "a credential with this name already exists",
                    "delete it first or choose another name",
                )
            row = CredentialRow(
                name=name,
                provider=provider,
                ciphertext=ciphertext,
                created_at=created_at or utc_now(),
                disabled=False,
            )
            session.add(row)
            return _credential_record(row)

    def get_credential(self, name: str) -> EncryptedCredential | None:
        """Return one stored credential with its ciphertext, or None."""
        with self._database.session() as session:
            row = session.get(CredentialRow, name)
            return None if row is None else _encrypted_credential(row)

    def list_credentials(self) -> list[CredentialRecord]:
        """Return every stored credential's metadata, never its secret."""
        with self._database.session() as session:
            rows = session.scalars(
                select(CredentialRow).order_by(CredentialRow.name)
            ).all()
            return [_credential_record(row) for row in rows]

    def test_credential(
        self,
        name: str,
        *,
        error: str | None = None,
        tested_at: datetime | None = None,
    ) -> CredentialRecord:
        """Record one verification attempt against a stored credential.

        A successful attempt stamps last_ok_at and clears last_error; a failed
        one keeps the last successful time and records the provider message,
        bounded so a provider's HTML error page cannot land in the console.
        """
        with self._database.session() as session:
            row = _credential_row(session, name)
            if error is None:
                row.last_ok_at = tested_at or utc_now()
                row.last_error = None
            else:
                row.last_error = error[:MAX_LAST_ERROR_CHARS]
            return _credential_record(row)

    def set_credential_disabled(
        self, name: str, disabled: bool
    ) -> CredentialRecord:
        """Enable or disable one stored credential and return its metadata."""
        with self._database.session() as session:
            row = _credential_row(session, name)
            row.disabled = disabled
            return _credential_record(row)

    def delete_credential(self, name: str) -> None:
        """Delete one stored credential, or raise when it does not exist."""
        with self._database.session() as session:
            session.delete(_credential_row(session, name))


def _credential_row(session: Session, name: str) -> CredentialRow:
    """Return one credential row, or raise the shared actionable unknown error."""
    row = session.get(CredentialRow, name)
    if row is None:
        raise unknown_credential(name)
    return row


def _credential_record(row: CredentialRow) -> CredentialRecord:
    return CredentialRecord(
        name=row.name,
        provider=row.provider,
        created_at=_utc(row.created_at),
        last_ok_at=_utc(row.last_ok_at),
        last_error=row.last_error,
        disabled=row.disabled,
    )


def _utc(value: datetime | None) -> datetime | None:
    """Read a stored timestamp as UTC; SQLite drops the offset on write."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _encrypted_credential(row: CredentialRow) -> EncryptedCredential:
    return EncryptedCredential(
        **_credential_record(row).model_dump(),
        ciphertext=bytes(row.ciphertext),
    )


def _group_names(pairs: list[tuple[str, str]]) -> dict[str, set[str]]:
    """Group the second column of (run_id, name) pairs by the first."""
    grouped: dict[str, set[str]] = {}
    for run_id, name in pairs:
        grouped.setdefault(run_id, set()).add(name)
    return grouped


def _run_summary(
    row: RunRow,
    totals: tuple[int, int],
    candidates: set[str],
    metrics: set[str],
    failed_gates: set[str],
) -> RunSummary:
    """Build one console run row from a stored run, checks and gate outcomes."""
    passed, checks = totals
    duration = (
        (row.finished_at - row.started_at).total_seconds()
        if row.finished_at is not None
        else None
    )
    return RunSummary(
        run_id=row.run_id,
        spec_name=str(dict(row.spec_snapshot).get("name", "")),
        status=row.status,
        cost_usd=row.cost_usd,
        pass_rate=passed / checks if checks else None,
        duration_s=duration,
        n_cells=row.n_cells,
        n_ok=row.n_ok,
        started_at=row.started_at,
        finished_at=row.finished_at,
        candidates=tuple(sorted(candidates)),
        metrics=tuple(sorted(metrics)),
        failed_gates=tuple(sorted(failed_gates)),
        spec_snapshot=dict(row.spec_snapshot),
    )


def _event_record(row: EventRow) -> Event:
    """Turn one event row into its core record."""
    return Event(
        run_id=row.run_id,
        seq=row.seq,
        ts=row.ts,
        kind=row.kind,
        payload=dict(row.payload),
    )


def _run_row(run: RunRecord) -> RunRow:
    return RunRow(
        run_id=run.run_id,
        spec_id=run.spec_id,
        spec_snapshot=dict(run.spec_snapshot),
        prices_snapshot=dict(run.prices_snapshot),
        source_snapshot=dict(run.source_snapshot),
        harness_sha=run.harness_sha,
        operator=run.operator,
        seed=run.seed,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        cost_usd=run.cost_usd,
        budget_usd=run.budget_usd,
        budget_override_usd=run.budget_override_usd,
        n_cells=run.n_cells,
        n_ok=run.n_ok,
        error=run.error,
    )


def _run_record(row: RunRow) -> RunRecord:
    return RunRecord(
        run_id=row.run_id,
        spec_id=row.spec_id,
        spec_snapshot=dict(row.spec_snapshot),
        prices_snapshot=dict(row.prices_snapshot),
        source_snapshot=dict(row.source_snapshot or {}),
        harness_sha=row.harness_sha,
        operator=row.operator or "",
        seed=row.seed,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        cost_usd=row.cost_usd,
        budget_usd=row.budget_usd,
        budget_override_usd=row.budget_override_usd,
        n_cells=row.n_cells,
        n_ok=row.n_ok,
        error=row.error,
    )


def _cell_row(cell: CellRecord) -> CellRow:
    """Return the stored row for a cell, with the total and split consistent.

    A caller may record only a total and leave the split at zero, for example a
    deterministic grader or an imported cell. Reads trust the split columns, so
    when all three parts are zero and the total is not, the total is stored as
    the answer part. A caller that supplies a split keeps it, and the stored
    total is recomputed with the canonical helper so an inconsistent caller
    cannot persist a total that disagrees with its parts.
    """
    answer_usd = cell.answer_cost_usd
    judge_usd = cell.judge_cost_usd
    tool_usd = cell.tool_cost_usd
    if not answer_usd and not judge_usd and not tool_usd and cell.cost_usd:
        answer_usd = cell.cost_usd
    return CellRow(
        cell_id=cell.cell_id,
        run_id=cell.run_id,
        candidate=cell.candidate,
        case_id=cell.case_id,
        replica=cell.replica,
        variant=dict(cell.variant),
        status=cell.status,
        wall_ms=cell.wall_ms,
        prompt_tokens=cell.prompt_tokens,
        completion_tokens=cell.completion_tokens,
        tool_calls=cell.tool_calls,
        cost_usd=cost_total(answer_usd, judge_usd, tool_usd),
        answer_cost_usd=answer_usd,
        judge_cost_usd=judge_usd,
        tool_cost_usd=tool_usd,
        judge_prompt_tokens=cell.judge_prompt_tokens,
        judge_completion_tokens=cell.judge_completion_tokens,
        usage_source=cell.usage_source,
        provider=cell.provider,
        judge_provider_usage={
            provider: dict(usage)
            for provider, usage in cell.judge_provider_usage.items()
        },
        answer=cell.answer,
        output=dict(cell.output),
        artifact_dir=cell.artifact_dir,
    )


def _cell_record(row: CellRow) -> CellRecord:
    """Return one stored cell with its cost split rebuilt and summed.

    The split columns are nullable so a database written before the split
    existed can gain them in place; a missing part counts as zero and the total
    is recomputed with the canonical cost helper, so the invariant
    answer + judge + tool holds for old rows and new ones alike.
    """
    answer_usd = row.answer_cost_usd or 0.0
    judge_usd = row.judge_cost_usd or 0.0
    tool_usd = row.tool_cost_usd or 0.0
    return CellRecord(
        cell_id=row.cell_id,
        run_id=row.run_id,
        candidate=row.candidate,
        case_id=row.case_id,
        replica=row.replica,
        variant=dict(row.variant),
        status=row.status,
        wall_ms=row.wall_ms,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        tool_calls=row.tool_calls,
        cost_usd=cost_total(answer_usd, judge_usd, tool_usd),
        answer_cost_usd=answer_usd,
        judge_cost_usd=judge_usd,
        tool_cost_usd=tool_usd,
        judge_prompt_tokens=row.judge_prompt_tokens or 0,
        judge_completion_tokens=row.judge_completion_tokens or 0,
        usage_source=row.usage_source,
        provider=row.provider or "",
        judge_provider_usage=dict(row.judge_provider_usage or {}),
        answer=row.answer,
        output=dict(row.output),
        artifact_dir=row.artifact_dir,
    )


def _provider_cost_row(cost: ProviderCost) -> ProviderCostRow:
    return ProviderCostRow(
        run_id=cost.run_id,
        provider=cost.provider,
        n_cells=cost.n_cells,
        prompt_tokens=cost.prompt_tokens,
        completion_tokens=cost.completion_tokens,
        judge_prompt_tokens=cost.judge_prompt_tokens,
        judge_completion_tokens=cost.judge_completion_tokens,
        answer_cost_usd=cost.answer_cost_usd,
        judge_cost_usd=cost.judge_cost_usd,
        tool_cost_usd=cost.tool_cost_usd,
        cost_usd=cost.cost_usd,
    )


def _provider_cost_record(row: ProviderCostRow) -> ProviderCost:
    return ProviderCost(
        run_id=row.run_id,
        provider=row.provider,
        n_cells=row.n_cells,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        judge_prompt_tokens=row.judge_prompt_tokens,
        judge_completion_tokens=row.judge_completion_tokens,
        answer_cost_usd=row.answer_cost_usd,
        judge_cost_usd=row.judge_cost_usd,
        tool_cost_usd=row.tool_cost_usd,
        cost_usd=row.cost_usd,
    )


def _check_row(run_id: str, cell_id: str, check: Check) -> CheckRow:
    return CheckRow(
        run_id=run_id,
        cell_id=cell_id,
        metric=check.metric,
        passed=check.passed,
        score=check.score,
        reason=check.reason,
        evidence=dict(check.evidence),
        judge_config_hash=check.judge_config_hash,
    )


def _check_record(row: CheckRow) -> Check:
    return Check(
        metric=row.metric,
        passed=row.passed,
        score=row.score,
        reason=row.reason,
        evidence=dict(row.evidence),
        judge_config_hash=row.judge_config_hash,
    )


def _aggregate_row(aggregate: Aggregate) -> AggregateRow:
    return AggregateRow(
        run_id=aggregate.run_id,
        candidate=aggregate.candidate,
        metric=aggregate.metric,
        tier=aggregate.tier,
        n=aggregate.n,
        value=aggregate.value,
        ci_low=aggregate.ci_low,
        ci_high=aggregate.ci_high,
        pass_at_k=aggregate.pass_at_k,
        pass_k=aggregate.pass_k,
    )


def _aggregate_record(row: AggregateRow) -> Aggregate:
    return Aggregate(
        run_id=row.run_id,
        candidate=row.candidate,
        metric=row.metric,
        tier=row.tier,
        n=row.n,
        value=row.value,
        ci_low=row.ci_low,
        ci_high=row.ci_high,
        pass_at_k=row.pass_at_k,
        pass_k=row.pass_k,
    )


def _gate_row(run_id: str, gate: GateResult) -> GateRow:
    return GateRow(
        run_id=run_id,
        name=gate.name,
        metric=gate.metric,
        passed=gate.passed,
        candidate=gate.candidate,
        comparison=gate.comparison,
        threshold=gate.threshold,
        observed=gate.observed,
        n=gate.n,
        detail=dict(gate.detail),
    )
