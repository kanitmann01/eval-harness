"""The trace page: one case, every candidate, every check in one view."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from evalharness.api.deps import get_repository, render
from evalharness.api.trace_views import build_trace
from evalharness.api.views import run_verdict
from evalharness.read_model import load_run_read
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get(
    "/runs/{run_id}/trace",
    response_class=HTMLResponse,
    name="run_trace",
)
def run_trace(
    request: Request,
    run_id: str,
    replay: bool = False,
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """Render one run's per-case trace, optionally with the replay panel.

    The replay reads the events the run already stores; it never calls a
    candidate and never spends a token. Without JavaScript the panel stays
    hidden and the trace below is still complete.
    """
    read = load_run_read(repository, run_id, with_events=replay)
    if read is None:
        return render(
            request,
            "not_found.html",
            {"message": f"run {run_id} does not exist"},
            status_code=404,
        )
    return render(
        request,
        "trace.html",
        {
            "run": read.run,
            "trace": build_trace(read),
            "verdict": run_verdict(
                read.run, read.cells, read.checks, read.gates
            ),
            "gates": read.gates,
            "replay": replay,
            "replay_events": [
                event.model_dump(mode="json") for event in read.events
            ],
        },
    )
