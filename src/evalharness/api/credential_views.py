"""Read models and form validation for the credentials console.

Every row is metadata plus a truncated SHA-256 fingerprint of the decrypted
secret; a secret, and the ciphertext that holds it, never reaches a response.
The four display states are verified, unverified, disabled and error, and the
spec editor treats only verified and error as selectable because both carry a
past successful test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from cryptography.fernet import InvalidToken

from evalharness.adapters.verify.http_verifier import (
    KNOWN_PROVIDERS,
    provider_choices,
)
from evalharness.core.models import CredentialRecord
from evalharness.core.vault import Vault
from evalharness.errors import SpecError
from evalharness.storage.repository import Repository

NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""A credential name is URL-safe and readable in a route."""

MAX_NAME_LENGTH = 200
"""The credentials table stores a name in a 200 character column."""

USABLE_STATES = frozenset({"verified", "error"})
"""Display states that carry a successful test and may be selected in a spec."""


@dataclass(frozen=True, slots=True)
class CredentialRow:
    """One stored credential as the page and the JSON API display it."""

    name: str
    provider: str
    created_at: datetime
    last_ok_at: datetime | None
    last_error: str | None
    disabled: bool
    fingerprint: str | None
    state: str

    @property
    def usable(self) -> bool:
        """Return whether the spec editor may offer this credential."""
        return self.state in USABLE_STATES


@dataclass(frozen=True, slots=True)
class CredentialChoice:
    """One credential as the spec editor's sidebar lists it."""

    name: str
    provider: str
    state: str
    usable: bool


@dataclass(frozen=True, slots=True)
class ProviderOption:
    """One provider choice, derived from the verifier's auth table."""

    name: str
    auth: str


@dataclass(frozen=True, slots=True)
class CredentialInput:
    """One validated add request; the secret stays in this object."""

    name: str
    provider: str
    secret: str


def state_of(record: CredentialRecord) -> str:
    """Return the display state for one stored credential.

    A failed test on a credential that passed before reads as error; one that
    never passed reads as unverified, which is the state the editor refuses.
    """
    if record.disabled:
        return "disabled"
    if record.last_ok_at is None:
        return "unverified"
    if record.last_error is not None:
        return "error"
    return "verified"


def provider_options() -> list[ProviderOption]:
    """Return the provider choices the verifier's auth table defines."""
    return [
        ProviderOption(name=name, auth=auth)
        for name, auth in provider_choices()
    ]


def credential_rows(
    repository: Repository,
    vault: Vault,
) -> list[CredentialRow]:
    """Return every stored credential with its non-reversible fingerprint."""
    rows: list[CredentialRow] = []
    for record in repository.list_credentials():
        stored = repository.get_credential(record.name)
        # The row was just listed, so its ciphertext is present.
        assert stored is not None
        rows.append(_row(record, _fingerprint(vault, stored.ciphertext)))
    return rows


def row_for(
    record: CredentialRecord,
    repository: Repository,
    vault: Vault,
) -> CredentialRow:
    """Build one display row for a credential that was just written or tested."""
    stored = repository.get_credential(record.name)
    # The record came from this database in the same request.
    assert stored is not None
    return _row(record, _fingerprint(vault, stored.ciphertext))


def credential_choices(records: list[CredentialRecord]) -> list[CredentialChoice]:
    """Return the spec editor's credential choices from stored metadata."""
    choices: list[CredentialChoice] = []
    for record in records:
        state = state_of(record)
        choices.append(
            CredentialChoice(
                name=record.name,
                provider=record.provider,
                state=state,
                usable=state in USABLE_STATES,
            )
        )
    return choices


def read_input(payload: Mapping[str, Any]) -> CredentialInput:
    """Validate one add request without ever echoing the secret.

    Every rejected value in an error is the name, the provider or a fixed
    placeholder, so a validation response cannot carry a submitted key.
    """
    name = payload.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise SpecError(
            "name",
            _shown(name),
            "a credential name must be a slug: letters, digits, dot, dash or "
            "underscore, starting with a letter or digit",
            "rename it, for example openai-prod",
        )
    if len(name) > MAX_NAME_LENGTH:
        raise SpecError(
            "name",
            name,
            f"the name is longer than {MAX_NAME_LENGTH} characters",
            "shorten the credential name",
        )
    provider = payload.get("provider")
    if not isinstance(provider, str) or provider not in KNOWN_PROVIDERS:
        raise SpecError(
            "provider",
            _shown(provider),
            "the provider is not one the verifier knows",
            f"use one of: {', '.join(KNOWN_PROVIDERS)}",
        )
    secret = payload.get("secret")
    if not isinstance(secret, str) or not secret:
        raise SpecError(
            "secret",
            "<empty>" if secret == "" else "<not text>",
            "the secret must be a non-empty string",
            "paste the provider key",
        )
    return CredentialInput(name=name, provider=provider, secret=secret)


def row_data(row: CredentialRow) -> dict[str, Any]:
    """Return the JSON shape of one credential row, never its secret."""
    return {
        "name": row.name,
        "provider": row.provider,
        "created_at": row.created_at.isoformat(),
        "last_ok_at": _isoformat(row.last_ok_at),
        "last_error": row.last_error,
        "disabled": row.disabled,
        "fingerprint": row.fingerprint,
        "state": row.state,
        "usable": row.usable,
    }


def error_data(error: SpecError) -> dict[str, Any]:
    """Return the JSON shape of one credential field error."""
    return {
        "field": error.field,
        "value": _shown(error.value),
        "reason": error.reason,
        "fix": error.fix,
    }


def unknown_credential(name: str) -> SpecError:
    """Return the actionable error for a name the vault does not store."""
    return SpecError(
        f"credentials.{name}",
        name,
        "unknown credential",
        "list the stored credentials with 'evalh credentials list'",
    )


def _row(record: CredentialRecord, fingerprint: str | None) -> CredentialRow:
    """Turn one stored record and its fingerprint into a display row."""
    return CredentialRow(
        name=record.name,
        provider=record.provider,
        created_at=record.created_at,
        last_ok_at=record.last_ok_at,
        last_error=record.last_error,
        disabled=record.disabled,
        fingerprint=fingerprint,
        state=state_of(record),
    )


def _fingerprint(vault: Vault, ciphertext: bytes) -> str | None:
    """Return the non-reversible secret prefix, or None under another key."""
    try:
        return vault.fingerprint(vault.decrypt(ciphertext))
    except InvalidToken:
        return None


def _shown(value: Any) -> str:
    """Render one rejected value safely; a secret never reaches this call."""
    if value is None:
        return "<missing>"
    if isinstance(value, str):
        return value
    return "<not text>"


def _isoformat(value: datetime | None) -> str | None:
    """Format a stored timestamp, or None when it was never recorded."""
    return value.isoformat() if value is not None else None
