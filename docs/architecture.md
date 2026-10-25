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
    protocols.py       Candidate, Judge, Metric, Source, Sink (typing.Protocol)
    models.py          pydantic records: Spec, Case, Cell, Check, Aggregate, Gate, Run
    registry.py        name -> factory, dotted path resolution, test registration
    spec.py            YAML load, schema versioning, validation, price resolution
    planner.py         spec -> cells (candidate x case x replica), deterministic ids
    context.py         RunContext: credential, cases, prompt variant, chunks, seed, budget
    cancel.py          cooperative cancellation token, checked between cells
    executor.py        bounded concurrency, retries, budget guard, cancel, resume
    scorer.py          metrics -> checks per cell, awaiting each metric
    judges.py          judge config hashing and per-cell usage sessions
    stats.py           Wilson interval, pass@k, pass^k, per-tier breakdowns
    cost.py            price tables, token estimation, answer/judge/tool cost split
    vault.py           master key bootstrap, encrypt at rest, verify, redact
    redaction.py       canary-proof filter used by logs, events, artifacts, records
    events.py          append-only event stream + SSE fan-out
    config.py          environment-derived settings for the process, never for adapters
  storage/
    db.py              engine/session factory, SQLite and Postgres, schema creation
    migrations.py      schema_version marker, additive nullable columns, issue #2 rebuild
    models.py          ORM tables: specs, runs, cells, checks, aggregates, gates, events, credentials
    repository.py      the only code that talks SQL, returns core records
  adapters/
    manifest.py        every first-party adapter path and kind; the conformance gate
    candidates/        scripted stubs plus the HTTP candidate (contract/candidate-v1.schema.json)
    judges/            deterministic rule-based and scripted claim judges
    metrics/           exact match, containment, citation support, latency
    sources/           inline, jsonl file
    sinks/             json file
  conformance/
    suite.py           the public contract suite (candidate, judge, metric, source, sink)
    runner.py          run the suite against a dotted path, human-readable clause failures
  api/
    app.py             create_app(deps): FastAPI + Jinja2, auto-discovers route modules
    routes/            one module per surface, each exposing router
    templates/         server-rendered pages
    static/            one stylesheet, small vanilla JS
```

## The five protocols

```python
async def Candidate.run(case: Case, ctx: RunContext) -> CandidateOutput
async def Judge.verdict(request: JudgeRequest) -> Verdict
async def Metric.check(cell: CellRecord, ctx: ScoreContext) -> list[Check]
async def Source.load(spec: Spec, ctx: RunContext) -> list[Case]
async def Sink.open_run(run: RunRecord) / record_cell(cell) / close_run(run)
```

- `CandidateOutput` carries answer, citations, tool calls, prompt and completion tokens,
  latency, queue wait, model, usage source (`provider` or `estimated`) and artifacts.
  A candidate never returns pass or fail.
- `RunContext` carries everything ambient: resolved credential handle, prompt variant,
  injected chunks, seed, remaining budget, event emitter. No adapter receives settings
  and no adapter reads global configuration.
- `Metric.check` is async, so a judge-backed metric awaits the judge the spec wires to
  it. `ScoreContext` carries a fresh `JudgeSession` per declared judge plus the
  redactor; the session records judge tokens and cost on the cell that triggered it.
- A candidate declares what it does not support (`capabilities`); the planner refuses to
  build those cells with a validation error naming the candidate and the axis.

## Seams

1. **CLI run entry point** (`evalh run spec.yaml`). Most coverage lives here: planner,
   executor, retries, resume, cancel, budget, scorer, stats, cost, storage, gates.
2. **HTTP API** (`create_app` + `httpx.ASGITransport`). Console read and write surfaces.
3. **Adapter wire contracts**. One conformance suite per protocol; the HTTP candidate is
   exercised against a real local stub server, never a mock. Its public contract is
   `contract/candidate-v1.schema.json`, documented in [http-candidate.md](http-candidate.md)
   for an endpoint author who never reads this repository.

## Runs

- Cell id is deterministic: `sha1(candidate|case_id|replica)` truncated. Ordering is
  seeded, so a resumed run continues the same matrix.
- Every state change appends to `events`; SSE replays from an event id.
- A run records spec snapshot, resolved price snapshot, harness revision, seed, status.
- A cell stores its cost split (`answer_cost_usd`, `judge_cost_usd`, `tool_cost_usd`)
  and the judge tokens it spent; `cost_usd` is always their sum.
- Artifacts are files under the artifacts directory; the database stores paths only.
- Budget, cancel and resume keep one run id: a resume skips cells already `ok`
  and rewrites the run's aggregates and gates in place. [runs.md](runs.md) is the
  reference, including the one-run-at-a-time rule issue #12 relies on.

## Storage

SQLAlchemy 2.0, dialect-portable between SQLite (local, tests) and PostgreSQL (compose).
The harness database is its own database; no foreign key crosses into a candidate's data.
