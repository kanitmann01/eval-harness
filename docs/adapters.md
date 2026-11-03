# Adapter contracts and the conformance suite

An adapter is trustworthy because it passes the same public suite as every
other adapter. The suite is code, not a checklist: each clause is a test
with a stable identifier, and a failure names the clause that broke.

## The five contracts

A factory takes two arguments, the identity the harness assigned and the
adapter options mapping from the spec, and returns an object that
satisfies one protocol.

| Kind | Factory identity | Contract |
| --- | --- | --- |
| `candidate` | the candidate name | `name`, `capabilities`, `async run(case, ctx) -> CandidateOutput` |
| `judge` | the judge name | `name`, `async verdict(JudgeRequest) -> Verdict` |
| `metric` | the metric key | `key`, `async check(CellRecord, ScoreContext) -> list[Check]` |
| `source` | the literal `source` | `load(SourceSpec, RunContext) -> Iterable[Case]` |
| `sink` | the literal `sink` | `open_run(RunRecord)`, `record_cell(CellRecord, checks)`, `close_run(RunRecord)` |

A candidate returns an answer and usage, never a pass or fail. A metric
turns a cell into checks. A judge is a metric's dependency. A source owns
the case set. A sink owns artifacts outside the database.

### Metric.check is async

Issue #7 made `Metric.check` async, because a judge-backed metric has to
await its judge and the judge protocol was already async:

~~~python
async def check(self, cell: CellRecord, ctx: ScoreContext) -> list[Check]: ...
~~~

This is a protocol change, not an adapter option. It touched the protocol,
both deterministic metrics, the scorer's `checks_for_cell`, the executor's
call site, the `metric/*` conformance clauses, and every test that called
`check` directly. A deterministic metric simply has nothing to await.
`ScoreContext` now carries resolved judge sessions by name and the run's
redactor; [judges.md](judges.md) describes the wiring, the configuration hash
and the cost split.

## Clause identifiers and numbering

Every clause identifier is `<kind>/<number>`, for example
`candidate/3`. Numbers are append-only: a published clause keeps
its number forever, and a new guarantee takes the next number for that
kind. A CI failure therefore points at the same sentence it always did.

### candidate

| Clause | Title | What it enforces |
| --- | --- | --- |
| `candidate/1` | run returns a CandidateOutput | `run` returns the core record, not a dict or a bare string |
| `candidate/2` | usage fields are non-negative integers | `prompt_tokens`, `completion_tokens`, `tool_calls` and `latency_ms` are ints >= 0 |
| `candidate/3` | capabilities declared | `capabilities` is a `Capabilities` instance, so the planner can refuse unsupported axes |
| `candidate/4` | usage source is provider or estimated | the cell never hides where usage came from |
| `candidate/5` | prompt override is ignored when unsupported | a candidate declaring `supports_prompt_override=False` does not quietly use the override |
| `candidate/6` | a transport failure raises TransportError | retryable failures are typed |
| `candidate/7` | a wrong answer is an output, not an error | a bad answer is scored, not retried |
| `candidate/8` | a retry budget is declared | `capabilities.max_attempts` is an integer >= 1, so the executor knows how many total attempts a transport failure gets |

### judge

| Clause | Title | What it enforces |
| --- | --- | --- |
| `judge/1` | verdict is a Verdict | `verdict` returns the core record |
| `judge/2` | passed is a bool | no truthy integer standing in for a verdict |
| `judge/3` | reason is non-empty | the verdict explains itself |
| `judge/4` | evidence is JSON-serialisable | evidence survives storage and the console |
| `judge/5` | the same request gives the same verdict | a deterministic judge repeats exactly |
| `judge/6` | the verdict reports its token usage | `prompt_tokens` and `completion_tokens` are non-negative ints and `usage_source` is provider or estimated, so judge cost attribution never loses its basis |

### metric

| Clause | Title | What it enforces |
| --- | --- | --- |
| `metric/1` | check returns Check instances | the typed list is the scorer's only input |
| `metric/2` | check returns at least one check | a metric that silently returns nothing hides a scoring bug |
| `metric/3` | every check names this metric key | a check cannot be attributed to another metric |
| `metric/4` | evidence is JSON-serialisable | evidence survives storage and the console |
| `metric/5` | check does not mutate the cell | scoring is read-only over the recorded cell |

### source

| Clause | Title | What it enforces |
| --- | --- | --- |
| `source/1` | load returns at least one case | an empty suite is a configuration error, not a run |
| `source/2` | load returns Case instances | the planner gets core cases |
| `source/3` | case ids are unique | cell ids stay deterministic and unambiguous |
| `source/4` | two loads return the same cases | a source over a prepared snapshot is deterministic |
| `source/5` | case metadata carries snapshot-relative paths only | no host path leaks into a case or cell |

### sink

| Clause | Title | What it enforces |
| --- | --- | --- |
| `sink/1` | the run leaves an artifact on disk | open, record and close produce a file |
| `sink/2` | every artifact stays inside the artifacts root | a sink cannot escape its root |

## The public API

```python
from evalharness.conformance import ConformanceError, run_all, run_suite

report = run_suite("evalharness.adapters.candidates.stub:build", "candidate")
assert report.passed
for result in report.results:
    print(result.clause, result.ok, result.detail)
report.raise_for_failure()   # raises ConformanceError on the first failure

reports = run_all()          # every adapter in the manifest
```

`run_suite(adapter_path, kind, *, options=None, workdir=None)`
returns a `ConformanceReport` with `adapter`, `kind`,
`passed` and `results`. Each `ClauseResult` carries
`clause`, `ok`, `detail` and `title`.
`run_all()` runs the manifest and returns one report per entry.

A clause failure is reported, not raised, so one broken clause does not
hide the rest. The detail is actionable, for example:

```text
candidate/3 (capabilities declared): expected a Capabilities instance, got dict
```

`report.raise_for_failure()` raises that text as a
`ConformanceError` when a caller wants an exception. The CLI prints
one line per clause and exits 1 when any clause failed.

## Fault-injection hooks

Two candidate clauses need a candidate to misbehave on purpose. The suite
builds a fresh instance with hook options:

| Hook | Default options | Used by |
| --- | --- | --- |
| `transport` | `{"fail": "transport"}` | `candidate/6` |
| `wrong` | `{"answer": "conformance-wrong-answer"}` | `candidate/7` |

A factory can declare its own options as a function attribute:

```python
CONFORMANCE_HOOKS = {
    "transport": {"fail": "transport"},
    "wrong": {"answer": "__conformance_wrong_answer__"},
}
build.CONFORMANCE_HOOKS = CONFORMANCE_HOOKS
```

The stub candidate functions declare both. An adapter that does not need
custom options can ignore the hooks; the defaults are then used.

## Kind defaults and scratch space

The runner supplies the options each kind needs to be exercised in
isolation: a source gets `cases`, a sink gets
`artifacts_dir`, and every kind gets a scratch directory under
`EVALH_HOME/conformance` (override with `workdir=`). A factory may set
`CONFORMANCE_SETUP`, a callable that receives the scratch directory and returns
an options mapping plus prepared repositories, for example the snapshot the
repo source needs. The scratch directory is removed after the run. Identities
are fixed:
`conformance-candidate`, `conformance-judge` and
`conformance_metric`, plus the literal `source` and
`sink` the spec uses.

## The manifest is the CI gate

`src/evalharness/adapters/manifest.py` lists every first-party
adapter path with its kind. Two tests enforce it:

1. `first_party_factories()` discovers every public
   `build*` factory under `src/evalharness/adapters` and
   asserts the manifest lists exactly those paths and kinds. Adding an
   adapter without a manifest row fails the test.
2. Every manifest entry runs the full suite. Adding an adapter without a
   conformance run fails the test.

A manifest row may carry options for the suite. The always-failing stub
uses this: its normal-run conformance options are `{"fail": ""}`,
so the clauses that expect a normal output pass, while the transport hook
still exercises `candidate/6`.

To register an adapter:

1. Add the factory under the matching `adapters/` subpackage.
2. Add a `ManifestEntry` row with the dotted path and the kind.
3. Run `python -m evalharness conformance --adapter <path> --kind <kind>`.
4. Run `python -m pytest`. The manifest tests are the gate.

## The repository source

`evalharness.adapters.sources.repo:build` reads cases and fixture metadata from
a repository snapshot declared under top-level `repos`:

```yaml
repos:
  docs:
    root: fixtures/docs

source:
  adapter: evalharness.adapters.sources.repo:build
  options:
    repo: docs
    cases: cases/questions.jsonl
    fixtures: fixtures
```

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `repo` | string | required | Name of a declared repository. |
| `cases` | string | required | Snapshot-relative JSONL or YAML case file. |
| `fixtures` | string or null | null | Snapshot-relative file or directory a case may reference. |
| `case_limit` | integer >= 1 | 500 | Refuse a larger case file. |
| `inline_fixtures` | boolean | false | Include small UTF-8 fixture text in each case. |
| `inline_max_bytes` | integer >= 1 | 65536 | Largest fixture inlined per case. |

A JSONL case file holds one JSON object per line; a YAML file holds a list of
objects. Case ids must be unique. A case `metadata.fixtures` list names files
below the configured fixture root, and the adapter resolves each to
`{"path", "sha256", "bytes"}`, plus `text` when inlining is on and the file is
UTF-8 and within the cap. Paths are snapshot-relative, never host paths, and a
path outside the root is rejected, including one that escapes through a symlink.

The conformance factory declares `CONFORMANCE_SETUP`: it creates a two-case
repository and fixture under the scratch directory, snapshots it, and returns
the options plus the prepared snapshots. That exercises the same load path a
real run uses.

Remote `url` repositories are declared by the spec but refused at snapshot
time in 0.4.0a with `repos.<name>.url: remote repository snapshots are not
available in 0.4.0a`. See [design-repo-context.md](design-repo-context.md).

## The HTTP candidate

`evalharness.adapters.candidates.http:build` implements the wire contract in
[`contract/candidate-v1.schema.json`](../contract/candidate-v1.schema.json).
The endpoint author's guide, including auth, retries, error mapping and the
usage-estimation rule, is [http-candidate.md](http-candidate.md).

Two capability fields exist because of this adapter:

- `requires_credential` is true unless `options.auth` is `none`. Validation
  refuses a candidate that needs a secret the vault cannot resolve, before any
  cell runs, and names `candidates[i].credential`.
- `max_attempts` is the total attempt budget for a typed transport failure.
  `candidate/8` publishes that contract. The HTTP candidate declares 3 (one
  call plus two retries); every other adapter keeps the default 2, so the
  issue #3 retry tests stay green.

The manifest row for the HTTP candidate carries `auth: none` and a
`base_url` pointing at the reference demo server's default loopback port. That
is a live process, not a mock: start `examples/http_candidate_server.py` before
`evalh conformance --all`, or the HTTP clauses fail with a connection error.
CI does exactly that in `.github/workflows/ci.yml`: it starts the reference
server and sets `EVALH_HTTP_CONFORMANCE_BASE_URL` for the conformance step. A
different host or port is configured through the same variable; an explicit
`base_url` on the manifest row wins over it. The test fixture that starts the
stub server replaces `base_url` with an ephemeral port for that run; the suite
already merges entry options into every clause.

The four provider rows follow the same rule with
`EVALH_PROVIDER_CONFORMANCE_BASE_URL` and `examples/provider_server.py` on
port 8770. They share one mutable options mapping, carry `auth: none` so the
suite needs no stored secret, and the test fixture points all four at one
ephemeral stub. [providers.md](providers.md) is the reference for the provider
matrix, auth shapes, prices and error mapping.

## The command

```text
evalh conformance --all
evalh conformance --adapter evalharness.adapters.judges.rule_based:build --kind judge
```

The command prints one line per clause, for example
`candidate/1: ok` or
`candidate/3: FAIL expected a Capabilities instance, got dict`.
Exit codes: 0 all clauses passed, 1 at least one clause failed (the same
code as a gate failure, so CI treats both as a failed gate), 2 usage error
or an adapter path that cannot be resolved.

## The stub pack

Every contract has a credential-free first-party implementation, and the
same registry production uses resolves them:

| Kind | Adapter | Purpose |
| --- | --- | --- |
| candidate | `candidates.stub:build` | scripted answer, synthetic usage |
| candidate | `candidates.stub:build_slow` | sleeps, for concurrency and queueing tests |
| candidate | `candidates.stub:build_failing` | always raises TransportError |
| candidate | `candidates.stub:build_fixed_prompt` | refuses prompt overrides |
| candidate | `candidates.http:build` | POSTs the v1 contract to another agent's endpoint |
| candidate | `candidates.openai_compat:build` | POSTs `/chat/completions` to an OpenAI-compatible provider |
| candidate | `candidates.anthropic:build` | POSTs `/v1/messages` to an Anthropic-style provider |
| judge | `judges.rule_based:build` | deterministic expected-substring verdict |
| judge | `judges.scripted:build` | deterministic rubric/claim verdict with estimated usage |
| judge | `judges.openai_compat:build` | structured JSON verdict from an OpenAI-compatible model |
| judge | `judges.anthropic:build` | structured JSON verdict from an Anthropic-style model |
| metric | `metrics.exact_match:build` | exact match against the expected answer |
| metric | `metrics.contains:build` | containment against the expected answer |
| metric | `metrics.citation_support:build` | judge-backed citation support; needs `options.judge` |
| metric | `metrics.judge_pass:build` | judge-backed correctness of the whole answer; needs `options.judge` |
| source | `sources.inline:build` | cases written in the spec |
| sink | `sinks.json_sink:build` | one directory per run and cell |

The no-key demo runs all four candidate shapes in one spec:

```bash
python -m evalharness run examples/specs/stub-pack.yaml
```

It exits 0. The always-failing cell is recorded with `status=error`
(so the run status is `partial`), while the gate still sees the
six passing checks and reports `pass`.

## Decisions later tickets must keep

- Clause identifiers are append-only. Add a clause, never renumber one.
- A candidate's retry budget comes from `capabilities.max_attempts`, not from a
  fixed constant in the executor. A new adapter that needs more or fewer
  attempts declares it and `candidate/8` checks it.
- A new first-party adapter needs a manifest row and a green suite; the
  manifest test is the CI gate, not a convention.
- `Metric.check` is async. Adding a metric means implementing
  `async def check`; a caller awaits it. Do not add a synchronous
  compatibility shim, because two calling conventions would split the
  contract.
- A metric names its judge through `options.judge`; the scorer builds one
  fresh `JudgeSession` per judge per cell, so judge cost and tokens stay
  attributed to the triggering cell. Two metrics may name two judges.
- The metric conformance suite supplies a deterministic
  `conformance-judge` session, plus one for any judge name in the
  adapter's options, so a judge-backed metric is exercised without
  depending on a specific judge adapter.
- Conformance runs write only under `EVALH_HOME/conformance` and
  delete their scratch directory. A sink must still stay inside the
  artifacts root it is given.
- A judge declares `capabilities` exactly as a candidate does: a provider judge
  sets `requires_credential`, `requires_model` and `provider`, and a
  deterministic judge keeps the defaults so it needs no credential. Validation
  enforces those flags and names `judges[i].credential` or `judges[i].model`.
  The session passes the resolved credential and declared model through
  `JudgeRequest`, so a judge adapter never reads settings.
- A provider adapter declares its provider in `Capabilities.provider`. That
  string is the price key and the attribution key; an adapter that is not tied
  to one provider leaves it empty and lands in the provider-agnostic bucket.
