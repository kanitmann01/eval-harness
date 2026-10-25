"""Master key bootstrap and at-rest encryption for the credential vault."""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from evalharness.core.config import Settings
from evalharness.core.context import CredentialHandle
from evalharness.core.models import CredentialRecord, EncryptedCredential
from evalharness.errors import SpecError

MASTER_KEY_BYTES = 32
"""The decoded key length Fernet expects: 32 bytes, 44 urlsafe base64 chars."""

MASTER_KEY_SHAPE = "44 urlsafe base64 characters that decode to 32 bytes"
"""The expected master key shape, named by every validation error."""

EVALH_MASTER_KEY = "EVALH_MASTER_KEY"
"""The environment variable that holds the master key."""

EVALH_MASTER_KEY_FILE = "EVALH_MASTER_KEY_FILE"
"""The environment variable that names a file holding the master key."""


class MasterKeyError(SpecError):
    """The vault cannot open; carries a generated key when none was configured."""

    def __init__(
        self,
        field: str,
        value: object,
        reason: str,
        fix: str,
        *,
        generated_key: str | None = None,
    ) -> None:
        super().__init__(field, value, reason, fix)
        self.generated_key = generated_key


class CredentialReader(Protocol):
    """The storage seam the vault reads encrypted credentials through."""

    def get_credential(self, name: str) -> EncryptedCredential | None:
        """Return one stored credential with its ciphertext, or None."""
        ...

    def list_credentials(self) -> list[CredentialRecord]:
        """Return every stored credential's metadata, never its secret."""
        ...


def generate_master_key() -> str:
    """Return a fresh Fernet key as urlsafe base64 text."""
    return Fernet.generate_key().decode("ascii")


def decode_master_key(text: str, variable: str = EVALH_MASTER_KEY) -> bytes:
    """Decode a master key, naming the variable and shape when it is malformed."""
    try:
        decoded = base64.b64decode(text.strip(), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MasterKeyError(
            variable,
            "<redacted>",
            f"the master key is not valid urlsafe base64; expected {MASTER_KEY_SHAPE}",
            f"set {variable} to an unmodified key printed by the first run",
        ) from exc
    if len(decoded) != MASTER_KEY_BYTES:
        raise MasterKeyError(
            variable,
            "<redacted>",
            f"the master key decodes to {len(decoded)} bytes; expected "
            f"{MASTER_KEY_BYTES} ({MASTER_KEY_SHAPE})",
            f"set {variable} to an unmodified key printed by the first run",
        )
    return decoded


class Vault:
    """Encrypt and decrypt credential secrets with the process master key."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    @classmethod
    def from_text(cls, text: str, variable: str = EVALH_MASTER_KEY) -> Vault:
        """Build a vault from urlsafe base64 key text."""
        return cls(decode_master_key(text, variable))

    def encrypt(self, secret: str) -> bytes:
        """Encrypt one secret into Fernet ciphertext."""
        return self._fernet.encrypt(secret.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt one ciphertext back into its secret."""
        return self._fernet.decrypt(ciphertext).decode("utf-8")

    def fingerprint(self, secret: str) -> str:
        """Return a stable, non-reversible identifier for a secret."""
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        return f"sha256:{digest[:16]}"


def open_vault(settings: Settings) -> Vault:
    """Open the vault from the environment, a key file, or refuse to boot.

    A missing key is not generated silently: the generated key is returned on
    the raised error so the CLI can print it exactly once and exit 2.
    """
    if settings.master_key:
        return Vault.from_text(settings.master_key, EVALH_MASTER_KEY)
    if settings.master_key_file:
        try:
            text = Path(settings.master_key_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise MasterKeyError(
                EVALH_MASTER_KEY_FILE,
                settings.master_key_file,
                f"the master key file could not be read: {exc}",
                "point EVALH_MASTER_KEY_FILE at a readable file holding the key",
            ) from exc
        return Vault.from_text(text, EVALH_MASTER_KEY_FILE)
    generated = generate_master_key()
    raise MasterKeyError(
        EVALH_MASTER_KEY,
        None,
        "no master key is configured, so the credential vault cannot open",
        "store the generated key in the environment and re-run this command",
        generated_key=generated,
    )


class VaultCredentialStore:
    """Resolve credential handles from the credentials table, decrypting lazily."""

    def __init__(self, vault: Vault, repository: CredentialReader) -> None:
        self._vault = vault
        self._repository = repository

    def get(self, name: str) -> CredentialHandle | None:
        """Return the decrypted handle for a name, or None when it is unknown."""
        stored = self._repository.get_credential(name)
        if stored is None:
            return None
        try:
            secret = self._vault.decrypt(stored.ciphertext)
        except InvalidToken as exc:
            raise SpecError(
                f"credentials.{name}",
                name,
                "the stored ciphertext cannot be decrypted with the current "
                "master key",
                "delete the credential and add it again with the current key",
            ) from exc
        return CredentialHandle(
            name=stored.name,
            provider=stored.provider,
            secret=secret,
        )

    def names(self) -> list[str]:
        """Return the stored credential names in sorted order."""
        return [record.name for record in self._repository.list_credentials()]
