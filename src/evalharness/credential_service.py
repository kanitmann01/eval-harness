"""Credential operations shared by the command line and the console.

The CLI and the HTTP routes call these functions, so a key is added, tested,
enabled, disabled and deleted exactly one way. Every function takes a
Repository and, where the secret is involved, a Vault; no caller talks SQL.
"""

from __future__ import annotations

from typing import Iterable

from evalharness.adapters.verify.http_verifier import build as build_verifier
from evalharness.core.models import CredentialRecord, CredentialState
from evalharness.core.protocols import VerificationResult
from evalharness.core.redaction import Redactor, SecretRedactor
from evalharness.core.vault import Vault, VaultCredentialStore
from evalharness.errors import SpecError
from evalharness.storage.repository import Repository

UNKNOWN_CREDENTIAL_FIX = "list the stored credentials with 'evalh credentials list'"
"""The fix every unknown-credential error names, in the CLI and the console."""


def add_credential(
    repository: Repository,
    vault: Vault,
    name: str,
    provider: str,
    secret: str,
) -> CredentialRecord:
    """Encrypt one secret and store it; only metadata comes back."""
    return repository.add_credential(name, provider, vault.encrypt(secret))


def set_enabled(
    repository: Repository,
    name: str,
    *,
    enabled: bool,
) -> CredentialRecord:
    """Enable or disable one stored credential and return its metadata."""
    return repository.set_credential_disabled(name, not enabled)


def delete_credential(repository: Repository, name: str) -> None:
    """Delete one stored credential and forget its secret."""
    repository.delete_credential(name)


def credential_states(
    records: Iterable[CredentialRecord],
) -> list[CredentialState]:
    """Map stored metadata to the usability state spec validation reads.

    A credential counts as verified when a successful provider test exists;
    a later failure changes the display state but not that historical fact.
    """
    return [
        CredentialState(
            name=record.name,
            disabled=record.disabled,
            verified=record.last_ok_at is not None,
        )
        for record in records
    ]


async def verify_credential(
    repository: Repository,
    vault: Vault,
    name: str,
    verify_url: str,
    *,
    redactor: Redactor | None = None,
) -> tuple[CredentialRecord, VerificationResult]:
    """Make one provider request and record its redacted outcome.

    Returns the stored metadata and the provider's message with every known
    secret replaced by its [redacted:name] marker. The command line passes the
    same redactor a run uses; the console builds one from the tested secret.
    """
    store = VaultCredentialStore(vault, repository)
    handle = store.get(name)
    if handle is None:
        raise SpecError(
            f"credentials.{name}",
            name,
            "unknown credential",
            UNKNOWN_CREDENTIAL_FIX,
        )
    if not verify_url:
        raise SpecError(
            "EVALH_VERIFY_URL",
            "",
            "no verification endpoint is configured",
            "set EVALH_VERIFY_URL to the provider base URL or pass --verify-url",
        )
    verifier = build_verifier("verifier", {"base_url": verify_url})
    result = await verifier.verify(handle.provider, handle.secret)
    known = redactor or SecretRedactor({name: handle.secret})
    message = known.redact(result.message)
    record = repository.test_credential(
        name,
        error=None if result.ok else message,
    )
    return record, result.model_copy(update={"message": message})
