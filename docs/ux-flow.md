# Console v2: screen flow

Status: shipped in console v2 (release 0.2.0, 2026-11-01). Written 2026-10-25 on
branch `design/console-v2`; the flow below is what the server renders.
This file is the contract for every screen that follows. Wireframes live in `docs/mockups/`.

## 1. What the console is

A comparison lab for prompts and models. An operator declares what should vary, gives every
candidate the same context and the same cases, sees the projected cost, runs it, then reads a
verdict and the evidence behind it.

The console is a **composer for `spec.yaml`**, not a second source of truth. Every screen either
reads a spec, writes a spec, or reads the run that a spec produced. `AGENTS.md` rule 7 holds:
an interface holds no state the spec file does not.

The four steps an operator walks are the four blocks the spec already has:

| Step | Operator question | Spec keys it writes |
| --- | --- | --- |
| 1 What varies | What am I comparing, and on what cases? | `name`, `description`, `source`, `prompts`, `seed` |
| 2 Context | What does every candidate get to read? | `contexts`, `credentials` |
| 3 Candidates | Which model, prompt and parameters, in which combination? | `candidates[].{name,adapter,model,credential,options}`, `replicas`, `concurrency` |
| 4 Cost | What will this cost, and may I spend it? | `prices`, `budget_usd`, `metrics`, `judges`, `gates` |

## 2. Decisions taken

- **Signature element: the spec graph.** One wiring diagram, rendered four ways (thumbnail,
  run, compare pair, compact rail). It is the thing the console is remembered for.
- **First run is a wizard, later edits are one page.** The wizard creates; the edit page is a
  structured form with "View YAML" as the escape hatch to the existing editor.
- **Flow before screens.** This document and the wireframes are reviewed before any screen
  code is rewritten.
- **The 26 uncommitted files are re-checked, not kept as-is.** Where they disagree with this
  flow they get rewritten, and the PR says so.
- **Single user, local only.** No accounts, no sharing, no permissions model.

## 3. Dials

Values are 1 to 10, per the anti-slop skill. The baseline is 7/5/4, tuned for marketing pages.
This is an operator cockpit, so:

- **VARIANCE 4.** Layout is deliberately regular: the operator scans columns of numbers, and
  irregularity costs them. Above 4 would be dishonest here. The asymmetry lives in one place
  only, the split between matrix and rail.
- **MOTION 2.** Calm at rest. The only motion is live progress, the copy confirmation, and the
  graph edges lighting as cells resolve. All of it under `prefers-reduced-motion`.
- **DENSITY 8.** Data cockpit. Consequence: no generic cards, hairline dividers instead, all
  numbers in tabular mono, 12.5 to 13.5px body.

## 3b. Signature element

**The spec graph: a hairline wiring diagram that is the same object on the runs list, the run
detail and the compare view, and that lights up as cells resolve.**

- Memorable: an operator describes it as "the run looks like a circuit, and you watch the
  current reach the verdict".
- Owned: it is the spec itself. Cases, prompts and contexts feed candidates, candidates feed
  metrics, metrics feed gates, gates produce the verdict. Only this product can draw it,
  because only this product has that intermediate object.
- Sustained: 144 x 48 thumbnail on every run row, full width on run detail, one 640-unit graph
  per side on compare with short column headings, compact 320-unit graph in the wizard rail.

The node columns are fixed, so the same shape reads at 144px wide, at 320 units and at 960
units. The compact variant drops node labels, not nodes: at 320 units a 9px label is unreadable,
so the rail shows the structure and the page shows the names.

## 4. Information architecture

Top navigation keeps five items and one primary action:

```
Runs          the results that exist          /
Specs         the specs that can run          /specs
Compare       two runs, side by side          /compare
Launch        pick a spec and run it          /launch
Credentials   the vault                       /credentials
                                    [+ New comparison]
```

- The page heading and the nav item both say `Specs`; the route stays `/specs`, the JSON API
  is untouched, and the YAML file stays the object. The comparison wizard and the structured
  edit form keep their `/comparisons/...` routes.
- `/comparisons/new` is the wizard. `/comparisons/{name}/edit` is the structured edit page.
  `/specs/{name}/edit` remains the raw YAML editor, reachable from the edit page.

## 5. Screen graph

```mermaid
flowchart TD
  R[/runs] -->|select 2 or 3| CMP[/compare]
  R -->|open| D[/runs/id]
  D --> CMP
  R -->|New comparison| W1[/comparisons/new step 1]
  W1 --> W2[step 2 context]
  W2 --> W3[step 3 candidates]
  W3 --> W4[step 4 cost]
  W4 -->|create and run| L[/launch]
  W4 -->|create| E[/comparisons/name/edit]
  L --> D
  CP[/specs comparisons] --> W1
  CP -->|edit| E
  CP -->|run| L
  E -->|view YAML| Y[/specs/name/edit]
  E -->|run| L
  CR[/credentials] --> W2
```

## 6. Screens

Each screen states its job, its regions, its primary action and its states. Regions are
ordered as they read top to bottom, then left to right.

### 6.1 Runs (`/`)

- **Job:** what has run, what is running, what needs attention, and get me into the evidence.
- **Regions:** page header (counts, New comparison, Compare selected) / toolbar (candidate,
  metric, status filters) / the run table / empty state.
- **Table:** spec name plus run id, the graph thumbnail, status, candidates, cells ok, pass
  rate with meter, cost, duration, started. Check pass rate is the only coloured number.
- **Primary action:** open a run. Secondary: create a comparison.
- **States:** empty (no runs, points at the wizard), filtered-empty, running row with a live
  dot, error row, long list (table scrolls, header sticks).
- **Mobile:** the table becomes stacked rows, two lines each: name and status, then cells, rate
  and cost on one dense line. Thumbnail hidden below 768px.

### 6.2 Run detail (`/runs/{id}`)

- **Job:** did it pass, where did it fail, and show me the exact cell.
- **Regions:** verdict bar (status, pass rate with Wilson interval, failed gates, cost,
  duration, cancel when live) / spec graph, full width / heatmap, candidate rows by case
  columns / evidence sheet for the selected cell / live progress panel while running.
- **Primary action:** read the verdict, then select a cell. Secondary: compare this run.
- **Evidence sheet:** answer or recorded error, citations, every check with its reason and
  evidence JSON, tokens, cost, latency, usage source, links to `cell.json` and `answer.txt`.
- **States:** running (SSE, counts animate, graph edges light), completed pass, completed gate
  failure, cancelled, budget exceeded, error cell, empty checks, artifact missing (404 copy).
- **Mobile:** verdict stacks, graph becomes the vertical stage list, heatmap scrolls inside
  its own container with a sticky candidate column, evidence below.

### 6.3 Compare (`/compare?a=<run>&b=<run>`)

- **Job:** which of these runs should I trust, and why. Opened from the runs list, or
  directly. With no query it is the chooser: two run selects and a Compare button, not a 404.
  It also accepts `run=<id>&candidate_a=<name>&candidate_b=<name>` for two candidates in one run.
- **Regions:** decision banner (the recommendation in one sentence, plus the deltas that drove
  it) / two spec graphs, aligned, shared nodes marked / paired metric table with deltas and
  intervals / per-case disagreement list, only the cells that differ.
- **Primary action:** pick a winner, then open the losing cells.
- **States:** fewer than two runs selected, one run selected, runs with no shared metrics,
  identical results (say "no measurable difference" rather than inventing a winner),
  incomparable runs (different cases) which explains why instead of guessing.
- **Mobile:** graphs stack, table becomes one card per metric.

### 6.4 Comparisons (`/specs`)

- **Job:** my saved experiments: see them, run them, edit them.
- **Regions:** header with the spec count and "Launch a spec"; a panel naming the three ways
  to edit one file (wizard, structured edit, raw YAML); table: spec file, parsed name,
  candidates, cells, size, modified (UTC), validation, actions / empty state.
- **Primary action:** open a spec with Edit. Secondary: YAML, Duplicate. Launch is a separate
  page action; the list does not launch a row.
- **States:** empty (points at the wizard), invalid spec (inline status and the first error),
  saved (inline confirmation naming the file). The list shows no run history; the runs page
  owns that.
- **Mobile:** the name link is always visible; candidate, cell, size, modified and action
  columns drop by breakpoint, so a phone reads file, validation and the way in.

### 6.5 Wizard (`/comparisons/new`, steps 1 to 4)

- **Job:** produce a valid `spec.yaml` without asking the operator to write YAML.
- **Chrome:** left rail with the four steps, current one marked, completed ones clickable, and
  a live estimate at the bottom. Right: the step body. Footer: Back, Cancel, and one primary
  action: Continue on steps 1 to 3, `Create comparison` on step 4. The last step saves and
  redirects to `/specs`; the wizard never starts a run.
- **Step 1, what varies.** Comparison name (slug previewed), source, and the case rows
  (case id, prompt, expected, rubric, tier). Cases are the unit every candidate answers.
- **Step 2, add context.** Prompt, context and credential rows: name a set, paste its text, or
  point at a stored credential. Credentials needed by an adapter show their vault state and a
  link to `/credentials`.
- **Step 3, choose candidates.** One row per candidate: name, adapter, model, credential,
  prompt, context. Rows are editable, addable, removable, and the derived cell count updates.
  Validation errors appear under the field they name.
- **Step 4, cost and rules.** Estimate hero (total, cells, tokens per cell, medians source),
  budget cap with the percent meter, price rows, metrics, judges and gates. Over-cap is a
  warning with the cap named.
- **States:** invalid field (inline, field named), no cases, candidate referencing a missing
  prompt, context or credential, credential missing or untested, estimate unavailable, over
  budget, spec write conflict.
- **Mobile:** rail becomes a horizontal step strip under the header; the matrix becomes one
  card per candidate; the primary action stays a 44px target.

### 6.6 Edit (`/comparisons/{name}/edit`)

- **Job:** change a saved comparison without reading YAML.
- **Regions:** every section on one page (identity and cases, contexts and credentials,
  candidates, cost and rules), sticky estimate rail, Save and `Save and run` in the header,
  Cancel, "View YAML" as a quiet link.
- **Primary action:** Save. The form and the YAML round-trip through the same validator.
  `Save and run` writes the file and continues to `/launch?spec=<stem>`; Save alone returns to
  `/specs?saved=<stem>`.
- **States:** invalid (400, same page, field errors), saved (303 plus an inline confirmation,
  not a toast), spec changed on disk since load (409, the write is refused; reload and reapply).
- **Mobile:** one column; the estimate stacks under the form and the actions wrap into 44px
  targets.

### 6.7 YAML editor (`/specs/{name}/edit`)

The escape hatch, opened from the YAML action on `/specs`. Check validates and refreshes the
estimate without writing; Save writes the file atomically and never writes a spec that fails
validation; errors sit under the editor and in the error list; Ctrl/Cmd+S saves when valid.
Duplicate lives on the spec list, not in the editor.

### 6.8 Launch (`/launch`)

- **Job:** confirm spend, then start. Reached from the "Launch a spec" action on `/specs` or
  from `Save and run` on the structured edit form, not from the top nav.
- **Regions:** spec summary, estimate, budget override, what happens next, equivalent CLI
  command. One run at a time is stated, not discovered.
- **States:** no specs, running-lock, over budget, unknown spec, launch refused with the
  reason.

### 6.9 Credentials (`/credentials`)

- **Job:** which keys exist, which work, and add or replace one.
- **Regions:** table (name, provider, fingerprint prefix, created, last verified, last error,
  state, actions) / add form / test result.
- **States:** masked (the secret is never rendered, only a fingerprint prefix), untested,
  failed test, disabled, empty, vault locked (503). Test, disable, enable and delete act on one
  row at a time; there is no reveal control.

### 6.10 Error pages

`/runs/{unknown}`, `/specs/{unknown}`, `/compare` with unknown runs, traversal attempts and
missing artifacts all land on one HTML error page that names what was asked for, why it failed,
and the way back. A refused cancel is a 409 page with the same shape; a missing master key on
`/credentials` is a 503 page. No stack traces, no ids the operator cannot act on.

## 7. The wizard contract

The wizard is stateless. Every step is a POST that carries the draft spec in a hidden field,
and the server re-renders the next step from it.

- The draft **is** YAML. Step 1 emits the first valid partial document; each later step patches
  keys and re-validates with the CLI validator, so an invalid draft cannot advance.
- Nothing is written to disk until step 4. Somewhere in the middle the operator can close the
  tab and lose nothing but the draft, and the console holds no state the spec does not.
- The final action writes `<slug>.yaml` through the existing atomic writer, then redirects to
  `/specs?saved=<stem>`. The wizard never launches; `Save and run` on the edit form or the
  Launch page starts a run after a separate confirmation.
- JavaScript is an enhancement only: live estimate recalculation, add-row, dirty marker. The
  POST-per-step flow works with JavaScript disabled.
- Field errors name the field, the value and the fix, matching rule 6 in `AGENTS.md`.

## 8. The spec graph

Server-rendered SVG. No layout library, no client-side layout.

Columns, left to right: `cases` / `prompts and contexts` / `candidates` / `metrics` / `gates and verdict`.

- Node: name, mono count, state tone. Edges: 1px hairlines, square with 8px corner radius on
  the vertical runs, no curves, no arrowheads except into the verdict.
- Edge weight is the number of cells it carries, drawn as line width, not opacity.
- Run state: pending is `--line`, active is `--info`, pass is `--ok`, fail is `--bad` with one tick,
  error is `--warn` with a dashed stroke. Colour is never alone: every state also changes
  the stroke pattern or adds a glyph, matching the heatmap legend.
- Accessible name: one `role="img"` with a generated description ("4 candidates over 12 cases
  produce 48 cells; 3 gates, 1 failed"). The full graph uses `<title>` and `<desc>` ids; the
  thumbnail is `aria-hidden="true"` because the row cells carry the same facts.
- Variants are transforms of one model: the thumbnail renders the 96 x 32 model at 144 x 48,
  the rail relayouts it to 320 units without labels, and both keep node order and tones.
- Reduced motion: the lit-paths sweep is replaced by static states.
- No JavaScript: the SVG is server-rendered, so it is the fallback.

## 9. States matrix

Every screen must show, at minimum:

| State | Where it surfaces |
| --- | --- |
| Empty | runs, comparisons, evidence sheet, credentials, compare |
| Loading | server-rendered, never a spinner; a live heatmap shows skeleton pending cells and the progress region reports the count |
| Error | field-level for forms, page-level for a failed read, inline callout for a refused action |
| Running | live dot, SSE counts, graph edges, cancel available |
| Partial | cancelled and budget-exceeded runs keep their cells and say so |
| Denied | missing artifact, unknown id, traversal, vault locked |
| Success | inline confirmation that names what changed |

## 10. Viewports

| Width | Target | Behaviour |
| --- | --- | --- |
| 1440 x 900 | desktop | full matrix, rail on the right, graph at full width |
| 1280 x 800 | laptop | same, tighter gutters, table hides duration column |
| 823 x 789 | tablet portrait | rail moves under the content, table loses two columns |
| 375 x 812 | phone | single column, stacked rows, rail graph at 320 units, 44px targets |

Breakpoints: 768px and 1024px. No horizontal scroll at any width; wide tables scroll inside
their own `role="region"` container only.

## 11. Anti-slop pre-flight

Adapted from `skills/antislop-web/SKILL.md` Part C, with the stack mapped to plain CSS.

- [ ] No AI tells: no Inter, no purple, no `#000`, no three identical cards, no emoji, no
      "Elevate/Seamless", no `99.99%`. Real content: `smoke`, `gated`, `judges` specs, real
      numbers from seed runs.
- [ ] Signature present, single, sustained: the spec graph on runs, detail, compare, wizard.
- [ ] Loading, empty and error states everywhere they can occur.
- [ ] `min-height: 100dvh`, never `100vh`.
- [ ] Motion only under `prefers-reduced-motion`, transform and opacity only, content visible
      by default.
- [ ] Focus visible on every interactive element, 2px accent outline with offset.
- [ ] Contrast: AA in both schemes, for every token pair actually used.
- [ ] Tap targets 44px on the phone width.
- [ ] Numbers in tabular mono; ids and hashes mono.
- [ ] No JS-only path: every flow completes with scripting off.

## 12. Out of scope, with TODOs

- **Attaching a repo for the agent.** Not in this pass. TODO marker in the context step: the
  card is described as "not yet supported" and links to no control. Implementation needs an
  adapter that reads a working tree, and a decision about sandboxing.
- **Knowledge graph as a first-class object.** This pass accepts a file and chunks it into a
  `contexts` set. No graph queries, no embeddings, no vector store: no new dependency,
  and `contexts` is already the spec mechanism. TODO: a graph-aware adapter after this.
- **Teams, sharing, permissions.** Out of scope by decision.
- **Editing cases offline.** Cases come from the source adapter; the wizard uploads or pastes.
- **Live multi-run scheduling.** One run at a time, unchanged.

## 13. Phasing

Console v2 shipped in 0.2.0 on 2026-11-01. The phases landed together rather than as
separate releases:

1. **Design.** This document, `docs/mockups/`, and the dials, signature and bans in
   `docs/design-system.md`.
2. **Spec graph.** Server-rendered SVG used in the runs list, run detail, compare and wizard
   rail.
3. **Wizard.** `/comparisons/new` steps 1 to 4 with the stateless hidden draft.
4. **Structured edit.** `/comparisons/{name}/edit`, plus the raw YAML editor as the escape
   hatch.
5. **Screen passes.** Runs, run detail, compare chooser, comparisons, credentials, and the
   404/409/503 error pages.
6. **Evidence.** Screenshot matrix under `docs/evidence/*-after.jpg`, the 100% line coverage
   gate, and the offline demo.

The three 0.2.0 console follow-ups are closed. Reduced motion is targeted static
states, not a global duration override; the skip link and progress fill animate
transform only; and the runs list remains the only in-page fetch region that
swaps a table, with its skeleton rows already shipped. The spec editor's
validation fetch marks the form `aria-busy` and swaps panels rather than table
rows, so it needs no skeleton. No other table region lacked a loading state, so
none was added.
