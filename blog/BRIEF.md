# Blog brief: the eval harness piece

## Deliverable
One corporate engineering blog post, 1,500 to 1,750 words, in a .docx with an architecture diagram.
Audience: engineering leaders and senior engineers who buy or build AI systems. They know what an
LLM is. They do not know this repo.

## Voice
- Written by the forward deployed engineer who sits with the customer, without naming the role.
  First person plural ("we"), occasional singular ("I") for the on-site moments.
- Technical level: names real mechanisms (cells, intervals, adapters, gates) but never tours the
  repo. Explain the mechanism in one sentence, then say why it matters.
- Plain, concrete, short sentences. No hype. No vendor voice.
- Hard rules: no em dashes anywhere. Use commas, colons, parentheses, or a plain hyphen.
  No en dash used as a dash. No "leverage", "seamless", "robust", "delve", "empower", "unlock",
  "game changer", "in today's fast-paced", "it is not just X, it is Y". No fake customer quotes.
  No named client. No invented adoption metrics, savings, or headcount.
- British or American spelling, pick one and stay consistent. Prefer American.

## The story spine
1. A policy assistant over a loan-policy corpus was demoed to a bank. It worked in the room.
2. The first real question was narrow: which way of feeding the pipeline context should ship?
   We wrote a script that ran three arms and printed a table. It answered that question once.
3. The table: bare 4/8, map 4/8, map_rules 8/8. Two arms tie on the headline number and fail on
   opposite tiers: bare gets tier 1 0/4 and tier 2 4/4; map gets tier 1 4/4 and tier 2 0/4.
   The headline hid the only interesting fact. Cost also differed by roughly 27x
   ($0.0082 vs $0.0003 for the same eight tasks).
4. Then the questions multiplied: cheaper model, new judge, more context, a teammate's agent,
   "why did last week's green run differ from this week's?" Each question meant a new script.
   Worse, a score had no provenance: no spec hash, no price snapshot, no judge configuration,
   no interval. Two green runs could differ and nobody could say why.
5. The fix was not a better script. It was a harness: one declarative spec expands into a matrix
   of cells, runs behind adapters, scores with metrics and judges, stores every check, and emits
   aggregates with uncertainty, cost attribution, and a receipt.
6. The close: a demo is a claim; the harness is the evidence. Start with twenty cases, put a gate
   in CI, keep the receipt.

## Why now (the argument, not a trend list)
- Model and prompt changes now ship weekly, so "is it better?" is a recurring release question,
  not a one-off study. Ad hoc scripts do not survive that cadence.
- LLM judges are useful and unstable: a judge model or prompt change silently moves the score.
  The harness pins judge configuration and flags drift.
- Cost is now part of correctness. A candidate that scores one point higher at 30x cost is a
  different decision, so cost per correct answer belongs next to the pass rate.
- Enterprise delivery needs a defensible artifact: what ran, against which spec, at which prices,
  with which judge, by whom. A screenshot of a dashboard is not that artifact.

## What the harness is (facts, use only these)
- A versioned YAML spec is the source of truth: source of cases, candidates (provider, model,
  prompt variant, context), metrics, per-model prices, optional gates. It never contains a secret.
- The planner expands the spec into cells: candidate x case x replica, deterministic ids.
- The executor runs cells with bounded concurrency, retries, a budget guard, cancel and resume.
  Resume skips cells already ok and keeps one run id.
- Five protocols: Candidate, Judge, Metric, Source, Sink. Core imports protocols and a registry,
  never a concrete adapter. A candidate returns answer, citations, tool calls, tokens, latency,
  usage source, artifacts. A candidate never returns pass or fail.
- A judge verdict carries a reason, evidence, its own tokens and cost, attributed to the cell that
  triggered it, split from the answer cost and rolled up per provider.
- Metrics produce checks per cell; aggregates carry n, value, 95 percent Wilson interval,
  pass@k and pass^k. No samples means undefined, not zero.
- Gates are thresholds on aggregates, evaluated after the run. Exit codes are the automation
  contract: 0 success, 1 gate failure, 2 spec error, 3 budget abort.
- Storage keeps specs, runs, cells, checks, aggregates, gates, events. Artifacts are files, the
  database stores paths.
- The console is server-rendered: run list, candidate by case heatmap, per-cell evidence, compare
  with deltas and cost per correct answer, decision view, receipt, calibration.
- BYOK: keys are encrypted at rest, verified on demand, redacted everywhere. One container per
  person.
- A receipt records spec hash, harness revision, operator, seed, timestamps, candidates, judge
  pins with configuration hashes, price snapshot, gates, and the diff to the previous run.
- A parity test replays a recorded three-arm minirag scoreboard through the CLI and asserts the
  totals, so a scorer or cost regression fails the suite.

## Architecture diagram (I am drawing it; describe it, do not draw ASCII)
Four layers, dependencies point down:
- Surfaces: CLI, HTTP API, server-rendered console.
- Core: spec -> planner -> executor -> scorer -> aggregates/gates, plus events and vault.
- Adapters: candidates (RAG pipeline, HTTP endpoint, provider APIs), judges, metrics, sources, sinks.
- Storage: PostgreSQL/SQLite for runs/cells/checks, files for artifacts, encrypted credential vault.
Flow: YAML spec + price table -> planner -> cells -> executor -> candidates -> scorer (metrics and
judges) -> checks and aggregates -> store -> console and gates. Redaction wraps logs, events,
artifacts, and records.

## Section budget
1. Title + standfirst: 40 words.
2. The tie: 350 words.
3. Why the scoreboard broke: 300 words.
4. What a harness is: 300 words (introduce the diagram here).
5. How a run actually goes: 300 words (walk the diagram left to right).
6. What changed in practice: 300 words (intervals, cost per correct, judge drift, receipt, gate).
7. Close: 150 words.

## Claims to avoid
- Do not claim the harness replaced a specific vendor product or saved a specific amount.
- Do not claim 100 percent test coverage; say the suite includes a parity test.
- Do not claim the bank adopted it, or name the bank.
- Do not invent a team size, timeline, or customer quote.
- Do not say the harness makes models trustworthy. It makes results auditable.
