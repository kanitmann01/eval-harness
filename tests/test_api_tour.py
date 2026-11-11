"""The first-visit tour is markup plus a client preference, never server state."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from evalharness.api.app import create_app


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console.test"
    )


@pytest.fixture
def tour_app(evalh_home: Path) -> Iterator[Any]:
    del evalh_home
    yield create_app()


async def test_every_page_ships_the_tour_hidden_in_the_markup(tour_app: Any) -> None:
    async with _client(tour_app) as client:
        response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'id="tour"' in body
    assert "data-tour-root hidden" in body
    assert "Quick tour" in body
    assert "1 / 5" in body
    assert body.count('class="tour-step"') == 5
    for target in (
        "[data-tour='nav-runs']",
        "[data-tour='nav-specs']",
        "[data-tour='nav-demos']",
        "[data-tour='new-comparison']",
        "[data-tour='nav-credentials']",
    ):
        assert target in body
    assert 'id="tour-skip"' in body
    assert 'id="tour-back"' in body
    assert 'id="tour-next"' in body
    assert "/static/tour.js" in body
    assert " defer" in body


async def test_the_tour_targets_exist_on_the_page_that_ships_it(
    tour_app: Any,
) -> None:
    async with _client(tour_app) as client:
        response = await client.get("/")
    body = response.text
    for hook in (
        'data-tour="nav-runs"',
        'data-tour="nav-specs"',
        'data-tour="nav-demos"',
        'data-tour="new-comparison"',
        'data-tour="nav-credentials"',
    ):
        assert hook in body


async def test_demos_are_the_current_section(tour_app: Any) -> None:
    async with _client(tour_app) as client:
        response = await client.get("/demos")
    body = response.text
    assert '/demos"' in body
    assert 'aria-current="page"' in body
