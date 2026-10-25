# Design: adapter-based evaluation harness

Status: proposed, no code written. Supersedes the `score` path as the
product spine.

The harness is the product. The RAG pipeline, the Fineract tree, the
Graphify graph and the call-scan skill stop being the centre of this
repository and become the first example candidate. What ships is a
configurable evaluation engine with a console: pick candidates, pick
prompt variants, pick context settings, pick metrics, run the matrix,
read results with real token and cost accounting, keep the run for later.

---

## 1. Decisions locked before design

| # | Decision | Consequence |
|---|---|---|
| D1 | Everything declarative: a YAML eval spec is the source of truth | no literal paths, ports, models, prices or provider enums in code |
| D2 | Adapters behind protocols, resolved by name from the spec | `module:factory` dotted paths plus entry points for installed plugins |
| D3 | A candidate is a self-contained pluggable agent | someone else's agent plugs in as an HTTP endpoint, without touching this repo |
| D4 | BYOK, keys survive the run | encrypted credential store, master key from the environment |
| D5 | One container per person, no accounts | the credential store belongs to whoever runs the container |
| D6 | Console is a spec builder that launches runs and keeps them | not a live playground; every result traces back to a spec snapshot |
| D7 | Console owns results; Grafana leaves the critical path | the 2,093-line generated board is deleted or archived |
| D8 | Focus is evaluation mechanics, not corpus or repo | ingestion, graph mapping and repo scanning become candidate concerns |

D5 is the load-bearing one. It means no login, no users table, no tenancy:
one container, one human, one set of keys. Everything else follows from
that, and it is also the first thing to revisit if the team wants one
shared instance.

---

## 2. Layering

```
  surfaces        CLI            HTTP API           web console
                    \              |                  /
  core           spec  ->  matrix planner  ->  executor  ->  scorer  ->  record
                    \              |                  /
  adapters     candidate      judge       metric      source      sink
                    \              |                  /
  storage      harness schema (Postgres)   +   credential vault (encrypted)
```

Rule: dependencies point down only. Core never imports an adapter. Core
imports a registry that returns protocols. An adapter may import core
types. Surfaces may import anything.

Corollary that today's code violates: **the harness store and the
candidate's own data must be different databases.** Right now
`metrics.py` writes `runs` and `task_results` into the same Postgres that
holds `policy_chunks`, so the scorer shares a connection pool with the
thing it scores and a candidate's schema changes can break the audit
trail. Two databases in one container: `harness` and whatever the
candidate owns.

---

## 3. Module map

New and moved. `app/` stays the top package so `python -m app` and the CI
workflow keep working.

```
app/
  evals/                      NEW core, candidate-agnostic
    spec.py                   pydantic models + YAML loader + validation
    matrix.py                 expands spec into cells (deterministic order)
    executor.py               asyncio queue, worker slots, retries, resume
    scorer.py                 runs checks, aggregates, intervals
    stats.py                  wilson interval, pass@k, pass^k
    cost.py                   price table, usage to USD, budget guard
    registry.py               resolve "module:factory" and entry points
    protocols.py              Candidate, Judge, Metric, Source, Sink
    models.py                 Case, CandidateOutput, Check, Aggregate, RunRecord
  adapters/                   NEW first-party adapters
    candidates/minirag.py     today's RAG + repo agent, wrapped
    candidates/http.py        POST {prompt, context} -> JSON contract
    judges/ollama.py          moved from app/generation/judge.py
    judges/bedrock.py         moved
    judges/openai_compat.py   NEW: any /v1/chat/completions base URL
    metrics/retrieval.py      recall@k, mrr
    metrics/citation.py       judge-backed citation support
    metrics/correctness.py    judge-backed rubric
    metrics/ops.py            cost, latency, tool calls
    sources/goldens.py        JSON case files
    sources/corpus.py         derive cases from a corpus directory
  surfaces/
    cli.py                    moved from app/cli.py
    api.py                    NEW: FastAPI app, JSON + SSE
    web/                      NEW console templates and assets
  storage/
    harness_db.py             NEW: harness schema, migrations
    vault.py                  NEW: credential encryption and lifecycle
    db.py, pgadapter.py       unchanged, used only by candidate adapters
  retrieve/, corpus/, generation/, observability/
                              reused by the minirag candidate, not by core
```

`app/harness/{score,evaluate,gate,metrics,tasks,graph,adapter}.py` does
not survive as core. Each one is either folded into `app/evals/` or
becomes part of the `candidates/minirag.py` adapter. See section 13.

---

## 4. Protocols

Five protocols. Deliberately small, so the second implementation is a
half-day, not a rewrite.

```python
class Candidate(Protocol):
    name: str
    async def run(self, case: Case, ctx: RunContext) -> CandidateOutput: ...

class Judge(Protocol):
    async def verdict(self, req: JudgeRequest) -> Verdict: ...

class Metric(Protocol):
    key: str
    def check(self, cell: CellRecord, ctx: ScoreContext) -> list[Check]: ...

class Source(Protocol):
    def load(self, spec: SourceSpec, ctx: RunContext) -> Iterable[Case]: ...

class Sink(Protocol):
    def open_run(self, run: RunRecord) -> None: ...
    def record_cell(self, cell: CellRecord, checks: list[Check]) -> None: ...
    def close_run(self, run: RunRecord) -> None: ...
```

Core models, all pydantic:

```python
class Case(BaseModel):
    case_id: str
    prompt: str
    expected: dict | None          # objective ground truth, if any
    rubric: str | None             # judge instructions, if subjective
    tier: int = 1
    metadata: dict = {}

class CandidateOutput(BaseModel):
    answer: str
    tool_calls: int = 0
    prompt_tokens: int
    completion_tokens: int
    citations: list[str] = []
    latency_ms: int
    model: str
    artifacts: dict = {}           # trace, raw response, files

class Check(BaseModel):
    metric: str
    passed: bool
    score: float | None = None
    reason: str = ""
    evidence: dict = {}
```

Three rules that make this work as an adapter layer and not as a pile of
optional arguments:

1. `RunContext` carries everything ambient: credential handle, resolved
   case set, prompt variant, context chunks, seed, budget left, event
   emitter. No candidate receives a settings object.
2. A candidate never scores itself. It returns output and usage, nothing
   else. Every pass or fail comes from a metric.
3. Prompt override and context injection are part of `RunContext`, not a
   candidate feature. A candidate that cannot accept an override declares
   `supports_prompt_override = False`, and the matrix planner refuses to
   build those cells instead of silently ignoring them.

The `http` candidate defines the wire contract for other people's agents:

```json
POST /run
{"case_id": "...", "prompt": "...", "context": [{"chunk_id": "...", "text": "..."}],
 "prompt_override": "...", "seed": 7}
->
{"answer": "...", "citations": ["..."], "usage": {"prompt_tokens": 812, "completion_tokens": 96},
 "tool_calls": 0, "artifacts": {"trace_id": "..."}}
```

That JSON is the whole integration story for a teammate who does not
touch this repository.

---

## 5. Spec schema

One YAML file per evaluation, committed to git, no secrets in it. The
console edits it through the API and shows the diff before saving.

```yaml
spec_version: 1
name: prompt-ab-banking
description: Does policy_answer_v4 beat v3 at k=5?
seed: 7

source:
  adapter: adapters.sources.corpus:load
  options:
    corpus_dir: corpora/banking
    limit: 40
    tiers: [1, 2]

candidates:
  - name: rag-v3
    adapter: adapters.candidates.minirag:build
    credential: ollama-local
    model: qwen3:8b
    options:
      prompt: v3
      context: {mode: retrieved, k: 5}
  - name: rag-v4
    adapter: adapters.candidates.minirag:build
    credential: ollama-local
    model: qwen3:8b
    options:
      prompt: v4
      context: {mode: retrieved, k: 5}
  - name: rag-v4-ctx12
    adapter: adapters.candidates.minirag:build
    credential: ollama-local
    model: qwen3:8b
    options:
      prompt: v4
      context: {mode: retrieved, k: 12}

replicas: 3          # statistical repeats per cell
concurrency: 2       # cells in flight at once
budget_usd: 2.50     # executor aborts the run when exceeded

metrics:
  - {key: recall_at_5, adapter: adapters.metrics.retrieval:recall, options: {k: 5}}
  - {key: citation_support, adapter: adapters.metrics.citation:support, options: {judge: ollama-local}}
  - {key: token_cost, adapter: adapters.metrics.ops:cost}
  - {key: latency, adapter: adapters.metrics.ops:latency, options: {p: 95}}

judges:
  - name: ollama-local
    adapter: adapters.judges.ollama:build
    credential: ollama-local
    model: qwen3:8b
    temperature: 0.0

gates:
  - {metric: recall_at_5, min: 0.90}
  - {metric: citation_support, min: 0.85}
  - {metric: cost_usd_per_case, max: 0.02}

prices:                       # USD per million tokens, snapshot per run
  qwen3:8b: {input: 0.15, output: 0.60, tool: 0.001}
```

Three axes are visible in one file: candidate, prompt variant, context
size. Adding a fourth (temperature, retriever, chunker) is a new key
under `options`, not a code change. That is the "interference pattern"
requirement, expressed as data.

Spec validation rejects, before any spend: unknown adapter path, unknown
credential name, missing price for a declared model, a prompt variant the
candidate cannot accept, a metric whose judge is not declared.

---

## 6. Credential model (BYOK)

Single-user container, keys persist, therefore keys are at rest and must
be encrypted at rest.

- Store: `harness.credentials(name, provider, ciphertext, created_at,
  last_ok_at, last_error)` in Postgres.
- Encryption: Fernet (AES-128-CBC + HMAC) from `HARNESS_MASTER_KEY`,
  a 32-byte base64 value in the environment or a file mounted into the
  container. On first start with no key, the app generates one and prints
  it once with an instruction to store it, then refuses to start until it
  is present again. Losing it means re-entering keys, which is the correct
  failure mode.
- Providers are adapters: `openai-compatible`, `anthropic`, `bedrock`,
  `azure-openai`, `ollama`, `custom`. Each declares how auth is shaped
  (bearer, `x-api-key`, SigV4, none) and how usage is reported.
- Save is verified: the console pings the provider with a 1-token request
  and stores `last_ok_at`. An unverified credential cannot be selected in
  a spec.
- Redaction is a filter, not a habit: any string matching a stored secret
  is replaced with `[redacted:name]` on the way into logs, events,
  artifacts and cell records. One test asserts it, with a canary value.
- Forbidden: secrets in YAML, secrets in the run snapshot, secrets in the
  artifact directory, secrets in error text returned to the browser.

Non-goal for v1: per-user accounts. When that changes, the vault gains an
owner column and the encryption master key becomes a KMS reference.

---

## 7. Cost model

Today one global triple (`input_usd_per_million`,
`output_usd_per_million`, `tool_usd`) prices every model, which silently
prices a Bedrock judge at Ollama rates.

- Prices live in the spec, with a default table in `config/prices.yaml`
  keyed by provider and model. The spec snapshot stores the resolved
  table, so an old run keeps its old prices.
- Usage is ground truth when the provider reports it. Tokenizer estimate
  is a fallback, and every cell records `usage_source: provider |
  estimated` so a cost figure never hides which one it is.
- Cost is computed per cell, summed per candidate and per run, and split
  into answer cost, judge cost, and tool cost. The judge is charged to the
  cell that triggered it, which is what makes "judge cost is 40 percent of
  this run" visible.
- Estimate before launch: median tokens per case from the latest
  comparable run (or a 1-case calibration) times cells. The console shows
  the estimate and refuses a spec whose worst case exceeds `budget_usd`
  unless the operator raises the cap.
- Budget guard: executor checks spend every N cells and aborts with
  `status=budget_exceeded`, keeping completed cells.

---

## 8. Matrix executor

A cell is `(candidate, case, replica)`. Variants are properties of the
candidate, so they do not multiply the cell count twice.

```
cells = candidates x cases x replicas
cells = 3 x 40 x 3 = 360
```

Expansion is deterministic and sorted, so a rerun of the same spec with
the same seed produces the same cell ids. That is what makes resume,
comparison and a stable heatmap possible.

Execution:

- One asyncio queue, `concurrency` worker slots from the spec. Each worker
  owns one candidate instance, built once and reused, so a candidate may
  hold an HTTP client or a DB pool.
- Per-cell retry: 2 attempts, exponential backoff, only on transport
  errors. A candidate that returns a wrong answer is not a retry.
- Resume: cells with `status=ok` are skipped. `harness run --resume
  <run_id>` continues after a crash or a budget abort.
- Cancel: cooperative, checked between cells, marks remaining cells
  `cancelled` and still closes the run so the partial results are legible.
- Events: every state change appends to `harness.events` and publishes on
  SSE. The console streams progress without polling.
- Determinism: seed passed to the candidate, temperature from the spec, no
  wall-clock in any hash.
- Latency honesty: with `concurrency > 1` against one local GPU, latency
  measures queueing, not the model. The ops metric records
  `provider_latency_ms` when the candidate reports it and `wall_ms`
  always, and the console labels the difference. Concurrency 1 is the only
  setting where latency comparisons are valid, and the spec validator
  warns when a latency gate is combined with `concurrency > 1`.

---

## 9. Scoring, statistics, gates

Three levels, three different things:

- `Check`: per cell, pass/fail plus evidence. Deterministic where ground
  truth exists (recall@k, exact decision match), judge-backed where it
  does not (citation support, rubric correctness).
- `Aggregate`: per candidate, per metric, with `n`, mean, and a 95 percent
  Wilson interval for proportions. Also `pass@k` (at least one of k
  replicas passes) and `pass^k` (all k pass), which are different claims
  and are never collapsed into one number.
- `Gate`: a threshold on an aggregate. Fails the run with a nonzero exit
  code, which is what CI consumes.

A judge is a metric's dependency, not a global setting. Two metrics in one
run can use two different judges, at different costs, and the report shows
which judge produced which verdict. Judge calibration (agreement against
human labels, Cohen's kappa) is stored per judge configuration hash, so a
judge that drifts is visible rather than trusted.

---

## 10. Storage schema

Namespace: one Postgres database `harness`, separate from any candidate
database.

```sql
eval_specs   (spec_id uuid pk, name text, yaml text, sha text, created_at)
runs         (run_id uuid pk, spec_id uuid, spec_snapshot jsonb,
              prices_snapshot jsonb, harness_sha text, seed int,
              status text, started_at, finished_at, cost_usd float,
              budget_usd float, n_cells int, n_ok int, error text)
cells        (cell_id uuid pk, run_id uuid, candidate text, case_id text,
              replica int, variant jsonb, status text, wall_ms int,
              prompt_tokens int, completion_tokens int, tool_calls int,
              cost_usd float, usage_source text, answer text,
              output jsonb, artifact_dir text)
checks       (cell_id uuid, metric text, passed bool, score float,
              reason text, evidence jsonb, judge_config_hash text)
aggregates   (run_id uuid, candidate text, metric text, n int, value float,
              ci_low float, ci_high float, pass_at_k float, pass_k float)
gates        (run_id uuid, name text, metric text, candidate text,
              passed bool, comparison text, threshold float, observed float,
              n int, detail jsonb)
events       (run_id uuid, seq bigint, ts timestamptz, kind text, payload jsonb)
credentials  (name text pk, provider text, ciphertext bytea, created_at,
              last_ok_at, last_error text)
```

Artifacts (traces, raw responses, long answers) go to
`$HARNESS_HOME/artifacts/<run_id>/<cell_id>/` and only paths go in the DB.
That keeps the database small enough to back up and the artifacts cheap
enough to delete.

Migration from today: `runs` and `task_results` are recreated under the new
shape. Old rows are exported to `eval/legacy/` as JSON rather than
migrated, because the old rows have no candidate column and no usage
source, so a migration would invent data.

---

## 11. Console

FastAPI plus server-rendered templates plus Chart.js. No SPA build step,
no node in the container.

New runtime dependencies, deliberately few: `fastapi`, `uvicorn[standard]`,
`jinja2`, `cryptography`. PyYAML, pydantic and jsonschema are already in
`requirements.txt`, so the spec loader adds nothing. Everything else stays
stdlib or existing.

| Page | What it does |
|---|---|
| Runs | list with status, spec name, cost, pass rate, duration; filter by candidate and metric |
| Spec editor | form over the YAML with live validation, estimated cell count and cost, diff before save |
| Run detail | matrix heatmap (candidate x case), per-cell drilldown: answer, citations, every check with evidence, tokens, cost, latency, raw artifact |
| Compare | two runs or two candidates side by side, deltas with intervals, cost per correct answer |
| Credentials | add, test, disable, delete; masked display, provider, last verified |
| Live | SSE progress while a run executes, cancel button |

API surface, all JSON except the HTML routes:

```
GET  /api/specs            POST /api/specs            PUT /api/specs/{id}
POST /api/specs/validate   POST /api/runs             GET /api/runs
GET  /api/runs/{id}        POST /api/runs/{id}/cancel
GET  /api/runs/{id}/cells  GET  /api/runs/{id}/events (SSE)
GET  /api/compare?a=&b=    GET/POST/DELETE /api/credentials
POST /api/credentials/{name}/test
```

The compare view is the point of the whole thing: "v4 beat v3 on citation
support by 7 points, n=40, CI 1 to 13, at 1.4x the cost, and the extra
context bought 0". That sentence is the deliverable, not a chart.

---

## 12. CI and the gate

- `harness run SPEC` replaces `python -m app score` in the workflow. It
  exits 0 on success, 1 on gate failure, 2 on spec error and 3 on budget
  abort; gate failure always exits 1, with no flag to turn it off. A
  cancelled run exits 0 unless a gate failed. The full matrix is in
  [runs.md](runs.md).
- CI uses the `ollama` credential if present, otherwise it runs the
  `deterministic` judge-only path against committed fixtures. The harness
  must still run green with zero paid keys, which is the existing
  constraint and stays.
- A conformance suite in `tests/conformance/` runs every registered
  adapter against its protocol contract with a fake transport. Adding an
  adapter without conformance tests fails CI.

---

## 13. Migration order

Each phase is shippable and leaves `master` green.

**P0, foundation, no behavior change.** `protocols.py`, `models.py`,
`registry.py`, `spec.py` with validation, `cost.py` with the price table,
`storage/vault.py`, `storage/harness_db.py`, new schema. Wrap today's
`run_score` as `adapters/candidates/minirag.py`. Exit: a legacy CLI flag
`--spec` loads and validates a spec, prints the expanded cell count and
cost estimate, runs nothing.

**P1, executor and parity.** `executor.py`, `matrix.py`, `scorer.py`,
`stats.py`, `sources/goldens.py`. Exit: a spec-driven run reproduces
today's `python -m app score` numbers on one fixture, cell for cell, and
the parity test is committed.

**P2, storage and gates.** Sinks, aggregates, gates, artifact layout.
Exit: `--fail-on-gate` works, exit codes are CI-usable, an old-format
export exists in `eval/legacy/`.

**P3, read-only console.** `surfaces/api.py`, runs list, run detail,
compare, SSE. Exit: a run can be launched from the CLI and read entirely
from the browser, including per-cell evidence.

**P4, spec builder and credentials.** Editor with validation and cost
estimate, credential vault UI with ping, launch and cancel from the
console. Exit: a full evaluation is composed, launched and read without
touching a terminal.

**P5, provider breadth and packaging.** `openai_compat` and `anthropic`
judges and candidates, `Dockerfile`, `HARNESS_MASTER_KEY` bootstrap,
README with the BYOK contract. Exit: someone who is not you runs it with
their own key and gets a result.

**P6, demotion.** Move Fineract, Graphify, call-scan, OSSIE and the
Grafana board out of the core path into `examples/fineract/` or delete
them. Exit: core has no import of anything repo-specific, enforced by a
test that greps `app/evals/` for the strings `fineract`, `grafana`,
`graphify` and `loanaccount`.

Sequencing note: P1 is the only phase with real risk, because parity on
today's numbers is what proves the abstraction is faithful rather than
merely plausible. If parity fails, the protocol is wrong and it is cheaper
to learn that in P1 than in P4.

---

## 14. What happens to today's code

| Today | Fate |
|---|---|
| `harness/score.py` (542) | split: orchestration to `evals/executor.py`, row building to `evals/scorer.py`, candidate internals to `adapters/candidates/minirag.py` |
| `harness/tasks.py` | becomes `adapters/candidates/minirag.py` task generation |
| `harness/evaluate.py` | becomes `sources/goldens.py` plus `metrics/retrieval.py` |
| `harness/gate.py` | becomes a metric (`metrics/callsite.py`) invoked by the spec, not a subcommand |
| `harness/metrics.py` | replaced by `evals/scorer.py` + `storage/harness_db.py` |
| `harness/adapter.py` (25, Fineract only) | `examples/fineract/` |
| `harness/graph.py` | `examples/fineract/` (Graphify input) |
| `observability/ossie.py` | `examples/fineract/` |
| `generation/judge.py` | `adapters/judges/{ollama,bedrock}.py`, then add `openai_compat` |
| `web/compare.py`, `web/live.py` | replaced by `surfaces/api.py` and console |
| `grafana/`, `scripts/build_dashboard.py` | archived; results live in the console |
| `corpus/`, `retrieval/`, `generation/` | kept, imported only by the minirag candidate |

---

## 15. Non-goals for v1

Multi-user accounts or SSO; a shared team instance; scheduled regression
runs; a live interactive playground; human labelling UI; distributed
workers across machines; cost forecasting beyond median-token extrapolation;
streaming token-level traces.

## 16. Risks

| Risk | Mitigation |
|---|---|
| Parity fails in P1 | that is the finding; fix the protocol there, not in the UI |
| Secrets leak into artifacts or events | canary test on the redaction filter, plus a pre-commit grep for key-shaped strings |
| Cost figures silently wrong | `usage_source` on every cell, price snapshot per run, judge cost split out |
| Latency numbers meaningless under concurrency | `wall_ms` and `provider_latency_ms` both stored, validator warns on latency gates with `concurrency > 1` |
| Spec drift: UI state that never becomes a spec | the API only writes specs; a run that has no spec cannot exist |
| Candidate DB coupling returns | separate databases, and a test that asserts the harness schema has no foreign keys into candidate tables |
| One local GPU makes "parallel agents" a lie | worker slots are real but throughput is the model's; the console shows queue time separately |

## 17. Open question

One only. When a teammate plugs a candidate in over HTTP, does the
harness own the credential for that endpoint, or does the endpoint own it?
Both are defensible. Harness-owned keeps cost attribution in one place;
endpoint-owned keeps other people's secrets out of your vault. Default in
this design is harness-owned, matching D4, with `auth: none` as the escape
hatch for an endpoint that holds its own key.
