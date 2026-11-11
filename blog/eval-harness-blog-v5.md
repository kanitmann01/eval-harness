# How to strengthen your demos with evidence

URL: https://github.com/kenmann01/eval-harness

Every enterprise has a question it needs answered the same way twice. Ours came from a bank. A loan file is a piece of code plus the policy that governs it, and an engineer needs two things from it: which symbol the file calls, and what rule the loan states at that bank. Get either wrong and the answer fails review. The same question also has to keep its answer next month, after the code, the policy, and the model have all changed.

Three possible solutions went on the table. The first was a baseline: an agent with tools and no context, free to search the repository for itself. The second was a knowledge graph built from the code, where each method call becomes a node and an edge, so the agent can look up structure instead of hunting for it. The third kept the graph and added the business rules, the policy text that tells the agent what the code is allowed to do. Those descriptions are enough for now. The interesting part is what happened when we ran them.

All three answered the same eight questions. Four asked which symbol a loan file calls, and four asked what rule it states at a given bank. The baseline got four of eight, all of them policy questions, at $0.0081797. The graph got the other four, all code questions, at $0.0003042. The graph plus rules got all eight, at $0.0003024.

| Candidate | Context provided | Score | Cost |
|---|---|---|---|
| bare | None | 4 / 8 (policy only) | $0.0081797 |
| map | Mapped chunk | 4 / 8 (code only) | $0.0003042 |
| map_rules | Mapped chunk + rule text | 8 / 8 | $0.0003024 |

Table 1. Results by context configuration (8 tasks each).

On the headline number, two solutions tie. Their costs differ by a factor of 27, and they fail in opposite directions: one knows the code and not the rules, the other knows the rules and not the code. In the baseline, four failed retrieval calls accounted for about half the bill, which is its own small lesson about where the money goes when retrieval misses.

We took the table back to the team.

"Which one should we ship?"

"The table says these two are the same."

"Then the table is wrong."

The table was accurate in its counts and silent about everything else. It did not say which repository state produced the cases, which judge scored the policy answers, which prices it used, or how much of that 4-out-of-8 was noise. Change any one of those and the number moves without the solution changing. What the table recorded was a result. What the room needed was evidence.

Evidence, in this sense, is a result that someone else can reproduce: the same cases, the same repository state, the same judge configuration, the same prices, and enough samples that a one-task difference is not read as a signal. Reproducible, costed, and traceable to its source. A demo is built to show that something can happen once. Evidence is built to show how often it happens, and whether it will happen again.

That distinction matters here because the scoreboard does not look broken. A wrong answer in this domain does not announce itself; it reads as confidently as a correct one. The count alone cannot settle the question, which is why the missing metadata matters more than the number it surrounds.

None of this is an argument about which context strategy won. It is an argument about what a release team needs before it can trust any of them. The narrow answer was the graph plus rules. The useful finding was that we could not explain why it would keep working.

## Why now

The parts move independently. The model changes, the prompt changes, the context strategy changes, the judge changes, the repository changes, and none of them waits for the others. A demo tests one arrangement on one afternoon. A release has to test several arrangements, repeatedly, and compare them honestly.

An ad hoc script encodes one arrangement in one file. Change the model and the script needs an edit. Change the judge and the score moves without a warning. Change the repository and the cases may no longer mean what they meant. By the next release nobody can say which version produced the green number, so the green number becomes folklore.

Cost belongs in the same sentence, for a reason that is easy to miss. A candidate that wins by one task at many times the price is not a win; it is a different decision. A cost column shows what you spent, never what the spending bought. A judge swap is worse, because it looks like progress. The number goes up, the reason is a new judge, and the meeting ends early. Judges cost money too, and a model judge is one more variable that can change without the prompt changing.

Put together, a release decision needs five things:

- Consistent cases for evaluation
- A fixed repository state
- Judge configuration recorded and versioned
- Transparent pricing details
- Sufficient sample size to ensure minor differences are not misinterpreted as significant

None of it is exotic. All of it was missing from the script that printed our table.

Here is the uncomfortable version. Most teams do not have an evaluation problem. They have a demo problem wearing evaluation's clothes: the demo worked, the deck has a chart, and the chart has no receipt. A number you cannot reproduce is not a number. It is a memory of a number.

The script answered the question once. The release asks it every time.

## What an eval harness is

An eval harness is the instrument that answers the release question: how often, at what cost, under which code, and would it happen again. It starts with one YAML spec, a single file that names the cases and the repositories they come from, the candidates (adapter, model, prompt variant, context), the metrics, the prices, and the gates. That file is the source of truth. A run snapshots the spec and the resolved price table, so an old run keeps its old meaning after the file moves on. The spec holds no secret, only the name of a credential the vault resolves.

The planner expands the spec into cells. A cell is one candidate solution run against one test case, repeated once per replica, and it is the unit of execution, cost, and evidence. Cell IDs are deterministic and the order is seeded, so the same spec produces the same matrix twice. Before cell one, the harness snapshots the declared tree under a content digest, with the commit and a dirty flag when it can see them. A repository source reads cases and fixtures from that snapshot. A resume compares the digest and refuses a changed tree, because a number from a different tree is a different number.

Context selection runs once per candidate and case, before the cells run. Each cell keeps the chunk IDs it was given and a digest of the selection, so an answer can be traced to the text that produced it.

Six protocols bound the system: Candidate, Judge, Metric, Source, Sink, ContextSelector. Core imports those protocols and a registry, never a concrete adapter. That is what lets a RAG pipeline, an HTTP endpoint, and a provider API sit in one matrix without core knowing which is which. The harness is the product. A pipeline is a candidate. It does not decide what good looks like; it makes the claim checkable.

## Built like release infrastructure

The harness we built runs like a service rather than a script. The CLI and the HTTP API drive the same orchestration. A run keeps one ID through cancel and resume, concurrency is bounded, retries stop at the attempt budget the candidate declares, and a budget guard stops the run when spend crosses the cap. Cancel is honored between cells; work in flight is recorded, not lost. Storage is PostgreSQL or SQLite, and compose brings both up.

It treats inputs as evidence. The run stores the spec and price snapshots, pins every judge configuration with a hash, records the repository digest, commit, and dirty flag, and keeps the context chunk IDs each cell received. The receipt collects those facts, and the diff compares them with the previous run, so a judge swap shows up as drift instead of progress. If the tree moved, the old run does not continue.

It is honest about uncertainty. The plain rate carries its n and a 95 percent Wilson interval; replicas add the case-level pass@k and pass^k. The decision view puts cost per correct check beside the rate, and the compare view adds cost per correct answer. When the interval on the delta includes zero, the compare page calls it inconclusive: no measurable difference, and no winner from a decimal. Gates read the aggregates, and the exit code is the contract: 0 gates pass, 1 gate failure, 2 spec error, 3 budget abort.

It is built to be trusted, not certified. Keys are BYOK, encrypted at rest, and redacted from logs, events, artifacts, cells, and HTML; one container per person keeps credentials and runs separate. The suite holds a 100 percent line coverage gate, and a parity test replays a recorded three-arm scoreboard. Conformance runs per adapter kind, and a smoke script drives the running service. The console is server-rendered: no Node build, no CDN, no client framework. We call that built-like release infrastructure, and we mean the properties, not a badge.

## How a run goes

Before the first cell, validate the spec. It is parsed, checked, and refused before any spend if a field is wrong, and the errors name the field, the value, and the fix. Snapshot the repository when the spec names one. Then come the six stages, in four moves.

Plan and select: the planner builds the matrix and fills context for each case. Execute: cells run under limited concurrency and the budget guard, and each candidate returns an answer and usage. Score: metrics turn cells into checks. A judge-backed metric gets a verdict whose tokens are priced and charged to the triggering cell, separate from answer cost and rolled up per provider. Record and read: the CLI prints the run, its cost split and the aggregates, and the console, JSON API and receipt read the same rows.

Here is a real smoke run, two stub candidates, no network; one aggregate row is shown. The point is the shape, not the score.

~~~text
run cdfd34e19b6547168eaad9ae54865371 status=ok cells=2 ok=2 cost=$0.000013
cost: answer $0.000013, judge $0.000000, tools $0.000000
stub-concise   exact_match   n=1   100.0%   20.7% to 100.0%
~~~

The run ID, interval, and cost breakdown are consolidated in one location. The receipt includes the spec hash, harness revision, operator, seed, judge identifiers, price fingerprint, source digest, and differences.

[[FIGURE 1]]
Figure 1. One run, end to end.

## Start with twenty cases

The fix is smaller than the argument. Start with twenty defensible cases: common, meaningful scenarios that you can review with the team without apologizing for them. Pick cases that are clear and stable, and keep the first run to one candidate and one gate that fails the build when performance drops below an agreed benchmark.

Keep the receipt: the spec, the repository state, the judge configuration, the prices. Keep the interval next to the score, so nobody reads a one-task difference as a trend. When the process stabilizes, review the cases with the team and ask whether they still reflect what you care about. Then add a second candidate for comparison. The first run may be imperfect. That is acceptable as long as the second run can be compared to it.

None of this makes a model trustworthy. It makes a result auditable, which is what a release meeting can actually use. A demo presents a claim; the harness provides the evidence.
