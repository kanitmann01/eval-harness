## What an eval harness actually is

A harness starts with one declarative YAML spec. That file names the source of cases, the repositories the run needs, the candidates with their provider, model, prompt variant and context setting, the metrics, per-model prices, and optional gates. It holds no secret, only the logical name of a credential the vault resolves at run time. The spec is the source of truth: a run snapshots the spec and the price table, so last month's number keeps its meaning after the file changes.

The planner expands that spec into cells: one per candidate times case times replica, with deterministic ids and an order fixed by the seed. A cell is the unit of execution, cost, evidence and retry; the whole matrix is decided before the first request.

Six protocols define the boundary. A Candidate takes a case and a run context and returns an answer with citations, tool calls, tokens, latency, usage source and artifacts, never a pass or a fail. A Judge turns a request into a verdict with a reason and evidence, plus its own tokens and cost. A Metric turns a recorded cell into checks. A Source loads the case set. A Sink writes results to a destination other than the database; the JSON sink writes artifacts while the database stores paths. A ContextSelector picks the chunks that one case receives. Core imports those protocols and a registry, never a concrete adapter.

The 0.4 layer adds two inputs. A repository is a local root or an HTTPS URL, an optional ref and credential, include and exclude globs, caps, and a policy for symlinks and binaries. Before the first cell the harness copies the included files into a content-addressed tree under $EVALH_HOME/repos/<digest>, with a manifest, and reuses that directory when the digest repeats. A repository source reads cases and fixtures from the snapshot, so the case file and the code it tests cannot drift apart silently.

Context selection sits between planning and execution. A selector is synchronous, returns the same chunks for the same seed, and runs once per candidate and case. The cell records the chunk ids, the selector name and a digest, which makes a context comparison one variable, not a story.

[[FIGURE 1]]
Figure 1. One run, end to end.

The pipeline runs left to right: spec, plan, select, execute, score, aggregate, record. The adapter seam sits under execute and score, and inputs arrive from the spec, a repository snapshot and a context selector. Storage sits under the run, holding every cell, check and gate.

That seam is what a customer with an existing agent feels first. Their agent joins by exposing one HTTP endpoint that takes a case and returns an answer, with no harness code and no Python package imported. They keep what they built and get scoring, intervals, cost split and a receipt around it.

The receipt is the only object meant to leave the building. It carries the run and spec ids, the spec hash, the harness revision, the operator, the seed, UTC timestamps, the candidates, the judge pins with their configuration hashes, the price snapshot fingerprint, the gates, the diff to the previous run, and the Source panel. It is read-only and built from the run's snapshot, not the current files.

## How a run actually goes

Validate first. A run loads the spec, checks the repository declarations, resolves credentials through the vault, and writes the spec and price snapshots. No repository is read at this stage, so a bad path or an unreadable URL fails before any spend.

Then the snapshot. The harness copies the included files into the content-addressed tree, writes a manifest with file hashes, the commit and the dirty flag, and records the adapter, the repository rows and the digest in the run's provenance. Cases load from that snapshot, fixtures resolve inside it, and every case prompt is hashed.

Planning comes next. The planner writes the cell list, and for every candidate and case pair it asks the context selector for chunks, caches the result, and writes the chunk ids, selector name and digest into the variant.

Execution runs the cells with bounded concurrency. Transport failures retry up to the candidate's declared max_attempts. The budget guard runs after every finished cell, a cancel token is honored between cells, and a resume skips cells that are already ok and keeps the same run id.

Each cell goes to its candidate through the adapter and comes back with an answer and its usage, which is the only thing a candidate may conclude. Then the metrics run. A judge-backed metric awaits its judge, and the verdict carries a reason, evidence, its own tokens and its own cost. That cost is attributed to the cell that triggered it, kept apart from answer cost, and rolled up per provider.

Aggregation follows: n, the value, the 95 percent Wilson interval, and pass@k and pass^k when replicas exceed one. No samples means undefined, not zero. Gates read the aggregates after the run, never during it.

Storage writes specs, runs, cells, checks, gates, events and the source snapshot. Artifacts stay on disk, the database keeps paths and digests and never repository bytes, and the encrypted vault holds the credentials. One redactor wraps logs, events, artifacts, cell records and HTML, so a stored key cannot reach a page or a receipt.

The console reads the same records: run list, candidate by case heatmap, per-cell evidence, compare with deltas and cost per correct answer, decision view, receipt, calibration, and the Source and Context panels.

Exit codes are the automation contract. A run whose gates pass exits 0, even when some cells failed or the run was cancelled. A gate failure is 1, a spec error is 2, a budget abort is 3.

## What changed in practice

A tie stopped looking like a decision. An eight of ten result spans 0.49 to 0.94 at 95 percent, so a headline rate alone cannot separate two arms, and when the interval on the delta crosses zero the compare view says inconclusive instead of naming a winner. Cost sits beside the score, because a point bought at many times the price is a different decision, not a better one. The judge is pinned: its adapter, model and options are hashed into the run, and a change to any of them is reported as drift that blocks a like-for-like claim. The receipt is the artifact we hand a client, read-only and built from the run's snapshot. A gate in CI turns an eval into a release check, and a parity test replays a recorded three-arm scoreboard through the CLI and asserts the totals, so a scorer or cost regression fails the suite before it can move a number anyone trusts.

The 0.4 additions extend that same discipline to inputs. The receipt gains a Source panel with the digest, commit, files, bytes, skipped count and dirty flag, and the diff gains a source category before cases, so a changed tree shows up as a change instead of hiding behind an unchanged spec hash. A Context panel on the run page lists the selector, the selection digest, the chunk count and bytes, with the first chunks per candidate as evidence. Resume refuses a repository whose digest changed, exiting 2 on the repos.<name> field, because continuing a half-finished run against different code would produce a number no one can defend. And since snapshots are shared and content-addressed, evalh repos gc, with a dry run, removes the ones no recorded run references.

None of that is a changelog item on its own. It is the same question, asked about inputs instead of scores: can we say exactly what produced this number, and would the answer survive a rerun? The closing section takes that question somewhere smaller: twenty cases, one gate, and a receipt you keep.
