"""Environment-derived process settings; never passed to an adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from sqlalchemy.engine import make_url

from evalharness import __version__


@dataclass(frozen=True, slots=True)
class Settings:
    """The process configuration the CLI reads once and keeps to itself."""

    home: Path
    database_url: str
    artifacts_dir: Path
    specs_dir: Path
    master_key: str | None = None
    master_key_file: str | None = None
    verify_url: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from EVALH_* variables with stable defaults."""
        source = os.environ if env is None else env
        home = Path(source.get("EVALH_HOME") or Path.home() / ".evalharness")
        database_url = source.get("EVALH_DATABASE_URL") or (
            "sqlite:///" + (home / "harness.db").as_posix()
        )
        artifacts_dir = Path(source.get("EVALH_ARTIFACTS_DIR") or home / "artifacts")
        specs_dir = Path(
            source.get("EVALH_SPECS_DIR") or _next_to_database(database_url, home)
        )
        return cls(
            home=home,
            database_url=database_url,
            artifacts_dir=artifacts_dir,
            specs_dir=specs_dir,
            master_key=source.get("EVALH_MASTER_KEY"),
            master_key_file=source.get("EVALH_MASTER_KEY_FILE"),
            verify_url=source.get("EVALH_VERIFY_URL"),
        )


def _next_to_database(database_url: str, home: Path) -> Path:
    """Return the default specs directory: ./specs beside a SQLite database."""
    parsed = make_url(database_url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (
        None,
        "",
        ":memory:",
    ):
        return Path(parsed.database).parent / "specs"
    return home / "specs"


def harness_revision(env: Mapping[str, str] | None = None) -> str:
    """Return the harness revision recorded on every run."""
    source = os.environ if env is None else env
    return source.get("EVALH_HARNESS_SHA") or f"evalharness/{__version__}"
