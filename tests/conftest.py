"""Shared fixtures: a workspace temp directory, a clean environment, a stub server."""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from tests.helpers import TEST_MASTER_KEY

_ENV_KEYS = (
    "EVALH_DATABASE_URL",
    "EVALH_ARTIFACTS_DIR",
    "EVALH_HARNESS_SHA",
    "EVALH_MASTER_KEY",
    "EVALH_MASTER_KEY_FILE",
    "EVALH_VERIFY_URL",
)


@dataclass
class VerifyStub:
    """A local HTTP server standing in for a provider verification endpoint."""

    base_url: str
    routes: dict[str, tuple[int, object]] = field(default_factory=dict)
    requests: list[dict[str, object]] = field(default_factory=list)

    def url(self, path: str) -> str:
        """Return the absolute URL for one stubbed path."""
        return self.base_url + path


@pytest.fixture
def workdir() -> Iterator[Path]:
    """Create a disposable directory under the workspace.

    pytest's tmp_path base is not writable in this sandbox, so tests use a
    workspace-local scratch directory instead.
    """
    base = Path.cwd() / ".test-work"
    base.mkdir(exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def evalh_home(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point EVALH_HOME at a fresh directory and strip other harness env."""
    home = workdir / "home"
    monkeypatch.setenv("EVALH_HOME", str(home))
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("EVALH_MASTER_KEY", TEST_MASTER_KEY)
    return home


@pytest.fixture
def verify_stub() -> Iterator[VerifyStub]:
    """Serve provider-shaped responses on a local port; never the internet."""
    routes: dict[str, tuple[int, object]] = {}
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(
                {"path": self.path, "headers": dict(self.headers.items())}
            )
            status, body = routes.get(self.path, (404, {"error": "unknown route"}))
            if body is None:
                self.close_connection = True
                return
            if isinstance(body, (dict, list)):
                payload = json.dumps(body).encode("utf-8")
                content_type = "application/json"
            else:
                payload = str(body).encode("utf-8")
                content_type = "text/plain"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stub = VerifyStub(
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        routes=routes,
        requests=requests,
    )
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
