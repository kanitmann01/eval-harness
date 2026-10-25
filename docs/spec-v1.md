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
| credentials | list of credential declarations | no | [] | Logical credential names. Secrets come from the environment now and from the encrypted vault in issue #6. |
| candidates | list of candidates | yes | none | At least one. |
| replicas | integer >= 1 | no | 1 | Repeats per candidate x case. |
| concurrency | integer >= 1 | no | 1 | Cells in flight at once. Latency is only comparable at 1. |
| budget_usd | number >= 0 | no | unset | The executor marks remaining cells cancelled once spend exceeds it and exits 3. |
| metrics | list of metrics | yes | none | At least one; keys are unique. |
| judges | list of judges | no | [] | Named judges a metric may name. |
| gates | list of gates | no | [] | Thresholds on run-wide metric proportions. |
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

The spec declares names and providers only. The secret is resolved at run time
from CredentialStore (EVALH_CREDENTIAL_<NAME> now, the vault in issue #6). A
candidate or judge that names an undeclared credential fails validation.

## candidates

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| name | string | yes | Unique. Recorded on every cell. |
| adapter | string | yes | Registered name or module:attr dotted path. |
| credential | string | yes | A name declared under credentials. |
| model | string | yes | Must have an entry under prices. |
| options | mapping | no | Adapter options. prompt, context and answers are the ones this ticket uses. |

The candidate name is the identity passed to its factory. Prompt and context
are ambient: the candidate reads them from RunContext, not from settings.

## metrics, judges and gates

A metric has a unique key, an adapter and options. A metric option named judge
must match a declared judge name. The adapter must return a metric whose key is
the spec key. A gate names a declared metric and sets min, max, or both; the
run-wide proportion for that metric is compared against them. A latency gate
with concurrency greater than 1 prints a warning because latency then measures
queueing, not the model.

## prices

USD per million tokens, keyed by model:

~~~yaml
prices:
  stub-1: {input: 0.15, output: 0.60, tool: 0.0}
~~~

Missing prices are a validation error naming prices.<model>. The resolved table
is snapshotted on the run. Cost is split into answer, judge and tool parts
internally; this ticket stores the total per cell and records usage_source as
provider or estimated.

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

## CLI contract

~~~text
evalh validate SPEC                 validate and print one line, exit 0 or 2
evalh run SPEC --dry-run            print cells, per-candidate counts, estimate; execute nothing
evalh run SPEC                     execute, print a run table and aggregates, exit 0
evalh run SPEC --db URL --artifacts DIR
~~~

After the run table, the aggregates table prints candidate, metric, tier
(`all` for the overall row), n, the value as a percentage or `undefined`,
the 95 percent Wilson interval, pass@k and pass^k. See
[statistics.md](statistics.md) for the formulas and for why an empty
proportion is undefined rather than zero.

Exit codes: 0 success, 1 gate failure, 2 spec error, 3 budget abort.
Validation failures print spec error: <field>=<value>: <reason>. Fix: <fix>.
