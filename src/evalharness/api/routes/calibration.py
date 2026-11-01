"""Judge calibration console: the page, the JSON report and label forms.

A label is written through the same repository and database the page reads, so
the page, the JSON route and the stored agreement always share one source of
truth. Unknown runs are a 404 page; a cell or metric that the run does not
store is a 400 that names the field, the value and the fix.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from evalharness.api.calibration_views import calibration_data
from evalharness.api.deps import get_database, get_repository, render
from evalharness.core.calibration import CalibrationReport, calibration_report
from evalharness.core.models import RunRecord, utc_now
from evalharness.errors import SpecError
from evalharness.storage.db import Database
from evalharness.storage.labels import HumanLabel, LabelStore
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get(
    "/runs/{run_id}/calibration", response_class=HTMLResponse, name="run_calibration"
)
def calibration_page(
    request: Request,
    run_id: str,
    repository: Repository = Depends(get_repository),
    database: Database = Depends(get_database),
) -> Response:
    """Render the agreement page for one run."""
    return _page(request, repository, database, run_id)


@router.get("/api/runs/{run_id}/calibration", name="api_run_calibration")
def calibration_json(
    run_id: str,
    repository: Repository = Depends(get_repository),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    """Return the calibration report as JSON, or a 404 for an unknown run."""
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"run {run_id} does not exist"
        )
    return calibration_data(_report(run, repository, database))


@router.post(
    "/runs/{run_id}/calibration/labels", name="calibration_label_save"
)
async def calibration_label_save(
    request: Request,
    run_id: str,
    repository: Repository = Depends(get_repository),
    database: Database = Depends(get_database),
) -> Response:
    """Save or update one human verdict and return to the calibration page."""
    if repository.get_run(run_id) is None:
        return _not_found(request, run_id)
    values = dict(await request.form())
    try:
        cell_id, metric = _check_target(values, run_id, repository)
        verdict = str(values.get("verdict", "")).strip()
        if verdict not in {"pass", "fail"}:
            raise SpecError(
                "verdict",
                verdict,
                "verdict must be pass or fail",
                "send verdict=pass or verdict=fail",
            )
        note = str(values.get("note", "")).strip()
        LabelStore(database).upsert(
            HumanLabel(
                run_id=run_id,
                cell_id=cell_id,
                metric=metric,
                passed=verdict == "pass",
                note=note,
                operator=_operator(),
                created_at=utc_now(),
            )
        )
    except SpecError as error:
        return _page(
            request, repository, database, run_id, error=error, status_code=400
        )
    return RedirectResponse(url=f"/runs/{run_id}/calibration", status_code=303)


@router.post(
    "/runs/{run_id}/calibration/labels/clear", name="calibration_label_clear"
)
async def calibration_label_clear(
    request: Request,
    run_id: str,
    repository: Repository = Depends(get_repository),
    database: Database = Depends(get_database),
) -> Response:
    """Delete one human verdict; clearing an absent label is still a success."""
    if repository.get_run(run_id) is None:
        return _not_found(request, run_id)
    values = dict(await request.form())
    try:
        cell_id, metric = _check_target(values, run_id, repository)
    except SpecError as error:
        return _page(
            request, repository, database, run_id, error=error, status_code=400
        )
    LabelStore(database).delete(run_id, cell_id, metric)
    return RedirectResponse(url=f"/runs/{run_id}/calibration", status_code=303)


def _page(
    request: Request,
    repository: Repository,
    database: Database,
    run_id: str,
    error: SpecError | None = None,
    status_code: int = 200,
) -> Response:
    """Render the calibration page, or its not-found page for a missing run."""
    run = repository.get_run(run_id)
    if run is None:
        return _not_found(request, run_id)
    return render(
        request,
        "calibration.html",
        {
            "run": run,
            "report": _report(run, repository, database),
            "error": error,
        },
        status_code=status_code,
    )


def _report(
    run: RunRecord, repository: Repository, database: Database
) -> CalibrationReport:
    """Build one run's calibration report from stored rows."""
    return calibration_report(
        run,
        repository.list_cells(run.run_id),
        repository.list_checks_for_run(run.run_id),
        LabelStore(database).list_for_run(run.run_id),
    )


def _check_target(
    values: dict[str, Any], run_id: str, repository: Repository
) -> tuple[str, str]:
    """Return the cell and metric a form names, or raise an actionable error."""
    cell_id = str(values.get("cell_id", "")).strip()
    metric = str(values.get("metric", "")).strip()
    if repository.get_cell(run_id, cell_id) is None:
        raise SpecError(
            "cell_id",
            cell_id,
            f"cell {cell_id!r} is not part of run {run_id}",
            "choose a cell id from the calibration review table",
        )
    checks = repository.list_checks(run_id, cell_id)
    if not any(check.metric == metric for check in checks):
        raise SpecError(
            "metric",
            metric,
            f"metric {metric!r} has no stored check for cell {cell_id}",
            "choose a metric from the calibration review table",
        )
    return cell_id, metric


def _not_found(request: Request, run_id: str) -> Response:
    """Render the console's not-found page for a missing run."""
    return render(
        request,
        "not_found.html",
        {"message": f"run {run_id} does not exist"},
        status_code=404,
    )


def _operator() -> str:
    """Return the stripped operator name, or the honest placeholder."""
    return os.environ.get("EVALH_OPERATOR", "").strip() or "not recorded"
