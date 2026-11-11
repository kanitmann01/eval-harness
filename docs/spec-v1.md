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
| repos | mapping name -> repository | no | {} | Local or HTTPS repositories a source may read. Each is snapshotted before the first cell. |
| prompts | mapping string -> string | no | {} | Named prompt overrides. A candidate names one under options.prompt. |
| contexts | mapping string -> list of chunks or selector | no | {} | Named context sets. A list is a fixed set; a mapping with adapter and options selects chunks per case. A candidate names one under options.context. |
| credentials | list of credential declarations | no | [] | Logical credential names. Secrets come from the encrypted vault (docs/credentials.md). |
| candidates | list of candidates | yes | none | At least one. |
| replicas | integer >= 1 | no | 1 | Repeats per candidate x case. |
| concurrency | integer >= 1 | no | 1 | Cells in flight at once. Latency is only comparable at 1. |
| budget_usd | number >= 0 | no | unset | The launch estimate is compared with it: above the cap the run is refused (exit 2). Mid-run, spend above it marks the remaining cells cancelled, sets status budget_exceeded and exits 3. See [runs.md](runs.md). |
| metrics | list of metrics | yes | none | At least one; keys are unique. |
| judges | list of judges | no | [] | Named judges a metric may name. |
| gates | list of gates | no | [] | Thresholds on metric aggregates, run-wide or for one named candidate. |
| prices | mapping model -> price | no | {} | Model-only price overrides, USD per million tokens. Every candidate model, and every declared judge model, needs a price from here, provider_prices, or the shipped table. |
| provider_prices | mapping provider -> model -> price | no | {} | Provider-specific price overrides; wins over prices and over the shipped table. |
| sink | mapping | no | JSON artifact sink | Optional sink override with adapter and options. |

## source

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| adapter | string | yes | Registered name or dotted path in module:attr form. |
| options | mapping | no | Adapter-specific. The inline source reads options.cases. |
| credential | string | no | A declared or stored credential name. Only valid when at least one repository declares an https url; used for the remote fetch. |

A case has case_id (required), prompt (required), expected (optional mapping),
rubric (optional string), tier (integer, default 1) and metadata (mapping).
Duplicate case ids fail validation.

## repos

Declare a local repository when a source should read cases or fixtures from a
snapshot rather than from inline values:

```yaml
repos:
  docs:
    root: fixtures/docs
    include: ["**/*.md", "**/*.jsonl"]
    exclude: ["drafts/"]
    max_files: 500

source:
  adapter: evalharness.adapters.sources.repo:build
  options:
    repo: docs
    cases: cases/questions.jsonl
    fixtures: fixtures
```

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| root | string | "." | Local path relative to the spec file. Must exist when the spec is validated. |
| url | string or null | null | HTTPS repository URL. Fetched shallowly into `$EVALH_HOME/repos/remotes/` before the snapshot. |
| ref | string or null | null | Branch or tag for a remote repository. Defaults to the remote HEAD. Rejected with a local root. |
| credential | string or null | null | Credential used as an HTTP authorization header for a remote repository. Rejected with a local root. |
| include | list of globs | ["**/*"] | Snapshot only matching paths. |
| exclude | list of globs | [".git/", "node_modules/", ".venv/", "__pycache__/", "*.pyc"] | Skip matching paths. |
| max_files | integer >= 1 | 2000 | Refuse a larger included set. |
| max_file_bytes | integer >= 1 | 1000000 | Refuse one larger file. |
| max_total_bytes | integer >= 1 | 20000000 | Refuse a larger included total. |
| follow_symlinks | boolean | false | Follow symlinks that stay inside the root; escaping symlinks are refused and a directory cycle is walked once. |
| binary | "skip" or "error" | "skip" | Skip NUL-byte files or fail on the first one. |

Exactly one location is allowed: omit root and set url, or keep root and leave
url unset. Names match [A-Za-z0-9][A-Za-z0-9._-]*. Globs are relative to the
repository root and may not escape it: an absolute path, drive letter, home
shortcut or ".." segment is a spec error naming the field. A root is relative
to the spec file and rejects an absolute path, drive letter or home shortcut;
".." is allowed so a spec can point at a sibling checkout, and the directory
must exist when the spec is loaded.

A remote repository is fetched once per URL and ref into
$EVALH_HOME/repos/remotes/<key>, shallow and detached at the fetched commit, then
cleaned. The credential is passed to git as an HTTP authorization header in the
process environment, never as an argument, and its secret is redacted from a
failure message. A missing secret fails before any cell with
`repos.<name>.credential`.

Before the first cell the harness copies every included file into
$EVALH_HOME/repos/<digest>/tree and writes a manifest beside it. The digest is
the SHA-256 of the sorted blob hash and path lines, so an unchanged tree reuses
the same directory. Only snapshot-relative paths, hashes, sizes and modes enter
the manifest; host paths and bytes never enter the database. The source receives
the prepared tree through RunContext.repos. The run records the source snapshot,
and a resume refuses a repository whose digest changed.

Snapshots are never removed automatically. `evalh repos gc --dry-run` lists the
digests no recorded run references; without the flag it removes exactly those.

## prompts and contexts

A prompt is a named string. A context is either a named list of chunks, each
with chunk_id and text, or a named selector:

~~~yaml
contexts:
  graph:
    adapter: evalharness.adapters.contexts.repo_graph:build
    options:
      repo: docs
      rank: pagerank
      max_chunks: 8
~~~

A list is fixed for every case. A selector is called once per candidate and
case with a RunContext carrying the prepared repositories and artifacts root, and
returns the chunks for that case. Its name and options enter the source snapshot;
each cell records the selected chunk ids, the selector name and a context digest.
A selector failure names `contexts.<name>.adapter`; a candidate that declares
supports_context_injection as false cannot be given the axis. The two shipped
selectors are [repo_graph and artifact_graph](adapters.md#the-context-selectors).

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
the vault's credential metadata (names, disabled flag and test history), never its
secrets.

A stored credential that is disabled is a spec error on the reference
(`candidates[i].credential` or `judges[i].credential`) in both the console and
the CLI. The console also refuses a credential that has never passed a test,
because the browser is where a key is selected; the message names the field and
the `test it at /credentials` fix. A name only declared in the spec and not
stored keeps its old behaviour, which is what lets a non-credential adapter run
without a vault entry. See [credentials.md](credentials.md) and
[console.md#credentials](console.md#credentials).

There is a stronger rule for a candidate or judge whose adapter needs a secret,
such as the HTTP candidate with `auth: bearer` or `auth: header`, or either
provider adapter. Declaring the name in the spec is not enough: the name must be
**stored in the vault**, because a spec declaration cannot supply a secret.
Validation fails before any cell and names `candidates[i].credential` or
`judges[i].credential`. An adapter with `auth: none` needs no credential, and
may omit the field entirely. See [http-candidate.md](http-candidate.md) and
[providers.md](providers.md) for the auth shapes.

## candidates

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| name | string | yes | Unique. Recorded on every cell. |
| adapter | string | yes | Registered name or module:attr dotted path. |
| credential | string | no | A name declared under credentials or stored in the vault. Optional only when the adapter needs no secret, for example an HTTP candidate with `auth: none`. |
| model | string | yes | Must have a resolved price (see prices). A provider adapter uses it as the model it calls and reports its provider's price. |
| options | mapping | no | Adapter options. prompt, context and answers are the ones this ticket uses. The HTTP candidate's options are base_url, path, timeout_s, auth and header_name; the provider candidates add system, temperature, max_tokens and headers. |

The candidate name is the identity passed to its factory. Prompt and context
are ambient: the candidate reads them from RunContext, not from settings.

## metrics, judges and gates

A metric has a unique key, an adapter and options. A metric option named judge
must match a declared judge name. The adapter must return a metric whose key is
the spec key. `Metric.check` is async, so a judge-backed metric can await its
judge; [judges.md](judges.md) covers the wiring, hashing and cost split.

A judge has a unique name, an adapter, an optional credential and an optional
`model`. When `model` is set it needs a resolved price and it is the key used
to price the judge's tokens; when it is omitted the judge costs zero. A judge
adapter that declares `requires_model` (both provider judges do) must be given a
model, and validation names `judges[i].model` when it is missing. A judge
adapter that declares `requires_credential` must name a **stored** credential;
validation names `judges[i].credential` otherwise. Everything else that changes
judge behaviour (temperature, instructions, claims) belongs under `options` and
is covered by the check's `judge_config_hash`.

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

USD per million tokens. Every price is resolved from three sources, most
specific first: a spec `provider_prices` entry, a spec `prices` entry, then the
shipped table at `config/prices.yaml` (packaged as
`evalharness/config/prices.yaml`), keyed by provider then model.

~~~yaml
prices:
  stub-1: {input: 0.15, output: 0.60, tool: 0.0}
provider_prices:
  openai-compatible:
    gpt-4o-mini: {input: 0.10, output: 0.40}
~~~

`prices` is the model-only syntax every earlier spec uses and still wins over a
shipped default; `provider_prices` lets the same model carry two prices at two
providers and wins over both. A declared model with no price anywhere is a
validation error naming `prices.<model>` and, for a provider adapter, the
provider it was looked up under, including the model of a declared judge.

The resolved table is snapshotted on the run as version 2, nested by provider;
the empty provider key is the provider-agnostic bucket. Cost is split into
answer, judge and tool parts; every cell stores `answer_cost_usd`,
`judge_cost_usd`, `tool_cost_usd` and their sum in `cost_usd`, plus the judge
prompt and completion tokens it triggered. Judge cost is charged to the cell
that triggered it, and separately attributed to the judge's provider in the
run's `provider_costs` rows. A candidate records `usage_source` as provider or
estimated; a verdict records its own. [providers.md](providers.md) has the full
price and attribution contract.

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
secret, not just a declared name), `requires_model` (a judge that calls a
provider must be given a model), `provider` (the pricing and attribution
identity) and `max_attempts` (the total attempt budget for a typed transport
failure; the default 2 means one call plus one retry, and the HTTP and provider
adapters declare 3). Judges expose the same declaration; a deterministic judge
keeps the defaults. See [adapters.md](adapters.md).

## One validator

`evalh validate`, `evalh run` and the browser spec editor call the same loader
in `core/spec.py`: `parse_spec` then `validate_spec`, wrapped for the HTTP
surface as `validate_yaml_text`. The browser therefore reports the same field
name, rejected value, reason and fix that the CLI prints. The one extra console
rule is credential usability: the editor passes the vault's states and requires a
verified credential, so a spec naming a never-tested credential is refused there
but still validates on the command line, where a run surfaces the provider's
rejection. A disabled credential is refused by both. See
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

evalh repos gc [--dry-run]          prune snapshots no recorded run references

evalh credentials add NAME --provider P [--secret-stdin | --secret-env VAR]
evalh credentials list
evalh credentials test NAME [--verify-url URL]
evalh credentials delete NAME [--yes]
~~~

After the run table, a cost line prints the split from the stored cells:
`cost: answer $x, judge $y, tools $z`. One provider line then prints the
cells, candidate tokens, judge tokens and total cost per provider, so a
multi-provider run attributes its spend without opening the database. The
aggregates table then prints candidate, metric, tier (`all` for the overall
row), n, the value as a percentage or `undefined`, the 95 percent Wilson
interval, pass@k and pass^k.
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
