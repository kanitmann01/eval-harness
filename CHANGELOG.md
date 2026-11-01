# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html), and every release is
compared against the spec of record in issue #1.

## [0.2.0] - 2026-11-01

The console becomes the front door: a spec you can build, edit and compare from
the browser, with the same redaction and credit rules as the CLI.

### Added

- Console v2 shell and navigation. One server-rendered layout with `Runs`,
  `Specs`, `Compare`, `Launch` and `Credentials` sections, a `New comparison` primary action,
  a skip link, a favicon served inline (no CDN request) and an active-section
  marker. Still Jinja2 and FastAPI only: no Node, no SPA build, no webfont.
- Spec graph on every list and detail page: a 144 x 48 thumbnail in the runs
  table, the full graph on a run page, 640 units side by side on compare and a
  320-unit rail inside the wizard.
- Four-step comparison wizard at `/comparisons/new` (what to vary, add context,
  choose candidates, cost and rules). It is stateless, re-validates a hidden
  draft on each step, writes nothing until step 4, then returns to the list.
- Structured edit form at `/comparisons/{name}/edit`: every section of the spec
  on one page, a sticky estimate rail, `Save`, `Save and run` and a `View YAML`
  escape hatch. A file changed on disk since load is refused with 409.
- Raw YAML editor at `/specs/{name}/edit` with Check and Save. An invalid
  document is never written; a valid one restarts the run list's estimate.
- Compare chooser at `/compare`: two runs, or one run and two candidates, with
  deltas, intervals, verdict, cost per correct answer and both spec graphs. An
  empty query renders the chooser instead of a 404.
- Credentials page at `/credentials`: add, mask, test, disable, enable and
  delete provider keys; a missing master key renders the bootstrap page (503)
  instead of a stack trace.

### Changed

- Every timestamp in the console is UTC, rendered as `YYYY-MM-DD HH:MM:SS UTC`
  without a local-time conversion, so two machines reading the same run agree.
- Costs are formatted for reading: `$0.00` for zero, two decimals from one cent
  up, three significant digits below that, and the exact value in the `title`
  tooltip. A real cost never collapses to `$0.00`.
- Shareable HTML error pages: not found (404, names what was asked and the way
  back), spec conflict (409), run-in-flight refusal (409) and vault unavailable
  (503). The JSON API keeps returning JSON errors.

### Fixed

- The invalid-input focus ring no longer disappears; an invalid control keeps a
  visible accent ring outside its error halo.
- The heatmap row rate is visible on phones (it was hidden below 700px).
- Touch targets are at least 44 x 44 on a coarse pointer and at 375px: buttons,
  links, copy buttons, step links and heatmap cells.
- `--faint` is lifted to 5.13:1 light and 5.53:1 dark and is metadata only.
  The unused `--on-accent-muted` token is deleted.
- Long tables keep a sticky header inside their own scroll region.
- One dead token, one dead CSS rule and the last stale "phase 1" note removed
  from the design system; the document now describes what ships.

### Operations

- `scripts/demo.py`: one command that builds a throwaway demo home, generates a
  master key, copies the four offline specs, runs them (the gate-failure witness
  exits 1), prints the talk track, the run ids and the console URL, then serves.
  No network and no paid key.
- README front door rewritten around the stranger's path and the console, with
  the committed merge conflict resolved and the backup/restore section intact.
- Console and design-system docs updated for v2 routes, the wizard, the edit
  form, the compare chooser, the error pages and the UTC timestamp rule.

## [0.1.0] - 2026-10-25

- Spec-driven runs: validate, plan, execute with bounded concurrency and a
  budget, score, aggregate with intervals, gate, and write artifacts.
- Encrypted BYOK credential vault, provider and judge adapters, the adapter
  conformance suite, the smoke script and the Docker image with PostgreSQL.
