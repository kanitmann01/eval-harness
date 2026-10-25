"""The only code in the harness that talks SQL."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select

from evalharness.core.events import Event
from evalharness.core.models import (
    Aggregate,
    CellRecord,
    Check,
    CredentialRecord,
    EncryptedCredential,
    GateResult,
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
    RunRow,
    SpecRow,
)


class Repository:
    """Read and write harness records; callers never see ORM rows."""

    def __init__(self, database: Database) -> None:
        self._database = database

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
        with self._database.session() as session:
            rows = session.scalars(
                select(EventRow)
                .where(EventRow.run_id == run_id)
                .order_by(EventRow.seq)
            ).all()
            return [
                Event(
                    run_id=row.run_id,
                    seq=row.seq,
                    ts=row.ts,
                    kind=row.kind,
                    payload=dict(row.payload),
                )
                for row in rows
            ]

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
        one keeps the last successful time and records the provider message.
        """
        with self._database.session() as session:
            row = session.get(CredentialRow, name)
            if row is None:
                raise SpecError(
                    f"credentials.{name}",
                    name,
                    "unknown credential",
                    "list the stored credentials with 'evalh credentials list'",
                )
            if error is None:
                row.last_ok_at = tested_at or utc_now()
                row.last_error = None
            else:
                row.last_error = error
            return _credential_record(row)

    def delete_credential(self, name: str) -> None:
        """Delete one stored credential, or raise when it does not exist."""
        with self._database.session() as session:
            row = session.get(CredentialRow, name)
            if row is None:
                raise SpecError(
                    f"credentials.{name}",
                    name,
                    "unknown credential",
                    "list the stored credentials with 'evalh credentials list'",
                )
            session.delete(row)


def _credential_record(row: CredentialRow) -> CredentialRecord:
    return CredentialRecord(
        name=row.name,
        provider=row.provider,
        created_at=row.created_at,
        last_ok_at=row.last_ok_at,
        last_error=row.last_error,
    )


def _encrypted_credential(row: CredentialRow) -> EncryptedCredential:
    return EncryptedCredential(
        **_credential_record(row).model_dump(),
        ciphertext=bytes(row.ciphertext),
    )


def _run_row(run: RunRecord) -> RunRow:
    return RunRow(
        run_id=run.run_id,
        spec_id=run.spec_id,
        spec_snapshot=dict(run.spec_snapshot),
        prices_snapshot=dict(run.prices_snapshot),
        harness_sha=run.harness_sha,
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
        harness_sha=row.harness_sha,
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
        cost_usd=cell.cost_usd,
        usage_source=cell.usage_source,
        answer=cell.answer,
        output=dict(cell.output),
        artifact_dir=cell.artifact_dir,
    )


def _cell_record(row: CellRow) -> CellRecord:
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
        cost_usd=row.cost_usd,
        usage_source=row.usage_source,
        answer=row.answer,
        output=dict(row.output),
        artifact_dir=row.artifact_dir,
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
