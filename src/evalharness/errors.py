"""Typed errors that carry an actionable field name and fix."""

from __future__ import annotations

from typing import Any


class HarnessError(Exception):
    """Base class for errors raised by the harness itself."""


class SpecError(HarnessError):
    """A spec is invalid; the message names the field, the value and the fix."""

    def __init__(self, field: str, value: Any, reason: str, fix: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        self.fix = fix
        super().__init__(f"{field}={value!r}: {reason}. Fix: {fix}")

    def __str__(self) -> str:
        return f"{self.field}={self.value!r}: {self.reason}. Fix: {self.fix}"


class RegistryError(HarnessError):
    """A dotted adapter path cannot be resolved to a factory."""

    def __init__(self, path: str, reason: str, fix: str) -> None:
        self.path = path
        self.reason = reason
        self.fix = fix
        super().__init__(f"{path!r}: {reason}. Fix: {fix}")

    def __str__(self) -> str:
        return f"{self.path!r}: {self.reason}. Fix: {self.fix}"


class SchemaMigrationError(HarnessError):
    """A stored database is too old to migrate without inventing data."""

    def __init__(self, field: str, reason: str, fix: str) -> None:
        self.field = field
        self.reason = reason
        self.fix = fix
        super().__init__(f"{field}: {reason}. Fix: {fix}")

    def __str__(self) -> str:
        return f"{self.field}: {self.reason}. Fix: {self.fix}"


class TransportError(HarnessError):
    """A candidate transport failed in a way that is safe to retry."""


class BudgetExceeded(HarnessError):
    """The run stopped spending at the configured budget."""
