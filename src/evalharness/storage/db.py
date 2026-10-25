"""Engine and session factory, portable between SQLite and PostgreSQL."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from evalharness.storage.models import Base


class Database:
    """Owns one engine and hands out short-lived sessions."""

    def __init__(self, url: str) -> None:
        parsed = make_url(url)
        is_sqlite = parsed.get_backend_name() == "sqlite"
        connect_args = {"check_same_thread": False} if is_sqlite else {}
        if is_sqlite and parsed.database and parsed.database != ":memory:":
            Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine: Engine = create_engine(url, connect_args=connect_args)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        """Create every harness table on startup; no migration framework yet."""
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session, committing on success and rolling back on error."""
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Release the engine's connections."""
        self.engine.dispose()
