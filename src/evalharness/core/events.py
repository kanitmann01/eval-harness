"""Append-only run events with monotonic sequence numbers per run."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, Field

from evalharness.core.models import utc_now
from evalharness.core.redaction import NoOpRedactor, Redactor


class Event(BaseModel):
    """One state change in a run."""

    run_id: str
    seq: int
    ts: datetime = Field(default_factory=utc_now)
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class EventEmitter:
    """Assign sequence numbers, append events and fan them out in-process."""

    def __init__(
        self,
        run_id: str,
        *,
        append: Callable[[Event], None] | None = None,
        redactor: Redactor | None = None,
        start_seq: int = 0,
    ) -> None:
        self._run_id = run_id
        self._append = append
        self._redactor = redactor or NoOpRedactor()
        self._seq = start_seq
        self._listeners: list[Callable[[Event], None]] = []

    @property
    def seq(self) -> int:
        """The last emitted sequence number."""
        return self._seq

    def subscribe(self, listener: Callable[[Event], None]) -> None:
        """Register a listener for later events, used by the SSE surface."""
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[Event], None]) -> None:
        """Remove a listener if it is registered."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, kind: str, payload: dict[str, Any] | None = None) -> Event:
        """Append and publish one event with the next sequence number."""
        self._seq += 1
        event = Event(
            run_id=self._run_id,
            seq=self._seq,
            kind=kind,
            payload=self._redactor.redact_data(dict(payload or {})),
        )
        if self._append is not None:
            self._append(event)
        for listener in list(self._listeners):
            listener(event)
        return event
