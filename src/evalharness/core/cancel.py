"""Cooperative cancellation, checked between cells and never mid-cell."""

from __future__ import annotations

import threading


class CancellationToken:
    """A thread-safe cancel flag carried by one run invocation.

    The CLI wires SIGINT to :meth:`cancel`; `cancel_after` gives tests and
    scripted operators the same path without a signal, cancelling once that many
    cells have completed.
    """

    def __init__(self, cancel_after: int | None = None) -> None:
        self._lock = threading.Lock()
        self._cancel_after = cancel_after
        self._completed = 0
        self._cancelled = False
        self._reason = ""
        if cancel_after is not None and cancel_after <= 0:
            # A zero limit means "cancel before the first cell"; honour it now so
            # the worker loop never has to special-case it.
            self._cancelled = True
            self._reason = f"--cancel-after {cancel_after} reached"

    @property
    def cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        with self._lock:
            return self._cancelled

    @property
    def reason(self) -> str:
        """Why cancellation was requested, empty until it is."""
        with self._lock:
            return self._reason

    def cancel(self, reason: str) -> None:
        """Request cancellation; the first reason is kept."""
        with self._lock:
            if not self._cancelled:
                self._reason = reason
            self._cancelled = True

    def record_completion(self) -> None:
        """Count one finished cell, cancelling at the configured cell limit."""
        with self._lock:
            self._completed += 1
            reached = (
                self._cancel_after is not None
                and self._completed >= self._cancel_after
            )
            if not reached:
                return
            self._cancelled = True
            self._reason = (
                self._reason or f"--cancel-after {self._cancel_after} reached"
            )
