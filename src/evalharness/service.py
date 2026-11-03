"""One run orchestration, shared by the command line and the console launch.

The CLI and the console call the same functions, so one spec launched from
either place produces the same run record: the same spec snapshot, price
snapshot, deterministic cell ids, checks, aggregates and gates. Printing the
run table stays in the CLI; this module reads, writes and returns.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.engine import make_url

from evalharness import exit_codes
from evalharness.core.cancel import CancellationToken
from evalharness.core.config import Settings, harness_revision
from evalharness.core.context import CredentialStore, RunContext
from evalharness.core.cost import (
    DEFAULT_MEDIAN_COMPLETION_TOKENS,
    DEFAULT_MEDIAN_PROMPT_TOKENS,
    CostEstimate,
    estimate_cost,
    observed_medians,
    provider_cost_rows,
)
from evalharness.core.events import EventEmitter
from evalharness.core.executor import (
    CandidateSummary,
    ExecutionResult,
    execute_run,
    summarise,
)
from evalharness.core.models import (
    Aggregate,
    Case,
    CellRecord,
    Check,
    CredentialRecord,
    CredentialState,
    GateResult,
    ProviderCost,
    RunRecord,
    Spec,
    utc_now,
)
from evalharness.core.planner import PlannedCell, expand, per_candidate_counts
from evalharness.core.protocols import Sink
from evalharness.core.redaction import (
    NoOpRedactor,
    RedactionFilter,
    Redactor,
    SecretRedactor,
)
from evalharness.core.repo_snapshot import (
    RepoSnapshot,
    changed_repos,
    prepare_repos,
    source_snapshot,
)
from evalharness.core.scorer import aggregate_cells, evaluate_gates
from evalharness.core.spec import ResolvedSpec, load_and_validate
from evalharness.core.vault import VaultCredentialStore, open_vault
from evalharness.credential_service import credential_states
from evalharness.errors import SpecError
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository

LOGGER = logging.getLogger("evalharness.service")


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """A validated spec with its cases, planned cells and launch estimate."""

    resolved: ResolvedSpec
    cases: list[Case]
    cells: list[PlannedCell]
    estimate: CostEstimate
    repos: dict[str, RepoSnapshot]
    source_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What one executed run left in storage, with its exit code and reason."""

    run: RunRecord
    cells: list[CellRecord]
    checks: dict[str, list[Check]]
    summaries: list[CandidateSummary]
    aggregates: list[Aggregate]
    gates: list[GateResult]
    provider_costs: list[ProviderCost]
    exit_code: int
    reason: str


def stored_credentials(settings: Settings) -> list[CredentialRecord]:
    """Read stored credential metadata without creating an absent database."""
    parsed = make_url(settings.database_url)
    if parsed.get_backend_name() == "sqlite":
        database_path = parsed.database or ""
        if database_path in {"", ":memory:"} or not Path(database_path).exists():
            return []
    database = Database(settings.database_url)
    try:
        database.create_all()
        return Repository(database).list_credentials()
    finally:
        database.dispose()


def stored_credential_states(settings: Settings) -> list[CredentialState]:
    """Read each stored credential's usability state, never its secret."""
    return credential_states(stored_credentials(settings))


def redactor_for(store: CredentialStore) -> SecretRedactor:
    """Build a redactor from every secret the store can resolve."""
    secrets: dict[str, str] = {}
    for name in store.names():
        handle = store.get(name)
        if handle is not None and handle.secret:
            secrets[name] = handle.secret
    return SecretRedactor(secrets)


def _repo_credentials(spec: Spec, settings: Settings) -> dict[str, str]:
    """Resolve only the secrets a declared remote repository needs.

    A local-only spec never opens the vault, so the common path keeps working
    without a master key. A missing key for a remote repo surfaces the vault's
    own actionable error instead of a bare fetch failure.
    """
    names = sorted(
        {
            repo.credential
            for repo in spec.repos.values()
            if repo.credential is not None
        }
        | ({spec.source.credential} if spec.source.credential else set())
    )
    if not names:
        return {}
    vault = open_vault(settings)
    database = Database(settings.database_url)
    try:
        database.create_all()
        store = VaultCredentialStore(vault, Repository(database))
        secrets: dict[str, str] = {}
        for name in names:
            handle = store.get(name)
            if handle is not None and handle.secret:
                secrets[name] = handle.secret
        return secrets
    finally:
        database.dispose()


def load_run(
    spec_path: str | Path,
    *,
    settings: Settings,
    run_id: str,
) -> PreparedRun:
    """Load, validate, plan and estimate one spec without executing it.

    Parameter:
        run_id: the id the run will use, so events a source emits while
            loading cases carry the same identity as the run.
    """
    resolved = load_and_validate(
        spec_path,
        credential_states=stored_credential_states(settings),
    )
    repos = prepare_repos(
        resolved.spec,
        base_dir=Path(spec_path).parent,
        home=settings.home,
        credentials=_repo_credentials(resolved.spec, settings),
    )
    cases = _load_cases(resolved, run_id, repos=repos)
    snapshot = source_snapshot(
        resolved.spec.source.adapter,
        repos,
        cases,
        resolved.spec.contexts,
    )
    cells = expand(
        resolved,
        cases,
        repos=repos,
        artifacts_root=settings.artifacts_dir,
        run_id=run_id,
    )
    return PreparedRun(
        resolved=resolved,
        cases=cases,
        cells=cells,
        estimate=_estimate(resolved, cells, None),
        repos=repos,
        source_snapshot=snapshot,
    )


def execute(
    prepared: PreparedRun,
    *,
    settings: Settings,
    run_id: str,
    cancel_token: CancellationToken,
    override_budget: float | None = None,
    resume_run_id: str | None = None,
    on_run_created: Callable[[RunRecord], None] | None = None,
) -> RunOutcome:
    """Apply the budget, execute every cell, score the run and close it.

    The budget refusal, the resume checks and the sink build all raise
    :class:`SpecError`, exactly as the CLI reports them. Signal handling stays
    with the caller: the API drives this from a worker thread, where signals
    are not available.
    """
    resolved = prepared.resolved
    cases = prepared.cases
    cells = prepared.cells
    vault = open_vault(settings)
    try:
        settings.home.mkdir(parents=True, exist_ok=True)
        sink = _build_sink(resolved, settings)
    except OSError as exc:
        raise SpecError(
            "EVALH_HOME",
            str(settings.home),
            f"the harness home could not be created: {exc}",
            "set EVALH_HOME to a writable directory",
        ) from exc
    database = Database(settings.database_url)
    database.create_all()
    try:
        repository = Repository(database)
        store = VaultCredentialStore(vault, repository)
        redactor = redactor_for(store)
        _configure_logging(redactor)
        if resume_run_id is not None:
            run = _resume_run(
                repository,
                redactor,
                resolved,
                cells,
                resume_run_id,
                override_budget,
                prepared.source_snapshot,
            )
        else:
            run = _launch_run(
                repository,
                redactor,
                resolved,
                run_id,
                cells,
                override_budget,
                prepared.source_snapshot,
            )
        if on_run_created is not None:
            on_run_created(run)
        execution = asyncio.run(
            execute_run(
                resolved=resolved,
                cells=cells,
                cases=cases,
                run=run,
                repository=repository,
                sink=sink,
                credentials=store,
                redactor=redactor,
                artifacts_root=settings.artifacts_dir,
                repos=prepared.repos,
                cancel_token=cancel_token,
                resumed=resume_run_id is not None,
            )
        )
        all_cells = repository.list_cells(run.run_id)
        checks_by_cell = {
            cell.cell_id: repository.list_checks(run.run_id, cell.cell_id)
            for cell in all_cells
        }
        aggregates = aggregate_cells(
            run_id=run.run_id,
            cases={case.case_id: case for case in cases},
            cells=all_cells,
            checks_by_cell=checks_by_cell,
            metric_keys=list(resolved.metrics),
            replicas=resolved.spec.replicas,
        )
        repository.record_aggregates(aggregates)
        gates = evaluate_gates(resolved.spec.gates, aggregates)
        repository.record_gates(run.run_id, gates)
        provider_costs = provider_cost_rows(run.run_id, all_cells)
        repository.record_provider_costs(run.run_id, provider_costs)
        result = ExecutionResult(
            run=execution.run,
            cells=all_cells,
            checks=checks_by_cell,
            summaries=summarise(resolved, all_cells),
        )
        return RunOutcome(
            run=result.run,
            cells=result.cells,
            checks=result.checks,
            summaries=result.summaries,
            aggregates=aggregates,
            gates=gates,
            provider_costs=provider_costs,
            exit_code=exit_codes.for_run(
                status=result.run.status,
                gate_failed=any(not gate.passed for gate in gates),
            ),
            reason=_run_reason(result.run, cancel_token.reason),
        )
    finally:
        database.dispose()


def _load_cases(
    resolved: ResolvedSpec,
    run_id: str,
    *,
    repos: dict[str, RepoSnapshot] | None = None,
) -> list[Case]:
    """Load the spec's cases through its source, or raise an actionable error."""
    context = RunContext(
        run_id=run_id,
        seed=resolved.spec.seed,
        credential=None,
        events=EventEmitter(run_id),
        redactor=NoOpRedactor(),
        repos=repos or {},
    )
    try:
        cases = list(resolved.source.load(resolved.spec.source, context))
    except SpecError:
        raise
    except Exception as exc:  # source adapters are external code
        raise SpecError(
            "source",
            resolved.spec.source.adapter,
            f"the source failed to load cases: {exc}",
            "fix the source options",
        ) from exc
    if not cases:
        raise SpecError(
            "source",
            resolved.spec.source.adapter,
            "the source returned no cases",
            "add at least one case",
        )
    return cases


def _estimate(
    resolved: ResolvedSpec,
    cells: list[PlannedCell],
    medians: tuple[int, int] | None,
) -> CostEstimate:
    """Estimate run cost, using observed medians when a comparable run exists."""
    prompt, completion = medians or (
        DEFAULT_MEDIAN_PROMPT_TOKENS,
        DEFAULT_MEDIAN_COMPLETION_TOKENS,
    )
    return estimate_cost(
        resolved.spec,
        resolved.candidate_prices,
        per_candidate_counts(cells),
        median_prompt_tokens=prompt,
        median_completion_tokens=completion,
    )


def _build_sink(resolved: ResolvedSpec, settings: Settings) -> Sink:
    """Build the spec's artifact sink rooted at the configured directory."""
    options = dict(resolved.sink_options)
    options["artifacts_dir"] = str(settings.artifacts_dir)
    field = "sink.adapter" if resolved.spec.sink is not None else "sink"
    try:
        return resolved.sink_factory("sink", options)
    except Exception as exc:  # sink factories are external code
        raise SpecError(
            field,
            resolved.spec.sink.adapter if resolved.spec.sink is not None else "default",
            f"the sink factory raised: {exc}",
            "fix the sink options",
        ) from exc


def _emit(
    repository: Repository,
    redactor: Redactor,
    run_id: str,
    kind: str,
    payload: dict[str, Any],
) -> None:
    """Append one lifecycle event to a run outside the executor."""
    EventEmitter(
        run_id,
        append=repository.append_event,
        redactor=redactor,
        start_seq=repository.max_event_seq(run_id),
    ).emit(kind, payload)


def _launch_run(
    repository: Repository,
    redactor: Redactor,
    resolved: ResolvedSpec,
    run_id: str,
    cells: list[PlannedCell],
    override_budget: float | None,
    snapshot: dict[str, Any],
) -> RunRecord:
    """Check the launch estimate, snapshot the spec and record the run.

    The estimate uses the medians an earlier run of the same spec measured, so
    the cap is compared with what this spec actually spends, not a guess.
    """
    earlier = repository.latest_run_for_spec(resolved.sha)
    medians = None
    if earlier is not None:
        medians = observed_medians(repository.list_cells(earlier.run_id))
    estimate = _estimate(resolved, cells, medians)
    spec_budget = resolved.spec.budget_usd
    cap = override_budget if override_budget is not None else spec_budget
    if cap is not None and estimate.cost_usd > cap:
        raise SpecError(
            "budget_usd",
            cap,
            f"the estimated cost ${estimate.cost_usd:.6f} exceeds the cap ${cap:.6f}",
            "raise budget_usd or pass --override-budget USD",
        )
    started = utc_now()
    spec_id = uuid.uuid4().hex
    repository.save_spec(
        spec_id,
        resolved.spec.name,
        resolved.raw_text,
        resolved.sha,
        started,
    )
    run = RunRecord(
        run_id=run_id,
        spec_id=spec_id,
        spec_name=resolved.spec.name,
        spec_snapshot=resolved.spec.model_dump(mode="json"),
        prices_snapshot=resolved.price_table.snapshot(),
        source_snapshot=snapshot,
        harness_sha=harness_revision(),
        operator=os.environ.get("EVALH_OPERATOR", "").strip(),
        seed=resolved.spec.seed,
        status="running",
        started_at=started,
        budget_usd=cap,
        budget_override_usd=override_budget,
        n_cells=len(cells),
    )
    repository.create_run(run)
    if override_budget is not None:
        _emit(
            repository,
            redactor,
            run_id,
            "budget_overridden",
            {
                "spec_budget_usd": spec_budget,
                "budget_usd": cap,
                "estimate_usd": estimate.cost_usd,
            },
        )
    return run


def _resume_run(
    repository: Repository,
    redactor: Redactor,
    resolved: ResolvedSpec,
    cells: list[PlannedCell],
    run_id: str,
    override_budget: float | None,
    snapshot: dict[str, Any],
) -> RunRecord:
    """Reopen an existing run in place, raising the cap when asked."""
    run = _resume_target(repository, resolved, run_id, snapshot)
    if override_budget is not None:
        run = _record_override(
            repository, redactor, run, resolved, override_budget
        )
    run = run.model_copy(
        update={
            "status": "running",
            "finished_at": None,
            "n_cells": len(cells),
        }
    )
    repository.update_run(run)
    return run


def _resume_target(
    repository: Repository,
    resolved: ResolvedSpec,
    run_id: str,
    snapshot: dict[str, Any],
) -> RunRecord:
    """Load a run and refuse to resume one that cannot be resumed faithfully."""
    run = repository.get_run(run_id)
    if run is None:
        raise SpecError(
            "--resume",
            run_id,
            "no run with this id exists in this database",
            "pass the run id printed by an earlier run",
        )
    if run.status == "ok":
        raise SpecError(
            "--resume",
            run_id,
            "the run already finished with status=ok",
            "start a new run instead of resuming this one",
        )
    if resolved.spec.seed != run.seed:
        raise SpecError(
            "seed",
            resolved.spec.seed,
            f"the spec seed does not match the seed {run.seed} this run used",
            "restore the spec seed or start a new run",
        )
    stored = repository.get_spec(run.spec_id)
    stored_sha = stored["sha"] if stored is not None else ""
    if stored_sha != resolved.sha:
        raise SpecError(
            "spec",
            resolved.sha[:12],
            "the spec file sha does not match the snapshot sha "
            f"{stored_sha[:12] or '<missing>'} this run used",
            "restore the spec file or start a new run",
        )
    changed = changed_repos(
        run.source_snapshot.get("repos", []),
        snapshot.get("repos", []),
    )
    if changed:
        name, _recorded, _current = changed[0]
        raise SpecError(
            f"repos.{name}",
            name,
            "the repository content changed since this run was launched",
            "restore the tree or start a new run",
        )
    return run


def _record_override(
    repository: Repository,
    redactor: Redactor,
    run: RunRecord,
    resolved: ResolvedSpec,
    override_budget: float,
) -> RunRecord:
    """Raise a run's cap for this resume and record where it came from."""
    _emit(
        repository,
        redactor,
        run.run_id,
        "budget_overridden",
        {
            "spec_budget_usd": resolved.spec.budget_usd,
            "budget_usd": override_budget,
            "estimate_usd": None,
        },
    )
    return run.model_copy(
        update={
            "budget_usd": override_budget,
            "budget_override_usd": override_budget,
        }
    )


def _configure_logging(redactor: Redactor) -> None:
    """Send harness logs to stderr through the redaction filter."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactionFilter(redactor))
    logger = logging.getLogger("evalharness")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _run_reason(run: RunRecord, cancelled_reason: str) -> str:
    """Return the one-line reason a run did not finish normally, or empty."""
    if run.status == "budget_exceeded":
        cap = run.budget_usd or 0.0
        return f"budget exceeded: spent ${run.cost_usd:.6f} of ${cap:.6f}"
    if run.status == "cancelled":
        return f"cancelled: {cancelled_reason or 'no reason recorded'}"
    return ""


__all__ = [
    "PreparedRun",
    "RunOutcome",
    "execute",
    "load_run",
    "redactor_for",
    "stored_credential_states",
    "stored_credentials",
]
