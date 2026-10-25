"""Bounded-concurrency executor: retries, budget guard and cell persistence."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from evalharness.core.context import CredentialStore, RunContext
from evalharness.core.cost import split_cost
from evalharness.core.events import EventEmitter
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

MAX_ATTEMPTS = 2
"""Total attempts for a transport failure: the first call plus one retry."""

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
) -> ExecutionResult:
    """Execute every planned cell with the spec's concurrency and persist progress."""
    execution = _Execution(
        resolved=resolved,
        cases=cases,
        run=run,
        repository=repository,
        sink=sink,
        credentials=credentials,
        redactor=redactor,
        artifacts_root=artifacts_root,
    )
    return await execution.run(cells)


async def _call_with_retries(
    candidate: Candidate,
    case: Case,
    ctx: RunContext,
) -> CandidateOutput:
    """Call a candidate, retrying only typed transport failures."""
    attempt = 1
    while True:
        try:
            return await candidate.run(case, ctx)
        except TransportError:
            if attempt >= MAX_ATTEMPTS:
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
    ) -> None:
        self.resolved = resolved
        self.run_record = run
        self.repository = repository
        self.sink = sink
        self.credentials = credentials
        self.redactor = redactor
        self.artifacts_root = artifacts_root
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
        self.errors = 0
        self.records: dict[str, CellRecord] = {}
        self.checks: dict[str, list[Check]] = {}

    async def run(self, cells: list[PlannedCell]) -> ExecutionResult:
        """Drive the worker pool and close the run."""
        self.emitter.emit(
            "run_started", {"n_cells": len(cells), "seed": self.run_record.seed}
        )
        if self.sink is not None:
            self.sink.open_run(self.run_record)
        queue: asyncio.Queue[PlannedCell | None] = asyncio.Queue()
        for cell in cells:
            queue.put_nowait(cell)
        worker_count = max(1, min(self.resolved.spec.concurrency, len(cells)))
        workers = [
            asyncio.create_task(self._worker(queue)) for _ in range(worker_count)
        ]
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)
        await asyncio.gather(*workers)
        ordered = [self.records[cell.cell_id] for cell in cells]
        status = (
            "budget_exceeded"
            if self.aborted
            else ("partial" if self.errors else "ok")
        )
        finished = self.run_record.model_copy(
            update={
                "status": status,
                "finished_at": utc_now(),
                "cost_usd": self.spend_usd,
                "n_cells": len(cells),
                "n_ok": sum(1 for cell in ordered if cell.status == "ok"),
            }
        )
        self.repository.update_run(finished)
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
            summaries=self._summaries(ordered),
        )

    async def _worker(self, queue: asyncio.Queue[PlannedCell | None]) -> None:
        instances: dict[str, Candidate] = {}
        while True:
            cell = await queue.get()
            try:
                if cell is None:
                    return
                if self.aborted:
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
        credential = self.credentials.get(candidate_spec.credential)
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
            self._check_budget()
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
        )
        checks = checks_for_cell(record, self.metrics, score_ctx)
        self._store(record, checks)
        self.spend_usd += split.total_usd
        self.emitter.emit(
            "cell_completed",
            {
                "cell_id": cell.cell_id,
                "cost_usd": split.total_usd,
                "wall_ms": wall_ms,
                "passed": sum(1 for check in checks if check.passed),
            },
        )
        self._check_budget()

    def _store(self, cell: CellRecord, checks: list[Check]) -> None:
        self.repository.record_cell(cell)
        self.repository.record_checks(cell.run_id, cell.cell_id, checks)
        self.records[cell.cell_id] = cell
        self.checks[cell.cell_id] = checks
        if self.sink is not None:
            self.sink.record_cell(cell, checks)

    def _record_cancelled(self, cell: PlannedCell) -> None:
        cancelled = CellRecord(
            cell_id=cell.cell_id,
            run_id=self.run_record.run_id,
            candidate=cell.candidate,
            case_id=cell.case.case_id,
            replica=cell.replica,
            variant=cell.variant,
            status="cancelled",
            artifact_dir=str(
                self.artifacts_root / self.run_record.run_id / cell.cell_id
            ),
        )
        self._store(cancelled, [])
        self.emitter.emit("cell_cancelled", {"cell_id": cell.cell_id})

    def _check_budget(self) -> None:
        budget = self.run_record.budget_usd
        if budget is not None and not self.aborted and self.spend_usd > budget:
            self.aborted = True
            self.emitter.emit(
                "budget_exceeded",
                {"spend_usd": self.spend_usd, "budget_usd": budget},
            )

    def _summaries(self, cells: list[CellRecord]) -> list[CandidateSummary]:
        summaries: list[CandidateSummary] = []
        for candidate_spec in self.resolved.spec.candidates:
            owned = [cell for cell in cells if cell.candidate == candidate_spec.name]
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
