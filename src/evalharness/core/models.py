"""Pydantic records shared across the core, adapters and storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evalharness.core.redaction import NoOpRedactor


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
    """The stored outcome of one spec gate.

    The gate's arithmetic is explicit and stored, not left inside the detail
    blob: comparison is the operator the gate applied, threshold is the bound
    it applied, observed is the aggregate value it saw and n is the number of
    checks behind that value. observed is None only when the aggregate is
    undefined, which always fails the gate with "no samples" in the detail.
    """

    name: str
    metric: str
    passed: bool
    candidate: str | None = None
    comparison: str = ""
    threshold: float | None = None
    observed: float | None = None
    n: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)


class ProviderUsage(BaseModel):
    """Tokens and cost one judge provider spent while scoring one cell."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class ProviderCost(BaseModel):
    """One provider's share of a run: cells, tokens and the three cost parts.

    cost_usd is answer_cost_usd + judge_cost_usd + tool_cost_usd, exactly as
    on a cell, so per-provider rows always sum to the run's stored total.
    """

    run_id: str
    provider: str = ""
    n_cells: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    judge_prompt_tokens: int = 0
    judge_completion_tokens: int = 0
    answer_cost_usd: float = 0.0
    judge_cost_usd: float = 0.0
    tool_cost_usd: float = 0.0
    cost_usd: float = 0.0


class CellRecord(BaseModel):
    """One executed cell: identity, usage, cost split, answer and artifacts.

    cost_usd is always answer_cost_usd + judge_cost_usd + tool_cost_usd. The
    three components are stored separately so judge spend is visible against
    the answer it judged, and a deterministic run keeps judge cost at zero.
    """

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
    answer_cost_usd: float = 0.0
    judge_cost_usd: float = 0.0
    tool_cost_usd: float = 0.0
    judge_prompt_tokens: int = 0
    judge_completion_tokens: int = 0
    usage_source: str = "estimated"
    provider: str = ""
    judge_provider_usage: dict[str, ProviderUsage] = Field(default_factory=dict)
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
    operator: str = ""
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
    """The material a judge sees when producing a verdict.

    The session fills `model` and `credential` from the judge declaration just
    before the call, so a provider judge can name its model and authenticate
    while a deterministic judge simply ignores both. The secret is excluded
    from every dump by CredentialHandle, so a request never carries it into a
    log or an artifact.
    """

    case_id: str
    prompt: str
    answer: str
    rubric: str | None = None
    expected: dict[str, Any] | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    model: str = ""
    credential: Any = None


class Verdict(BaseModel):
    """A judge's verdict with its reason, evidence and the usage it cost."""

    passed: bool
    score: float | None = None
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    usage_source: Literal["provider", "estimated"] = "estimated"


class Price(BaseModel):
    """USD per million tokens for one model, as written in the spec."""

    model_config = ConfigDict(extra="forbid")

    input: float
    output: float
    tool: float = 0.0

    def usage_usd(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        tool_calls: int = 0,
    ) -> float:
        """Convert a usage triple into USD at this price."""
        return (
            prompt_tokens / 1_000_000 * self.input
            + completion_tokens / 1_000_000 * self.output
            + tool_calls * self.tool
        )


class JudgeSession(BaseModel):
    """One judge resolved for one cell, with the usage this cell accumulated.

    The judge instance is shared across cells; the counters are not, so a
    concurrent run attributes every verdict to the cell that triggered it. The
    session carries the declaration's model, provider, price and resolved
    credential; a deterministic judge without a model has no price and costs
    zero while still recording whatever tokens it reports.
    """

    name: str
    judge: Any
    model: str = ""
    provider: str = ""
    config_hash: str = ""
    price: Price | None = None
    credential: Any = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    async def verdict(self, request: JudgeRequest) -> Verdict:
        """Ask the wrapped judge and add the verdict's usage to this cell.

        The request is copied, never mutated, so two metrics naming two judges
        for one cell each see their own model and credential.
        """
        resolved = request.model_copy(
            update={"model": self.model, "credential": self.credential}
        )
        verdict = await self.judge.verdict(resolved)
        self.prompt_tokens += verdict.prompt_tokens
        self.completion_tokens += verdict.completion_tokens
        return verdict

    def usage_usd(self) -> float:
        """Return what this cell's judge usage costs at the declared price."""
        if self.price is None:
            return 0.0
        return self.price.usage_usd(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )


class ScoreContext(BaseModel):
    """Ambient data a metric needs that is not carried by the cell.

    judges holds one fresh session per declared judge, so a metric calls the
    judge by the name its options declare and the usage lands on this cell.
    """

    run_id: str
    candidate: str
    cases: dict[str, Case] = Field(default_factory=dict)
    judges: dict[str, JudgeSession] = Field(default_factory=dict)
    redactor: Any = Field(default_factory=NoOpRedactor)


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
    credential: str | None = None
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
    """A threshold on one metric's aggregate, overall or for one candidate.

    candidate is optional: when it is set the gate reads that candidate's
    overall aggregate row; when it is None the gate pools every candidate's
    overall row for the metric into the run-wide figure.
    """

    model_config = ConfigDict(extra="forbid")

    metric: str
    min: float | None = None
    max: float | None = None
    candidate: str | None = None


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
    disabled: bool = False


class CredentialState(BaseModel):
    """What spec validation may know about one stored credential's usability.

    The state carries no secret and no ciphertext: disabled is the console's
    switch, and verified means the last successful provider test exists.
    """

    name: str
    disabled: bool = False
    verified: bool = False


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
    provider_prices: dict[str, dict[str, Price]] = Field(default_factory=dict)
    sink: SinkSpec | None = None
