# Tickets: demo library, trace and first-visit tour

Source spec: docs/demo-and-trace-spec.md
Order: 1, 2, 3, 4, 5, 6. Tickets 2 and 5 can run in parallel after 1; ticket 3
depends on 2; ticket 4 depends on 2 and 3; ticket 6 depends on all.

## 1. Demo catalog and bundled specs

What to build: the evalharness.demos package, the four YAML specs, the catalog
metadata and the materialize/read helpers. Add the package data entry.

Blocked by: none.

Acceptance:

- [ ] evalharness.demos.catalog exposes DEMOS, DemoScenario, find_demo,
      materialize and materialized_slugs.
- [ ] All four YAML files parse and validate with the real spec loader.
- [ ] materialize writes demo-<slug>.yaml only when absent and returns its path.
- [ ] A user edit to a materialised file is never overwritten.
- [ ] pyproject.toml ships evalharness.demos/*.yaml.
- [ ] tests/test_demo_catalog.py passes.

## 2. Trace page

What to build: evalharness.api.trace_views, the /runs/{run_id}/trace route, the
trace template, the run detail Trace link and the trace CSS.

Blocked by: 1 (for the sample spec in tests).

Acceptance:

- [ ] Two candidates for one case show both effective prompts, both answers,
      every check with metric/pass/score/reason/evidence, and the gate result.
- [ ] The effective prompt is the prompt override when present, otherwise the
      case prompt.
- [ ] Exactly two candidates produce a word diff; one candidate does not.
- [ ] An unknown run is 404; a running run shows the progress note.
- [ ] No secret, ciphertext or master key appears in the HTML.
- [ ] tests/test_api_trace.py passes.

## 3. Demo library page and run/open routes

What to build: /demos, the demo cards, the seven-stage pipeline, POST run and
POST open, the Demos nav item and the page CSS.

Blocked by: 1, 2.

Acceptance:

- [ ] GET /demos lists the four titles, questions, what-varies and look-for
      lines, the pipeline and the offline callout.
- [ ] Run demo on prompt-choice returns 303 to /runs/{id}/trace?replay=1 and the
      trace shows the strict pass and the explained exact_match failure.
- [ ] A second Run demo on the same slug redirects to the same run; no second
      run is recorded.
- [ ] Run demo is deterministic and reads no provider credential.
- [ ] Open spec returns 303 to /specs/demo-<slug>/edit?copied=demo and the
      editor shows the bundled YAML.
- [ ] An unknown slug is 404; an invalid demo spec re-renders /demos with 400.
- [ ] tests/test_api_demos.py passes.

## 4. Replay panel and trace.js

What to build: the replay JSON script, the replay panel on the trace template,
static/trace.js and the replay CSS.

Blocked by: 2, 3.

Acceptance:

- [ ] ?replay=1 renders the replay panel and the stored events as JSON.
- [ ] trace.js renders one log line per event, updates the bar with scaleX and
      says "recorded run" and "timings simulated".
- [ ] prefers-reduced-motion renders every event immediately.
- [ ] Without JavaScript the trace is complete and the replay panel is hidden.
- [ ] No model call or token spend is added.
- [ ] tests/test_tour_javascript.py pins the trace.js contract.

## 5. First-visit tour

What to build: _tour.html, static/tour.js, the base.html include and the tour
CSS.

Blocked by: none (the nav link text must match ticket 3).

Acceptance:

- [ ] Every page renders the hidden tour section with exactly five steps.
- [ ] tour.js removes the section when localStorage already holds
      evalh:console-tour:v1.
- [ ] tour.js supports Escape, ArrowLeft, ArrowRight, moves focus to Next and
      restores the prior focus.
- [ ] Without JavaScript nothing blocks the page.
- [ ] tests/test_api_tour.py and tests/test_tour_javascript.py pass.

## 6. Docs, nav and full gate

What to build: README route table, docs/console.md page map and replay/tour
sections, CHANGELOG Unreleased entry, any docs contract updates, and the full
suite.

Blocked by: 1, 2, 3, 4, 5.

Acceptance:

- [ ] README names /demos and /runs/{id}/trace and still names the four original
      offline specs.
- [ ] docs/console.md documents the demo page, trace, replay, tour and the new
      routes.
- [ ] CHANGELOG has an Unreleased entry; the released 0.4.0 heading and date are
      unchanged.
- [ ] python -m pytest passes with 100% line coverage.
- [ ] No push and no merge.