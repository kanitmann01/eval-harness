# Architecture

The authoritative design note is [design-adapter-harness.md](design-adapter-harness.md).
This file is the working summary: where code goes, which seam a test drives, and the
contracts that follow from the design note. Where the two disagree, the design note wins
and this file is corrected.

Four layers, dependencies point downward only.

| Layer | Package | May import |
| --- | --- | --- |
| Surfaces | `evalharness.cli`, `evalharness.api` | core, adapters, storage |
| Core | `evalharness.core` | protocols, registry, storage interfaces |
| Adapters | `evalharness.adapters.*` | core types only |
| Storage | `evalharness.storage` | core types only |

Core never imports a concrete adapter, a vendor SDK, a corpus, or a repository-specific
module. A test enforces this by scanning `core` imports.

## Module map

```
src/evalharness/
  cli.py               command surface: run, validate, specs, credentials, compare, serve
  exit_codes.py        the automation contract: 0 success, 1 gate failure, 2 spec error, 3 budget abort
  errors.py            typed errors that carry a field name and a fix
  core/
    protocols.py       Candidate, Judge, Metric, Source, Sink, ContextSelector (typing.Protocol)
    models.py          pydantic records: Spec, Case, Cell, Check, Aggregate, Gate, Run
    registry.py        name -> factory, dotted path resolution, test registration
    spec.py            YAML load, schema versioning, validation, price resolution
    planner.py         spec -> cells (candidate x case x replica), deterministic ids
    context.py         RunContext: credential, cases, prompt variant, chunks, repos, seed, budget
    repo_snapshot.py   local repository snapshot, content digest, manifest, changed check
    cancel.py          cooperative cancellation token, checked between cells
    executor.py        bounded concurrency, retries, budget guard, cancel, resume
    scorer.py          metrics -> checks per cell, awaiting each metric
    judges.py          judge config hashing and per-cell usage sessions
    stats.py           Wilson interval, pass@k, pass^k, per-tier breakdowns
    prices.py          shipped provider price table, provider-aware lookup, snapshot
    cost.py            price tables, token estimation, answer/judge/tool cost split, per-provider roll-up
    vault.py           master key bootstrap, encrypt at rest, verify, redact
    redaction.py       canary-proof filter used by logs, events, artifacts, records
    events.py          append-only event stream + SSE fan-out
    config.py          environment-derived settings for the process, never for adapters
  storage/
    db.py              engine/session factory, SQLite and Postgres, schema creation
    migrations.py      schema_version marker, additive columns, issue #2 rebuild
    models.py          ORM tables: specs, runs, cells, checks, aggregates, gates, events, credentials
    repository.py      the only code that talks SQL, returns core records
  adapters/
    manifest.py        every first-party adapter path and kind; the conformance gate
    transport.py       one HTTP policy: auth shapes, retries, provider messages, usage fallback
    provider_wire.py   the OpenAI-compatible and Anthropic-style wire dialects
    candidates/        scripted stubs, the HTTP candidate, and the two provider candidates
    judges/            deterministic rule-based and scripted judges, plus the two provider judges
    metrics/           exact match, containment, citation support, judge pass, latency
    sources/           inline, jsonl file, prepared repository snapshot
    sinks/             json file
  config/
    prices.yaml        the shipped USD-per-million-token table, keyed by provider and model
  conformance/
    suite.py           the public contract suite (candidate, judge, metric, source, sink)
    runner.py          run the suite against a dotted path, human-readable clause failures
  api/
    app.py             create_app(deps): FastAPI + Jinja2, auto-discovers route modules
    routes/            one module per surface, each exposing router
    templates/         server-rendered pages
    static/            one stylesheet, small vanilla JS
```

## The six protocols

```python
async def Candidate.run(case: Case, ctx: RunContext) -> CandidateOutput
async def Judge.verdict(request: JudgeRequest) -> Verdict
async def Metric.check(cell: CellRecord, ctx: ScoreContext) -> list[Check]
async def Source.load(spec: Spec, ctx: RunContext) -> list[Case]
async def Sink.open_run(run: RunRecord) / record_cell(cell) / close_run(run)
def ContextSelector.select(case: Case, ctx: RunContext) -> list[ContextChunk]
```

- `CandidateOutput` carries answer, citations, tool calls, prompt and completion tokens,
  latency, queue wait, model, usage source (`provider` or `estimated`) and artifacts.
  A candidate never returns pass or fail.
- `RunContext` carries everything ambient: resolved credential handle, prompt variant,
  injected chunks, seed, remaining budget, event emitter, prepared repositories and the
  artifacts root. No adapter receives settings and no adapter reads global
  configuration.
- `Metric.check` is async, so a judge-backed metric awaits the judge the spec wires to
  it. `ScoreContext` carries a fresh `JudgeSession` per declared judge plus the
  redactor; the session records judge tokens and cost on the cell that triggered it.
- A candidate declares what it does not support (`capabilities`); the planner refuses to
  build those cells with a validation error naming the candidate and the axis. A judge
  declares the same capabilities and validation enforces `requires_credential` and
  `requires_model` before any cell; the session passes the resolved credential and
  declared model to the adapter through `JudgeRequest`.

## Seams

1. **CLI run entry point** (`evalh run spec.yaml`). Most coverage lives here: planner,
   executor, retries, resume, cancel, budget, scorer, stats, cost, storage, gates,
   repository snapshots and context selection.
2. **HTTP API** (`create_app` + `httpx.ASGITransport`). Console read and write surfaces.
3. **Adapter wire contracts**. One conformance suite per protocol; the HTTP candidate and
   the four provider adapters are exercised against a real local stub server, never a
   mock. The HTTP candidate's public contract is
   `contract/candidate-v1.schema.json`, documented in [http-candidate.md](http-candidate.md)
   for an endpoint author who never reads this repository; the provider families are in
   [providers.md](providers.md).

## Parity with the RAG pipeline

`examples/parity/fixture/` is the recorded minirag three-arm scoreboard, copied
from `kenmann01/minirag` at commit `10e2103`; its README records the expected
totals and pins the sha256 of `expected.json`.
`examples/parity/minirag_replay_server.py` is the recorded pipeline speaking
candidate-v1, not a mock of the harness. `tests/test_parity_minirag.py` builds the
three-candidate spec from the fixture, runs it through the CLI against that server,
and asserts task totals, per-tier pass counts, tool calls, token totals and cost, so
a scorer, metric or cost regression fails the suite.

## Runs

- Cell id is deterministic: `sha1(candidate|case_id|replica)` truncated. Ordering is
  seeded, so a resumed run continues the same matrix.
- Every state change appends to `events`; SSE replays from an event id.
- A run records spec snapshot, resolved price snapshot, source snapshot, harness
  revision, seed, status. The source snapshot names the source adapter, the case
  count, and every repository digest, so a resume can refuse a changed tree.
- A cell stores its cost split (`answer_cost_usd`, `judge_cost_usd`, `tool_cost_usd`)
  and the judge tokens it spent; `cost_usd` is always their sum. It also stores its
  candidate provider and the judge usage per judge provider, which a run rolls up into
  `provider_costs` rows that sum to the run total.
- Artifacts are files under the artifacts directory; the database stores paths only.
- Budget, cancel and resume keep one run id: a resume skips cells already `ok`
  and rewrites the run's aggregates and gates in place. [runs.md](runs.md) is the
  reference, including the one-run-at-a-time rule issue #12 relies on.

## Storage

SQLAlchemy 2.0, dialect-portable between SQLite (local, tests) and PostgreSQL (compose).
The harness database is its own database; no foreign key crosses into a candidate's data.
