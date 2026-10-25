# Spec schema, version 1

One YAML file declares a whole evaluation. It is the source of truth: a run
stores a snapshot of it and the resolved price table, so an old run keeps its
old meaning. Secrets never appear in a spec; only logical credential names do.

~~~yaml
spec_version: 1
name: smoke
description: Two stubs, one case, two prompt variants.
seed: 7

source:
  adapter: evalharness.adapters.sources.inline:build
  options:
    cases:
      - case_id: case-1
        prompt: What is 6 times 7?
        expected: {answer: "42"}
        tier: 1

prompts:
  concise: Answer with digits only, no other words.
  explained: Answer with the number and one short clause.

contexts:
  none: []
  policy:
    - chunk_id: policy-1
      text: The answer must be a bare number.

credentials:
  - name: stub
    provider: none

candidates:
  - name: stub-concise
    adapter: evalharness.adapters.candidates.stub:build
    credential: stub
    model: stub-1
    options:
      prompt: concise
      context: none
  - name: stub-explained
    adapter: evalharness.adapters.candidates.stub:build
    credential: stub
    model: stub-1
    options:
      prompt: explained
      context: policy

replicas: 1
concurrency: 1
budget_usd: 1.00

metrics:
  - key: exact_match
    adapter: evalharness.adapters.metrics.exact_match:build
    options: {}

judges: []
gates: []

prices:
  stub-1:
    input: 0.15
    output: 0.60
~~~

Unknown fields are rejected so a typo fails validation instead of being
silently ignored.

## Top level

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| spec_version | integer | yes | none | Must be 1. Any other value exits 2. |
| name | string | yes | none | Human name, stored on runs and specs. |
| description | string | no | "" | Free text. |
| seed | integer | no | 0 | Seeds cell ordering and is passed to every candidate. |
| source | mapping | yes | none | Where the cases come from. |
| prompts | mapping string -> string | no | {} | Named prompt overrides. A candidate names one under options.prompt. |
| contexts | mapping string -> list of chunks | no | {} | Named context sets. A candidate names one under options.context. |
| credentials | list of credential declarations | no | [] | Logical credential names. Secrets come from the encrypted vault (docs/credentials.md). |
| candidates | list of candidates | yes | none | At least one. |
| replicas | integer >= 1 | no | 1 | Repeats per candidate x case. |
| concurrency | integer >= 1 | no | 1 | Cells in flight at once. Latency is only comparable at 1. |
| budget_usd | number >= 0 | no | unset | The launch estimate is compared with it: above the cap the run is refused (exit 2). Mid-run, spend above it marks the remaining cells cancelled, sets status budget_exceeded and exits 3. See [runs.md](runs.md). |
| metrics | list of metrics | yes | none | At least one; keys are unique. |
| judges | list of judges | no | [] | Named judges a metric may name. |
| gates | list of gates | no | [] | Thresholds on metric aggregates, run-wide or for one named candidate. |
| prices | mapping model -> price | no | {} | Every candidate model, and every declared judge model, needs a price. |
| sink | mapping | no | JSON artifact sink | Optional sink override with adapter and options. |

## source

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| adapter | string | yes | Registered name or dotted path in module:attr form. |
| options | mapping | no | Adapter-specific. The inline source reads options.cases. |

A case has case_id (required), prompt (required), expected (optional mapping),
rubric (optional string), tier (integer, default 1) and metadata (mapping).
Duplicate case ids fail validation.

## prompts and contexts

A prompt is a named string. A context is a named list of chunks, each with
chunk_id and text. They are core axes, not adapter flags: the planner puts the
resolved text and chunks in RunContext and records them on every cell in the
cell variant. A candidate that declares supports_prompt_override or
supports_context_injection as false cannot be given that axis, and validation
fails naming the candidate option.

## credentials

~~~yaml
credentials:
  - name: stub
    provider: none
~~~

The spec declares names and providers only; the secret is resolved at run time
from the encrypted vault. A candidate or judge credential is valid when it is
declared here **or** a credential with that name is stored in the vault:

~~~bash
evalh credentials add openai-prod --provider openai --secret-stdin
~~~

An undeclared name that is neither in the spec nor in the vault is a spec error
naming the field, the name, and the `evalh credentials add` fix. Validation reads
the vault's credential names (metadata only), never its secrets.

There is a stronger rule for a candidate whose adapter needs a secret, such as
the HTTP candidate with `auth: bearer` or `auth: header`. Declaring the name in
the spec is not enough: the name must be **stored in the vault**, because a spec
declaration cannot supply a secret. Validation fails before any cell and names
`candidates[i].credential`. A candidate with `auth: none` needs no credential,
and may omit the field entirely. See
[http-candidate.md](http-candidate.md) for the auth shapes.

## candidates

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| name | string | yes | Unique. Recorded on every cell. |
| adapter | string | yes | Registered name or module:attr dotted path. |
| credential | string | no | A name declared under credentials or stored in the vault. Optional only when the adapter needs no secret, for example an HTTP candidate with `auth: none`. |
| model | string | yes | Must have an entry under prices. |
| options | mapping | no | Adapter options. prompt, context and answers are the ones this ticket uses. The HTTP candidate's options are base_url, path, timeout_s, auth and header_name. |

The candidate name is the identity passed to its factory. Prompt and context
are ambient: the candidate reads them from RunContext, not from settings.

## metrics, judges and gates

A metric has a unique key, an adapter and options. A metric option named judge
must match a declared judge name. The adapter must return a metric whose key is
the spec key. `Metric.check` is async, so a judge-backed metric can await its
judge; [judges.md](judges.md) covers the wiring, hashing and cost split.

A judge has a unique name, an adapter, an optional credential and an optional
`model`. When `model` is set it needs a price and it is the key used to price
the judge's tokens; when it is omitted the judge costs zero. Everything else
that changes judge behaviour (temperature, instructions, claims) belongs under
`options` and is covered by the check's `judge_config_hash`.

~~~yaml
metrics:
  - {key: citation_support, adapter: evalharness.adapters.metrics.citation_support:build,
     options: {judge: strict-judge}}
judges:
  - name: strict-judge
    adapter: evalharness.adapters.judges.scripted:build
    model: scripted-strong
    options: {model: scripted-strong, instructions: Require every claim.}
~~~

A gate names a declared metric and sets min, max, or both. It optionally names
a declared candidate: without `candidate` the gate reads the run-wide
aggregate for the metric, and with `candidate` it reads that candidate's
overall aggregate row only. A metric and candidate pair may have at most one
gate; the same metric may be gated run-wide and per candidate. A latency gate
with concurrency greater than 1 prints a warning because latency then measures
queueing, not the model.

~~~yaml
gates:
  - metric: exact_match
    min: 0.90
  - metric: exact_match
    candidate: stub-concise
    max: 0.95
~~~

Gate results store metric, candidate, comparison, threshold, observed value
and n. A gate on a metric with no samples fails with `no samples` in its
detail. See [runs.md](runs.md) for the exit-code contract.

## prices

USD per million tokens, keyed by model:

~~~yaml
prices:
  stub-1: {input: 0.15, output: 0.60, tool: 0.0}
~~~

Missing prices are a validation error naming prices.<model>, including the model
of a declared judge. The resolved table is snapshotted on the run. Cost is split
into answer, judge and tool parts; every cell stores `answer_cost_usd`,
`judge_cost_usd`, `tool_cost_usd` and their sum in `cost_usd`, plus the judge
prompt and completion tokens it triggered. Judge cost is charged to the cell
that triggered it. A candidate records `usage_source` as provider or estimated;
a verdict records its own.

## Adapter factory contract

Every adapter path resolves to a callable with two arguments:
identity and options. Identity is the candidate name, the judge name, the
metric key, or the literal source or sink for those two adapters. Options is
the mapping written under the spec entry (empty when omitted). The factory
returns an object that satisfies its protocol; candidates also expose
Capabilities. A factory must be safe to build during validation, because
validation builds candidates, metrics and judges to inspect capabilities and
keys before anything runs. The executor builds one candidate instance per
worker and reuses it for that worker's cells.

`Capabilities` also declares `requires_credential` (validation needs a stored
secret, not just a declared name) and `max_attempts` (the total attempt budget
for a typed transport failure; the default 2 means one call plus one retry, and
the HTTP candidate declares 3). See [adapters.md](adapters.md).

## One validator

`evalh validate`, `evalh run` and the browser spec editor call the same loader
in `core/spec.py`: `parse_spec` then `validate_spec`, wrapped for the HTTP
surface as `validate_yaml_text`. The browser therefore reports the same field
name, rejected value, reason and fix that the CLI prints, and a document that
the console refuses to save is a document `evalh run` would refuse too. See
[console.md](console.md#spec-editor) for the editor routes.

## CLI contract

~~~text
evalh validate SPEC                 validate and print one line, exit 0 or 2
evalh run SPEC --dry-run            print cells, per-candidate counts, estimate; execute nothing
evalh run SPEC                     execute, print a run table and aggregates, exit 0
evalh run SPEC --db URL --artifacts DIR
evalh run SPEC --override-budget USD   replace the spec budget for this run and record it
evalh run SPEC --cancel-after N     cancel once N cells have completed, between cells
evalh run SPEC --resume RUN_ID      continue an unfinished run into the same record

evalh credentials add NAME --provider P [--secret-stdin | --secret-env VAR]
evalh credentials list
evalh credentials test NAME [--verify-url URL]
evalh credentials delete NAME [--yes]
~~~

After the run table, a cost line prints the split from the stored cells:
`cost: answer $x, judge $y, tools $z`. The aggregates table then prints
candidate, metric, tier (`all` for the overall row), n, the value as a
percentage or `undefined`, the 95 percent Wilson interval, pass@k and pass^k.
See [statistics.md](statistics.md) for the formulas and for why an empty
proportion is undefined rather than zero.

Exit codes: 0 success, 1 gate failure (or a rejected credential from
`credentials test`), 2 spec error, 3 budget abort. A run whose cells errored
but whose gates passed exits 0. Validation failures print spec error:
<field>=<value>: <reason>. Fix: <fix>. A cancelled run exits 0 unless a gate
failed or the budget abort already decided the code. [runs.md](runs.md) is the
reference for budget, cancel, resume, the state x exit-code matrix, how gates
are evaluated against aggregates and the one-run-at-a-time rule. The credential
vault, its master key and its redaction guarantees are in
[docs/credentials.md](credentials.md).
