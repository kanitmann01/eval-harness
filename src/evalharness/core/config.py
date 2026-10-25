"""Environment-derived process settings; never passed to an adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from evalharness import __version__


@dataclass(frozen=True, slots=True)
class Settings:
    """The process configuration the CLI reads once and keeps to itself."""

    home: Path
    database_url: str
    artifacts_dir: Path
    master_key: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from EVALH_* variables with stable defaults."""
        source = os.environ if env is None else env
        home = Path(source.get("EVALH_HOME") or Path.home() / ".evalharness")
        database_url = source.get("EVALH_DATABASE_URL") or (
            "sqlite:///" + (home / "harness.db").as_posix()
        )
        artifacts_dir = Path(source.get("EVALH_ARTIFACTS_DIR") or home / "artifacts")
        return cls(
            home=home,
            database_url=database_url,
            artifacts_dir=artifacts_dir,
            master_key=source.get("EVALH_MASTER_KEY"),
        )


def harness_revision(env: Mapping[str, str] | None = None) -> str:
    """Return the harness revision recorded on every run."""
    source = os.environ if env is None else env
    return source.get("EVALH_HARNESS_SHA") or f"evalharness/{__version__}"
