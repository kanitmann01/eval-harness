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
    """What a candidate can accept from the spec's prompt and context axes."""

    model_config = ConfigDict(extra="forbid")

    supports_prompt_override: bool = True
    supports_context_injection: bool = True


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
    """A metric turns one cell into one or more checks."""

    key: str

    def check(self, cell: CellRecord, ctx: ScoreContext) -> list[Check]:
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
