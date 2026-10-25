"""Console launch: spec resolution, the one-run-at-a-time lock and shutdown.

The console never invents a second execution path. A launch loads the same
shared run orchestration the command line uses and drives it from a daemon
thread with its own asyncio loop, so the stored cells and events reach the
existing SSE stream and heatmap while the run is still in flight.
"""

from __future__ import annotations

import logging
import math
import threading
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Request

from evalharness.api.deps import get_settings
from evalharness.core.cancel import CancellationToken
from evalharness.core.config import Settings
from evalharness.core.models import RunRecord
from evalharness.service import PreparedRun, RunOutcome, execute, load_run

LOGGER = logging.getLogger("evalharness.api.launch")

SPEC_SUFFIXES = (".yaml", ".yml")
"""The file names the picker lists and a bare name resolves to."""

SHUTDOWN_JOIN_TIMEOUT_S = 10.0
"""How long application shutdown waits for a cancelled launch to finish."""


class LaunchError(Exception):
    """A launch was refused; the message is safe to show to the operator."""

    status_code = 400


class LaunchRefused(LaunchError):
    """The spec, the budget or the launch itself was refused."""


class LaunchBusy(LaunchError):
    """Another run is already in flight in this container."""

    status_code = 409


@dataclass(frozen=True, slots=True)
class SpecRef:
    """One saved spec the console may launch."""

    name: str
    path: Path


def specs_directory(settings: Settings) -> Path:
    """Return the directory the console may launch specs from.

    The spec editor writes to this same directory, so a spec saved there is
    launchable here without copying it. `Settings.specs_dir` is the one
    source of truth for it.
    """
    return Path(settings.specs_dir).resolve()


def available_specs(settings: Settings) -> list[SpecRef]:
    """List the YAML specs the console may launch, sorted by name."""
    root = specs_directory(settings)
    if not root.is_dir():
        return []
    return [
        SpecRef(name=path.name, path=path)
        for path in sorted(root.iterdir())
        if path.is_file() and path.suffix.lower() in SPEC_SUFFIXES
    ]


def resolve_spec(requested: str, *, settings: Settings) -> Path:
    """Resolve a spec name or path inside the specs directory.

    A bare name resolves to `<name>.yaml` or `<name>.yml`. A path outside the
    specs directory is refused rather than read, so the console can only
    launch what an operator put there.
    """
    text = requested.strip()
    if not text:
        raise LaunchRefused("choose a spec from the specs directory")
    root = specs_directory(settings)
    candidate = Path(text)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise LaunchRefused(f"spec {text!r} is outside the specs directory {root}")
    if not resolved.exists() and resolved.suffix == "":
        for suffix in SPEC_SUFFIXES:
            alternative = resolved.with_suffix(suffix)
            if alternative.is_file():
                resolved = alternative
                break
    if not resolved.is_file():
        raise LaunchRefused(f"no spec named {text!r} in {root}")
    return resolved


def parse_budget_override(value: Any) -> float | None:
    """Validate a JSON budget override; None means "no override"."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LaunchRefused("budget_override_usd must be a number of USD")
    return _positive_money(float(value))


def parse_budget_override_text(text: str) -> float | None:
    """Validate a budget override typed into the launch form."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        number = float(stripped)
    except ValueError:
        raise LaunchRefused("budget_override_usd must be a number of USD") from None
    return _positive_money(number)


def _positive_money(value: float) -> float:
    """Refuse a budget override that could not cap a run."""
    if not math.isfinite(value) or value <= 0:
        raise LaunchRefused("budget_override_usd must be a positive number of USD")
    return value


@dataclass
class ActiveLaunch:
    """One in-flight console launch, its token and its worker thread."""

    run_id: str
    token: CancellationToken = field(default_factory=CancellationToken)
    thread: threading.Thread | None = None
    created: bool = False
    error: str = ""
    ready: threading.Event = field(default_factory=threading.Event)

    def mark_created(self, _run: RunRecord) -> None:
        """Record that the run row exists and unblock the launch response."""
        self.created = True
        self.ready.set()


class LaunchRegistry:
    """The process-wide one-run-at-a-time lock for one console application."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._active: ActiveLaunch | None = None

    def reserve(self, run_id: str) -> ActiveLaunch | None:
        """Claim the lock for a new run, or None when one is already running."""
        with self._guard:
            if self._active is not None:
                return None
            self._active = ActiveLaunch(run_id=run_id)
            return self._active

    def active(self) -> ActiveLaunch | None:
        """Return the launch this container is driving, or None."""
        with self._guard:
            return self._active

    def active_run_id(self) -> str | None:
        """Return the id of the run this container is driving, or None."""
        active = self.active()
        return None if active is None else active.run_id

    def release(self, run_id: str) -> None:
        """Release the lock when this run still holds it."""
        with self._guard:
            if self._active is not None and self._active.run_id == run_id:
                self._active = None

    def cancel(self, run_id: str) -> bool:
        """Cancel this container's run; False when it is not the one in flight."""
        active = self.active()
        if active is None or active.run_id != run_id:
            return False
        active.token.cancel("cancelled from the console")
        return True

    def shutdown(self, timeout: float) -> None:
        """Cancel the in-flight launch and wait up to timeout for its thread."""
        active = self.active()
        if active is None:
            return
        active.token.cancel("console shutting down")
        if active.thread is not None:
            active.thread.join(timeout)


_REGISTRIES: weakref.WeakSet[LaunchRegistry] = weakref.WeakSet()
"""Every registry this process created, so shutdown reaches all of them."""


def get_launches(request: Request) -> LaunchRegistry:
    """Return the launch registry this application owns, creating it once."""
    registry = getattr(request.app.state, "launches", None)
    if registry is None:
        registry = LaunchRegistry()
        request.app.state.launches = registry
        _REGISTRIES.add(registry)
    return registry


def shutdown_launches() -> None:
    """Cancel and join every launch this process is still driving."""
    for registry in list(_REGISTRIES):
        registry.shutdown(SHUTDOWN_JOIN_TIMEOUT_S)


def start_launch(
    request: Request,
    spec_path: Path,
    *,
    budget_override: float | None,
) -> str:
    """Reserve the lock and start one run, returning its id.

    The caller waits only until the run row exists, so a refused launch (bad
    spec, budget refusal, sink failure) is reported to the operator instead of
    being hidden in a thread.
    """
    registry = get_launches(request)
    settings = get_settings(request)
    run_id = uuid.uuid4().hex
    active = registry.reserve(run_id)
    if active is None:
        raise LaunchBusy(_busy_message(registry.active_run_id() or ""))
    active.thread = threading.Thread(
        target=_drive,
        args=(active, registry, spec_path, settings, budget_override),
        name=f"evalh-launch-{run_id}",
        daemon=True,
    )
    active.thread.start()
    active.ready.wait()
    if active.error and not active.created:
        raise LaunchRefused(active.error)
    return run_id


def _busy_message(running: str) -> str:
    """Name the run in flight and the way to stop it."""
    return (
        f"run {running} is already running in this console; one run at a time, "
        f"so cancel it with POST /api/runs/{running}/cancel before launching "
        "another"
    )


def _drive(
    active: ActiveLaunch,
    registry: LaunchRegistry,
    spec_path: Path,
    settings: Settings,
    budget_override: float | None,
) -> None:
    """Load and execute one launch, releasing the lock whatever happens."""
    try:
        prepared: PreparedRun = load_run(
            spec_path, settings=settings, run_id=active.run_id
        )
        outcome: RunOutcome = execute(
            prepared,
            settings=settings,
            run_id=active.run_id,
            cancel_token=active.token,
            override_budget=budget_override,
            on_run_created=active.mark_created,
        )
        LOGGER.info(
            "console run %s finished status=%s cost=%s",
            outcome.run.run_id,
            outcome.run.status,
            f"${outcome.run.cost_usd:.6f}",
        )
    except Exception as exc:  # a refused launch or a crashed loop: release either way
        active.error = str(exc)
        LOGGER.exception("console run %s failed", active.run_id)
    finally:
        registry.release(active.run_id)
        active.ready.set()


__all__ = [
    "ActiveLaunch",
    "LaunchBusy",
    "LaunchError",
    "LaunchRefused",
    "LaunchRegistry",
    "SpecRef",
    "available_specs",
    "get_launches",
    "parse_budget_override",
    "parse_budget_override_text",
    "resolve_spec",
    "shutdown_launches",
    "specs_directory",
    "start_launch",
]
