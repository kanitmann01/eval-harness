# How to strengthen your demos with evidence

URL: https://github.com/kenmann01/eval-harness

Your agents follow orders well. In fact, they are so good that they'll invent facts ("hallucinate") to produce a viable answer rather than say they don't know. These hallucinations and errors have causes, but you can't see them from the output: a fabricated answer reads just as confidently as a correct one. A demo shows that something can happen once, but a release requires understanding how often it occurs, at what cost, under which code, and whether it will recur. For evidence to be sufficient for release, it must be reproducible under consistent conditions, document the costs involved, and include clear provenance that traces results to their source and configuration. This explains why result tables at demos do not qualify as evidence.

Let's take a simple scenario: the customer asks FDEs to find the context configuration for best agent performance. They pull all-nighters comparing 3 agents: a bare agent with no context, an agent with repo map, and an agent with repo map and rules. The next day, the demo is successful, but it provided little insight as they only evaluated three context configurations, each with eight tasks, and the team sought a decision based on that.

The tasks were evenly divided: four focused on identifying which symbol a loan file calls, and four on determining what rule a loan states in a specific bank. The bare configuration provided no context and scored 4 out of 8, answering only the policy questions, at a cost of $0.0081797. The map configuration used a mapped chunk and also scored 4 out of 8, but answered only the code questions, at $0.0003042. The map_rules configuration included the rule text and achieved 8 out of 8 at $0.0003024.

| Configuration | Context provided | Score | Cost |
|---|---|---|---|
| bare | None | 4 / 8 (policy only) | $0.0081797 |
| map | Mapped chunk | 4 / 8 (code only) | $0.0003042 |
| map_rules | Mapped chunk + rule text | 8 / 8 | $0.0003024 |

Table 1. Results by context configuration (8 tasks each).

The two approaches had similar overall results but failed in opposite areas. Their costs differed by a factor of 27. In the bare configuration, four failed retrieval calls accounted for about half the total cost, highlighting how expensive retrieval failures are.

We presented the table to the team again.

"Which one should we ship?"

"The table says these two are the same."

"Then the table is wrong."

The table was accurate in its counts but insufficient as an answer. It didn't explain why the two approaches failed in opposite ways because it lacked key metadata: repository source, judge version, pricing details, and a measure of noise in the 4-out-of-8 score. Without explicitly tracking and presenting this metadata, future evaluations risk missing critical context needed for interpretation and comparison.

> A score without provenance is merely an opinion expressed numerically.

This is not about which context strategy performed best. It concerns what evidence a release team requires to trust any of them.

While map_rules was the immediate solution, the key insight was that we couldn't explain why it would keep performing well.

## Why now

Each component changes independently: the model, prompt, context strategy, judge, and repository all evolve separately. A demo tests a single configuration at a specific time, while a release must evaluate multiple configurations repeatedly and compare them objectively.

An ad hoc script captures a single configuration in one file. If the model changes, the script requires modification. Changing the judge alters the score without notice. Updating the repository may change the meaning of the cases. By the next release, it is unclear which version produced the reported result, making it unreliable.

Cost must be considered alongside performance. A candidate that outperforms by one task but at significantly higher cost is not necessarily preferable. A cost column shows expenditure, not value. Changing the judge can create the illusion of progress, as scores may increase due to the new judge rather than actual improvement. Judges also incur costs, and a model judge is another variable that can change independently of the prompt.

A release decision requires:

- Consistent cases for evaluation
- A fixed repository state
- Judge configuration recorded and versioned
- Transparent pricing details
- Sufficient sample size to ensure minor differences are not misinterpreted as significant

These are standard requirements, yet they were absent from the script that generated our table.

The core issue is that most teams face a demo problem disguised as an evaluation problem. The demo succeeds, a chart is presented, but no supporting evidence follows. A result that cannot be reproduced isn't valid data; it is just a recollection.

The script provided a one-time answer. A release process requires consistent answers to this question.

## What an eval harness is

An eval harness is the instrument that answers the release question: how often, at what cost, under which code, and would it happen again. It starts with one YAML spec as the source of truth. The spec names the source of the cases and any repositories they come from, the candidates (adapter, model, prompt variant, context), the metrics, the prices, and the gates. A run snapshots the spec and the resolved price table, so an old run keeps its old meaning after the file moves on. The spec holds no secret, only the name of a credential the vault resolves.

The planner expands that spec into cells, one per candidate times case times replica. Cell IDs are deterministic, and the order is seeded, so the same spec produces the same matrix twice. A cell is the unit of execution, cost, and evidence. Before cell one, the harness snapshots the declared tree under a content digest, with the commit and a dirty flag when it can see them. A repository source reads cases and fixtures from that snapshot. A resume compares the digest and refuses a changed tree, because a number from a different tree is a different number.

Context selection runs once per candidate and case, before the cells run. The cell keeps the chunk IDs it was given and a digest of the selection, so an answer can be traced to the text that produced it.

Six protocols bound the system: Candidate, Judge, Metric, Source, Sink, ContextSelector. Core imports those protocols and a registry, never a concrete adapter. That is what lets a RAG pipeline, an HTTP endpoint, and a provider API sit in one matrix without core knowing which is which. The harness is the product. A pipeline is a candidate. It does not decide what good looks like; it makes the claim checkable.

## Built like release infrastructure

The harness we built runs like a service rather than a script. The CLI and the HTTP API drive the same orchestration; a run keeps one ID through cancel and resume, concurrency is bounded, retries stop at the attempt budget the candidate declares, and a budget guard stops the run when spend crosses the cap. Cancel is honored between cells; work in flight is recorded, not lost. Storage is PostgreSQL or SQLite; compose brings both up.

It treats inputs as evidence. The run stores the spec and price snapshots, pins every judge configuration with a hash, records the repository digest, commit, and dirty flag, and keeps the context chunk IDs each cell received. The receipt collects those facts, and the diff compares them with the previous run, so a judge swap shows up as drift instead of progress. If the tree moved, the old run does not continue.

It is honest about uncertainty. The plain rate carries its n and a 95 percent Wilson interval; replicas add the case-level pass@k and pass^k, the decision view puts cost per correct check beside the rate, and the compare view adds cost per correct answer. When the interval on the delta includes zero, the compare page calls it inconclusive: no measurable difference, and no winner from a decimal. Gates read the aggregates, and the exit code is the contract: 0 gates pass, 1 gate failure, 2 spec error, 3 budget abort.

It is built to be trusted, not certified. Keys are BYOK, encrypted at rest, and redacted from logs, events, artifacts, cells, and HTML; one container per person keeps credentials and runs separate. The suite holds a 100 percent line coverage gate, and a parity test replays a recorded three-arm scoreboard; conformance runs per adapter kind, and a smoke script drives the running service. The console is server-rendered: no Node build, no CDN, no client framework. We call that built-like release infrastructure, and we mean the properties, not a badge.

## How a run goes

Before the first cell, validate the spec: it is parsed, checked, and refused before any spend if a field is wrong. Errors name the field, the value, and the fix. Snapshot the repository when the spec names one. Then the six stages. Plan and select: the planner builds the matrix and fills context for each case. Execute: cells run under limited concurrency and the budget guard; each candidate returns an answer and usage. Score: metrics turn cells into checks; a judge-backed metric gets a verdict whose tokens are priced and charged to the triggering cell, separate from answer cost and rolled up per provider. Record and read: the CLI prints the run, its cost split and the aggregates; the console, JSON API and receipt read the same rows.

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

The solution is simpler than the debate. For teams adopting this approach, start by selecting twenty defensible cases that represent common, meaningful scenarios relevant to your application. Choose cases that are clear, stable, and easy to review with the team. Set up an initial candidate and configure a gate that fails the build if its performance drops below an agreed benchmark. Use the eval harness to run your candidate through these cases. Retain the receipt, including the spec, repository state, judge configuration, and prices, to ensure your results are auditable and reproducible. Display the confidence interval alongside the score to communicate uncertainty clearly. As your process stabilizes, discuss with the team whether the cases and candidate still reflect your objectives, then introduce a second candidate for comparison. The first run may be imperfect, but this is acceptable as long as subsequent runs are comparable and improvements are intentional.

These steps don't guarantee model trustworthiness, but they ensure auditable results, which are essential for release decisions. A demo presents a claim; the harness provides the evidence.
