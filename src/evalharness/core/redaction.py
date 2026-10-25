"""The redaction filter used by logs, events, artifacts and records."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Protocol


class Redactor(Protocol):
    """Replace secret-bearing strings before they surface anywhere."""

    def redact(self, text: str) -> str:
        """Redact one string."""
        ...

    def redact_data(self, value: Any) -> Any:
        """Redact strings inside nested lists, tuples and mappings."""
        ...


class NoOpRedactor:
    """The default redactor: no secrets are known, so nothing changes."""

    def redact(self, text: str) -> str:
        """Return the text unchanged."""
        return text

    def redact_data(self, value: Any) -> Any:
        """Return the value unchanged."""
        return value


class SecretRedactor:
    """Replace known secret values with [redacted:name], longest secret first."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._secrets = sorted(
            ((name, secret) for name, secret in secrets.items() if secret),
            key=lambda pair: len(pair[1]),
            reverse=True,
        )

    def redact(self, text: str) -> str:
        """Replace every known secret in the text."""
        for name, secret in self._secrets:
            text = text.replace(secret, f"[redacted:{name}]")
        return text

    def redact_data(self, value: Any) -> Any:
        """Redact every string in a nested structure."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {key: self.redact_data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact_data(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_data(item) for item in value)
        return value


class RedactionFilter(logging.Filter):
    """A logging filter that redacts every record before a handler writes it."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the message and its arguments, keeping the record."""
        if isinstance(record.msg, str):
            record.msg = self._redactor.redact(record.msg)
        if record.args:
            record.args = self._redactor.redact_data(record.args)
        return True
