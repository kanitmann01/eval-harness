# eval-harness

Adapter-based evaluation harness. The harness is the product; pipelines, agents and corpora are candidates behind adapters.

Status: walking skeleton plus the encrypted credential vault, the public adapter
contract suite and the read-only console. One command validates a spec, expands it into
cells, executes them with bounded concurrency, and writes a run with cells, checks,
aggregates with intervals, and artifact paths that read back. Provider keys are added,
listed, verified and deleted from the command line, encrypted at rest and redacted
everywhere. The console serves the run list, the candidate by case heatmap and per-cell
evidence; the compare view follows.
The spec of record is issue #1 in this repository.

## What it will be

A versioned YAML **spec** declares the source of cases, the **candidates** (each with its own credential, provider, model, prompt variant and context setting), the **metrics** to score, per-model **prices**, and the **gates** that decide pass or fail. A planner expands the spec into **cells**, an executor runs them with bounded concurrency and a budget, a scorer produces per-cell **checks** and per-candidate **aggregates** with intervals, and a console serves the results: heatmap, per-cell evidence, and a compare view with deltas and cost per correct answer.

Credentials are BYOK. One container per person, keys verified on demand, encrypted at rest, masked everywhere.

## Start here

- `docs/design-adapter-harness.md` — the design note: layering, protocols, spec schema, credential and cost model, executor semantics, storage schema, migration phases.
- `docs/spec-v1.md` — the spec schema as implemented, field by field.
- `docs/adapters.md` — the five adapter contracts, clause numbering, the manifest and the conformance command.
- `docs/credentials.md` — BYOK flow, master key lifecycle, what is stored, redaction guarantees, verification request.
- `docs/console.md` — the page map, JSON API, SSE contract and how to serve a run in a browser.
- Issue #1 — the spec: problem, solution, 58 user stories, implementation decisions, testing decisions, out of scope.

## Quick start

~~~bash
python -m pip install -e .
python -m evalharness validate examples/specs/smoke.yaml
python -m evalharness run examples/specs/smoke.yaml --dry-run
python -m evalharness run examples/specs/smoke.yaml
python -m evalharness run examples/specs/stub-pack.yaml
python -m evalharness serve
python -m evalharness conformance --all
~~~

The smoke spec needs no keys and no network. It declares a stub candidate and a
stub price, expands to two cells (one per prompt variant), and writes artifacts
under `$EVALH_HOME/artifacts/<run_id>/<cell_id>/` with only paths in the database.
Set `EVALH_HOME` to choose where the SQLite database and artifacts live;
`EVALH_DATABASE_URL` and `EVALH_ARTIFACTS_DIR` override them individually.
The stub-pack demo runs all four candidate stubs (scripted, slow, prompt-fixed
and always-failing) plus the deterministic judge in one spec. It exits 0, records
the always-failing cells as errors, and needs no credentials. The
`evalh conformance --all` command runs the same public contract suite as CI and
prints one line per clause.

## Exit codes and gates

Exit codes are the automation contract, defined once in
[`exit_codes.py`](src/evalharness/exit_codes.py):

| Code | Meaning |
| --- | --- |
| 0 | Success: the run finished and no gate failed. A run whose cells errored but whose gates passed still exits 0, and a cancelled run exits 0 unless a gate failed. |
| 1 | Gate failure: the run finished and at least one spec gate failed. A gate failure also wins over cancellation. |
| 2 | Spec error: the spec is invalid or unreadable, nothing was executed. |
| 3 | Budget abort: the run stopped because spend exceeded `budget_usd`. It wins over a gate. |

A gate is a threshold on an aggregate, evaluated after the run. Omit
`candidate` for the run-wide figure, or name one candidate to gate only that
candidate's aggregate:

~~~yaml
gates:
  - metric: exact_match
    min: 0.90
  - metric: exact_match
    candidate: stub-concise
    max: 0.95
~~~

Each gate result is stored with its metric, candidate (or none for run-wide),
comparison (`>=` or `<=`), threshold, observed value and n, so a red build says
which kind of failure it was. A metric with no samples is undefined, so its
gate fails with `no samples` in the detail. Gate failure always exits 1; there
is no flag to turn it off. See [docs/runs.md](docs/runs.md) for the full
contract and [examples/specs/gated.yaml](examples/specs/gated.yaml) for a spec
that passes its gate without a paid credential. A run still needs the vault
master key described below; CI generates an ephemeral one for its disposable
database.

## Credentials

The vault needs a master key. On the first command that opens it, a missing key is
generated, printed exactly once with an instruction to store it, and the command
exits 2. Re-run with it in the environment:

~~~bash
evalh credentials add openai-prod --provider openai --secret-stdin
evalh credentials list
evalh credentials test openai-prod --verify-url https://api.openai.com/v1/models
evalh credentials delete openai-prod
~~~

`list` shows name, provider, creation time, last verification and last error, never
the secret. `test` makes one request and exits 1 on a rejected key, recording the
provider message as `last_error`. Keys are Fernet-encrypted at rest; a database dump
contains ciphertext only. Every known secret is replaced with
`[redacted:<name>]` in logs, events, artifacts and cell records. See
[docs/credentials.md](docs/credentials.md) for the master key state machine,
verification contract and rotation.

## Provenance

Split out of `minirag`, which stays a RAG project and becomes the first example candidate. The design note was written against that tree, so its `app/` module map describes `minirag` until extraction lands; nothing here depends on it.
