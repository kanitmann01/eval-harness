"""The favicon route: a real asset at the conventional browser path.

Browsers ask for /favicon.ico by convention, and the console answers with the
same brand mark the header link carries, as a cacheable SVG file.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from evalharness.api.app import create_app
from evalharness.core.config import Settings

FAVICON = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "evalharness"
    / "api"
    / "static"
    / "favicon.svg"
)


async def _get(app, path: str) -> httpx.Response:
    """GET one path through the ASGI app without starting a server."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://console.test"
    ) as client:
        return await client.get(path)


async def test_the_favicon_is_served_as_svg(evalh_home: Path) -> None:
    app = create_app(Settings.from_env())
    try:
        response = await _get(app, "/favicon.ico")
    finally:
        app.state.database.dispose()

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.content == FAVICON.read_bytes()


def test_the_favicon_asset_is_the_brand_mark() -> None:
    text = FAVICON.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg")
    assert 'viewBox="0 0 32 32"' in text
    assert 'rx="7"' in text
    assert 'fill="#1f5e4b"' in text
    assert 'stroke="#ffffff"' in text
    assert 'stroke-width="3.5"' in text
