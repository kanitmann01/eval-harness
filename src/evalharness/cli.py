"""The command surface: run, validate, conformance, credentials and read-back."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import signal
import sys
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import uvicorn

from evalharness import exit_codes, service
from evalharness.adapters.verify.http_verifier import build as build_verifier
from evalharness.api.app import create_app
from evalharness.conformance import KINDS, run_all, run_suite
from evalharness.conformance.suite import ConformanceReport
from evalharness.core.cancel import CancellationToken
from evalharness.core.config import Settings
from evalharness.core.cost import (
    DEFAULT_MEDIAN_COMPLETION_TOKENS,
    DEFAULT_MEDIAN_PROMPT_TOKENS,
)
from evalharness.core.models import Aggregate
from evalharness.core.planner import per_candidate_counts
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


def _print_warnings(resolved: ResolvedSpec) -> None:
    for warning in resolved.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _validate(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    try:
        resolved = load_and_validate(
            args.spec,
            stored_credentials=service.stored_credential_names(settings),
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


def _print_dry_run(prepared: service.PreparedRun) -> None:
    """Print the planned cells and estimate for a spec, executing nothing."""
    resolved = prepared.resolved
    spec = resolved.spec
    counts = per_candidate_counts(prepared.cells)
    estimate = prepared.estimate
    print(f"spec: {spec.name} (sha256 {resolved.sha[:12]})")
    print(
        f"candidates: {len(spec.candidates)}  cases: {len(prepared.cases)}  "
        f"replicas: {spec.replicas}  concurrency: {spec.concurrency}"
    )
    print(f"cells: {len(prepared.cells)}")
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


def _sigint_handler(token: CancellationToken) -> Callable[[int, Any], None]:
    """Build the SIGINT handler that cancels the run between cells."""

    def handler(signum: int, frame: Any) -> None:
        token.cancel("interrupted (SIGINT)")

    return handler


def _run(args: argparse.Namespace) -> int:
    """Execute one spec or resume an existing run and return its exit code."""
    settings = _settings_for(args)
    run_id = args.resume or uuid.uuid4().hex
    try:
        prepared = service.load_run(args.spec, settings=settings, run_id=run_id)
    except SpecError as exc:
        return _spec_failure(exc)
    _print_warnings(prepared.resolved)
    if args.dry_run:
        _print_dry_run(prepared)
        return exit_codes.SUCCESS
    token = CancellationToken(cancel_after=args.cancel_after)
    previous_sigint = signal.signal(signal.SIGINT, _sigint_handler(token))
    try:
        outcome = service.execute(
            prepared,
            settings=settings,
            run_id=run_id,
            cancel_token=token,
            override_budget=args.override_budget,
            resume_run_id=args.resume,
        )
    except MasterKeyError as exc:
        return _master_key_failure(exc)
    except SpecError as exc:
        return _spec_failure(exc)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
    _print_run_table(outcome)
    return outcome.exit_code


def _print_run_table(outcome: service.RunOutcome) -> None:
    """Print the candidate table, the run line, the reason, aggregates, gates."""
    print(
        f"{'candidate':<24} {'cells':>5} {'ok':>4} {'fail':>5} "
        f"{'cost_usd':>10} {'duration_s':>10}"
    )
    for summary in outcome.summaries:
        print(
            f"{summary.candidate:<24} {summary.cells:>5} {summary.ok:>4} "
            f"{summary.failed:>5} {summary.cost_usd:>10.6f} "
            f"{summary.duration_s:>10.3f}"
        )
    run = outcome.run
    print(
        f"run {run.run_id} status={run.status} cells={run.n_cells} "
        f"ok={run.n_ok} cost=${run.cost_usd:.6f}"
    )
    answer_cost = sum(cell.answer_cost_usd for cell in outcome.cells)
    judge_cost = sum(cell.judge_cost_usd for cell in outcome.cells)
    tool_cost = sum(cell.tool_cost_usd for cell in outcome.cells)
    print(
        f"cost: answer ${answer_cost:.6f}, judge ${judge_cost:.6f}, "
        f"tools ${tool_cost:.6f}"
    )
    if outcome.reason:
        print(f"reason: {outcome.reason}")
    _print_aggregates(outcome.aggregates)
    for gate in outcome.gates:
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
    message = service.redactor_for(store).redact(result.message)
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
