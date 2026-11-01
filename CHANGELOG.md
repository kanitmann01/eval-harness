# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html), and every release is
compared against the spec of record in issue #1.

## [0.3.0] - 2026-11-01

The Trust release: the console explains whether a result is believable, not just
what the score was.

### Added

- Decision view at `/runs/{id}/decision` and `/api/runs/{id}/decision`: ranked
  candidates with checks, pass rate and its 95 percent Wilson interval, cost per
  correct answer, p50/p95 latency, cells, dominance and a Pareto scatter over
  every candidate that has a defined rate and cost. Three or more candidates are
  compared without inventing a winner.
- What-would-change-my-mind line under the compare verdict: the samples per side
  needed for the observed delta to exclude zero at 80 percent power and a
  two-sided alpha of 0.05.
- `evalh sample-size --baseline RATE --effect DELTA` with `--power`, `--alpha`
  and `--json`. Invalid arguments exit 2 and name the field, the value and the
  fix.
- Structured spec diff between two runs: cases and their prompts, candidate
  adapter and model swaps, judge declarations, price rates, run fields and gates.
  Judge configuration changes are reported as drift and block a like-for-like
  claim.
- Run receipt at `/runs/{id}/receipt` and `/api/runs/{id}/receipt`: spec id and
  hash, harness revision, operator, seed, UTC timestamps, candidates, judge pins
  with configuration hashes, price snapshot fingerprint, gates and the diff to
  the previous run.
- Judge calibration at `/runs/{id}/calibration`: human pass or fail per stored
  check, agreement rate with a 95 percent Wilson interval, labelled and
  unlabelled counts, one row per judge configuration with its last-validated
  stamp, and the disagreement list linked to each cell. Labels live in the new
  `human_labels` table; the stored check is never edited.
- `EVALH_OPERATOR` is recorded on every run and shown on the receipt; the demo
  sets it to `demo`.

### Changed

- The run page links `Decision`, `Receipt`, `Calibration`, `Compare` and
  `All runs`. The compare page links both receipts and shows their spec diff
  above the metric table.
- `create_app` always resolves a database, including when a repository is passed,
  and keeps the health endpoints working with a repository double.
- Database schema version 7 adds the nullable `runs.operator` column; opening a
  version 6 database migrates it without losing a run.
- The demo talk track covers Decision, Receipt, Calibration and Diff, so the four
  seeded specs demonstrate the Trust surfaces without a new script.

### Fixed

- The receipt label is `Harness revision`, matching the value it shows: the
  `EVALH_HARNESS_SHA` when the build sets one, otherwise the package version.
- A spec diff field the baseline never declared reads `absent`, not `not
  recorded`; the two mean different things.

### Docs

- README console section now documents the four Trust routes, the sample-size
  command, operator recording and the exact offline demo; `docs/console.md` adds
  the Trust pages to the map. [docs/decision.md](docs/decision.md),
  [docs/statistics.md](docs/statistics.md), [docs/provenance.md](docs/provenance.md)
  and [docs/calibration.md](docs/calibration.md) are the deep references.

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
