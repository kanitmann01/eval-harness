"""The command surface: 'evalh run' and 'evalh validate'."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from evalharness import exit_codes
from evalharness.core.config import Settings, harness_revision
from evalharness.core.context import EnvCredentialStore, RunContext
from evalharness.core.cost import (
    DEFAULT_MEDIAN_COMPLETION_TOKENS,
    DEFAULT_MEDIAN_PROMPT_TOKENS,
    estimate_cost,
)
from evalharness.core.events import EventEmitter
from evalharness.core.executor import ExecutionResult, execute_run
from evalharness.core.models import Case, GateResult, RunRecord, utc_now
from evalharness.core.planner import PlannedCell, expand, per_candidate_counts
from evalharness.core.protocols import Sink
from evalharness.core.redaction import NoOpRedactor, SecretRedactor
from evalharness.core.scorer import aggregate_cells, evaluate_gates
from evalharness.core.spec import ResolvedSpec, load_and_validate
from evalharness.errors import SpecError
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
    run_parser.set_defaults(handler=_run)
    validate_parser = commands.add_parser("validate", help="validate a spec only")
    validate_parser.add_argument("spec", help="path to the YAML spec")
    validate_parser.set_defaults(handler=_validate)
    return parser


def _spec_failure(error: SpecError) -> int:
    print(f"spec error: {error}", file=sys.stderr)
    return exit_codes.SPEC_ERROR


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
    try:
        resolved = load_and_validate(args.spec)
    except SpecError as exc:
        return _spec_failure(exc)
    _print_warnings(resolved)
    spec = resolved.spec
    print(
        f"spec valid: {spec.name} "
        f"({len(spec.candidates)} candidates, {len(spec.metrics)} metrics)"
    )
    return exit_codes.SUCCESS


def _print_dry_run(
    resolved: ResolvedSpec,
    cases: list[Case],
    cells: list[PlannedCell],
) -> None:
    spec = resolved.spec
    counts = per_candidate_counts(cells)
    estimate = estimate_cost(spec, resolved.prices, counts)
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


def _run(args: argparse.Namespace) -> int:
    try:
        resolved = load_and_validate(args.spec)
    except SpecError as exc:
        return _spec_failure(exc)
    _print_warnings(resolved)
    run_id = uuid.uuid4().hex
    try:
        cases = _load_cases(resolved, run_id)
        cells = expand(resolved, cases)
    except SpecError as exc:
        return _spec_failure(exc)
    if args.dry_run:
        _print_dry_run(resolved, cases, cells)
        return exit_codes.SUCCESS

    settings = Settings.from_env()
    if args.db:
        settings = replace(settings, database_url=args.db)
    if args.artifacts:
        settings = replace(settings, artifacts_dir=Path(args.artifacts))
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
            budget_usd=resolved.spec.budget_usd,
            n_cells=len(cells),
        )
        repository.create_run(run)
        credentials = EnvCredentialStore.from_spec(resolved.spec.credentials)
        redactor = SecretRedactor(
            {
                name: handle.secret
                for name in credentials.names()
                if (handle := credentials.get(name)) is not None
            }
        )
        execution = asyncio.run(
            execute_run(
                resolved=resolved,
                cells=cells,
                cases=cases,
                run=run,
                repository=repository,
                sink=sink,
                credentials=credentials,
                redactor=redactor,
                artifacts_root=settings.artifacts_dir,
            )
        )
        aggregates = aggregate_cells(run.run_id, execution.cells, execution.checks)
        repository.record_aggregates(aggregates)
        gates = evaluate_gates(resolved.spec.gates, execution.checks)
        repository.record_gates(run.run_id, gates)
        _print_run_table(execution, gates)
    finally:
        database.dispose()
    if execution.run.status == "budget_exceeded":
        return exit_codes.BUDGET_ABORT
    if any(not gate.passed for gate in gates):
        return exit_codes.GATE_FAILURE
    return exit_codes.SUCCESS


def _print_run_table(result: ExecutionResult, gates: list[GateResult]) -> None:
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
    for gate in gates:
        print(
            f"gate {gate.name}: {'pass' if gate.passed else 'FAIL'} "
            f"value={gate.detail.get('value')} n={gate.detail.get('n')}"
        )
