## 4. What an eval harness actually is

A harness starts with one declarative YAML spec. That file names the source of cases, the candidates with their provider, model, prompt variant and context setting, the metrics to score, the prices per model, and optional gates. It holds no secret, only the logical name of a credential the vault resolves at run time. The spec is the source of truth: a run stores a snapshot of it and of the resolved price table, so last month's run keeps its meaning after the file changes.

The planner expands that spec into cells: one per candidate times case times replica, with deterministic ids. A cell is the unit of execution, cost, evidence and retry.

The harness is the product, and a pipeline is a candidate behind a protocol. Five protocols define the boundary. A Candidate takes a case and a run context and returns an answer with citations, tool calls, tokens, latency, usage source and artifacts, never a pass or a fail. A Judge turns a request into a verdict with a reason and evidence. A Metric turns a recorded cell into checks. A Source loads the case set. A Sink writes artifacts outside the database. Core imports those protocols and a registry, never a concrete adapter.

[[FIGURE 1]]
Figure 1. One run, four layers.

Look at the direction of the arrows: dependencies point down, surfaces call core, core calls adapters, and core never imports an adapter, which is what makes a new candidate a plugin rather than a patch.

That seam is what a customer with an existing agent feels first. Their agent joins by exposing one HTTP endpoint that takes a case and returns an answer, with no harness code and no Python package imported. They keep what they built and get scoring, intervals, cost split and receipt around it.

## 5. How a run actually goes

A run starts where the diagram does: the spec and a snapshot of the price table, so every cost is computed against rates that cannot move under it. The planner writes the cell list. The executor schedules those cells with bounded concurrency, retries transport failures up to the attempt budget the candidate declares, checks the budget guard after every finished cell, and honors a cancel token between cells. A resume skips cells already ok and keeps the same run id, so an interrupted run continues instead of restarting.

Each cell goes to its candidate through the adapter. The candidate returns an answer and its usage, which is the only thing a candidate is allowed to conclude.

The scorer runs the metrics. A metric writes checks, one per cell per metric. A judge-backed metric awaits its judge, and the verdict carries a reason, evidence, its own tokens and its own cost. That cost is attributed to the cell that triggered the judge, kept separate from the answer cost, and rolled up per provider.

The aggregates follow: n, the value, the 95 percent Wilson interval, and pass@k and pass^k once replicas exceed one. No samples means undefined, not zero. Gates read those aggregates after the run. Storage keeps specs, runs, cells, checks, aggregates, gates and events, with artifacts as files and paths in the database. The console reads the same records: run list, candidate by case heatmap, per-cell evidence, compare with deltas and cost per correct answer, decision view, receipt, calibration.

The exit code is the automation contract: 0 for a completed run whose gates pass, 1 for a gate failure, 2 for a spec error, 3 for a budget abort.

## 6. What changed in practice

A tie stopped looking like a decision. A rate of eight in ten spans 0.49 to 0.94 at 95 percent, so a headline rate alone cannot separate two arms. When two arms do differ, the interval on the delta decides whether the difference survives the sample size, and when it crosses zero the compare view says inconclusive instead of naming a winner.

Cost sits beside the score. A candidate that scores one point higher at roughly 27 times the cost is a different decision, not a better one, so cost per correct answer appears next to the pass rate in the compare view.

The judge is pinned. Its adapter, model and options are hashed into the run, and a change to any of them is reported as drift that blocks a like-for-like claim between two runs.

The receipt is the artifact we hand a client: spec hash, harness revision, operator, seed, timestamps, candidates, judge pins with their configuration hashes, the price snapshot, the gates, and the diff to the previous run. It is read-only and comes from the run's snapshot, not from the current file.

Two things keep this honest. A gate in CI turns an eval into a release check, so a threshold failure stops the change with exit code 1. A parity test replays a recorded three-arm scoreboard through the CLI and asserts the totals, so a scorer or cost regression fails the suite before it can move a number anyone trusts.

What that changes in the room is the last thing worth saying.
