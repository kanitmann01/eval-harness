"""The favicon: one real asset, served at the conventional browser path."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

# Resolved from this package, never from the working directory the server
# happened to start in.
FAVICON = Path(__file__).resolve().parents[1] / "static" / "favicon.svg"


@router.get("/favicon.ico", name="favicon", include_in_schema=False)
def favicon() -> FileResponse:
    """Serve the brand mark to browsers that request /favicon.ico directly."""
    return FileResponse(FAVICON, media_type="image/svg+xml")
