"""Server-rendered decision page and JSON report for one run."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from evalharness.api.decision_views import (
    decision_data,
    decision_page,
    load_decision,
)
from evalharness.api.deps import get_repository, render
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get(
    "/runs/{run_id}/decision",
    response_class=HTMLResponse,
    name="run_decision",
)
def run_decision(
    request: Request,
    run_id: str,
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """Render one run's ranked candidates and Pareto frontier."""
    snapshot = load_decision(repository, run_id)
    if snapshot is None:
        return render(
            request,
            "not_found.html",
            {"message": f"run {run_id} does not exist"},
            status_code=404,
        )
    return render(request, "decision.html", decision_page(snapshot))


@router.get("/api/runs/{run_id}/decision", name="api_run_decision")
def api_run_decision(
    run_id: str,
    repository: Repository = Depends(get_repository),
) -> dict[str, object]:
    """Return one run's ranked candidates and Pareto frontier as JSON."""
    snapshot = load_decision(repository, run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} does not exist")
    return decision_data(snapshot.report)
