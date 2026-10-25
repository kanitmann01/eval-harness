"""Shared fixtures: a workspace temp directory and a clean environment."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Iterator

import pytest

_ENV_KEYS = (
    "EVALH_DATABASE_URL",
    "EVALH_ARTIFACTS_DIR",
    "EVALH_HARNESS_SHA",
    "EVALH_MASTER_KEY",
)


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
    return home
