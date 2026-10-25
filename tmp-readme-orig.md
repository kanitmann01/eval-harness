# eval-harness

Adapter-based evaluation harness. The harness is the product; pipelines, agents and corpora are candidates behind adapters.

Status: design. No code yet. The spec of record is issue #1 in this repository.

## What it will be

A versioned YAML **spec** declares the source of cases, the **candidates** (each with its own credential, provider, model, prompt variant and context setting), the **metrics** to score, per-model **prices**, and the **gates** that decide pass or fail. A planner expands the spec into **cells**, an executor runs them with bounded concurrency and a budget, a scorer produces per-cell **checks** and per-candidate **aggregates** with intervals, and a console serves the results: heatmap, per-cell evidence, and a compare view with deltas and cost per correct answer.

Credentials are BYOK. One container per person, keys verified on save, encrypted at rest, masked everywhere.

## Start here

- `docs/design-adapter-harness.md` — the design note: layering, protocols, spec schema, credential and cost model, executor semantics, storage schema, migration phases.
- Issue #1 — the spec: problem, solution, 58 user stories, implementation decisions, testing decisions, out of scope.

## Provenance

Split out of `minirag`, which stays a RAG project and becomes the first example candidate. The design note was written against that tree, so its `app/` module map describes `minirag` until extraction lands; nothing here depends on it.
