"""RunContext, credential handles and the credential store protocol."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from pydantic import BaseModel, Field

from evalharness.core.events import EventEmitter
from evalharness.core.models import ContextChunk, CredentialSpec
from evalharness.core.redaction import NoOpRedactor, Redactor
from evalharness.core.repo_snapshot import RepoSnapshot


class CredentialHandle(BaseModel):
    """A resolved credential: the secret stays out of reprs and dumps."""

    name: str
    provider: str = "none"
    secret: str = Field(default="", exclude=True, repr=False)


class CredentialStore(Protocol):
    """Look up logical credential names without exposing them to the spec."""

    def get(self, name: str) -> CredentialHandle | None:
        """Return the handle for a name, or None when it is unknown."""
        ...

    def names(self) -> list[str]:
        """Return the known credential names."""
        ...


class InMemoryCredentialStore:
    """A credential store held in memory; tests use it directly."""

    def __init__(self, handles: Mapping[str, CredentialHandle] | None = None) -> None:
        self._handles = dict(handles or {})

    def get(self, name: str) -> CredentialHandle | None:
        """Return the handle for a name, or None when it is unknown."""
        return self._handles.get(name)

    def names(self) -> list[str]:
        """Return the known credential names in sorted order."""
        return sorted(self._handles)


def credential_env_var(name: str) -> str:
    """Map a credential name to its environment variable name."""
    sanitized = "".join(
        character if character.isalnum() else "_" for character in name
    ).upper()
    return f"EVALH_CREDENTIAL_{sanitized}"


class EnvCredentialStore(InMemoryCredentialStore):
    """A test seam that resolves declared credentials from EVALH_CREDENTIAL_*.

    Production never builds this store: `evalh run` and `evalh serve` resolve
    secrets only through the encrypted vault (`core/vault.py`). The class stays
    so a test can exercise a candidate with a credential and no database.
    """

    @classmethod
    def from_spec(
        cls,
        credentials: Iterable[CredentialSpec],
        env: Mapping[str, str] | None = None,
    ) -> EnvCredentialStore:
        """Build handles for the credentials a spec declares."""
        source = os.environ if env is None else env
        handles: dict[str, CredentialHandle] = {}
        for credential in credentials:
            handles[credential.name] = CredentialHandle(
                name=credential.name,
                provider=credential.provider,
                secret=source.get(credential_env_var(credential.name), ""),
            )
        return cls(handles)


@dataclass(slots=True)
class RunContext:
    """Everything ambient a candidate may see; never a settings object."""

    run_id: str
    seed: int
    credential: CredentialHandle | None
    events: EventEmitter
    prompt_variant: str | None = None
    prompt_override: str | None = None
    chunks: list[ContextChunk] = field(default_factory=list)
    budget_remaining_usd: float | None = None
    candidate: str = ""
    case_id: str = ""
    replica: int = 0
    model: str = ""
    redactor: Redactor = field(default_factory=NoOpRedactor)
    repos: Mapping[str, RepoSnapshot] = field(default_factory=dict)
    artifacts_root: Path | None = None

    def artifact_metadata(self) -> dict[str, Any]:
        """Return a redaction-safe summary for candidate artifacts."""
        return {
            "candidate": self.candidate,
            "case_id": self.case_id,
            "replica": self.replica,
            "prompt_variant": self.prompt_variant,
            "context_chunks": [chunk.chunk_id for chunk in self.chunks],
        }
