"""Pydantic records shared across the core, adapters and storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return the current time with a UTC timezone."""
    return datetime.now(timezone.utc)


class Case(BaseModel):
    """One evaluation case: the prompt plus any objective ground truth."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    prompt: str
    expected: dict[str, Any] | None = None
    rubric: str | None = None
    tier: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextChunk(BaseModel):
    """One piece of injected context, identified for evidence."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    text: str


class CandidateOutput(BaseModel):
    """What a candidate returns: an answer and usage, never a pass or fail."""

    answer: str
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    citations: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    model: str = ""
    usage_source: Literal["provider", "estimated"] = "estimated"
    artifacts: dict[str, Any] = Field(default_factory=dict)


class Check(BaseModel):
    """One metric verdict for one cell, with the evidence behind it."""

    metric: str
    passed: bool
    score: float | None = None
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    judge_config_hash: str | None = None


class Aggregate(BaseModel):
    """One candidate's value for one metric in one run, overall or per tier.

    The storage contract, made explicit:

    * There is one overall row per candidate and metric, with tier None, plus
      one row per case tier present in the run.
    * n is the number of checks behind the row. value is None when n is zero,
      because an empty proportion is undefined rather than zero or one.
    * ci_low and ci_high are the 95 percent Wilson bounds, None when n is zero.
    * pass_at_k and pass_k are None unless the spec ran more than one replica;
      they are proportions over cases, not over replicas.
    """

    run_id: str
    candidate: str
    metric: str
    tier: int | None = None
    n: int
    value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    pass_at_k: float | None = None
    pass_k: float | None = None


class GateResult(BaseModel):
    """The stored outcome of one spec gate."""

    name: str
    metric: str
    passed: bool
    detail: dict[str, Any] = Field(default_factory=dict)


class CellRecord(BaseModel):
    """One executed cell: identity, usage, answer and artifact location."""

    cell_id: str
    run_id: str
    candidate: str
    case_id: str
    replica: int
    variant: dict[str, Any] = Field(default_factory=dict)
    status: str = "ok"
    wall_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    usage_source: str = "estimated"
    answer: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    artifact_dir: str | None = None


class RunRecord(BaseModel):
    """One run: the snapshot it was launched from and how it ended."""

    run_id: str
    spec_id: str = ""
    spec_name: str = ""
    spec_snapshot: dict[str, Any] = Field(default_factory=dict)
    prices_snapshot: dict[str, Any] = Field(default_factory=dict)
    harness_sha: str = ""
    seed: int = 0
    status: str = "pending"
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    cost_usd: float = 0.0
    budget_usd: float | None = None
    budget_override_usd: float | None = None
    n_cells: int = 0
    n_ok: int = 0
    error: str | None = None


class JudgeRequest(BaseModel):
    """The material a judge sees when producing a verdict."""

    case_id: str
    prompt: str
    answer: str
    rubric: str | None = None
    expected: dict[str, Any] | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class Verdict(BaseModel):
    """A judge's verdict with the reason and evidence behind it."""

    passed: bool
    score: float | None = None
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class ScoreContext(BaseModel):
    """Ambient data a metric needs that is not carried by the cell."""

    run_id: str
    candidate: str
    cases: dict[str, Case] = Field(default_factory=dict)


class Price(BaseModel):
    """USD per million tokens for one model, as written in the spec."""

    model_config = ConfigDict(extra="forbid")

    input: float
    output: float
    tool: float = 0.0


class SourceSpec(BaseModel):
    """The source block of a spec: one adapter plus its options."""

    model_config = ConfigDict(extra="forbid")

    adapter: str
    options: dict[str, Any] = Field(default_factory=dict)


class CandidateSpec(BaseModel):
    """One candidate row in a spec."""

    model_config = ConfigDict(extra="forbid")

    name: str
    adapter: str
    credential: str
    model: str
    options: dict[str, Any] = Field(default_factory=dict)


class MetricSpec(BaseModel):
    """One metric row in a spec."""

    model_config = ConfigDict(extra="forbid")

    key: str
    adapter: str
    options: dict[str, Any] = Field(default_factory=dict)


class JudgeSpec(BaseModel):
    """One judge declaration in a spec."""

    model_config = ConfigDict(extra="forbid")

    name: str
    adapter: str
    credential: str | None = None
    model: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class GateSpec(BaseModel):
    """A threshold on one metric's aggregate."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    min: float | None = None
    max: float | None = None


class SinkSpec(BaseModel):
    """An optional sink override; the JSON artifact sink is the default."""

    model_config = ConfigDict(extra="forbid")

    adapter: str
    options: dict[str, Any] = Field(default_factory=dict)


class CredentialSpec(BaseModel):
    """A logical credential name declared by the spec; the secret lives elsewhere."""

    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str = "none"


class CredentialRecord(BaseModel):
    """Metadata for one stored credential; the secret is never part of it."""

    name: str
    provider: str
    created_at: datetime
    last_ok_at: datetime | None = None
    last_error: str | None = None


class EncryptedCredential(CredentialRecord):
    """A stored credential plus its ciphertext; only the vault decrypts it."""

    ciphertext: bytes


class Spec(BaseModel):
    """The whole YAML spec, structurally parsed before semantic validation."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int
    name: str
    description: str = ""
    seed: int = 0
    source: SourceSpec
    prompts: dict[str, str] = Field(default_factory=dict)
    contexts: dict[str, list[ContextChunk]] = Field(default_factory=dict)
    credentials: list[CredentialSpec] = Field(default_factory=list)
    candidates: list[CandidateSpec] = Field(min_length=1)
    replicas: int = Field(default=1, ge=1)
    concurrency: int = Field(default=1, ge=1)
    budget_usd: float | None = Field(default=None, ge=0)
    metrics: list[MetricSpec] = Field(min_length=1)
    judges: list[JudgeSpec] = Field(default_factory=list)
    gates: list[GateSpec] = Field(default_factory=list)
    prices: dict[str, Price] = Field(default_factory=dict)
    sink: SinkSpec | None = None
