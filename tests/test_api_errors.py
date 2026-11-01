"""Error responses: HTML for pages, JSON for the API, tracebacks only in logs.

The 500 path needs a route that raises, so the test app grows two of them; the
console's own modules never carry a debug route.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from evalharness.api.app import create_app
from evalharness.core.config import Settings

CANARY = "SECRET-CANARY-error-page-must-never-render-this"
NOT_A_PAGE = "/no-such-page"


@pytest.fixture
def client(evalh_home: Path) -> Iterator[TestClient]:
    """A console plus routes that raise, added only to this test application."""
    application: FastAPI = create_app(Settings.from_env())

    @application.get("/boom")
    def boom() -> None:
        raise RuntimeError(CANARY)

    @application.get("/api/boom")
    def api_boom() -> None:
        raise RuntimeError(CANARY)

    @application.get("/missing-widget")
    def missing_widget() -> None:
        raise HTTPException(status_code=404, detail="widget 7 does not exist")

    try:
        with TestClient(application, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        application.state.database.dispose()


def test_an_unknown_page_renders_the_not_found_template(
    client: TestClient,
) -> None:
    response = client.get(NOT_A_PAGE)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    text = response.text
    assert "That resource does not exist" in text
    assert f"The path {NOT_A_PAGE} does not exist." in text
    assert "Back to all runs" in text


def test_an_unknown_api_path_stays_json(client: TestClient) -> None:
    for path in ("/api/no-such-endpoint", "/api"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["detail"] == "Not Found"


def test_a_page_http_error_keeps_its_actionable_detail(
    client: TestClient,
) -> None:
    response = client.get("/missing-widget")
    assert response.status_code == 404
    assert "widget 7 does not exist" in response.text


def test_a_disallowed_method_renders_the_error_page(client: TestClient) -> None:
    response = client.post("/")
    assert response.status_code == 405
    assert response.headers["content-type"].startswith("text/html")
    text = response.text
    assert "Method Not Allowed" in text
    assert "405" in text
    # Standalone: no base.html chrome, so a broken template cannot cascade.
    assert "site-nav" not in text
    assert "single user" not in text


def test_a_raising_page_renders_a_generic_500_and_logs_the_traceback(
    client: TestClient,
) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("evalharness.api")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    try:
        response = client.get("/boom")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Internal Server Error" in body
    assert CANARY not in body
    assert "RuntimeError" not in body
    assert "Traceback" not in body
    assert "site-nav" not in body

    logged = stream.getvalue()
    assert "ERROR evalharness.api:" in logged
    assert "unhandled error on GET /boom" in logged
    assert "RuntimeError" in logged
    assert CANARY in logged


def test_a_raising_api_route_returns_json_without_the_exception(
    client: TestClient,
) -> None:
    response = client.get("/api/boom")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "internal server error"}
    assert CANARY not in response.text
