"""Route modules discovered by :func:`evalharness.api.app.create_app`.

Every module in this package exposes a `router`; adding a module is how a
later ticket adds a surface without editing the application factory.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
"""The package-level router is empty; discovery mounts the submodule routers."""
