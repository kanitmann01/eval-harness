#!/usr/bin/env python3
"""Smoke-test a running harness: health, one no-credential run and read-back.

The run is launched through the console JSON API (POST /api/runs), so the
script exercises the same path a stranger uses in the browser. The spec must
use only provider: none credentials and must not name a network endpoint,
which is what keeps the smoke run free of paid keys and outside traffic.

Exit 0 prints a short report; any failed assertion prints the fix and exits 1.

Usage::

    python scripts/smoke.py
    python scripts/smoke.py http://127.0.0.1:8000 --spec smoke.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from typing import Any, Sequence

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_SPEC = "smoke.yaml"

TERMINAL_STATUSES = {"ok", "partial", "cancelled", "budget_exceeded"}
"""Run statuses that mean the executor is no longer working on the run."""

PROVIDER_LINE = re.compile(r"^\s*provider:\s*(?P<provider>\S+)")
"""The provider declaration on one credential block in the spec text."""

NETWORK_URL = re.compile(r"https?://")
"""A spec that names one of these would call the network on a smoke run."""


class SmokeError(Exception):
    """One smoke assertion failed; the message names the fix."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run every smoke assertion and return 0 on success, 1 on a failure."""
    parser = argparse.ArgumentParser(
        description="Assert a running harness is healthy and can run one "
        "offline, no-credential spec end to end.",
    )
    parser.add_argument(
        "base_url",
        nargs="?",
        default=DEFAULT_BASE_URL,
        help=f"harness URL (default {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--spec",
        default=DEFAULT_SPEC,
        help="spec name inside the harness specs directory "
        f"(default {DEFAULT_SPEC})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the launched run to finish (default 120)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.2,
        help="seconds between run status reads (default 0.2)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    client = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0)
    try:
        health = _check_health(client, args.base_url)
        spec = _check_spec(client, args.spec)
        run_id = _launch(client, args.spec)
        run = _wait_for_run(client, run_id, args)
        _check_read_back(client, run)
        _check_console(client, run_id)
    except SmokeError as exc:
        print(f"smoke: FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    cells = run.get("cells") or []
    checks = [check for cell in cells for check in (cell.get("checks") or [])]
    passed = sum(1 for check in checks if check.get("passed"))
    print(f"smoke: ok against {args.base_url.rstrip('/')}")
    print(
        "  health: 200 "
        f"version={health.get('version')} "
        f"database={'ok' if health['database']['ok'] else 'down'} "
        f"artifacts={'ok' if health['artifacts']['ok'] else 'unwritable'} "
        f"vault={'configured' if health['vault']['configured'] else 'missing'}"
    )
    print(
        f"  spec: {args.spec} "
        f"cases={spec.get('cases')} cells={spec.get('cells')}"
    )
    print(
        f"  run: {run_id} status={run.get('status')} "
        f"cells={len(cells)} checks={len(checks)} passed={passed}"
    )
    print(f"  console: {args.base_url.rstrip('/')}/runs/{run_id}")
    return 0


def _check_health(client: httpx.Client, base_url: str) -> dict[str, Any]:
    """Assert /healthz is 200, the database answers and the vault is configured."""
    try:
        response = client.get("/healthz")
    except httpx.HTTPError as exc:
        raise SmokeError(
            f"cannot reach the harness at {base_url}: {exc}. Start it with "
            "'evalh serve' or 'docker compose up -d', then re-run this script "
            "with the URL it serves."
        ) from exc
    if response.status_code != 200:
        raise SmokeError(
            f"GET /healthz returned {response.status_code}: {_detail(response)}. "
            "Fix the harness dependency it names (the database URL, the "
            "artifacts directory or the master key) and re-run."
        )
    body = _json(response, "health")
    database = body.get("database") or {}
    artifacts = body.get("artifacts") or {}
    vault = body.get("vault") or {}
    if not database.get("ok"):
        raise SmokeError(
            f"the database is not reachable: {database.get('reason')}. Check "
            "EVALH_DATABASE_URL and that PostgreSQL (or the SQLite volume) is "
            "available."
        )
    if not artifacts.get("ok"):
        raise SmokeError(
            f"the artifacts directory is not writable: {artifacts.get('reason')}. "
            "Check the /data volume mount and its permissions."
        )
    if not vault.get("configured"):
        raise SmokeError(
            "the vault has no master key, so POST /api/runs cannot launch. Start "
            "the harness once without EVALH_MASTER_KEY to print one, put it in "
            ".env, then start it again."
        )
    return body


def _check_spec(client: httpx.Client, name: str) -> dict[str, Any]:
    """Read the spec through the console and assert it needs no paid key or network."""
    response = client.get(f"/api/specs/{name}")
    if response.status_code != 200:
        raise SmokeError(
            f"the harness cannot read spec {name!r}: {_detail(response)}. Put "
            "the spec in the harness specs directory (EVALH_SPECS_DIR, /data/"
            "specs in the image) or pass --spec with a name from GET /api/specs."
        )
    body = _json(response, "spec")
    if not body.get("valid"):
        raise SmokeError(
            f"spec {name!r} is not valid: {body.get('errors')}. Fix it with "
            "'evalh validate' before running the smoke test."
        )
    _assert_offline_spec(name, str(body.get("yaml") or ""))
    return body


def _assert_offline_spec(name: str, text: str) -> None:
    """Refuse a spec that declares a paid provider or a network endpoint."""
    providers: list[str] = []
    urls: list[str] = []
    for line in text.splitlines():
        content = line.split("#", 1)[0]
        match = PROVIDER_LINE.match(content)
        if match is not None:
            providers.append(match.group("provider").strip("'\""))
        if NETWORK_URL.search(content):
            urls.append(content.strip())
    paid = sorted({provider for provider in providers if provider != "none"})
    if paid:
        raise SmokeError(
            f"spec {name!r} declares provider(s) {', '.join(paid)}; the smoke "
            "run must need no paid credential. Use a spec whose credentials are "
            "'provider: none' (examples/specs/smoke.yaml or stub-pack.yaml)."
        )
    if urls:
        raise SmokeError(
            f"spec {name!r} names a network endpoint ({urls[0]!r}); the smoke "
            "run must stay offline. Use a stub spec with an inline source."
        )


def _launch(client: httpx.Client, spec: str) -> str:
    """Launch the spec through the console API and return its run id."""
    response = client.post("/api/runs", json={"spec": spec})
    if response.status_code == 404:
        raise SmokeError(
            "this harness has no POST /api/runs launch endpoint. Use a build "
            "with the console launch surface (issue #12) or run the spec with "
            "'evalh run' and read it back in the console."
        )
    if response.status_code != 202:
        raise SmokeError(
            f"POST /api/runs was refused with {response.status_code}: "
            f"{_detail(response)}. Fix the refusal and re-run."
        )
    body = _json(response, "launch")
    run_id = body.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise SmokeError(f"POST /api/runs returned no run_id: {body}")
    return run_id


def _wait_for_run(
    client: httpx.Client, run_id: str, args: argparse.Namespace
) -> dict[str, Any]:
    """Poll the run until the executor is finished, then return its detail."""
    deadline = time.monotonic() + args.timeout
    while True:
        response = client.get(f"/api/runs/{run_id}")
        if response.status_code == 404:
            raise SmokeError(
                f"run {run_id} is not visible at this URL. The API and the run "
                "must share one database and artifacts directory; check that "
                "the smoke URL points at the harness that launched it."
            )
        if response.status_code != 200:
            raise SmokeError(
                f"GET /api/runs/{run_id} returned {response.status_code}: "
                f"{_detail(response)}"
            )
        run = _json(response, "run")
        if run.get("finished_at") is not None or run.get("status") in TERMINAL_STATUSES:
            break
        if time.monotonic() >= deadline:
            raise SmokeError(
                f"run {run_id} did not finish within {args.timeout:g}s "
                f"(status={run.get('status')}). Read its progress at "
                f"{args.base_url.rstrip('/')}/runs/{run_id}."
            )
        time.sleep(args.poll_interval)
    # A run whose cells errored but whose gates passed exits 0, and
    # stub-pack.yaml depends on that (one candidate fails on purpose). Any
    # other terminal status is a failure.
    if run.get("status") not in {"ok", "partial"}:
        raise SmokeError(
            f"run {run_id} finished with status={run.get('status')}"
            f"{_error_suffix(run)}. Read the cells and gates at "
            f"{args.base_url.rstrip('/')}/runs/{run_id}."
        )
    failed_gates = [
        gate for gate in (run.get("gates") or []) if not gate.get("passed")
    ]
    if failed_gates:
        names = ", ".join(str(gate.get("metric")) for gate in failed_gates)
        raise SmokeError(
            f"run {run_id} failed gate(s) {names}; the run exits 1. Read the "
            f"observed values at {args.base_url.rstrip('/')}/runs/{run_id}."
        )
    return run


def _check_read_back(client: httpx.Client, run: dict[str, Any]) -> None:
    """Assert the run is readable through the API with cells and checks."""
    run_id = str(run.get("run_id"))
    cells = run.get("cells") or []
    if not cells:
        raise SmokeError(
            f"run {run_id} finished but the API shows no cells. Check that the "
            "run and the console read the same database."
        )
    checks = [check for cell in cells for check in (cell.get("checks") or [])]
    if not checks:
        raise SmokeError(
            f"run {run_id} recorded no checks, so the metrics did not run. "
            "Check the run detail page for the cell error."
        )
    if not any(check.get("passed") for check in checks):
        raise SmokeError(
            f"run {run_id} recorded no passing check. Read the failures at "
            "the run detail page; the stub spec should pass exact_match."
        )
    response = client.get(f"/api/runs/{run_id}/cells")
    if response.status_code != 200:
        raise SmokeError(
            f"GET /api/runs/{run_id}/cells returned {response.status_code}: "
            f"{_detail(response)}"
        )
    body = _json(response, "cells")
    if len(body.get("cells") or []) != len(cells):
        raise SmokeError(
            f"the run detail and the cells route disagree on run {run_id}; "
            "the database may be changing underneath the smoke test."
        )


def _check_console(client: httpx.Client, run_id: str) -> None:
    """Assert the browser surface answers for the run list and this run."""
    for path in ("/", f"/runs/{run_id}"):
        response = client.get(path)
        if response.status_code != 200:
            raise SmokeError(
                f"GET {path} returned {response.status_code}; the console is "
                "not reachable even though the run finished."
            )
    detail = client.get(f"/runs/{run_id}")
    if run_id not in detail.text:
        raise SmokeError(
            f"the run detail page at /runs/{run_id} does not mention the run. "
            "Check that the console template can read the run record."
        )


def _json(response: httpx.Response, what: str) -> dict[str, Any]:
    """Decode a JSON object response or fail with the body that was returned."""
    try:
        body = response.json()
    except ValueError as exc:
        raise SmokeError(
            f"the {what} response was not JSON: {response.text[:200]!r}"
        ) from exc
    if not isinstance(body, dict):
        raise SmokeError(f"the {what} response was not a JSON object: {body!r}")
    return body


def _detail(response: httpx.Response) -> str:
    """Return a short, safe detail string from an error response."""
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:200] if text else response.reason_phrase
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("reason")
        return str(detail) if detail else str(body)
    return str(body)


def _error_suffix(run: dict[str, Any]) -> str:
    """Name the recorded run error when there is one."""
    error = run.get("error")
    return f" ({error})" if error else ""


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
