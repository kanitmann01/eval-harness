# Design system

The console is a dense technical instrument: an operator reads it at a glance,
compares numbers, and then acts. The visual language is therefore calm first and
loud only where a decision is at stake. One stylesheet
(`src/evalharness/api/static/console.css`) holds the tokens and the components;
there is no CSS framework, no build step and no external asset.

This document is the source of truth for the console v2 redesign and the review
contract for it. The pre-flight checklist at the end is what a reviewer ticks
before merging; where this document and a template disagree, the template is
wrong until this document is amended.

## Contents

- [Principles](#principles)
- [Dials](#dials)
- [The signature element](#the-signature-element)
- [Banned list](#banned-list)
- [Tokens](#tokens)
- [Components](#components)
- [States](#states)
- [Motion](#motion)
- [Accessibility checklist](#accessibility-checklist)
- [Pre-flight checklist](#pre-flight-checklist)
- [Print](#print)

## Principles

1. **Hairlines, not boxes.** A panel is a surface with a 1px line. Avoid nested
   bordered cards; group with spacing and a single divider instead.
2. **Numbers are the content.** Tabular figures, right alignment and a visible
   interval matter more than decoration. A null value renders as undefined,
   never as zero.
3. **Colour never carries a meaning alone.** Every status has a glyph and a
   word, every heatmap cell has a glyph and an accessible label, and every
   warning has a heading.
4. **One accent.** The emerald is reserved for the primary action, focus and
   live activity. Semantic tones (ok, warn, bad, info, neutral) are separate
   ramps.
5. **Calm at rest, clear on focus.** Nothing moves unless the operator hovers,
   focuses, or a run is live. Motion respects `prefers-reduced-motion`.

## Dials

The anti-slop skill scores a page on three dials from 1 to 10. The console sets
them once, here, and every component obeys them. They are constraints, not
moods. A component that cannot be built inside these numbers is the wrong
component.

| Dial | Value | Reads as |
| --- | --- | --- |
| VARIANCE | 4 | regular grid, one asymmetry per screen |
| MOTION | 2 | nothing moves unless a state changed |
| DENSITY | 8 | numbers first, chrome last |

The marketing baseline is 7/5/4. Those values are tuned so a landing page feels
alive and airy: high variance to create rhythm, mid motion to demonstrate the
product, low density to leave room for a claim. None of that applies to an
operator comparing rows, so the baseline is rejected wholesale rather than
adjusted. At 7 variance a comparison costs a second look, at 5 motion a value
must be re-read, at 4 density a 48-cell matrix costs five screens.

### VARIANCE 4

Enables one deliberate asymmetry per screen, and makes it structural: the
matrix against its fixed rail, the five-cell verdict strip against the wide
heatmap, the heatmap's 3.1rem case columns against the 13rem row head. Panels
share a left edge, spacing comes from the 4/8 scale, and the only numbers that
differ between two screens are counts and widths, never offsets.

Forbids a centred hero, off-grid offsets, overlapping or rotated labels,
decorative whitespace above the fold, one-off margins that are not `--space-*`,
and panels of different widths on the same screen. It also forbids asymmetry
that changes per load or per run: the layout is the same for run 12 and run 13.

Why not 7: at 7 the layout is the message. Here the numbers are. An operator
scanning 48 cells needs the same x for the same column on every row, and a
hairline already provides the rhythm that variance would buy.

### MOTION 2

Enables four moves and no more: the live progress fill, the 7px live dot, the
copy confirmation, and the skeleton shimmer on a cell the run has not recorded
yet. Phase 1 adds a fifth, spec graph edges lighting as cells resolve. Every one
is a state change with a start and an end.

Forbids page-load reveals, staggered card entrances, hover parallax, number
count-ups, carousels, sliding drawers, spinners, and anything that keeps running
after the state is final. It also forbids motion used as an apology for a slow
request: the progress count tells the truth, the page does not spin.

Why not 5: 5 belongs to a page where motion explains a product. Here a value
that moves is a value the operator reads twice, and a loop that never stops is a
value they cannot compare.

### DENSITY 8

Enables 13.5px body, 12.5px buttons, 10.5px uppercase labels, 10px table heads
at 520px, 1px hairlines, 3.1rem heatmap cells, 7 to 10px cell padding, 20px
panel padding, tabular figures, and a 48-cell matrix inside 1280px without a
scroll.

Forbids cards inside cards, 24px padding around a single label, two-line labels
where one line fits, icon-only actions without an accessible name, decorative
illustration, and body text below 11.5px. 10.5px is a label size, never a
sentence size. It also forbids "breathing room" that costs a scroll before two
rows can be compared.

Why not 4: at 4 the console reads like a brochure and a 48-cell run takes five
screens. Density is the product, not a compromise.

## The signature element

The console has exactly one signature element, and it is the spec graph. It is
the one thing a reader should remember after the page is gone; everything else
is infrastructure around the numbers.

The graph is the spec, drawn. Five columns, fixed left to right: cases, prompts
and contexts, candidates, metrics, gates, and the verdict. A node is a name, a
mono count and a state. An edge is a hairline; its weight is the number of cells
it carries, and its colour and pattern are the state of those cells. It is
generated from the spec and the run record, never hand placed.

| Where | Size | Variant | Notes |
| --- | --- | --- | --- |
| Runs list, every row | 96 x 32px | thumbnail | `aria-hidden`, no labels, 1px edges, shape only, dropped below 768px |
| Run detail, between the stats row and the heatmap | 960 units wide | full | `role="img"`, title and description, one figcaption line with the counts |
| Compare, one per side above the metrics | 640 units per side | full boxes, short headings | same columns and node order, every node label kept, captioned Side A and Side B |
| Wizard rail, all four steps | 320 units wide | compact | column labels only, 18px nodes, every node kept |

The same object at every size. Node columns are fixed and nodes are ordered by
name, never by count or by state, so two runs of the same spec are
pixel-comparable and the 96px thumbnail has the silhouette of the 960px graph.
The thumbnail is not a different diagram. A compare panel is the full graph at
640 units with the abbreviated column headings: the long headings collide at that
width, the node labels do not. The compact rail variant is the same graph at 320
units: it keeps every node and drops the node labels, because at that width a
9px label is grey mush.

No competing element. A screen shows one spec graph, and compare shows two. A
second diagram, chart, illustration, decorative SVG background or icon set is a
defect: if a second thing genuinely needs a diagram, it is a table. The graph is
not a flowchart with an arrowhead on every edge, not a force-directed hairball,
not an icon.

The only motion it may carry is the lit-path sweep in phase 1: an edge changes
state when the cells it carries resolve, not when the page loads. Under reduced
motion the state class swaps with no transition.

## Banned list

The anti-slop skill names the tells that make a page read as generated. This is
the mapped list for the console. Each row is a review test; the right column is
the only acceptable replacement in this codebase.

| Banned | What this console does instead |
| --- | --- |
| Inter, or any other AI-default typeface | System UI stack in `--font-sans`, mono stack in `--font-mono`, both inherited from `body`. No webfont, no CDN, no `@font-face`, no font set on a component. |
| Purple, indigo, violet or a neon accent on dark | One desaturated emerald, `--accent`. Semantic tones are separate ramps. Gradients are used as shapes only: the select chevron, the error hatch, the skeleton shimmer and the progress fill. None is a hue ramp. |
| `#000` | `--ink` is the darkest text and `--canvas` the lightest surface. Every `#000` in the stylesheet sits inside the print block, where black on white is correct. |
| Three identical cards in a row | A hairline table, a 1px-gap stat strip (`.verdict-grid`), or the two-column `.side-grid` on compare. A third identical card is a table that has not been admitted. |
| Emoji | Text glyphs from the system font, declared once in `_macros.html` `glyph()`: ✓ ✕ ! ? ◐ · ∅ – ▲ ▼. No colour emoji, no flags, no pictographs. |
| Marketing cliches (Elevate, Seamless, Supercharge, Unlock, Effortless) | Nouns and measurements: Runs, Cells ok, Pass rate, Cost, Duration, Gates, and the real spec names. Empty states name the next action instead of selling it. |
| Fake precision (99.99%, "10x faster") | Every rate carries its n and an interval ("95% interval 0.52 to 0.74"); money is exact in `title` and readable on screen; a null is undefined. Two runs with overlapping intervals produce "no measurable difference", not a winner. |
| `100vh` | Content height, or `min-height: 100dvh` when a shell must fill the viewport. `body` still declares `min-height: 100vh` and that line is scheduled for phase 1. Only table and heatmap regions scroll, inside their own box. |
| A generic spinner | Server-rendered pages have no loading state. A live heatmap uses skeleton cells at real dimensions and the progress region reports done / total. A spinner is never the only feedback. |
| A centred hero at VARIANCE 4 or below | A left-aligned `h1`, a `lede` at 76ch, page actions on the right. The largest type on a page is the verdict or the estimate, never a slogan. |
| An icon with a different stroke weight | There is no icon set. Marks are text glyphs, so weight comes from `font-weight` (650 to 800). The one drawn path is the favicon (stroke 3.5) and it never appears in the interface. |

An exception needs a line deleted from this table in the same pull request, and
the reviewer will ask why.

## Tokens

Tokens live on `:root` and are swapped under `prefers-color-scheme: dark`.
Components only ever reference tokens, never a literal colour.

### Surface and ink

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--canvas` | `#f5f7f6` | `#0b0f0e` | page background |
| `--surface` | `#ffffff` | `#111715` | panels, tables, top bar |
| `--surface-2` | `#f0f3f1` | `#171f1c` | table head, hover, inset button |
| `--surface-3` | `#e6ebe8` | `#202a26` | progress track, skeleton |
| `--inset` | `#f7f9f8` | `#0e1311` | answer and evidence wells |
| `--ink` | `#151b19` | `#e8eeea` | primary text |
| `--ink-2` | `#39433f` | `#c3cdc7` | form labels, dense values |
| `--muted` | `#5f6a65` | `#98a49e` | secondary text, captions |
| `--faint` | `#66706a` | `#84918a` | ids, timestamps, placeholders |
| `--line` | `#e1e6e3` | `#24302a` | hairline borders |
| `--line-strong` | `#ccd4cf` | `#34433b` | input borders, emphasis rules |

All combinations of `--ink`, `--ink-2` and `--muted` on `--canvas` and
`--surface` clear WCAG AA (4.5:1) in both schemes. `--faint` is metadata only:
it is lifted to 5.13:1 on the light surface and 5.53:1 on the dark surface, so
it clears AA as well. Anything an operator must read still uses `--muted` or
above.

### Semantic tones

Each tone is a triple: background, ink and border.

| Tone | Tokens | Meaning |
| --- | --- | --- |
| ok | `--ok-bg`, `--ok-ink`, `--ok-line` | passed, verified, completed, gate passed |
| warn | `--warn-bg`, `--warn-ink`, `--warn-line` | errored cell, gate failed, guardrail |
| bad | `--bad-bg`, `--bad-ink`, `--bad-line` | failed, budget exceeded, destructive |
| info | `--info-bg`, `--info-ink`, `--info-line` | running, unverified, no checks |
| neutral | `--neutral-bg`, `--neutral-ink` | cancelled, disabled, not recorded |

### Brand and focus

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--accent` | `#1f5e4b` | `#7fd3b2` | primary button, links, focus, live dot |
| `--accent-hover` | `#174a3b` | `#a4e4c8` | primary hover |
| `--accent-soft` | `#e6f1ec` | `#17362b` | focus halo, selection |
| `--accent-ink` | `#ffffff` | `#0b1b15` | text on accent |

Focus is visible by default: `:focus-visible` gets a 2px `--accent` outline with
a 2px offset, taken from `--focus-ring` and `--focus-offset`. Inputs swap the
outline for a 3px `--accent-soft` halo plus an accent border, so focus survives
a coloured callout background. One case is a known bug: the
`input[aria-invalid="true"]` rule is declared after the focus rule with the same
specificity, so an invalid control loses the focus signal while focused. The fix
is specified under [Focus ring](#focus-ring).

### Type

System UI stack for prose, a mono stack for ids, hashes, numbers and code.

| Token | Size | Use |
| --- | --- | --- |
| `--text-3xl` | 32px | page title |
| `--text-2xl` | 26px | cost estimate |
| `--text-xl` | 20px | verdict headline, stat values |
| `--text-lg` | 16px | panel title, side name |
| `--text-md` | 13.5px | body and table text |
| `--text-sm` | 12.5px | buttons, hints, dense rows |
| `--text-xs` | 11.5px | captions, chips, metadata |
| `--text-2xs` | 10.5px | uppercase labels, eyebrows, table heads |

The scale is intentionally tight. Tables and forms sit at 13.5px; only the
verdict and the estimate break out. Numbers use
`font-variant-numeric: tabular-nums`; ids and code use `--font-mono`.

### Space, radius, elevation

Spacing is a 4/8 px scale: `--space-1` 4px, `--space-2` 8px, `--space-3` 12px,
`--space-4` 16px, `--space-5` 20px, `--space-6` 24px, `--space-7` 32px,
`--space-8` 40px, `--space-9` 56px. Panels use 20px, page gutters 16 to 32px.

| Token | Value | Use |
| --- | --- | --- |
| `--radius-sm` | 5px | chips, kbd, copy button |
| `--radius` | 8px | buttons, inputs, evidence wells |
| `--radius-lg` | 12px | panels, tables, verdict |
| `--radius-pill` | 999px | status pills, progress |
| `--shadow-sm` | 1px hairline shadow | hover lift |
| `--shadow-md` | soft ambient | skip link, floating only |

There is no persistent drop shadow on panels: elevation is reserved for things
that genuinely float.

### Tokens the components also use

These are declared in the stylesheet and consumed by components, or reserved for
one that lands in phase 2. A token used by a component is listed here in the
same pull request; a literal colour, size or radius outside `:root` is a review
defect.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--font-sans` | system UI stack | same | prose and headings |
| `--font-mono` | ui-monospace stack | same | ids, hashes, counts, code |
| `--code-bg` | `#f4f6f5` | `#0d1210` | spec source, evidence JSON, answer wells |
| `--on-accent-muted` | `#d7e9e1` | `#29483b` | declared, no consumer yet; proposed for secondary text on an accent fill in phase 2, or delete it |
| `--focus-ring` | `2px solid var(--accent)` | same | global focus outline |
| `--focus-offset` | `2px` | same | focus outline offset |
| `--max-width` | `1320px` | same | page shell |

## Components

The order below runs from shared primitives to page parts, not from the top of a
screen down. The run page reads verdict, graph, heatmap, evidence, progress.

### Page header

```html
<p class="eyebrow">Workspace</p>
<div class="page-header">
  <div class="page-header-main">
    <h1>Runs</h1>
    <p class="lede">Every recorded run, newest first.</p>
  </div>
  <div class="page-actions">
    <a class="btn btn-primary" href="/launch">Launch run</a>
  </div>
</div>
```

Every page answers three questions in the same order: where am I (breadcrumb,
eyebrow, active nav), what is this (title, lede), what can I do (actions).

### Panel

```html
<section class="panel" aria-labelledby="gates-heading">
  <div class="panel-head">
    <div><h2 id="gates-heading">Gates</h2><p class="subtle">…</p></div>
    <span class="chip">2 declared</span>
  </div>
  …
</section>
```

Use one panel per idea. Do not put a bordered card inside a panel unless it is
a genuine object (a check, a gate, a side of a comparison).

### Button

| Class | Use |
| --- | --- |
| `btn btn-primary` | the one primary action on a page |
| `btn btn-secondary` | secondary actions, row actions |
| `btn btn-ghost` | navigation-like actions, Clear |
| `btn btn-danger` | destructive actions (delete) |
| `btn btn-sm` | table rows and the top bar |
| `icon-btn` / `copy-button` | icon-only actions with an `aria-label` |

`button-link`, `button-primary` and `button-secondary` remain as aliases for
older templates.

### Chip and status

```html
<span class="status status-ok">
  <span class="status-glyph" aria-hidden="true">✓</span><span>ok</span>
</span>
<span class="gate-chip"><span aria-hidden="true">✕</span> gate failed</span>
<span class="chip">overall</span>
<span class="badge">single user</span>
```

The status class drives the tone; the glyph and the word carry the meaning. A
gate failure is a separate chip from the run status because a run can finish
`ok` and still fail its gate.

### Table

```html
<div class="table-wrap" tabindex="0" role="region" aria-label="Runs table">
  <table>
    <caption class="sr-only">Runs with status, pass rate and cost</caption>
    <thead><tr><th scope="col">Spec</th><th scope="col" class="num">Cost</th></tr></thead>
    <tbody><tr><th scope="row">smoke</th><td class="num">$0.000013</td></tr></tbody>
  </table>
</div>
```

Tables always have a caption (visible or `sr-only`), `scope` on every header,
and a scroll region that is keyboard reachable. Numeric cells use `num`.
Columns drop in this order on narrow screens: `col-lg` below 900px, `col-md`
below 700px, `col-sm` below 520px.

### Form field

```html
<div class="field">
  <label for="budget">Budget override (USD)</label>
  <input id="budget" name="budget_override_usd" type="number" step="0.000001"
         aria-describedby="budget-hint">
  <p class="field-hint" id="budget-hint">Leave empty to use the spec budget.</p>
</div>
```

A field error names the field, the rejected value, the reason and the fix. The
control gets `aria-invalid="true"` and `aria-describedby` pointing at the
message. The spec editor uses this shape; validation errors are also listed in
`#spec-errors`, which the textarea describes.

### Callout

```html
<div class="callout callout-warn" role="status">
  <span class="callout-icon" aria-hidden="true">!</span>
  <div><span class="callout-title">Gate failed</span> exact_match did not clear its floor.</div>
</div>
```

Tones: `callout-ok`, `callout-info`, `callout-warn`, `callout-error` (which
uses `role="alert"`). A callout states the consequence, not only the fact.

### Empty state, skeleton and kbd

```html
<div class="empty-state">
  <span class="empty-icon" aria-hidden="true">∅</span>
  <h2>No runs yet</h2>
  <p>Start one from a terminal with <code>evalh run spec.yaml</code>.</p>
</div>
<span class="skeleton" aria-hidden="true"></span>
<kbd>Ctrl</kbd> + <kbd>S</kbd>
```

The console is server-rendered, so loading is the gap between navigation and
the response; skeletons are used inside a live heatmap for cells the run has
not recorded yet. They are hidden from assistive technology, and the progress
region reports the real count.

### Heatmap

```html
<td class="heat-cell pass">
  <a href="/runs/…/cells/…" title="scripted / case-1 r1: ok (2 checks)"
     aria-label="scripted / case-1 r1: ok (2 checks)">✓</a>
</td>
<td class="heat-cell pending"><span class="skeleton" aria-hidden="true"></span></td>
```

Columns are 3.1rem; each cell is a 39.2px square link with a focus ring. The row
head is 13rem (10rem below 700px, 8rem below 520px) and sticks to the left while
the case columns scroll; the head row sticks to the top. Both keep an opaque
`--surface` background so rows pass underneath, and both take the row hover tint
so a row still reads as one line.

States: pass ✓, fail ✕, error ! with a 45 degree hatch so the tone survives a
greyscale print, cancelled –, unknown ? (no checks were recorded), pending (a
dashed border plus a skeleton), empty (a faint dot, outside the run plan). A
selected cell gets a double ring, `--surface` then `--accent`, not a colour
change.

Each cell carries a `title` and an `aria-label` with candidate, case, replica
and outcome, and the selected cell carries `aria-current`. Selection is a link
(`?cell=…`), so refresh, back and copy-link work. The legend repeats all five
glyphs and words; the summary column repeats each row's count and rate with a
5rem meter.

Below 700px the summary column is hidden today, so a row's rate leaves the
screen; the run verdict above still carries the overall rate. Phase 1 moves the
row rate into the row head under the candidate name instead of deleting it.

### Progress

```html
<div class="progress-track" role="progressbar" aria-labelledby="progress-count"
     aria-valuemin="0" aria-valuemax="18" aria-valuenow="12">
  <span class="progress-fill" style="width: 66.7%"></span>
</div>
<p id="progress-count" class="progress-count" aria-live="polite">12 <small>of 18 cells</small></p>
```

The live script updates `aria-valuenow`, the fill width and the text from the
SSE stream. A running state is a pulsing dot **and** the word "running"; a
replay is labelled "replay" or "complete".

### Tooltip

Tooltips use the native `title` attribute on a non-essential detail: the full
run id beside its short form, the exact USD value beside the readable one, the
full case id on a heatmap column. Never put the only copy of required
information in a tooltip.

### Verdict bar

```html
<section class="run-verdict" aria-labelledby="verdict-heading">
  <h2 id="verdict-heading" class="sr-only">Run verdict</h2>
  <div class="verdict-main">
    <span class="verdict-badge is-warn" aria-hidden="true">✕</span>
    <div class="verdict-body">
      <div class="verdict-kicker">
        <span class="status status-error"><span class="status-glyph" aria-hidden="true">✕</span><span>error</span></span>
        <span class="chip">overall</span>
      </div>
      <p class="verdict-headline">Gate failed: exact_match 0.61 against a 0.80 floor.</p>
      <p class="verdict-sub">23 of 36 checks passed.</p>
    </div>
  </div>
  <div class="verdict-grid">
    <div class="verdict-stat">
      <span class="stat-label">Pass rate</span>
      <span class="stat-value">64%</span>
      <span class="stat-detail">95% interval 52 to 74%</span>
    </div>
    …
  </div>
  <div class="verdict-warning">…callout…</div>
</section>
```

Classes: `run-verdict`, `verdict-main`, `verdict-badge` with `is-ok`, `is-warn`,
`is-bad` or `is-info`, `verdict-body`, `verdict-kicker`, `verdict-headline`,
`verdict-sub`, `verdict-grid`, `verdict-stat`, `stat-label`, `stat-value` (with
`small`), `stat-detail`, `verdict-warning`, `meter` and `is-over`.

Rules: the badge is 40px square and `aria-hidden`; the status chip carries the
word. Five stats sit on a 1px gap grid, three columns below 1100px, two below
700px, and the main row stacks below 700px with the headline at 16px. Every rate
carries its interval and n in `stat-detail`. The pass rate is the only stat that
may also carry a meter, and that meter is `aria-hidden` because the number is
the content. The headline is one sentence and contains the number that decided
the run.

States: running (info badge, live indicator, "N of M cells so far"), passed
(ok), gate failed (warn badge, gate chip in the header, the failing gate's
observed and threshold in the headline), cancelled (neutral, and the run page maps
that tone to the `is-warn` badge, so the headline and the chip must say
"cancelled"), budget exceeded (`is-bad`, the spent / cap meter with `is-over`,
a warn callout naming the cap), error (`is-bad`, an error callout with the
recorded error), partial (info badge and a callout naming the missing cells).

### Evidence sheet

```html
<section class="panel cell-panel" aria-labelledby="cell-heading">
  <div class="panel-head">
    <div><h2 id="cell-heading">Cell</h2><p class="subtle">scripted / case-1 r1</p></div>
    <div class="panel-actions"><span class="mono">cell-01H…</span><button class="copy-button" aria-label="Copy cell id">⧉</button></div>
  </div>
  <div class="cell-identity">
    <strong>scripted</strong><span class="faint">/</span><span class="mono">case-1</span>
    <span class="chip">r1</span><span class="status status-ok"><span class="status-glyph" aria-hidden="true">✓</span><span>ok</span></span>
  </div>
  <div class="cell-sheet">
    <div class="cell-main">
      <div class="evidence-block"><h3>Answer</h3><pre class="answer">…</pre></div>
      <div class="evidence-block"><h3>Citations</h3><ol class="citations">…</ol></div>
      <div class="evidence-block">
        <h3>Checks</h3>
        <article class="check check-fail">
          <header><span class="gate-glyph" aria-hidden="true">✕</span><strong class="mono">exact_match</strong>
            <span class="status status-error">…</span><span class="score">0.61 / 0.80</span></header>
          <p>Why it failed, in one sentence.</p>
          <details class="evidence-details"><summary>Evidence JSON</summary><pre class="evidence">…</pre></details>
          <p class="judge-hash">judge 4f2c…</p>
        </article>
      </div>
    </div>
    <aside class="cell-rail">
      <dl class="facts">…</dl>
      <ul class="artifact-links">…</ul>
    </aside>
  </div>
</section>
```

Classes: `cell-panel`, `cell-identity`, `cell-sheet`, `cell-main`, `cell-rail`,
`evidence-block`, `answer`, `citations`, `check` with `check-pass` or
`check-fail`, `score`, `evidence-details`, `evidence`, `judge-hash`, `facts`,
`artifact-links`.

Rules: one column of evidence and a 17rem rail, single column below 900px with
the rail under a hairline. The sheet is a page state, not a modal: selection is
`?cell=…`, so the back button and a copied link work. Long answers and evidence
JSON scroll inside their well (`26rem` and `20rem`) with `pre-wrap` and
overflow wrapping; nothing is truncated silently. Checks come in recorded order,
failure first is not allowed because the order is evidence. Artifacts are plain
links to `cell.json` and `answer.txt`.

States: no cell selected (empty state, icon ◎), error cell (error callout before
the answer, the recorded error text inside it), no checks recorded (empty state
inside Checks: "No checks were recorded for this cell"), missing artifact (the
link is disabled with a title, not a broken 404), cancelled cell (neutral status,
evidence kept), very long content (scrolls in place, never moves the page
layout).

### Spec graph

Proposed, phase 1. The signature element, specified here so the view model and
the SVG are built once.

```html
<figure class="spec-graph-figure">
  <svg class="spec-graph" viewBox="0 0 960 320" role="img"
       aria-labelledby="graph-title graph-desc" preserveAspectRatio="xMinYMin meet">
    <title id="graph-title">Spec graph: gated-qa</title>
    <desc id="graph-desc">4 candidates over 12 cases produce 48 cells; 3 gates, 1 failed; verdict: gate failed.</desc>
    <g class="graph-edges">…</g>
    <g class="graph-nodes">…</g>
  </svg>
  <figcaption>4 candidates, 12 cases, 3 gates, 1 failed.</figcaption>
</figure>
```

Column widths, from the viewBox:

| Column | x | Width | Content |
| --- | --- | --- | --- |
| cases | 0 | 160 | one node: the count |
| prompts and contexts | 184 | 200 | one node per prompt, context sets alongside |
| candidates | 408 | 200 | one node per candidate |
| metrics | 632 | 160 | one node per declared metric |
| gates and verdict | 816 | 144 | one node per gate, then the verdict node |

Gutters are 24px. A node is 30px tall with a 7px gap, radius 8, `--surface`
fill and a 1px `--line` border; the verdict node is the only filled node
(`--accent-soft` fill, `--accent` border). Labels are 11px `--ink-2`, truncated
to the column with the full name in a `<title>` child; counts are 10.5px mono,
right aligned. Six nodes per column, then one "+N more" node that keeps the
count. Nodes are ordered by name.

Edges are orthogonal: a horizontal stub out of the source, one vertical run in
the gutter, a horizontal stub into the target, corners rounded 8px, no beziers.
One edge per pair of nodes that share work, never one per cell; the weight is
the cell count it carries: 1px for 1 to 6 cells, 2px for 7 to 24, 3px for 25 or
more, and never opacity. Edges from the same gutter share a vertical run, 2px
apart, so a large matrix does not become a solid block. There is one arrowhead
in the whole diagram, a 6px triangle on the edge into the verdict; the marker
fill matches the edge tone.

Every stroke carries `vector-effect="non-scaling-stroke"` so the thumbnail keeps
hairlines instead of disappearing. The thumbnail drops weight and labels and
keeps node count, order and the verdict fill.

State encodings, applied to both the edge and the node border:

| State | Edge | Node | Glyph |
| --- | --- | --- | --- |
| pending | 1px `--line`, solid | `--line` border | none |
| active | 1px `--info-ink`, dash 8 4 | `--info-ink` border | ◐ |
| pass | 1px `--ok-ink`, solid | `--ok-ink` border | ✓ |
| fail | 1px `--bad-ink`, dash 4 2 | `--bad-ink` border | ✕ |
| error | 1px `--warn-ink`, dash 1 3 | `--warn-ink` border | ! |
| not planned | no edge | dashed `--line-strong` | none |

An edge takes the worst state of the cells it carries, in this order: fail, then
error, then active, then pass, then pending. The verdict node takes its tone
from the gates, not from the cells, because a run can pass every cell and still
fail a gate.

Accessible name: exactly one `role="img"` per graph, labelled by its `<title>`
and described by its `<desc>`. The description is generated, never authored by
hand: "4 candidates over 12 cases produce 48 cells; 3 gates, 1 failed; verdict:
gate failed." The graph may not be the only place a number appears: every number
in the description is also in the verdict bar or the heatmap summary, and the
figcaption repeats the counts on the full rendering. The thumbnail is
`aria-hidden="true"` with `focusable="false"` because the row cells carry the
same facts. Nodes are not links and not focusable; selection belongs to the
heatmap, so the graph never adds a second tab stop per node.

Reduced motion: the lit-path sweep is a class change (`is-lit`) on edges as
cells resolve, transitioned at 120ms on `stroke` and `stroke-dasharray`, and
removed entirely under `prefers-reduced-motion: reduce`, where the class swaps
with no transition. No SMIL, no keyframes in the SVG.

No JavaScript: the SVG is server-rendered with the final states at page load.
The stream handler only adds classes; with JavaScript off the graph is correct,
the page does not update itself, and the event history below the progress bar
plus a reload is the path. The existing `<noscript>` note says so.

Below 700px the SVG is hidden and the [Stage list](#stage-list) renders the same
five stages, so the graph never scrolls horizontally.

### Stage list

Proposed, phase 1. The mobile rendering of the spec graph, shown below 700px.

```html
<ol class="stage-list" aria-label="Spec graph">
  <li class="stage">
    <p class="stage-name">Cases <span class="mono">12</span></p>
    <p class="stage-state tone-pass"><span aria-hidden="true">✓</span> 48 of 48 cells recorded</p>
  </li>
  <li class="stage stage-linked tone-pass">
    <span class="stage-connector" aria-hidden="true"></span>
    <p class="stage-name">Candidates <span class="mono">4</span></p>
    <p class="stage-nodes">scripted, judges, gated</p>
  </li>
</ol>
```

Classes: `stage-list`, `stage`, `stage-linked`, `stage-name`, `stage-state`,
`stage-nodes`, `stage-connector`, `tone-*`.

Rules: one stage per graph column, same order, so the two renderings teach the
same structure. The heading repeats the count in mono; the state line repeats
the glyph and a word. The connector is a 2px rule carrying the tone and the dash
pattern of the edges that leave the stage, and it is `aria-hidden` because the
state line already says it. Names wrap instead of truncating: on a phone the
name is the content. No horizontal scroll, no nested table, no links (nodes are
not interactive in the SVG either).

States: the same six as the graph, rendered as text and a 2px rule, never
colour alone.

### Matrix table

Proposed, phase 2. Wizard step 3, one row per candidate.

```html
<div class="table-wrap matrix-wrap" tabindex="0" role="region" aria-label="Candidates">
  <table class="matrix">
    <caption class="sr-only">Candidates with model, prompt, context, credential, temperature and max tokens</caption>
    <thead>
      <tr><th scope="col">Name</th><th scope="col">Model</th><th scope="col">Prompt</th>
        <th scope="col">Context</th><th scope="col">Credential</th>
        <th scope="col" class="num">Temperature</th><th scope="col" class="num">Max tokens</th>
        <th scope="col" class="num">Replicas</th><th scope="col"><span class="sr-only">Remove</span></th></tr>
    </thead>
    <tbody>
      <tr class="matrix-row">
        <td><input class="matrix-input" aria-label="Candidate 1 name" value="scripted"></td>
        …
        <td class="num"><input class="matrix-input" aria-label="Candidate 1 temperature"
              type="number" step="0.01" inputmode="decimal" value="1.4"></td>
        <td><button class="icon-btn" aria-label="Remove candidate 1">✕</button></td>
      </tr>
    </tbody>
    <tfoot>
      <tr class="matrix-add"><td colspan="9"><button class="btn btn-sm">Add candidate</button></td></tr>
    </tfoot>
  </table>
</div>
```

Classes: `matrix-wrap`, `matrix`, `matrix-row` with `is-invalid`, `matrix-input`,
`matrix-add`, the derived count as a `chip` in the panel head.

Rules: a header cell is not a label for a control, so every input repeats its
column and row in `aria-label`. A row is added by a server POST that re-renders
the step, so the wizard works with JavaScript off; the enhancement clones the
last row, increments the labels and updates the count chip, and the server
rejects a malformed clone. Derived cell count (candidates x cases x replicas) is
shown in the panel head and next to the submit button, because it is the number
that changes the cost. Remove is disabled with a hint when one candidate would
remain. Temperature and max tokens are numeric inputs with tabular figures.

States: empty (one blank row plus the add action), invalid cell
(`aria-invalid`, `.field-error-text` under the input, 3px `--bad-ink` left rule
on the row), incomplete (warn tone chip "missing prompt", submit enabled but the
step explains the consequence), at the cap (add disabled, hint names the cap),
row removed (count updates, focus returns to the add button). Below 700px each
row becomes a `candidate-card` with a definition list and `data-label` pseudo
elements, the same technique as `compare-metrics`; a nine-column table does not
scroll sideways on a phone.

### Wizard rail and step strip

Proposed, phase 2.

```html
<nav class="wizard-rail" aria-label="Steps">
  <ol class="step-list">
    <li class="step is-current" aria-current="step">
      <span class="step-link"><span class="step-number mono">2</span>Cases and gates</span>
    </li>
    <li class="step is-done">
      <a class="step-link" href="?step=1"><span class="step-number mono">1</span>What varies</a>
    </li>
    <li class="step is-todo">
      <span class="step-link"><span class="step-number mono">3</span>Candidates</span>
    </li>
  </ol>
  <p class="step-summary"><span class="mono">48</span> cells, estimate <span class="mono">$0.0042</span></p>
</nav>
```

Classes: `wizard-layout`, `wizard-rail`, `step-list`, `step` with `is-current`,
`is-done`, `is-todo`, `step-number`, `step-link`, `step-summary`,
`wizard-estimate`; below 900px, `step-strip` with four `step-segment` children.

Rules: four steps in plain verbs (1 What varies, 2 Cases and gates, 3
Candidates, 4 Review and save). The rail is 20rem, sticky at `top: 72px` under
the 56px bar. The current step carries `aria-current="step"` and a 2px accent
left rule; completed steps are links and get a check glyph; future steps are
spans, not disabled anchors, so they stay out of the tab order and are not
announced as controls. The rail never holds the Next action: the step's form
does, so there is one primary action per screen. The rail repeats the cell count
and the estimate so navigation never loses the cost.

Below 900px the rail becomes a strip above the content: four segments, the
current one with a 2px accent bottom rule, completed ones with a check. At 375px
the strip shows numbers only and the current step's label is the panel heading;
it never scrolls horizontally, and each segment is at least 44px tall.

States: current, done, future, invalid step (the segment carries a warn glyph
when a previous step has an unresolved error), and the final review step where
the strip is fully done and the primary action is Save.

### Estimate hero

Proposed, phase 2 for the wizard; the launch page already ships this component.

```html
<section class="estimate-hero" aria-labelledby="estimate-heading">
  <div class="panel-title-row">
    <h2 id="estimate-heading">Cost estimate</h2>
    <span class="chip">observed medians</span>
  </div>
  <p class="estimate-total" title="$0.004210 USD">$0.0042</p>
  <p class="field-hint">48 cells, 1,204 tokens per cell</p>
  <div class="estimate-meter">
    <span class="meter is-over"><span style="width: 84%"></span></span>
    <div class="estimate-meter-labels"><span>84% of the cap</span><span>cap $0.005000</span></div>
  </div>
  <dl class="facts">…</dl>
</section>
```

Classes: `estimate-hero`, `estimate-total`, `estimate-meter`,
`estimate-meter-labels`, `meter` with `is-over`, `facts`, `chip`.

Rules: the total is the largest number on the page: 32px in the launch hero,
26px in the editor's estimate card. The exact value lives in `title`, the
readable value on screen, so the page never shows four decimals it cannot
support. The chip always
says which medians produced the number, observed or fallback. The meter is 5px
and `aria-hidden`; its labels carry the percentage and the cap. The facts list
is cells, tokens, spec budget, headroom and medians.

States: observed, fallback (the chip says so and a hint explains which runs
produced it), over cap (`is-over` plus a warn callout), no cap (no meter, the
copy says "No budget cap on this spec"), unavailable (an error callout with the
reason, never `$0.00`), locked (the vault callout sits above the form and the
estimate still renders). Below 900px the rail stacks under the form in the order
form, estimate, steps.

### Tone chips

The chip tones already exist; the semantic attribute is proposed for phase 2.

```html
<span class="chip chip-warn" data-tone="warn">
  <span class="chip-glyph" aria-hidden="true">!</span>over budget
</span>
<span class="chip chip-info" data-tone="info">no checks</span>
<span class="chip">overall</span>
```

Classes: `chip` plus `chip-ok`, `chip-warn`, `chip-bad`, `chip-info`,
`chip-neutral`. The `data-tone` attribute and the `chip-glyph` span are
proposed, phase 2; the tone classes ship today.

Rules: a tone chip carries a meaning and therefore a word; the glyph is
`aria-hidden` and never the only signal. At most one tone chip per row, the rest
stay plain metadata chips, otherwise the tone stops being scarce. A tone chip is
never a button: if it acts, it is a `btn btn-sm`. Chips stay at 11.5px / 650 /
pill radius and drop to 10.5px at 520px.

States: ok, warn, bad, info, neutral, and plain (no tone). Contrast comes from
the tone table; no new colour is introduced for a chip.

### Inline field errors

The stylesheet has `.field-error-text` today and no template uses it; the shape
is specified here for phase 2 and the YAML editor keeps its `#spec-errors` list.

```html
<div class="field">
  <label for="cand-1-temp">Temperature</label>
  <input id="cand-1-temp" name="temperature" type="number" step="0.01" value="1.4"
         aria-invalid="true" aria-describedby="cand-1-temp-error"
         aria-errormessage="cand-1-temp-error">
  <p class="field-error-text" id="cand-1-temp-error">
    temperature 1.4 is outside 0 to 2. Use a value from 0 to 2.
  </p>
</div>
```

Classes: `field`, `field-error-text`, and the row-level `matrix-row is-invalid`;
the older `field-error` block stays for the spec editor's error list.

Rules: the message names the field, the value, the reason and the fix, in that
order. `aria-invalid` plus `aria-describedby` is the minimum; `aria-errormessage`
is added because some ATs prefer it. The message is never a tooltip and never
only a red border. On a failed POST the first invalid control is focused, the
step's summary lists every error with links to the fields, and the fields keep
their values. Native validation bubbles are suppressed (`novalidate` on the
form) so the message is the same in every browser.

States: pristine, invalid, corrected on the next POST, multiple errors (summary
plus per-field messages), and server error (a callout above the form, fields
unchanged). The invalid control keeps a visible focus signal via the fix in
[Focus ring](#focus-ring).

### Skeleton rows

Proposed, phase 4. Only for a table region that a fetch replaces, which is not
the case on any page today; the heatmap's pending cells already use `skeleton`.

```html
<tr class="skeleton-row" aria-hidden="true">
  <td><span class="skeleton" style="width: 9rem"></span></td>
  <td><span class="skeleton" style="width: 4rem"></span></td>
</tr>
```

Classes: `skeleton-row`, `skeleton`, and `aria-busy="true"` on the replaced
`role="region"`.

Rules: the row is exactly as tall as a real row so the table does not jump. At
most five rows. The wrapper is `aria-busy` and a polite live region says what is
loading ("Loading runs"), because the skeleton itself is `aria-hidden`. A
skeleton is never used where the server can render the real thing, and there is
no spinner anywhere.

States: loading (skeleton rows), loaded (rows replace them), error (a callout
replaces the region and the busy state is cleared).

### Empty states per screen

The `empty-state` primitive is documented above; this table fixes the copy and
the single action per screen.

| Screen | Heading | Sentence | Action |
| --- | --- | --- | --- |
| Runs, none | No runs yet | Start one from a terminal with `evalh run spec.yaml`, or launch a saved spec. | Launch a run |
| Runs, filtered | No runs match these filters | Clear the status or spec filter to see the rest. | Clear filters |
| Run detail, no cell | Select a cell | Pick a cell in the heatmap to read its answer, checks and artifacts. | none |
| Run detail, no checks | No checks were recorded for this cell | The candidate errored before metrics ran, so the answer is the only evidence. | Open evidence JSON |
| Compare, fewer than two | Two runs are needed | Pick a baseline and a candidate from the runs list. | Choose runs |
| Compare, no difference | No measurable difference | The intervals overlap and the point estimates differ by less than the noise. | none |
| Comparisons, none | No saved specs | Specs live in the specs directory; add one with `PUT /api/specs/<name>`. | none |
| Launch, no spec | Choose a spec | The estimate appears once a spec is selected. | none |
| Credentials, none | No credentials stored | Add an API key to run a live candidate; scripted candidates need none. | Add a credential |

Each state uses the ∅ glyph (◎ for the unselected cell), a left-aligned
heading, one sentence and at most one action. An empty state is not an error: an error is a callout with the failure
text, or an error page.

### Focus ring

Mechanics, in order of specificity:

- The global rule is `:where(a, button, input, select, textarea, summary, [tabindex]):focus-visible`
  with `outline: var(--focus-ring)` and `outline-offset: var(--focus-offset)`.
  `[tabindex]` is what gives the table and heatmap scroll regions their ring.
- Inputs replace the outline with a 3px `--accent-soft` halo and an accent
  border, which stays visible on a tinted callout.
- The skip link is fixed at `top: -4rem` and moves to `top: var(--space-3)` on
  focus, above the top bar: z-index 100 against the bar's 40.
- Never write `outline: none` without an equivalent visible replacement, and
  never use a positive `tabindex`.

The known failure: an invalid input loses focus, because
`input[aria-invalid="true"]` is declared after `input:focus-visible` with equal
specificity. Proposed, phase 1:

```css
input[aria-invalid="true"]:focus-visible {
  box-shadow: 0 0 0 3px var(--bad-bg), 0 0 0 5px var(--accent);
}
```

That keeps the bad halo and adds the accent ring outside it, so the control
reads invalid and focused at the same time.

States: default, hover, focus-visible, invalid, invalid plus focus, and the
scroll-region ring on the table and heatmap wrappers. Selection rings (the
heatmap's double ring) are not focus rings and never replace one.

### Sticky table header

Rules for a table longer than the viewport:

```css
thead th {
  position: sticky;
  top: 0;                  /* inside .table-wrap, the scroll container */
  z-index: 1;
  background: var(--surface-2);
}
```

The header cell needs an opaque background, or rows show through it. The z-index
stack is: top bar 40, sticky head 1, and inside the heatmap the head row 2 with
the sticky row head at 1 and the head's own row head at 3.

The wrapper is the scroll container on both axes: `.table-wrap` is
`overflow: auto` with `max-height: 70vh`, so the header pins to the top of the
table's own viewport while the body scrolls underneath it, and a table wider than
a phone still scrolls horizontally. A long table therefore scrolls inside its
region, not with the page; keep that in mind when a page promises both.

## States

| State | Treatment |
| --- | --- |
| Empty | `empty-state` with an icon, a plain sentence and the next action. Filtered-empty names the filter and offers Clear. |
| Loading | server-rendered pages have no spinner; a live heatmap uses `skeleton` cells and the progress region reports done / total. |
| Partial | a `status-partial` chip, an info callout explaining which cells are missing, and a heatmap with pending cells. |
| Error | a `callout-error` with `role="alert"` for a run error, and a warning glyph on the cell. |
| Cancelled | a neutral `status-cancelled` chip, "Cancelled after N of M cells", and the kept cells still selectable. |
| Budget exceeded | a bad-toned status, a warning callout naming the cap, and the spent/ cap meter in the verdict. |
| Gate failed | a separate `gate-chip` in lists, a warning verdict line on the run page, and a gate card with observed vs threshold. |

## Motion

Every motion in the stylesheet answers a state: pointer hover, focus, a live run,
or a cell the run has not recorded. The spec graph is the only addition in
phase 1. Each is a state change; none plays after the state is final.

| Motion | Property | Duration | Verdict |
| --- | --- | --- | --- |
| heat cell hover | transform, box-shadow | 100ms | keep; transform is compositor-only, the shadow is paint-only |
| button hover | background, border, colour | 120ms | keep |
| skip link | `top` | 150ms | replace with `transform` in phase 1 |
| progress fill | `width` | 350ms | replace with `transform: scaleX` in phase 1 |
| skeleton shimmer | background-position | 1.5s loop | keep on a real pending cell, static under reduced motion |
| live dot pulse | box-shadow | 1.8s loop | keep, static ring under reduced motion |
| spec graph edge | stroke, dash pattern | 120ms, phase 1 | new, none under reduced motion |

New motion is transform or opacity only, per the anti-slop rule. Colour,
border-colour and box-shadow transitions already ship, they are paint-only and do
not reflow, so they stay and are not extended. Animating `width`, `height`, `top`
or `left` is not allowed; the two cases above are legacy and are replaced in
phase 1. No load reveals, no scroll-driven animation, no count-ups, no
`will-change` left on an element, no animation on focus.

Content is visible at first paint. Nothing waits for JavaScript to become
readable, and no element starts at `opacity: 0`.

Reduced motion, proposed phase 1, because the stylesheet has no such block
today:

```css
@media (prefers-reduced-motion: reduce) {
  .live-dot { animation: none; }
  .skeleton { animation: none; background: var(--surface-3); }
  .heat-cell a, .progress-fill { transition: none; }
  .spec-graph .graph-edge.is-lit { transition: none; }
}
```

The block is targeted, not a global `animation-duration: 0.01ms` kill: the
shimmer and the pulse are replaced with static states that still carry the
meaning, rather than being sped up into a flicker.

## Accessibility checklist

- Skip link is the first focusable element; focus order is DOM order.
- The active section carries `aria-current="page"`.
- Every status is glyph + word; every heatmap cell has `aria-label` and
  `title`; the selected cell also has `aria-current`.
- Tables have `caption` and `scope`; numeric columns are right aligned.
- Forms have a visible `<label>`, a hint via `aria-describedby`, and
  `aria-invalid` plus a linked error when validation fails.
- The SSE panel is `aria-live="polite"`; the copy action reports through a
  separate live region so it never steals focus.
- Contrast targets AA in both schemes; `--faint` is metadata only.
- `prefers-reduced-motion` replaces the shimmer, the pulse and the graph sweep
  with static states; the block is specified under [Motion](#motion) and is not
  in the stylesheet yet (phase 1).
- Focus is visible on every control, including an invalid input
  (see [Focus ring](#focus-ring)).
- Phone targets are 44 x 44 at 375px (see [Tap targets](#tap-targets); not met
  today).

### Contrast pairs actually used

Measured from the token values in this document, WCAG 2.1, both schemes:

| Pair | Light | Dark | Used by |
| --- | --- | --- | --- |
| `--ink` on `--surface` | 17.5 | 15.4 | body text |
| `--ink` on `--canvas` | 16.2 | 16.4 | page text |
| `--ink-2` on `--surface` | 10.3 | 11.1 | labels, dense values |
| `--muted` on `--surface` | 5.6 | 7.0 | captions, hints |
| `--muted` on `--surface-2` | 5.0 | 6.5 | table head, hover rows |
| `--accent` on `--surface` | 7.6 | 10.3 | links, live dot |
| `--accent-ink` on `--accent` | 7.6 | 10.0 | primary button |
| `--ok-ink` on `--ok-bg` | 8.0 | 9.8 | ok status, pass cell |
| `--bad-ink` on `--bad-bg` | 8.8 | 9.8 | fail cell, error |
| `--warn-ink` on `--warn-bg` | 7.6 | 9.9 | gate failed, warning |
| `--info-ink` on `--info-bg` | 10.0 | 9.6 | running, no checks |
| `--neutral-ink` on `--neutral-bg` | 7.0 | 8.0 | cancelled, disabled |
| `--bad-ink` on `--surface` | 10.3 | 11.4 | field errors, danger button |
| `--warn-ink` on `--surface` | 8.5 | 12.7 | warning text outside a callout |
| `--faint` on `--surface` | 5.13 | 5.53 | metadata only, passes AA |

Pairs not in this table are not used for text. The lowest text pair in use is
`--muted` on `--surface-2` at 5.0 (light). `--faint` was lifted from `#8a948f`
(3.13:1) to `#66706a` (5.13:1, 4.59:1 on `--surface-2`) in light and from
`#74817a` (4.46:1) to `#84918a` (5.53:1, 5.12:1 on `--surface-2`) in dark, so it
passes AA wherever it carries text. It is still only for ids, timestamps and
placeholders, never a label, a control or a sentence: the rule is about
hierarchy, not about the ratio. Border and glyph-only pairs are decorative
because the glyph and the word carry the meaning.

### Tap targets

The rule is 44 x 44px at 375px. Vertical neighbours are separated by at least
8px so a mis-tap is recoverable.

| Class | Desktop height | Coarse pointer and at most 768px |
| --- | --- | --- |
| `.btn` | 32px | 44 x 44 |
| `.btn-sm` | 26px | 44 x 44 |
| `.icon-btn` | 28px | 44 x 44 |
| `.copy-button` | 20px | 44 x 44 |
| site nav link | 30px | 44 x 44 |
| step link | 30px | 44 x 44 |
| heat cell link | 39.2px | 44 x 44 |

The dense sizes stay on a fine pointer: DENSITY is 8 and a desktop button does
not need 44px. Two media blocks raise `.btn`, `.button-link`, `.btn-sm`,
`.icon-btn`, `.copy-button`, `.site-nav a`, `.step-link` and `.heat-cell a` to
`min-width: 44px; min-height: 44px`: `(pointer: coarse)` covers a touch laptop
at any width, `(max-width: 768px)` covers a narrow window driven by a mouse. Grow
the visible box; do not add an absolutely positioned 44px overlay, because
overlapping hit areas steal taps from the next control in a dense table. The
3.1rem heat column leaves room for the 44px link without reflowing the matrix.

### Focus

- The ring is 2px `--accent` at 2px offset, or the 3px input halo; both are
  visible on canvas, surface and every callout tone.
- Scroll regions are focusable and carry the same ring, so a keyboard user can
  scroll the heatmap.
- The invalid-input override in [Focus ring](#focus-ring) is the only known
  place where focus disappears; fix it before phase 2.
- Selection is never the focus indicator: the heatmap's double ring marks the
  chosen cell, the outline marks the focused one.

## Pre-flight checklist

A reviewer ticks these before merging. Each line points at the section that
defines it; a line that cannot be ticked blocks the merge, and an exception is a
change to this document in the same pull request.

- [ ] No banned item is present: no webfont, no purple or neon accent, no `#000`,
      no row of three identical cards, no emoji, no cliche, no `100vh`, no
      spinner, no centred hero, no mixed-stroke icon.
- [ ] The spec graph appears once, is the only diagram on the screen, and reads
      at 96px, at 320 units in the wizard rail and at full width. Compare shows
      two and nothing else competes.
- [ ] Every state that can occur exists: empty, loading, error, running, partial,
      cancelled, denied, success, and the filtered-empty for lists.
- [ ] Every animation and transition is listed under Motion, is bound to a state
      change, and is replaced by a static state under `prefers-reduced-motion`.
- [ ] Focus is visible on every control, including invalid inputs, scroll regions
      and links inside tables; no `outline: none` was added.
- [ ] Every text pair on the screen is in the contrast table and clears 4.5:1;
      `--faint` is not used for a label, a control or a sentence.
- [ ] At 375px every target is at least 44 x 44 and nothing scrolls horizontally
      except a table region; the wizard rail uses the compact graph.
- [ ] Numbers are tabular and right aligned; ids, hashes and counts are mono; a
      null renders undefined, and every rate shows its n and interval.
- [ ] The flow completes with JavaScript off: the graph is server-rendered, the
      wizard POSTs per step, and no content starts hidden.
- [ ] The copy is nouns and measurements, not slogans; the empty state names the
      next action, not the product.
- [ ] New CSS references tokens only: no literal colour, size or radius outside
      `:root`, and any new token is in the token tables.
- [ ] The screen prints: no top bar, no rail, no live panel, black on white,
      hairlines still visible.

## Print

The print stylesheet drops the top bar, footer, filters, page actions, live
panel, editor actions and credential form. It forces white surfaces, black
text, no shadows, visible hairline borders and black underlined links, and it
keeps panels and tables from splitting across pages.
