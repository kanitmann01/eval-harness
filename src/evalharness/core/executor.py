"""Bounded-concurrency executor: retries, budget guard, cancel and resume."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, cast

from evalharness.core.cancel import CancellationToken
from evalharness.core.context import CredentialStore, RunContext
from evalharness.core.cost import split_cost
from evalharness.core.events import EventEmitter
from evalharness.core.judges import build_judge_sessions
from evalharness.core.models import (
    CandidateOutput,
    Case,
    CellRecord,
    Check,
    RunRecord,
    ScoreContext,
    utc_now,
)
from evalharness.core.planner import PlannedCell
from evalharness.core.protocols import Candidate, Metric, Sink
from evalharness.core.redaction import Redactor
from evalharness.core.scorer import checks_for_cell
from evalharness.errors import TransportError

if TYPE_CHECKING:
    from evalharness.core.spec import ResolvedSpec
    from evalharness.storage.repository import Repository

RETRY_BASE_DELAY_SECONDS = 0.05
"""Base delay; the retry waits this long, doubling per extra attempt."""


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    """One row of the CLI run table."""

    candidate: str
    cells: int
    ok: int
    failed: int
    cost_usd: float
    duration_s: float


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """What the executor produced: the closed run, its cells and their checks."""

    run: RunRecord
    cells: list[CellRecord]
    checks: dict[str, list[Check]]
    summaries: list[CandidateSummary]


def summarise(
    resolved: ResolvedSpec,
    cells: Iterable[CellRecord],
) -> list[CandidateSummary]:
    """Build one run-table row per candidate from a run's cells."""
    materialized = list(cells)
    summaries: list[CandidateSummary] = []
    for candidate_spec in resolved.spec.candidates:
        owned = [
            cell for cell in materialized if cell.candidate == candidate_spec.name
        ]
        summaries.append(
            CandidateSummary(
                candidate=candidate_spec.name,
                cells=len(owned),
                ok=sum(1 for cell in owned if cell.status == "ok"),
                failed=sum(1 for cell in owned if cell.status != "ok"),
                cost_usd=sum(cell.cost_usd for cell in owned),
                duration_s=sum(cell.wall_ms for cell in owned) / 1000,
            )
        )
    return summaries


async def execute_run(
    *,
    resolved: ResolvedSpec,
    cells: list[PlannedCell],
    cases: list[Case],
    run: RunRecord,
    repository: Repository,
    sink: Sink | None,
    credentials: CredentialStore,
    redactor: Redactor,
    artifacts_root: Path,
    cancel_token: CancellationToken | None = None,
    resumed: bool = False,
) -> ExecutionResult:
    """Execute every planned cell, skipping cells this run already finished ok.

    Passing `resumed=True` distinguishes a continuation from a fresh run in the
    event stream; the skip itself follows from the cells already stored.
    """
    execution = _Execution(
        resolved=resolved,
        cases=cases,
        run=run,
        repository=repository,
        sink=sink,
        credentials=credentials,
        redactor=redactor,
        artifacts_root=artifacts_root,
        cancel_token=cancel_token or CancellationToken(),
        resumed=resumed,
    )
    return await execution.run(cells)


def _attempt_budget(candidate: Candidate) -> int:
    """Return the adapter-declared attempt budget, never below one."""
    return max(1, int(candidate.capabilities.max_attempts))


async def _call_with_retries(
    candidate: Candidate,
    case: Case,
    ctx: RunContext,
) -> CandidateOutput:
    """Call a candidate, retrying only typed transport failures.

    The candidate declares how many total attempts it allows; a candidate that
    declares nothing keeps the two-attempt default from before this was
    configurable. Only `TransportError` is retried, so a wrong answer is
    returned and scored on its first request.
    """
    max_attempts = _attempt_budget(candidate)
    attempt = 1
    while True:
        try:
            return await candidate.run(case, ctx)
        except TransportError:
            if attempt >= max_attempts:
                raise
            await asyncio.sleep(RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
            attempt += 1


class _Execution:
    """One run's shared state and worker loop."""

    def __init__(
        self,
        *,
        resolved: ResolvedSpec,
        cases: list[Case],
        run: RunRecord,
        repository: Repository,
        sink: Sink | None,
        credentials: CredentialStore,
        redactor: Redactor,
        artifacts_root: Path,
        cancel_token: CancellationToken,
        resumed: bool,
    ) -> None:
        self.resolved = resolved
        self.run_record = run
        self.repository = repository
        self.sink = sink
        self.credentials = credentials
        self.redactor = redactor
        self.artifacts_root = artifacts_root
        self.cancel_token = cancel_token
        self.resumed = resumed
        self.cases_by_id = {case.case_id: case for case in cases}
        self.metrics: list[Metric] = list(resolved.metrics.values())
        self.emitter = EventEmitter(
            run.run_id,
            append=repository.append_event,
            redactor=redactor,
            start_seq=repository.max_event_seq(run.run_id),
        )
        self.spend_usd = 0.0
        self.aborted = False
        self.abort_reason = ""
        self.errors = 0
        self.records: dict[str, CellRecord] = {}
        self.checks: dict[str, list[Check]] = {}
        # A resume continues the same run id, so cells already ok are finished
        # work: their spend counts and their checks feed the final aggregates.
        for existing in repository.list_cells(run.run_id):
            self.spend_usd += existing.cost_usd
            if existing.status == "ok":
                self.records[existing.cell_id] = existing
                self.checks[existing.cell_id] = repository.list_checks(
                    run.run_id, existing.cell_id
                )

    async def run(self, cells: list[PlannedCell]) -> ExecutionResult:
        """Drive the worker pool and close the run."""
        pending = [cell for cell in cells if cell.cell_id not in self.records]
        skipped = len(cells) - len(pending)
        self.emitter.emit(
            "run_started",
            {
                "n_cells": len(cells),
                "seed": self.run_record.seed,
                "n_skipped": skipped,
            },
        )
        if self.resumed:
            self.emitter.emit(
                "run_resumed",
                {
                    "n_cells": len(cells),
                    "n_skipped": skipped,
                    "n_remaining": len(pending),
                },
            )
        self._check_budget()
        if self.sink is not None:
            self.sink.open_run(self.run_record)
        queue: asyncio.Queue[PlannedCell | None] = asyncio.Queue()
        for cell in pending:
            queue.put_nowait(cell)
        worker_count = max(1, min(self.resolved.spec.concurrency, len(pending) or 1))
        workers = [
            asyncio.create_task(self._worker(queue)) for _ in range(worker_count)
        ]
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)
        await asyncio.gather(*workers)
        ordered = [self.records[cell.cell_id] for cell in cells]
        n_ok = sum(1 for cell in ordered if cell.status == "ok")
        n_cancelled = sum(1 for cell in ordered if cell.status == "cancelled")
        if self.aborted:
            status = "budget_exceeded"
        elif self.cancel_token.cancelled:
            status = "cancelled"
        elif self.errors:
            status = "partial"
        else:
            status = "ok"
        finished = self.run_record.model_copy(
            update={
                "status": status,
                "finished_at": utc_now(),
                "cost_usd": self.spend_usd,
                "n_cells": len(cells),
                "n_ok": n_ok,
            }
        )
        self.repository.update_run(finished)
        if status == "cancelled":
            self.emitter.emit(
                "run_cancelled",
                {
                    "reason": self.cancel_token.reason,
                    "n_cells": finished.n_cells,
                    "n_ok": n_ok,
                    "n_cancelled": n_cancelled,
                },
            )
        self.emitter.emit(
            "run_finished",
            {
                "status": status,
                "n_cells": finished.n_cells,
                "n_ok": finished.n_ok,
                "cost_usd": finished.cost_usd,
            },
        )
        if self.sink is not None:
            self.sink.close_run(finished)
        return ExecutionResult(
            run=finished,
            cells=ordered,
            checks=self.checks,
            summaries=summarise(self.resolved, ordered),
        )

    async def _worker(self, queue: asyncio.Queue[PlannedCell | None]) -> None:
        instances: dict[str, Candidate] = {}
        while True:
            cell = await queue.get()
            try:
                if cell is None:
                    return
                if self.aborted or self.cancel_token.cancelled:
                    self._record_cancelled(cell)
                else:
                    await self._process(cell, instances)
            finally:
                queue.task_done()

    async def _process(
        self,
        cell: PlannedCell,
        instances: dict[str, Candidate],
    ) -> None:
        candidate_spec = self.resolved.candidates[cell.candidate]
        candidate = instances.get(cell.candidate)
        if candidate is None:
            factory = self.resolved.candidate_factories[cell.candidate]
            candidate = factory(cell.candidate, dict(candidate_spec.options))
            instances[cell.candidate] = candidate
        credential = (
            self.credentials.get(candidate_spec.credential)
            if candidate_spec.credential is not None
            else None
        )
        budget_remaining = (
            None
            if self.run_record.budget_usd is None
            else max(0.0, self.run_record.budget_usd - self.spend_usd)
        )
        ctx = RunContext(
            run_id=self.run_record.run_id,
            seed=self.run_record.seed,
            credential=credential,
            events=self.emitter,
            prompt_variant=cast(str | None, cell.variant.get("prompt_variant")),
            prompt_override=cast(str | None, cell.variant.get("prompt_override")),
            chunks=list(self.resolved.chunks[cell.candidate]),
            budget_remaining_usd=budget_remaining,
            candidate=cell.candidate,
            case_id=cell.case.case_id,
            replica=cell.replica,
            model=candidate_spec.model,
            redactor=self.redactor,
        )
        self.emitter.emit(
            "cell_started",
            {
                "cell_id": cell.cell_id,
                "candidate": cell.candidate,
                "case_id": cell.case.case_id,
                "replica": cell.replica,
            },
        )
        started = time.perf_counter()
        try:
            output = await _call_with_retries(candidate, cell.case, ctx)
        except Exception as exc:  # adapter failure is a cell outcome, not a run crash
            wall_ms = int((time.perf_counter() - started) * 1000)
            failed = CellRecord(
                cell_id=cell.cell_id,
                run_id=self.run_record.run_id,
                candidate=cell.candidate,
                case_id=cell.case.case_id,
                replica=cell.replica,
                variant=cell.variant,
                status="error",
                wall_ms=wall_ms,
                usage_source="estimated",
                output={
                    "error": self.redactor.redact(str(exc)),
                    "error_type": type(exc).__name__,
                },
                artifact_dir=str(
                    self.artifacts_root / self.run_record.run_id / cell.cell_id
                ),
            )
            self.errors += 1
            self._store(failed, [])
            self.emitter.emit(
                "cell_failed",
                {
                    "cell_id": cell.cell_id,
                    "error_type": type(exc).__name__,
                    "error": self.redactor.redact(str(exc)),
                },
            )
            self._finish_cell()
            return
        wall_ms = int((time.perf_counter() - started) * 1000)
        price = self.resolved.prices[candidate_spec.model]
        split = split_cost(price, output)
        output_data = self.redactor.redact_data(output.model_dump())
        record = CellRecord(
            cell_id=cell.cell_id,
            run_id=self.run_record.run_id,
            candidate=cell.candidate,
            case_id=cell.case.case_id,
            replica=cell.replica,
            variant=cell.variant,
            status="ok",
            wall_ms=wall_ms,
            prompt_tokens=output.prompt_tokens,
            completion_tokens=output.completion_tokens,
            tool_calls=output.tool_calls,
            cost_usd=split.total_usd,
            answer_cost_usd=split.answer_usd,
            judge_cost_usd=split.judge_usd,
            tool_cost_usd=split.tool_usd,
            usage_source=output.usage_source,
            answer=str(output_data.get("answer", "")),
            output=output_data,
            artifact_dir=str(
                self.artifacts_root / self.run_record.run_id / cell.cell_id
            ),
        )
        score_ctx = ScoreContext(
            run_id=self.run_record.run_id,
            candidate=cell.candidate,
            cases=self.cases_by_id,
            judges=build_judge_sessions(
                self.resolved.spec.judges,
                self.resolved.judges,
                self.resolved.prices,
            ),
            redactor=self.redactor,
        )
        checks = self._redact_checks(
            await checks_for_cell(record, self.metrics, score_ctx)
        )
        record = self._with_judge_usage(record, score_ctx)
        self._store(record, checks)
        self.spend_usd += record.cost_usd
        self.emitter.emit(
            "cell_completed",
            {
                "cell_id": cell.cell_id,
                "cost_usd": record.cost_usd,
                "wall_ms": wall_ms,
                "passed": sum(1 for check in checks if check.passed),
            },
        )
        self._finish_cell()

    @staticmethod
    def _with_judge_usage(
        record: CellRecord,
        ctx: ScoreContext,
    ) -> CellRecord:
        """Fold the judge usage a cell triggered into its stored cost split."""
        judge_usd = sum(session.usage_usd() for session in ctx.judges.values())
        judge_prompt = sum(session.prompt_tokens for session in ctx.judges.values())
        judge_completion = sum(
            session.completion_tokens for session in ctx.judges.values()
        )
        return record.model_copy(
            update={
                "judge_cost_usd": judge_usd,
                "judge_prompt_tokens": judge_prompt,
                "judge_completion_tokens": judge_completion,
                "cost_usd": (
                    record.answer_cost_usd + judge_usd + record.tool_cost_usd
                ),
            }
        )

    def _finish_cell(self) -> None:
        """Advance the cancel limit and re-check the budget after one cell."""
        self.cancel_token.record_completion()
        self._check_budget()

    def _redact_checks(self, checks: list[Check]) -> list[Check]:
        """Redact metric evidence before it reaches storage or a sink."""
        return [
            check.model_copy(
                update={
                    "reason": self.redactor.redact(check.reason),
                    "evidence": self.redactor.redact_data(check.evidence),
                }
            )
            for check in checks
        ]

    def _store(self, cell: CellRecord, checks: list[Check]) -> None:
        self.repository.record_cell(cell)
        self.repository.record_checks(cell.run_id, cell.cell_id, checks)
        self.records[cell.cell_id] = cell
        self.checks[cell.cell_id] = checks
        if self.sink is not None:
            self.sink.record_cell(cell, checks)

    def _record_cancelled(self, cell: PlannedCell) -> None:
        reason = self._cancel_reason()
        cancelled = CellRecord(
            cell_id=cell.cell_id,
            run_id=self.run_record.run_id,
            candidate=cell.candidate,
            case_id=cell.case.case_id,
            replica=cell.replica,
            variant=cell.variant,
            status="cancelled",
            output={"reason": reason},
            artifact_dir=str(
                self.artifacts_root / self.run_record.run_id / cell.cell_id
            ),
        )
        self._store(cancelled, [])
        self.emitter.emit(
            "cell_cancelled", {"cell_id": cell.cell_id, "reason": reason}
        )

    def _cancel_reason(self) -> str:
        if self.aborted:
            return self.abort_reason
        return self.cancel_token.reason or "cancelled"

    def _check_budget(self) -> None:
        budget = self.run_record.budget_usd
        if budget is None or self.aborted or self.spend_usd <= budget:
            return
        self.aborted = True
        self.abort_reason = (
            f"budget exceeded: spent ${self.spend_usd:.6f} of ${budget:.6f}"
        )
        self.emitter.emit(
            "budget_exceeded",
            {"spend_usd": self.spend_usd, "budget_usd": budget},
        )
