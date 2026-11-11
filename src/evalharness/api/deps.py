"""Shared request dependencies and server-side rendering for the console."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from fastapi import Request
from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates

from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository


def get_repository(request: Request) -> Repository:
    """Return the repository the application was built with."""
    return request.app.state.repository


def get_database(request: Request) -> Database:
    """Return the database the application opened for human labels.

    The console sets this when it is built with settings. An application built
    with an injected repository must also supply app.state.database before the
    calibration routes run; this dependency never reaches into the repository.
    """
    return request.app.state.database


def get_settings(request: Request) -> Settings:
    """Return the settings the application was built with."""
    return request.app.state.settings


def get_templates(request: Request) -> Jinja2Templates:
    """Return the template environment the application was built with."""
    return request.app.state.templates


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render one server-side template with the request in its context."""
    templates = get_templates(request)
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )


async def form_fields(request: Request) -> dict[str, str]:
    """Read a urlencoded form body without adding a form-parsing dependency.

    FastAPI's form parameters want python-multipart, which this project does
    not depend on; the console forms are plain urlencoded POSTs. Undecodable
    bytes are replaced rather than refused: a form a browser produced must
    never turn into a 500.
    """
    body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}
