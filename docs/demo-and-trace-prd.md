# PRD: demo library, trace and first-visit tour

Status: approved for build, 2026-11-11
Owner: Kanit
Surface: evalharness console

## Problem

The console can create and launch evaluations, but a new operator cannot see the
engine work before writing a spec. There is no demo, no side-by-side response
view and no trace. The first useful screen is a YAML editor, so the product
asks a stranger to author the hardest artifact first.

The run detail page is a matrix, not an explanation. It shows a heatmap and one
cell at a time. It does not put two candidate answers next to each other, does
not show the effective prompt that produced each answer, and does not make the
metric and gate arithmetic legible in one read.

## Users

- A new operator evaluating the harness for the first time.
- An evaluator comparing two prompts, two contexts or two models.
- A stakeholder who wants to see why a run passed or failed without reading the
  CLI output or the database.

## Goals

1. A demo library with four scripted, offline walkthroughs that need no
   provider key, no spend and no YAML authoring.
2. A trace page that puts candidate answers side by side for each case, with the
   effective prompt, prompt and context variants, checks, evidence, usage, cost,
   latency and a word-level diff for two candidates.
3. A replay of a recorded demo run that simulates progress and log timing
   without calling a model again.
4. A first-visit tour of five coach marks: Runs, Demos, New comparison, Specs and
   Credentials.
5. Every surface works without JavaScript; the replay and tour are progressive
   enhancements.

## Non-goals

- Live provider calls inside the demo library. The demos are stubs by design.
- A new execution path, scorer, adapter or runtime dependency.
- Editing a stored run, cell or check from the trace.
- Screenshots or a marketing site. The console is the product.

## Success criteria

- GET /demos lists four scenarios and the seven-stage pipeline.
- POST /demos/{slug}/run copies the bundled spec into the specs directory only
  when absent, launches through the existing console orchestration on the first
  click, waits for the run to finish and redirects to
  /runs/{id}/trace?replay=1. Later clicks replay the recorded run.
- The trace shows both effective prompts, both answers, every check with its
  evidence, the gate result and a word diff when exactly two candidates ran.
- Repeat demo plays are deterministic, replay the same recorded run and cost
  nothing; no provider key is read.
- A browser's first visit shows the tour; a returning visit does not.
- python -m pytest stays green at 100% line coverage.

## Risks and mitigations

- Scripted demos mistaken for live model behaviour. Every card and the page
  callout says "offline stubs", and the trace says the answers are scripted.
- Replay mistaken for a live run. The replay panel says "recorded run" and
  "timings simulated"; the trace below is the stored result.
- Demo spec drift. One catalog owns the slug, title, spec file and expected read;
  a test parses every bundled spec and checks the catalog matches.
- Token burn. Demo runs use the stub adapter only; the console never reads a
  provider credential for a demo.