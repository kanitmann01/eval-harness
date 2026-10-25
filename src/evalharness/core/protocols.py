"""The five adapter protocols and the capability declaration they carry."""

from __future__ import annotations

from typing import Iterable, Protocol

from pydantic import BaseModel, ConfigDict

from evalharness.core.context import RunContext
from evalharness.core.models import (
    CandidateOutput,
    Case,
    CellRecord,
    Check,
    JudgeRequest,
    RunRecord,
    ScoreContext,
    SourceSpec,
    Verdict,
)


class Capabilities(BaseModel):
    """What a candidate can accept, what it needs, and how often it may be retried.

    `requires_credential` lets validation refuse a candidate whose auth shape
    needs a secret no vault can resolve; `max_attempts` is the attempt budget
    the executor honours for a typed transport failure (one call plus retries).
    """

    model_config = ConfigDict(extra="forbid")

    supports_prompt_override: bool = True
    supports_context_injection: bool = True
    requires_credential: bool = False
    max_attempts: int = 2


class Candidate(Protocol):
    """A candidate agent: runs one case and returns output plus usage."""

    name: str
    capabilities: Capabilities

    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput:
        """Execute one case under the ambient run context."""
        ...


class Judge(Protocol):
    """A judge turns an answer into a verdict; a metric depends on it."""

    name: str

    async def verdict(self, request: JudgeRequest) -> Verdict:
        """Judge one candidate answer."""
        ...


class Metric(Protocol):
    """A metric turns one cell into one or more checks.

    ``check`` is async because a judge-backed metric awaits its judge; a
    deterministic metric simply has nothing to await.
    """

    key: str

    async def check(self, cell: CellRecord, ctx: ScoreContext) -> list[Check]:
        """Score one cell and return its checks."""
        ...


class Source(Protocol):
    """A source loads the case set a spec names."""

    def load(self, spec: SourceSpec, ctx: RunContext) -> Iterable[Case]:
        """Return the cases for this source's spec block."""
        ...


class Sink(Protocol):
    """A sink persists run artifacts outside the database."""

    def open_run(self, run: RunRecord) -> None:
        """Create the run's artifact location."""
        ...

    def record_cell(self, cell: CellRecord, checks: list[Check]) -> None:
        """Write one cell and its checks."""
        ...

    def close_run(self, run: RunRecord) -> None:
        """Finish the run's artifact location."""
        ...


class VerificationResult(BaseModel):
    """The outcome of one credential verification request."""

    ok: bool
    message: str = ""


class CredentialVerifier(Protocol):
    """Check one provider credential with a single request."""

    async def verify(self, provider: str, secret: str) -> VerificationResult:
        """Return the provider's verdict and its message verbatim."""
        ...
