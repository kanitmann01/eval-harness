"""Launch and cancel: the page, the JSON API and the cancel action.

This module is discovered like every other route module. It owns the launch
page, the write routes and the cancel action; the read-only run pages stay
where they are, and the cancel control reaches them through the shared
navigation partial instead of a second copy of the detail page.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from evalharness.api.deps import get_settings, render
from evalharness.api.launch import (
    LaunchError,
    get_launches,
    parse_budget_override,
    parse_budget_override_text,
    resolve_spec,
    shutdown_launches,
    start_launch,
)
from evalharness.api.launch_views import launch_page_context
from evalharness.core.config import Settings

router = APIRouter()


def _shutdown_launches() -> None:
    """Cancel and join a launch still running when the application stops."""
    shutdown_launches()


router.add_event_handler("shutdown", _shutdown_launches)


@router.get("/launch", response_class=HTMLResponse, name="launch_page")
def launch_page(
    request: Request,
    spec: str = "",
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Render the launch page with the spec picker and its estimate."""
    return render(
        request,
        "launch.html",
        launch_page_context(
            settings=settings,
            requested=spec,
            running_run_id=get_launches(request).active_run_id(),
        ),
    )


@router.post("/launch", response_class=HTMLResponse, name="launch_submit")
async def launch_submit(request: Request) -> HTMLResponse:
    """Launch the spec chosen in the form, then land on its run page."""
    fields = await _form_fields(request)
    spec_text = fields.get("spec", "")
    budget_text = fields.get("budget_override_usd", "")
    settings = get_settings(request)
    try:
        path = resolve_spec(spec_text, settings=settings)
        override = parse_budget_override_text(budget_text)
        run_id = await run_in_threadpool(
            start_launch, request, path, budget_override=override
        )
    except LaunchError as exc:
        return render(
            request,
            "launch.html",
            launch_page_context(
                settings=settings,
                requested=spec_text,
                budget_override=budget_text,
                error=str(exc),
                running_run_id=get_launches(request).active_run_id(),
            ),
            status_code=exc.status_code,
        )
    return RedirectResponse(
        url=str(request.url_for("run_detail", run_id=run_id)),
        status_code=303,
    )


@router.post("/api/runs", name="api_launch", status_code=202)
def api_launch(
    request: Request,
    payload: dict[str, Any] = Body(...),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Launch a saved spec and return the run id with its page URL."""
    try:
        path = resolve_spec(str(payload.get("spec") or ""), settings=settings)
        override = parse_budget_override(payload.get("budget_override_usd"))
        run_id = start_launch(request, path, budget_override=override)
    except LaunchError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
    return JSONResponse(
        {"run_id": run_id, "url": f"/runs/{run_id}"}, status_code=202
    )


@router.post("/api/runs/{run_id}/cancel", name="api_cancel_run")
def api_cancel_run(run_id: str, request: Request) -> JSONResponse:
    """Set the cancel token of the run this container is driving."""
    registry = get_launches(request)
    if not registry.cancel(run_id):
        return JSONResponse(
            {"detail": _cancel_refusal(run_id, registry.active_run_id())},
            status_code=409,
        )
    return JSONResponse({"run_id": run_id, "cancelling": True})


@router.post("/runs/{run_id}/cancel", name="cancel_run_page")
def cancel_run_page(run_id: str, request: Request) -> Response:
    """Cancel from the run page, or explain why this console cannot."""
    registry = get_launches(request)
    if not registry.cancel(run_id):
        return render(
            request,
            "cancel_refused.html",
            {
                "run_id": run_id,
                "active_run_id": registry.active_run_id(),
            },
            status_code=409,
        )
    return RedirectResponse(
        url=str(request.url_for("run_detail", run_id=run_id)),
        status_code=303,
    )


async def _form_fields(request: Request) -> dict[str, str]:
    """Read a urlencoded form body without adding a form-parsing dependency.

    FastAPI's form parameters want python-multipart, which this project does
    not depend on; the launch form is a plain urlencoded POST.
    """
    body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def _cancel_refusal(run_id: str, active_run_id: str | None) -> str:
    """Explain why this run cannot be cancelled here."""
    if active_run_id is None:
        return f"run {run_id} is not running in this console; no run is in flight"
    return (
        f"run {run_id} is not running in this console; run {active_run_id} is "
        f"the one in flight, cancel it with "
        f"POST /api/runs/{active_run_id}/cancel"
    )
