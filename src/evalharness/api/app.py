"""Application factory for the console.

Routes are discovered, not imported by name: any module under `api.routes`
that exposes `router` is mounted, so later tickets add files rather than edit
this module. Errors are handled here too: pages render templates, the `/api`
surface keeps JSON, and an unhandled error is logged rather than rendered.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from evalharness import __version__
from evalharness.api import routes
from evalharness.api.deps import render
from evalharness.api.templating import install_filters
from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository

LOGGER = logging.getLogger("evalharness.api")

API_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = API_ROOT / "templates"
STATIC_DIR = API_ROOT / "static"

_HTTP_PHRASES = {status.value: status.phrase for status in HTTPStatus}
"""Reason phrases for the status codes an error page can carry."""


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
) -> FastAPI:
    """Build the console, opening a database unless a repository is supplied.

    Parameter:
        settings: process settings; read from the environment when omitted.
        repository: an already-open repository, which keeps tests disposable;
            a repository that exposes a database also gets the schema created.
    """
    resolved = settings or Settings.from_env()
    database: Database | None
    if repository is None:
        database = Database(resolved.database_url)
        repository = Repository(database)
        database.create_all()
    else:
        database = getattr(repository, "database", None)
        if database is not None:
            database.create_all()
    app = FastAPI(title="evalharness console", version=__version__)
    app.state.settings = resolved
    app.state.repository = repository
    app.state.database = database
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    install_filters(templates)
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.middleware("http")(_log_request)
    _include_route_modules(app)
    _include_error_handlers(app)
    return app


def _include_route_modules(app: FastAPI) -> None:
    """Import every module under api.routes and mount the router it exposes."""
    names = sorted(info.name for info in pkgutil.iter_modules(routes.__path__))
    for name in names:
        module = importlib.import_module(f"{routes.__name__}.{name}")
        app.include_router(module.router)


def _include_error_handlers(app: FastAPI) -> None:
    """Render HTML errors for pages and keep JSON for the API surface."""
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)


def _api_surface(request: Request) -> bool:
    """Return True when the request belongs to the machine-readable API."""
    path = request.url.path
    return path == "/api" or path.startswith("/api/")


def _error_message(request: Request, exc: StarletteHTTPException) -> str:
    """Name the error for a page, preferring the app's own actionable detail."""
    phrase = _HTTP_PHRASES.get(exc.status_code, "Error")
    if isinstance(exc.detail, str) and exc.detail != phrase:
        return exc.detail
    if exc.status_code == 404:
        return f"The path {request.url.path} does not exist."
    return f"{request.method} {request.url.path} could not be served."


def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    """Render a page for a browser, or a JSON body for the API surface."""
    if _api_surface(request):
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )
    template = "not_found.html" if exc.status_code == 404 else "error.html"
    context = {
        "status_code": exc.status_code,
        "status_phrase": _HTTP_PHRASES.get(exc.status_code, "Error"),
        "message": _error_message(request, exc),
    }
    return render(request, template, context, status_code=exc.status_code)


def _unhandled_error(request: Request, exc: Exception) -> Response:
    """Log the traceback, then answer with a body that never echoes it."""
    LOGGER.error(
        "unhandled error on %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    if _api_surface(request):
        return JSONResponse({"detail": "internal server error"}, status_code=500)
    return render(
        request,
        "error.html",
        {
            "status_code": 500,
            "status_phrase": "Internal Server Error",
            "message": (
                "The console logged the unexpected error. Reload the page; "
                "if it keeps failing, check the server log."
            ),
        },
        status_code=500,
    )


async def _log_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Log the method, path and status, never the query string or headers."""
    response = await call_next(request)
    LOGGER.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response
