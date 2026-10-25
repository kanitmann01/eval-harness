# eval-harness

Adapter-based evaluation harness. The harness is the product; pipelines, agents and corpora are candidates behind adapters.

Status: walking skeleton. One command validates a spec, expands it into cells, executes
them sequentially, and writes a run with cells, checks and artifact paths that read back.
Console, vault and intervals follow. The spec of record is issue #1 in this repository.

## What it will be

A versioned YAML **spec** declares the source of cases, the **candidates** (each with its own credential, provider, model, prompt variant and context setting), the **metrics** to score, per-model **prices**, and the **gates** that decide pass or fail. A planner expands the spec into **cells**, an executor runs them with bounded concurrency and a budget, a scorer produces per-cell **checks** and per-candidate **aggregates** with intervals, and a console serves the results: heatmap, per-cell evidence, and a compare view with deltas and cost per correct answer.

Credentials are BYOK. One container per person, keys verified on save, encrypted at rest, masked everywhere.

## Start here

- `docs/design-adapter-harness.md` — the design note: layering, protocols, spec schema, credential and cost model, executor semantics, storage schema, migration phases.
- `docs/spec-v1.md` — the spec schema as implemented, field by field.
- Issue #1 — the spec: problem, solution, 58 user stories, implementation decisions, testing decisions, out of scope.

## Quick start

~~~bash
python -m pip install -e .
python -m evalharness validate examples/specs/smoke.yaml
python -m evalharness run examples/specs/smoke.yaml --dry-run
python -m evalharness run examples/specs/smoke.yaml
~~~

The smoke spec needs no keys and no network. It declares a stub candidate and a
stub price, expands to two cells (one per prompt variant), and writes artifacts
under `$EVALH_HOME/artifacts/<run_id>/<cell_id>/` with only paths in the database.
Set `EVALH_HOME` to choose where the SQLite database and artifacts live;
`EVALH_DATABASE_URL` and `EVALH_ARTIFACTS_DIR` override them individually.
Exit codes are 0 success, 1 gate failure, 2 spec error, 3 budget abort.

## Provenance

Split out of `minirag`, which stays a RAG project and becomes the first example candidate. The design note was written against that tree, so its `app/` module map describes `minirag` until extraction lands; nothing here depends on it.
