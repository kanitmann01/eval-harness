"""Shared request dependencies and server-side rendering for the console."""

from __future__ import annotations

from typing import Any

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
