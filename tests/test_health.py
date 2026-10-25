"""Health probes and the serve bootstrap, driven at the HTTP and CLI seams."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from evalharness import __version__
from evalharness.api.app import create_app
from evalharness.cli import main
from evalharness.core.config import Settings
from evalharness.core.vault import Vault
from tests.helpers import TEST_MASTER_KEY

CANARY = "SECRET-CANARY-health-must-not-leak"


class DeadRepository:
    """A repository whose ping fails as an unreachable database would."""

    def ping(self) -> None:
        """Raise the SQLAlchemy error a down database produces."""
        raise OperationalError(
            "SELECT 1", {}, Exception("connection refused: not a real DSN")
        )


async def _get(app, path: str) -> httpx.Response:
    """GET one path through the ASGI app without starting a server."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.get(path)


async def test_healthz_reports_ok_and_never_leaks_the_key_or_a_credential(
    evalh_home: Path,
) -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    app.state.repository.add_credential(
        "canary", "none", Vault.from_text(TEST_MASTER_KEY).encrypt(CANARY)
    )
    try:
        responses = [await _get(app, "/healthz"), await _get(app, "/api/health")]
    finally:
        app.state.database.dispose()
    for response in responses:
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__
        assert body["database"] == {"ok": True, "reason": None}
        assert body["artifacts"]["ok"] is True
        assert body["artifacts"]["path"] == str(settings.artifacts_dir)
        assert body["vault"] == {"configured": True}
        assert body["reason"] is None
        assert TEST_MASTER_KEY not in response.text
        assert CANARY not in response.text
        assert "canary" not in response.text


async def test_healthz_returns_503_when_the_database_is_unreachable(
    evalh_home: Path,
) -> None:
    settings = Settings.from_env()
    app = create_app(
        settings=settings, repository=DeadRepository()  # type: ignore[arg-type]
    )
    responses = [await _get(app, "/healthz"), await _get(app, "/api/health")]
    for response in responses:
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["database"]["ok"] is False
        assert "database is unreachable" in body["database"]["reason"]
        assert "database" in body["reason"]
        assert "connection refused" not in response.text


async def test_healthz_degrades_when_the_artifacts_directory_is_unwritable(
    evalh_home: Path, workdir: Path
) -> None:
    settings = Settings.from_env()
    blocked = workdir / "artifacts-is-a-file"
    blocked.write_text("not a directory", encoding="utf-8")
    app = create_app(settings=replace(settings, artifacts_dir=blocked))
    try:
        response = await _get(app, "/healthz")
    finally:
        app.state.database.dispose()
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["artifacts"]["ok"] is False
    assert "not writable" in body["artifacts"]["reason"]
    assert "artifacts" in body["reason"]


async def test_healthz_degrades_without_a_master_key(evalh_home: Path) -> None:
    settings = replace(Settings.from_env(), master_key=None, master_key_file=None)
    app = create_app(settings)
    try:
        response = await _get(app, "/healthz")
    finally:
        app.state.database.dispose()
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["vault"] == {"configured": False}
    assert "no master key is configured" in body["reason"]
    assert TEST_MASTER_KEY not in response.text


def test_serve_bootstraps_the_master_key_once_then_serves(
    evalh_home: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("EVALH_MASTER_KEY", raising=False)
    calls: list[tuple[object, str, int]] = []

    def fake_run(app: object, *, host: str, port: int, access_log: bool) -> None:
        calls.append((app, host, port))
        assert access_log is False

    monkeypatch.setattr("evalharness.cli.uvicorn.run", fake_run)
    database_path = workdir / "bootstrap" / "harness.db"
    args = [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8124",
        "--db",
        f"sqlite:///{database_path.as_posix()}",
    ]

    assert main(args) == 2
    first = capsys.readouterr()
    assert calls == []
    assert "EVALH_MASTER_KEY=" in first.err
    key = first.err.split("EVALH_MASTER_KEY=", 1)[1].splitlines()[0].strip()
    assert len(key) == 44
    assert not database_path.exists()

    monkeypatch.setenv("EVALH_MASTER_KEY", key)
    assert main(args) == 0
    second = capsys.readouterr()
    assert "EVALH_MASTER_KEY=" not in second.err
    assert len(calls) == 1
    app, host, port = calls[0]
    assert host == "127.0.0.1"
    assert port == 8124
    assert getattr(app, "state").settings.database_url == (
        f"sqlite:///{database_path.as_posix()}"
    )
    # Newer FastAPI keeps included routers nested, so read the public OpenAPI
    # paths rather than assuming a flat app.routes list.
    assert "/healthz" in app.openapi()["paths"]


def test_serve_reports_an_unopenable_database(
    evalh_home: Path,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    not_a_database = workdir / "database-is-a-directory"
    not_a_database.mkdir()
    code = main(["serve", "--db", f"sqlite:///{not_a_database.as_posix()}"])
    assert code == 2
    err = capsys.readouterr().err
    assert "serve error: the database could not be opened" in err
    assert "EVALH_DATABASE_URL" in err

def test_serve_reports_an_unusable_home(
    evalh_home: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocked = workdir / "home-is-a-file"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("EVALH_HOME", str(blocked))
    code = main(["serve", "--db", "sqlite:///:memory:"])
    assert code == 2
    err = capsys.readouterr().err
    assert "EVALH_HOME" in err
    assert "writable directory" in err
