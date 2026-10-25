"""The command surface: run, validate, conformance, credentials and read-back."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import signal
import sys
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from sqlalchemy.engine import make_url

import uvicorn

from evalharness import exit_codes
from evalharness.adapters.verify.http_verifier import build as build_verifier
from evalharness.api.app import create_app
from evalharness.conformance import KINDS, run_all, run_suite
from evalharness.conformance.suite import ConformanceReport
from evalharness.core.cancel import CancellationToken
from evalharness.core.config import Settings, harness_revision
from evalharness.core.context import CredentialStore, RunContext
from evalharness.core.cost import (
    DEFAULT_MEDIAN_COMPLETION_TOKENS,
    DEFAULT_MEDIAN_PROMPT_TOKENS,
    CostEstimate,
    estimate_cost,
    observed_medians,
)
from evalharness.core.events import EventEmitter
from evalharness.core.executor import ExecutionResult, execute_run, summarise
from evalharness.core.models import (
    Aggregate,
    Case,
    GateResult,
    RunRecord,
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
from evalharness.core.scorer import aggregate_cells, evaluate_gates
from evalharness.core.spec import ResolvedSpec, load_and_validate
from evalharness.core.vault import (
    MasterKeyError,
    Vault,
    VaultCredentialStore,
    open_vault,
)
from evalharness.errors import HarnessError, SpecError
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch one command, returning its exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else exit_codes.SPEC_ERROR
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return exit_codes.SUCCESS
    return handler(args)


def _non_negative_int(text: str) -> int:
    """Parse a CLI count that must be zero or greater."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evalh",
        description="Run and validate adapter-based evaluation specs.",
    )
    commands = parser.add_subparsers(dest="command")
    run_parser = commands.add_parser("run", help="execute a spec")
    run_parser.add_argument("spec", help="path to the YAML spec")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print cells and cost estimate without executing anything",
    )
    run_parser.add_argument("--db", help="override EVALH_DATABASE_URL")
    run_parser.add_argument("--artifacts", help="override EVALH_ARTIFACTS_DIR")
    run_parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="continue an existing run instead of starting a new one",
    )
    run_parser.add_argument(
        "--override-budget",
        type=float,
        metavar="USD",
        help="replace the spec budget for this run and record the override",
    )
    run_parser.add_argument(
        "--cancel-after",
        type=_non_negative_int,
        metavar="N",
        help="cancel once N cells have completed, between cells",
    )
    run_parser.set_defaults(handler=_run)
    validate_parser = commands.add_parser("validate", help="validate a spec only")
    validate_parser.add_argument("spec", help="path to the YAML spec")
    validate_parser.set_defaults(handler=_validate)
    conformance_parser = commands.add_parser(
        "conformance",
        help="run the public adapter contract suite",
    )
    selection = conformance_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--all",
        action="store_true",
        help="run every first-party adapter listed in the manifest",
    )
    selection.add_argument(
        "--adapter",
        help="dotted factory path, for example pkg.module:build",
    )
    conformance_parser.add_argument(
        "--kind",
        choices=list(KINDS),
        help="protocol kind for --adapter (required with --adapter)",
    )
    conformance_parser.set_defaults(handler=_conformance)

    credentials_parser = commands.add_parser(
        "credentials",
        help="add, list, test and delete encrypted provider keys",
    )
    credential_commands = credentials_parser.add_subparsers(
        dest="credentials_command"
    )
    add_parser = credential_commands.add_parser(
        "add", help="store one provider key, encrypted at rest"
    )
    add_parser.add_argument("name", help="logical credential name")
    add_parser.add_argument("--provider", required=True, help="provider name")
    secret_source = add_parser.add_mutually_exclusive_group()
    secret_source.add_argument(
        "--secret-stdin",
        action="store_true",
        help="read the secret from standard input without echoing it",
    )
    secret_source.add_argument(
        "--secret-env",
        metavar="VAR",
        help="read the secret from an environment variable",
    )
    add_parser.set_defaults(handler=_credentials)
    list_parser = credential_commands.add_parser(
        "list", help="list stored credentials without their secrets"
    )
    list_parser.set_defaults(handler=_credentials)
    test_parser = credential_commands.add_parser(
        "test", help="verify one stored credential with one provider request"
    )
    test_parser.add_argument("name", help="logical credential name")
    test_parser.add_argument(
        "--verify-url",
        help="override EVALH_VERIFY_URL for this request",
    )
    test_parser.set_defaults(handler=_credentials)
    delete_parser = credential_commands.add_parser(
        "delete", help="delete one stored credential"
    )
    delete_parser.add_argument("name", help="logical credential name")
    delete_parser.add_argument(
        "--yes", action="store_true", help="do not ask for confirmation"
    )
    delete_parser.set_defaults(handler=_credentials)
    credentials_parser.set_defaults(
        handler=_credentials,
        credentials_parser=credentials_parser,
    )
    serve_parser = commands.add_parser(
        "serve", help="serve the read-only console on the CLI's database"
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--db", help="override EVALH_DATABASE_URL")
    serve_parser.add_argument("--artifacts", help="override EVALH_ARTIFACTS_DIR")
    serve_parser.set_defaults(handler=_serve)
    return parser


def _spec_failure(error: SpecError) -> int:
    print(f"spec error: {error}", file=sys.stderr)
    return exit_codes.SPEC_ERROR


def _master_key_failure(error: MasterKeyError) -> int:
    """Print a generated key exactly once and refuse to continue."""
    if error.generated_key is not None:
        print(
            "no master key is configured; generated one (shown once, not saved):",
            file=sys.stderr,
        )
        print(f"  EVALH_MASTER_KEY={error.generated_key}", file=sys.stderr)
        print(
            "store it in the environment, then re-run this command.",
            file=sys.stderr,
        )
    return _spec_failure(error)


def _stored_credential_names(settings: Settings) -> list[str]:
    """Read stored credential names without creating an absent database."""
    parsed = make_url(settings.database_url)
    if parsed.get_backend_name() == "sqlite":
        database_path = parsed.database or ""
        if database_path in {"", ":memory:"} or not Path(database_path).exists():
            return []
    database = Database(settings.database_url)
    try:
        database.create_all()
        return [record.name for record in Repository(database).list_credentials()]
    finally:
        database.dispose()


def _redactor_for(store: CredentialStore) -> SecretRedactor:
    """Build a redactor from every secret the store can resolve."""
    secrets: dict[str, str] = {}
    for name in store.names():
        handle = store.get(name)
        if handle is not None and handle.secret:
            secrets[name] = handle.secret
    return SecretRedactor(secrets)


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


def _load_cases(resolved: ResolvedSpec, run_id: str) -> list[Case]:
    context = RunContext(
        run_id=run_id,
        seed=resolved.spec.seed,
        credential=None,
        events=EventEmitter(run_id),
        redactor=NoOpRedactor(),
    )
    try:
        cases = list(resolved.source.load(resolved.spec.source, context))
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


def _print_warnings(resolved: ResolvedSpec) -> None:
    for warning in resolved.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _validate(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    try:
        resolved = load_and_validate(
            args.spec,
            stored_credentials=_stored_credential_names(settings),
        )
    except SpecError as exc:
        return _spec_failure(exc)
    _print_warnings(resolved)
    spec = resolved.spec
    print(
        f"spec valid: {spec.name} "
        f"({len(spec.candidates)} candidates, {len(spec.metrics)} metrics)"
    )
    return exit_codes.SUCCESS


def _print_conformance_report(report: ConformanceReport) -> None:
    print(f"adapter {report.adapter} ({report.kind})")
    for result in report.results:
        if result.ok:
            print(f"  {result.clause}: ok")
        else:
            print(f"  {result.clause}: FAIL {result.detail}")


def _conformance(args: argparse.Namespace) -> int:
    if args.adapter is not None and args.kind is None:
        print("conformance error: --adapter requires --kind", file=sys.stderr)
        return exit_codes.SPEC_ERROR
    if args.all and args.kind is not None:
        print("conformance error: --kind only applies with --adapter", file=sys.stderr)
        return exit_codes.SPEC_ERROR
    try:
        if args.all:
            reports = run_all()
        else:
            reports = [run_suite(args.adapter, args.kind)]
    except (HarnessError, ValueError) as exc:
        print(f"conformance error: {exc}", file=sys.stderr)
        return exit_codes.SPEC_ERROR
    for report in reports:
        _print_conformance_report(report)
    total = sum(len(report.results) for report in reports)
    failed = sum(
        1 for report in reports for result in report.results if not result.ok
    )
    if failed:
        print(f"conformance failed: {failed} of {total} clause(s)", file=sys.stderr)
        return exit_codes.GATE_FAILURE
    print(f"conformance passed: {total} clause(s)")
    return exit_codes.SUCCESS


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
        resolved.prices,
        per_candidate_counts(cells),
        median_prompt_tokens=prompt,
        median_completion_tokens=completion,
    )


def _settings_for(args: argparse.Namespace) -> Settings:
    """Return environment settings with the command's path overrides applied."""
    settings = Settings.from_env()
    if args.db:
        settings = replace(settings, database_url=args.db)
    if args.artifacts:
        settings = replace(settings, artifacts_dir=Path(args.artifacts))
    return settings


def _serve(args: argparse.Namespace) -> int:
    """Serve the read-only console against the CLI's database and artifacts."""
    settings = _settings_for(args)
    app = create_app(settings)
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            access_log=False,
        )
    finally:
        if app.state.database is not None:
            app.state.database.dispose()
    return exit_codes.SUCCESS


def _print_dry_run(
    resolved: ResolvedSpec,
    cases: list[Case],
    cells: list[PlannedCell],
) -> None:
    spec = resolved.spec
    counts = per_candidate_counts(cells)
    estimate = _estimate(resolved, cells, None)
    print(f"spec: {spec.name} (sha256 {resolved.sha[:12]})")
    print(
        f"candidates: {len(spec.candidates)}  cases: {len(cases)}  "
        f"replicas: {spec.replicas}  concurrency: {spec.concurrency}"
    )
    print(f"cells: {len(cells)}")
    for candidate in spec.candidates:
        print(f"  {candidate.name}: {counts.get(candidate.name, 0)} cells")
    print(
        f"estimated cost: ${estimate.cost_usd:.6f} "
        f"(median {DEFAULT_MEDIAN_PROMPT_TOKENS} prompt + "
        f"{DEFAULT_MEDIAN_COMPLETION_TOKENS} completion tokens per cell)"
    )
    if spec.budget_usd is not None and estimate.cost_usd > spec.budget_usd:
        print(
            f"over budget: ${estimate.cost_usd:.6f} exceeds "
            f"budget_usd=${spec.budget_usd:.6f}; a real run is refused until "
            "you raise the budget or pass --override-budget"
        )
    print("dry run: nothing executed")


def _build_sink(resolved: ResolvedSpec, settings: Settings) -> Sink:
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
        prices_snapshot={
            model: price.model_dump() for model, price in resolved.prices.items()
        },
        harness_sha=harness_revision(),
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


def _resume_target(
    repository: Repository,
    resolved: ResolvedSpec,
    run_id: str,
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


def _sigint_handler(token: CancellationToken) -> Callable[[int, Any], None]:
    """Build the SIGINT handler that cancels the run between cells."""

    def handler(signum: int, frame: Any) -> None:
        token.cancel("interrupted (SIGINT)")

    return handler


def _run(args: argparse.Namespace) -> int:
    """Execute one spec or resume an existing run and return its exit code."""
    settings = _settings_for(args)
    try:
        resolved = load_and_validate(
            args.spec,
            stored_credentials=_stored_credential_names(settings),
        )
    except SpecError as exc:
        return _spec_failure(exc)
    _print_warnings(resolved)
    run_id = args.resume or uuid.uuid4().hex
    try:
        cases = _load_cases(resolved, run_id)
        cells = expand(resolved, cases)
    except SpecError as exc:
        return _spec_failure(exc)
    if args.dry_run:
        _print_dry_run(resolved, cases, cells)
        return exit_codes.SUCCESS

    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _master_key_failure(exc)
    try:
        settings.home.mkdir(parents=True, exist_ok=True)
        sink = _build_sink(resolved, settings)
    except OSError as exc:
        return _spec_failure(
            SpecError(
                "EVALH_HOME",
                str(settings.home),
                f"the harness home could not be created: {exc}",
                "set EVALH_HOME to a writable directory",
            )
        )
    except SpecError as exc:
        return _spec_failure(exc)

    database = Database(settings.database_url)
    database.create_all()
    try:
        repository = Repository(database)
        store = VaultCredentialStore(vault, repository)
        try:
            redactor = _redactor_for(store)
        except SpecError as exc:
            return _spec_failure(exc)
        _configure_logging(redactor)
        token = CancellationToken(cancel_after=args.cancel_after)
        resumed = args.resume is not None
        if resumed:
            try:
                run = _resume_target(repository, resolved, args.resume)
            except SpecError as exc:
                return _spec_failure(exc)
            if args.override_budget is not None:
                run = _record_override(
                    repository, redactor, run, resolved, args.override_budget
                )
            run = run.model_copy(
                update={
                    "status": "running",
                    "finished_at": None,
                    "n_cells": len(cells),
                }
            )
            repository.update_run(run)
        else:
            try:
                run = _launch_run(
                    repository,
                    redactor,
                    resolved,
                    run_id,
                    cells,
                    args.override_budget,
                )
            except SpecError as exc:
                return _spec_failure(exc)
        previous_sigint = signal.signal(signal.SIGINT, _sigint_handler(token))
        try:
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
                    cancel_token=token,
                    resumed=resumed,
                )
            )
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
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
        result = ExecutionResult(
            run=execution.run,
            cells=all_cells,
            checks=checks_by_cell,
            summaries=summarise(resolved, all_cells),
        )
        _print_run_table(
            result,
            aggregates,
            gates,
            _run_reason(execution.run, token.reason),
        )
    finally:
        database.dispose()
    return exit_codes.for_run(
        status=execution.run.status,
        gate_failed=any(not gate.passed for gate in gates),
    )


def _run_reason(run: RunRecord, cancelled_reason: str) -> str:
    """Return the one-line reason a run did not finish normally, or empty."""
    if run.status == "budget_exceeded":
        cap = run.budget_usd or 0.0
        return f"budget exceeded: spent ${run.cost_usd:.6f} of ${cap:.6f}"
    if run.status == "cancelled":
        return f"cancelled: {cancelled_reason or 'no reason recorded'}"
    return ""


def _print_run_table(
    result: ExecutionResult,
    aggregates: list[Aggregate],
    gates: list[GateResult],
    reason: str = "",
) -> None:
    """Print the candidate table, the run line, the reason, aggregates, gates."""
    print(
        f"{'candidate':<24} {'cells':>5} {'ok':>4} {'fail':>5} "
        f"{'cost_usd':>10} {'duration_s':>10}"
    )
    for summary in result.summaries:
        print(
            f"{summary.candidate:<24} {summary.cells:>5} {summary.ok:>4} "
            f"{summary.failed:>5} {summary.cost_usd:>10.6f} "
            f"{summary.duration_s:>10.3f}"
        )
    run = result.run
    print(
        f"run {run.run_id} status={run.status} cells={run.n_cells} "
        f"ok={run.n_ok} cost=${run.cost_usd:.6f}"
    )
    answer_cost = sum(cell.answer_cost_usd for cell in result.cells)
    judge_cost = sum(cell.judge_cost_usd for cell in result.cells)
    tool_cost = sum(cell.tool_cost_usd for cell in result.cells)
    print(
        f"cost: answer ${answer_cost:.6f}, judge ${judge_cost:.6f}, "
        f"tools ${tool_cost:.6f}"
    )
    if reason:
        print(f"reason: {reason}")
    _print_aggregates(aggregates)
    for gate in gates:
        print(
            f"gate {gate.name}: {'pass' if gate.passed else 'FAIL'} "
            f"observed={_format_percent(gate.observed, missing='undefined')} "
            f"{gate.comparison} {_format_percent(gate.threshold)} n={gate.n}"
        )


def _print_aggregates(aggregates: list[Aggregate]) -> None:
    """Print one overall row per candidate and metric, then one row per tier."""
    print(
        f"{'candidate':<24} {'metric':<20} {'tier':>5} {'n':>5} {'value':>9} "
        f"{'95% interval':>16} {'pass@k':>8} {'pass^k':>8}"
    )
    for aggregate in aggregates:
        tier = "all" if aggregate.tier is None else str(aggregate.tier)
        value = _format_percent(aggregate.value, missing="undefined")
        interval = _format_interval(aggregate.ci_low, aggregate.ci_high)
        print(
            f"{aggregate.candidate:<24} {aggregate.metric:<20} {tier:>5} "
            f"{aggregate.n:>5} {value:>9} {interval:>16} "
            f"{_format_percent(aggregate.pass_at_k):>8} "
            f"{_format_percent(aggregate.pass_k):>8}"
        )


def _format_percent(value: float | None, missing: str = "-") -> str:
    """Format a proportion as a one-decimal percentage, or the missing marker."""
    if value is None:
        return missing
    return f"{value * 100:.1f}%"


def _format_interval(low: float | None, high: float | None) -> str:
    """Format a confidence interval as two percentages, or a dash."""
    if low is None or high is None:
        return "-"
    return f"{low * 100:.1f}% to {high * 100:.1f}%"


def _format_time(value: datetime | None) -> str:
    """Format a stored timestamp for the CLI, or a dash when absent."""
    return value.isoformat(timespec="seconds") if value is not None else "-"


def _credentials(args: argparse.Namespace) -> int:
    """Dispatch one credentials subcommand against the vault."""
    if args.credentials_command is None:
        args.credentials_parser.print_help()
        return exit_codes.SUCCESS
    settings = Settings.from_env()
    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _master_key_failure(exc)
    try:
        settings.home.mkdir(parents=True, exist_ok=True)
        database = Database(settings.database_url)
    except OSError as exc:
        return _spec_failure(
            SpecError(
                "EVALH_HOME",
                str(settings.home),
                f"the harness home could not be created: {exc}",
                "set EVALH_HOME to a writable directory",
            )
        )
    database.create_all()
    repository = Repository(database)
    handlers = {
        "add": _credentials_add,
        "list": _credentials_list,
        "test": _credentials_test,
        "delete": _credentials_delete,
    }
    try:
        return handlers[args.credentials_command](args, settings, vault, repository)
    except SpecError as exc:
        return _spec_failure(exc)
    finally:
        database.dispose()


def _read_secret(args: argparse.Namespace) -> str:
    """Read the secret from an env var, stdin, or a no-echo prompt."""
    if args.secret_env:
        secret = os.environ.get(args.secret_env, "")
        if not secret:
            raise SpecError(
                "--secret-env",
                args.secret_env,
                "the environment variable is unset or empty",
                "export the key in that variable or pass --secret-stdin",
            )
        return secret
    if args.secret_stdin:
        secret = sys.stdin.readline().strip()
    else:
        secret = getpass.getpass(f"secret for {args.name}: ")
    if not secret:
        raise SpecError(
            "credentials",
            args.name,
            "the secret is empty",
            "pass a non-empty key with --secret-stdin or --secret-env",
        )
    return secret


def _credentials_add(
    args: argparse.Namespace,
    settings: Settings,
    vault: Vault,
    repository: Repository,
) -> int:
    """Encrypt and store one credential, never echoing the secret."""
    secret = _read_secret(args)
    record = repository.add_credential(
        args.name, args.provider, vault.encrypt(secret)
    )
    print(
        f"added credential {record.name} provider={record.provider} "
        f"created={_format_time(record.created_at)}"
    )
    return exit_codes.SUCCESS


def _credentials_list(
    args: argparse.Namespace,
    settings: Settings,
    vault: Vault,
    repository: Repository,
) -> int:
    """Print stored credential metadata; never the secret."""
    records = repository.list_credentials()
    if not records:
        print("no credentials stored")
        return exit_codes.SUCCESS
    print(
        f"{'name':<20} {'provider':<14} {'created':<22} "
        f"{'last_verified':<22} last_error"
    )
    for record in records:
        print(
            f"{record.name:<20} {record.provider:<14} "
            f"{_format_time(record.created_at):<22} "
            f"{_format_time(record.last_ok_at):<22} "
            f"{record.last_error or '-'}"
        )
    return exit_codes.SUCCESS


def _credentials_test(
    args: argparse.Namespace,
    settings: Settings,
    vault: Vault,
    repository: Repository,
) -> int:
    """Verify one credential once and record the provider's verdict."""
    store = VaultCredentialStore(vault, repository)
    handle = store.get(args.name)
    if handle is None:
        raise SpecError(
            f"credentials.{args.name}",
            args.name,
            "unknown credential",
            "list the stored credentials with 'evalh credentials list'",
        )
    base_url = args.verify_url or settings.verify_url
    if not base_url:
        raise SpecError(
            "EVALH_VERIFY_URL",
            "",
            "no verification endpoint is configured",
            "set EVALH_VERIFY_URL to the provider base URL or pass --verify-url",
        )
    verifier = build_verifier("verifier", {"base_url": base_url})
    result = asyncio.run(verifier.verify(handle.provider, handle.secret))
    message = _redactor_for(store).redact(result.message)
    repository.test_credential(
        args.name,
        error=None if result.ok else message,
    )
    if result.ok:
        print(f"verified: {message}")
        return exit_codes.SUCCESS
    print(f"rejected: {message}")
    return exit_codes.GATE_FAILURE


def _credentials_delete(
    args: argparse.Namespace,
    settings: Settings,
    vault: Vault,
    repository: Repository,
) -> int:
    """Delete one credential after confirmation."""
    if repository.get_credential(args.name) is None:
        raise SpecError(
            f"credentials.{args.name}",
            args.name,
            "unknown credential",
            "list the stored credentials with 'evalh credentials list'",
        )
    if not args.yes:
        print(f"delete credential {args.name}? [y/N] ", end="", flush=True)
        answer = sys.stdin.readline().strip().lower()
        if answer not in {"y", "yes"}:
            print("not deleted")
            return exit_codes.SUCCESS
    repository.delete_credential(args.name)
    print(f"deleted credential {args.name}")
    return exit_codes.SUCCESS
