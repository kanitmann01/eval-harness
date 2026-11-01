"""Server-rendered provenance routes: run receipt and two-run spec diff."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from evalharness.api.deps import get_repository, render
from evalharness.api.provenance_views import (
    ProvenanceError,
    diff_data,
    receipt_data,
)
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get(
    "/runs/{run_id}/receipt",
    response_class=HTMLResponse,
    name="run_receipt",
)
def run_receipt(
    request: Request,
    run_id: str,
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """Render the receipt for one run, or the not-found page."""
    try:
        receipt = receipt_data(repository, run_id)
    except ProvenanceError as exc:
        return render(
            request,
            "not_found.html",
            {"message": str(exc)},
            status_code=404,
        )
    return render(request, "receipt.html", {"receipt": receipt})


@router.get(
    "/api/runs/{run_id}/receipt",
    name="api_run_receipt",
)
def api_run_receipt(
    run_id: str,
    repository: Repository = Depends(get_repository),
) -> dict:
    """Return the same receipt data the HTML page renders as JSON."""
    try:
        return receipt_data(repository, run_id)
    except ProvenanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/runs/{run_id}/diff",
    response_class=HTMLResponse,
    name="run_diff",
)
def run_diff(
    request: Request,
    run_id: str,
    against: str = "",
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """Render the spec diff and judge drift against a baseline run."""
    try:
        context = diff_data(repository, run_id, against)
    except ProvenanceError as exc:
        return render(
            request,
            "not_found.html",
            {"message": str(exc)},
            status_code=404,
        )
    return render(request, "spec_diff.html", context)
