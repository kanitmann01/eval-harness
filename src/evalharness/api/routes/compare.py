"""Compare two runs or two candidates side by side, as a page and as JSON."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from evalharness.api.compare_views import (
    CompareError,
    comparison_data,
    comparison_page,
    load_comparison,
)
from evalharness.api.deps import get_repository, render
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get("/compare", response_class=HTMLResponse, name="compare")
def compare_page(
    request: Request,
    a: str = "",
    b: str = "",
    run: str = "",
    candidate_a: str = "",
    candidate_b: str = "",
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """Render the comparison, or explain what a comparison still needs."""
    try:
        context = comparison_page(
            repository,
            a=a,
            b=b,
            run=run,
            candidate_a=candidate_a,
            candidate_b=candidate_b,
        )
    except CompareError as exc:
        return render(
            request, "not_found.html", {"message": str(exc)}, status_code=404
        )
    return render(request, "compare.html", context)


@router.get("/api/compare", name="api_compare")
def compare_api(
    a: str = "",
    b: str = "",
    run: str = "",
    candidate_a: str = "",
    candidate_b: str = "",
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    """Return the comparison as JSON, or 404 with the actionable fix."""
    try:
        comparison = load_comparison(
            repository,
            a=a,
            b=b,
            run=run,
            candidate_a=candidate_a,
            candidate_b=candidate_b,
        )
    except CompareError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if comparison is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "a comparison needs two run ids; add ?a=<run id>&b=<run id>, "
                "or ?run=<run id>&candidate_a=<name>&candidate_b=<name>"
            ),
        )
    return comparison_data(comparison)
