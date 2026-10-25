"""Application factory for the read-only console.

Routes are discovered, not imported by name: any module under `api.routes`
that exposes `router` is mounted, so later tickets add files rather than edit
this module.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from evalharness import __version__
from evalharness.api import routes
from evalharness.api.templating import install_filters
from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository

LOGGER = logging.getLogger("evalharness.api")

API_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = API_ROOT / "templates"
STATIC_DIR = API_ROOT / "static"


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
) -> FastAPI:
    """Build the console, opening a database unless a repository is supplied.

    Parameter:
        settings: process settings; read from the environment when omitted.
        repository: an already-open repository, which keeps tests disposable.
    """
    resolved = settings or Settings.from_env()
    database: Database | None = None
    if repository is None:
        database = Database(resolved.database_url)
        database.create_all()
        repository = Repository(database)
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
    return app


def _include_route_modules(app: FastAPI) -> None:
    """Import every module under api.routes and mount the router it exposes."""
    names = sorted(info.name for info in pkgutil.iter_modules(routes.__path__))
    for name in names:
        module = importlib.import_module(f"{routes.__name__}.{name}")
        app.include_router(module.router)


async def _log_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Log the method, path and status, never the query string or headers."""
    response = await call_next(request)
    LOGGER.info(
        "%s %s -> %s", request.method, request.url.path, response.status_code
    )
    return response
