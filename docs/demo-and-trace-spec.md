# Spec: demo library, trace and first-visit tour

Status: approved for build, 2026-11-11
Source PRD: docs/demo-and-trace-prd.md

## Problem Statement

A new operator lands on a run list that is empty, then on a spec editor that
asks for YAML. There is no way to see a real run, compare two answers side by
side or understand how a check becomes a gate verdict without authoring a spec
and knowing which adapter to call. The console needs a guided, zero-cost path
from "show me" to "I understand the engine" to "I can edit this spec".

## Solution

Three connected surfaces plus one onboarding layer:

1. GET /demos is a library of four scripted walkthroughs. Each card states the
   question, what varies, what to look for and the expected read. Run demo
   copies the bundled YAML into the specs directory on first use. The first
   click launches the spec through the existing one-run-at-a-time console path,
   waits for the run to finish, and redirects to the trace with replay on.
   Every later click replays that recorded run without executing it. Open spec
   copies it and opens the existing editor.
2. GET /runs/{run_id}/trace is a read-only, per-case view. Candidate columns sit
   side by side. Each column shows the effective prompt (the prompt override when
   the spec set one, otherwise the case prompt), the prompt and context variant,
   the full answer, every check with its metric, pass flag, score, reason and
   raw evidence, usage, cost, latency, provider/model and an error when the cell
   failed. A two-candidate case gets a word-level diff in a details element.
3. The demo run redirect carries replay=1. The trace page renders a replay panel
   that reads the stored events as JSON and plays them with simulated delays. It
   never calls a model. Without JavaScript the panel is hidden and the trace is
   still complete.
4. Every page renders a hidden tour section with five steps. A small tour.js
   positions a bubble against the nav and page targets, moves focus to its own
   Next button, supports Escape and arrow keys, restores the prior focus, and
   writes evalh:console-tour:v1 to localStorage. A later visit removes the
   section before showing it. Without JavaScript the section stays hidden.

## User Stories

- As a new operator, I open Demos, press Run demo on "Prompt choice", and land on
  a trace that shows the strict answer 42 passing exact_match and the explained
  answer failing it, while contains passes both.
- As an evaluator, I open a trace from a run and see the effective prompt for
  each candidate, so I know the prompt variant is not just a label.
- As a stakeholder, I open a trace and see the failed gate, the checks behind it
  and the cost, without reading the database.
- As a returning operator, I do not see the tour again.
- As an operator without JavaScript, I still see the demos, the trace and every
  check; only the replay animation and coach marks are absent.

## Implementation Decisions

### Demo catalog and bundled specs

- New package evalharness.demos with catalog.py and four YAML specs:
  prompt-choice.yaml, context-grounding.yaml, model-tradeoff.yaml and
  failure-path.yaml.
- DemoScenario is a frozen slots dataclass: slug, title, question, varies,
  look_for, expected, spec_file, cost_note, duration_note.
- DEMOS is a tuple in display order. find_demo returns None for an unknown slug.
- materialize(settings, scenario) writes demo-<slug>.yaml through SpecStore only
  when absent, so a user edit survives. The bundled bytes are read with
  importlib.resources from evalharness.demos.
- materialized_slugs(settings) returns the slugs whose demo file exists.
- pyproject package-data adds evalharness.demos = ["*.yaml"].

### Demo scenarios

- prompt-choice: one case, two stub candidates, same model. strict answers 42 and
  explained answers "42 because 6 times 7 is 42". Metrics exact_match and
  contains. Gate exact_match min 0.90 fails because the pooled rate is 0.5.
  Lesson: the gate, not the model, defines "better".
- context-grounding: one refund-policy case. grounded has the policy chunk and
  answers "30 days"; ungrounded has no context and answers "I do not have the
  policy text." Gate exact_match min 1.0 fails. Lesson: context is visible and
  the gate catches an ungrounded answer.
- model-tradeoff: one case, cheap and strong stubs both answer 42. The cheap
  candidate uses 40/12 tokens at 0.10/0.40 per Mtok; strong uses 420/64 at
  3.00/15.00. Gate passes. Lesson: correctness alone does not pick a winner.
- failure-path: two cases, three stubs. ok and slow answer 42; failing raises a
  transport error. Gate exact_match min 1.0 still passes because the errored
  cell records no check. Lesson: a failed candidate is recorded, not hidden, and
  a gate only sees checks.

### Routes

- GET /demos, name demo_list, HTML.
- POST /demos/{slug}/run, name demo_run, 303 to
  /runs/{run_id}/trace?replay=1. The route first reuses the newest finished run
  of demo-<slug>; only when none exists does it materialize and launch, then
  wait for the run row to finish before redirecting. Unknown slug is 404.
  Before launching, a demo spec whose candidate or judge no longer points at
  the stub or scripted adapter is refused with 400: Run demo never spends, and
  a live spec must be launched deliberately from Launch. LaunchError re-renders
  /demos with the error and its status (400 refused, 409 busy).
- POST /demos/{slug}/open, name demo_open, 303 to
  /specs/demo-<slug>/edit?copied=demo.
- GET /runs/{run_id}/trace, name run_trace, HTML. Unknown run is the existing
  not_found page with 404.

### Trace view model

New evalharness.api.trace_views, pure and testable without HTTP:

- TraceCheck(metric, passed, score, reason, evidence)
- TraceCell(cell_id, candidate, case_id, replica, status, answer, error,
  effective_prompt, prompt_variant, context_name, context_chunks,
  prompt_tokens, completion_tokens, tool_calls, wall_ms, cost_usd,
  usage_source, provider, model, checks, artifact_url)
- TraceColumn(candidate, cells, checks_passed, checks_total, errored)
- TraceCase(case_id, prompt, columns, answers_match, candidate_count,
  diff_segments, diff_left, diff_right)
- TracePage(candidates, cases, cells_recorded, cells_total, running, has_cells)
- build_trace(read: RunRead) uses the spec snapshot for case and candidate
  order, falls back to the stored cells when the snapshot is absent, and keeps
  multiple replicas as multiple cell blocks in one column.
- A word diff uses difflib.SequenceMatcher over answer.split() and returns
  same/add/del segments, so the template escapes every token.
- The trace route reuses load_run_read, run_verdict and the existing render
  helper. It adds no repository method.

### Replay

- The trace template emits the stored events as JSON in a script element with
  id replay-events.
- New static trace.js reads the JSON, renders one log line per event, updates a
  progress bar with transform: scaleX(ratio), and uses setTimeout delays. It
  honours prefers-reduced-motion by rendering every event immediately.
- The panel says "recorded run" and "timings simulated". The trace below is the
  actual stored result. No model call and no token spend on replay.
- A second Run demo click on the same slug resolves to the same finished run and
  does not execute the spec again, so replay can never spend a token twice.

### First-visit tour

- base.html includes _tour.html on every page; the markup stays hidden and
  tour.js decides whether this browser has already seen it.
- _tour.html is a hidden section with five data-tour-step list items targeting
  the Runs nav link, the Demos nav link, the Specs nav link, the New comparison
  action and the Credentials nav link.
- New static tour.js positions a bubble, moves focus to Next, supports Escape,
  ArrowLeft and ArrowRight, writes evalh:console-tour:v1=1 to localStorage, and
  restores the prior focus. It never blocks the page without JavaScript.
- The tour is five steps, not four or six. Seen state is a browser preference,
  not a server record: no cookie and no request is made.

### Styling and discovery

- New CSS classes at the end of console.css. No new animation or transition
  declarations, so the existing reduced-motion guard stays complete and the
  two 44px touch-target blocks stay exactly two.
- Demos nav sits between Runs and Specs. Section detection adds /demos.
  The tour hooks on Runs, Specs, Demos, New comparison and Credentials exist on
  every page, so the tour can start wherever the operator lands.
- The run detail header gains a Trace link. The trace header links back to the
  run, Decision, Compare and Receipt.

## Testing Decisions

The highest existing seam is HTTP through httpx.ASGITransport against
create_app, with real runs seeded by evalharness.cli.main. No new seam is
introduced. Tests drive the console the way an operator does.

- tests/test_demo_catalog.py: every bundled spec parses, validates, has the
  catalog slug, and materialize is idempotent and preserves user text.
- tests/test_api_demos.py: GET lists four cards and the pipeline; each Run demo
  returns 303, the run finishes, and the trace has the expected answers and
  checks; a second Run demo on the same slug redirects to the same run and adds
  no second run; Open spec lands on the editor with the copied YAML; unknown
  slug is 404; an invalid materialised spec re-renders with 400; no provider key
  is read.
- tests/test_api_trace.py: two candidates show both effective prompts, both
  answers, check evidence, gate result and a word diff; one candidate has no
  diff; an unknown run is 404; a running run shows the progress note and is
  cancelled by the test.
- tests/test_api_tour.py: every page renders the hidden tour hooks and the nav
  targets they point at; the Demos page marks itself current.
- tests/test_tour_javascript.py: pins the localStorage key, the seen-state exit,
  the keyboard keys, focus restore and the absence of fetch, cookies and
  document.write in tour.js.
- The full gate is python -m pytest with the existing 100% line coverage floor.

## Out of Scope

- Live providers, credentials and spend in demos.
- Persisting replay timings or a recorded event file in the repository.
- A tour editor, analytics or a "do not show again" preference page.
- Screenshots. The README keeps its existing evidence images.
- A new database table, migration or repository method.

## Further Notes

- The demo specs are copied, not symlinked, so Windows and a container behave the
  same. Deleting demo-<slug>.yaml restores the bundled copy on the next run.
- The trace is deliberately read-only. The only writes on the page are the demo
  launch and the spec copy, both normal form POSTs that redirect.
- The replay uses the events the run already stores, so a run started from the
  CLI also has a usable replay if it is opened with replay=1.