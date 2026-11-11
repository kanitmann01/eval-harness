"""The demo library: four scripted walkthroughs of the engine, no ledger.

A demo is a bundled spec, not a second execution path. Run demo copies the
spec into the operator's specs directory on first use, launches it once
through the same one-run-at-a-time path as the Launch page, and redirects to
the trace with replay on. Every later click replays that recorded run without
executing anything. Open spec copies it and lands in the normal editor.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from evalharness.api.deps import get_repository, get_settings, render
from evalharness.api.launch import LaunchError, start_launch
from evalharness.core.config import Settings
from evalharness.core.spec import SpecError, parse_spec
from evalharness.demos.catalog import (
    PIPELINE,
    find_demo,
    materialize,
    scenario_rows,
)
from evalharness.storage.repository import Repository, RunSummary

router = APIRouter()


@router.get("/demos", response_class=HTMLResponse, name="demo_list")
def demo_list(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Render the demo library with its pipeline and offline callout."""
    return _demo_page(request, settings)


@router.post(
    "/demos/{slug}/run",
    response_class=HTMLResponse,
    name="demo_run",
)
async def demo_run(
    request: Request,
    slug: str,
    settings: Settings = Depends(get_settings),
    repository: Repository = Depends(get_repository),
) -> Response:
    """Replay the recorded demo run, or record it once before replaying.

    A gate failure is a recorded result, not a launch error: the redirect is
    the same for a passing and a failing demo. Only a refused launch (bad
    spec, another run in flight) comes back to the library with its status.

    Once a demo has a finished run, later clicks replay that run's stored
    events. Nothing is executed, so a demo can never spend a token twice.
    """
    scenario = find_demo(slug)
    if scenario is None:
        return _not_found(request, slug)
    recorded = _recorded_run(repository, scenario.spec_name)
    if recorded is not None:
        return _replay_trace(request, recorded.run_id)
    path = materialize(settings, scenario)
    refusal = _live_provider_refusal(path, scenario.spec_name)
    if refusal:
        return _demo_page(request, settings, error=refusal, status_code=400)
    try:
        run_id = await run_in_threadpool(
            start_launch, request, path, budget_override=None
        )
    except LaunchError as exc:
        return _demo_page(
            request,
            settings,
            error=str(exc),
            status_code=exc.status_code,
        )
    await run_in_threadpool(_wait_for_finish, repository, run_id)
    return _replay_trace(request, run_id)


@router.post(
    "/demos/{slug}/open",
    response_class=HTMLResponse,
    name="demo_open",
)
def demo_open(
    request: Request,
    slug: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Copy the demo spec if needed and open it in the normal spec editor."""
    scenario = find_demo(slug)
    if scenario is None:
        return _not_found(request, slug)
    materialize(settings, scenario)
    return RedirectResponse(
        url=f"{request.url_for('spec_edit', name=scenario.spec_name)}?copied=demo",
        status_code=303,
    )


_DEMO_CANDIDATE_PREFIX = "evalharness.adapters.candidates.stub:"
_DEMO_JUDGE_PREFIX = "evalharness.adapters.judges.scripted:"


def _live_provider_refusal(path: Path, spec_name: str) -> str:
    """Refuse to run a demo spec that was edited to call a live provider.

    A demo must never spend. The bundled specs use stub candidates and, where a
    judge is declared, the scripted judge. A spec that no longer does must be
    launched deliberately from the Launch page, never from Run demo.
    """
    try:
        spec = parse_spec(path.read_text(encoding="utf-8"))
    except (OSError, SpecError):
        return ""
    live = [
        candidate.name
        for candidate in spec.candidates
        if not candidate.adapter.startswith(_DEMO_CANDIDATE_PREFIX)
    ]
    live.extend(
        judge.name
        for judge in spec.judges
        if not judge.adapter.startswith(_DEMO_JUDGE_PREFIX)
    )
    if not live:
        return ""
    return (
        f"{spec_name} now names a live provider ({', '.join(live)}). "
        "Run demo never spends; restore the bundled copy, or launch it "
        "yourself from Launch."
    )


DEMO_FINISH_TIMEOUT_S = 30.0
"""The longest a demo launch waits before its trace is shown as it stands."""

_POLL_S = 0.02
"""How often the demo route checks whether its first run has finished."""


def _wait_for_finish(
    repository: Repository, run_id: str, timeout_s: float = DEMO_FINISH_TIMEOUT_S
) -> None:
    """Wait for the first demo run so the replay starts with a complete trace."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = repository.get_run(run_id)
        if run is None or run.status != "running":
            return
        time.sleep(_POLL_S)


def _recorded_run(
    repository: Repository, spec_name: str
) -> RunSummary | None:
    """Return the newest finished run of a demo spec, if one was recorded."""
    return next(
        (
            summary
            for summary in repository.list_run_summaries()
            if summary.spec_name == spec_name and summary.status != "running"
        ),
        None,
    )


def _replay_trace(request: Request, run_id: str) -> RedirectResponse:
    """Land on a recorded run's trace with event replay switched on."""
    return RedirectResponse(
        url=f"{request.url_for('run_trace', run_id=run_id)}?replay=1",
        status_code=303,
    )


def _demo_page(
    request: Request,
    settings: Settings,
    *,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """Render the demo library, with the bundled specs and copy state."""
    return render(
        request,
        "demos.html",
        {
            "demos": scenario_rows(settings),
            "pipeline": PIPELINE,
            "error": error,
        },
        status_code=status_code,
    )


def _not_found(request: Request, slug: str) -> HTMLResponse:
    """Render the console's 404 page for an unknown demo slug."""
    return render(
        request,
        "not_found.html",
        {"message": f"demo {slug!r} does not exist"},
        status_code=404,
    )