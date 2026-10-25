"""Liveness and readiness probes for containers and operators.

`/healthz` is the Docker healthcheck and the compose readiness probe; `/api/health`
is the same payload for a monitor that only reads JSON under `/api`. The payload
never carries the master key, a credential or the database URL, because a health
endpoint is usually the least protected surface in a deployment.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Response

from evalharness import __version__
from evalharness.api.deps import get_repository, get_settings
from evalharness.core.config import Settings
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get("/healthz", name="healthz")
@router.get("/api/health", name="api_health")
def health(
    response: Response,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Report the version, database reachability, artifact writability and vault.

    The status is 200 while the console can serve and read artifacts. A database
    that does not answer is 503: the run list cannot be read, so the process is
    not ready. An unwritable artifacts directory or an unconfigured vault is
    200 with `status: degraded`, because the console itself still answers.
    """
    database = _database_state(repository)
    artifacts = _artifacts_state(settings.artifacts_dir)
    vault_configured = bool(settings.master_key or settings.master_key_file)
    reasons = _reasons(database, artifacts, vault_configured)
    if not database["ok"]:
        response.status_code = 503
        status = "unavailable"
    elif reasons:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "version": __version__,
        "database": database,
        "artifacts": artifacts,
        "vault": {"configured": vault_configured},
        "reason": "; ".join(reasons) or None,
    }


def _database_state(repository: Repository) -> dict[str, object]:
    """Prove the database answers a trivial query without leaking its URL."""
    try:
        repository.ping()
    except Exception as exc:  # a probe must never turn a dependency failure into a 500
        return {
            "ok": False,
            "reason": (
                "the database is unreachable "
                f"({type(exc).__name__}); check EVALH_DATABASE_URL and that the "
                "database is running"
            ),
        }
    return {"ok": True, "reason": None}


def _artifacts_state(directory: Path) -> dict[str, object]:
    """Create the artifacts directory and prove it accepts a write and a delete."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".health-{os.getpid()}-{uuid.uuid4().hex}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        return {
            "ok": False,
            "path": str(directory),
            "reason": (
                f"the artifacts directory {directory} is not writable "
                f"({exc.strerror or type(exc).__name__})"
            ),
        }
    return {"ok": True, "path": str(directory), "reason": None}


def _reasons(
    database: dict[str, object],
    artifacts: dict[str, object],
    vault_configured: bool,
) -> list[str]:
    """Collect the human-readable reasons a probe is not fully healthy."""
    reasons: list[str] = []
    if not database["ok"]:
        reasons.append(str(database["reason"]))
    if not artifacts["ok"]:
        reasons.append(str(artifacts["reason"]))
    if not vault_configured:
        reasons.append(
            "no master key is configured; run 'evalh serve' once to print one, "
            "then set EVALH_MASTER_KEY"
        )
    return reasons
