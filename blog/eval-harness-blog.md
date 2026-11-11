# Your Demo Is Not Evidence

*A demo proves a thing can happen once. A release needs to know how often, at what cost, under which code, and whether it will happen again. This is the story of why that table was not evidence.*

## The question after the demo

The demo went well, which told us almost nothing. Three context configurations were on the table, eight tasks each, and the room wanted a decision.

The tasks split cleanly: four asked which symbol a loan file calls, four asked what rule a loan states in a given bank. `bare` injected no context and scored 4/8, with nothing on the code questions and everything on the policy questions, at $0.0081797. `map` used a mapped chunk and scored 4/8 the other way, all four code questions and none of the policy ones, at $0.0003042. `map_rules` added the rule text and scored 8/8 at the same $0.0003042.

Two arms, same headline, opposite failures. The costs were 27 times apart. Four failed retrieval calls in `bare` were about half its bill, which is its own small lesson about where the money goes when retrieval misses.

We brought the table back to the room.

"Which one should we ship?"

"The table says these two are the same."

"Then the table is wrong."

It was not wrong about the count. It was wrong as an answer. The table could not say why the two arms failed in opposite directions, because it did not record which repository tree the corpus came from, which judge version scored the policy answers, which prices it had used, or how much of that 4/8 was noise.

> A score without provenance is an opinion with a decimal point.

This is not an argument about which context strategy won. It is an argument about what a release team needs before it can believe any of them.

The narrow answer was `map_rules`. The useful finding was that we could not say why it would keep working.

## Why now

The parts move independently. The model changes, the prompt changes, the context strategy changes, the judge changes, the repository changes, and none of them waits for the others. A demo tests one arrangement on one afternoon. A release has to test several arrangements, repeatedly, and compare them honestly.

An ad hoc script encodes one arrangement in one file. Change the model and the script needs an edit. Change the judge and the score moves without a warning. Change the repository and the cases may no longer mean what they meant. By the next release nobody can say which version produced the green number, so the green number becomes folklore.

Cost belongs in the same sentence. A candidate that wins by one task at many times the price is not a win; it is a different decision. A cost column shows what you spent. It does not show what the spending bought. A judge swap is worse, because it looks like progress. The number goes up, the reason is a new judge, and the meeting ends early. Judges cost money too. A model judge is one more thing that can change without the prompt changing.

That is the standard a release question deserves: the same cases, the same repository state, the same judge configuration, the same prices, and enough samples that a one-task difference is not read as a signal. None of it is exotic. All of it was missing from the script that printed our table.

Here is the uncomfortable version of the argument. Most teams do not have an evaluation problem. They have a demo problem wearing evaluation's clothes. The demo worked, the deck has a chart, and the chart has no receipt. A number you cannot reproduce is not a number. It is a memory of a number.

The script answered the question once. The release asks it every time.

## What an eval harness is

An eval harness is the instrument that answers the release question: how often, at what cost, under which code, and would it happen again. It starts with one YAML spec, which is the source of truth. The spec names the source of the cases and any repositories they come from, the candidates (adapter, model, prompt variant, context), the metrics, the prices, and the gates. A run snapshots the spec and the resolved price table, so an old run keeps its old meaning after the file moves on. The spec holds no secret, only the name of a credential the vault resolves.

The planner expands that spec into cells, one per candidate times case times replica. Cell ids are deterministic and the order is seeded, so the same spec produces the same matrix twice. A cell is the unit of execution, cost and evidence. Before cell one, the harness snapshots the declared tree under a content digest, with the commit and a dirty flag when it can see them. A repository source reads cases and fixtures from that snapshot. A resume compares the digest and refuses a changed tree, because a number from a different tree is a different number.

Context selection runs once per candidate and case, before the cells run. The cell keeps the chunk ids it was given and a digest of the selection, so an answer can be traced to the text that produced it.

Six protocols bound the system: Candidate, Judge, Metric, Source, Sink, ContextSelector. Core imports those protocols and a registry, never a concrete adapter. That is what lets a RAG pipeline, an HTTP endpoint and a provider API sit in one matrix without core knowing which is which. The harness is the product. A pipeline is a candidate. It does not decide what good looks like; it makes the claim checkable.

## Built like release infrastructure

The harness we built runs like a service rather than a script. The CLI and the HTTP API drive the same orchestration, a run keeps one id through cancel and resume, concurrency is bounded, retries stop at the attempt budget the candidate declares, and a budget guard stops the run when spend crosses the cap. Cancel is honored between cells; work in flight is recorded, not lost. Storage is PostgreSQL or SQLite; compose brings both up.

It treats inputs as evidence. The run stores the spec and price snapshots, pins every judge configuration with a hash, records the repository digest, commit and dirty flag, and keeps the context chunk ids each cell received. The receipt collects those facts and the diff compares them with the previous run, so a judge swap shows up as drift instead of progress. If the tree moved, the old run does not continue.

It is honest about uncertainty. The plain rate carries its n and a 95 percent Wilson interval; replicas add the case-level pass@k and pass^k, the decision view puts cost per correct check beside the rate, and the compare view adds cost per correct answer. When the interval on the delta includes zero, the compare page calls it inconclusive: no measurable difference, and no winner from a decimal. Gates read the aggregates, and the exit code is the contract: 0 gates pass, 1 gate failure, 2 spec error, 3 budget abort.

It is built to be trusted, not certified. Keys are BYOK, encrypted at rest, and redacted from logs, events, artifacts, cells and HTML; one container per person keeps credentials and runs separate. The suite holds a 100 percent line coverage gate, and a parity test replays a recorded three-arm scoreboard; conformance runs per adapter kind, and a smoke script drives the running service. The console is server-rendered: no Node build, no CDN, no client framework. We call that built like release infrastructure, and we mean the properties, not a badge.

## How a run goes

Before the first cell, validate the spec: it is parsed, checked and refused before any spend if a field is wrong. Errors name the field, the value and the fix. Snapshot the repository, when the spec names one. Then the six stages. Plan and select: the planner builds the matrix and fills context for each case. Execute: cells run under bounded concurrency and the budget guard; each candidate returns an answer and usage. Score: metrics turn cells into checks; a judge-backed metric gets a verdict whose tokens are priced and charged to the triggering cell, separate from answer cost and rolled up per provider. Record and read: the CLI prints the run, its cost split and the aggregates; the console, JSON API and receipt read the same rows.

Here is a real smoke run, two stub candidates, no network; one aggregate row is shown. The point is the shape, not the score.

~~~text
run cdfd34e19b6547168eaad9ae54865371 status=ok cells=2 ok=2 cost=$0.000013
cost: answer $0.000013, judge $0.000000, tools $0.000000
stub-concise  exact_match  n=1  100.0%  20.7% to 100.0%
~~~

The run id, the interval and the cost split sit in one place. The receipt adds the spec hash, harness revision, operator, seed, judge pins, price fingerprint, source digest and diff.

[[FIGURE 1]]
Figure 1. One run, end to end.

## Start with twenty cases

The fix is smaller than the argument. Start with twenty cases you can defend, one candidate, and one gate that fails the build when the number drops. Keep the receipt: the spec, the repository state, the judge configuration, the prices. Keep the interval next to the score. Add a second candidate when the first number gets boring, which it will. The first run will be embarrassing. That is fine, as long as the second run can be compared to it.

None of this makes a model trustworthy. It makes a result auditable, which is what a release meeting can actually use. A demo is a claim. The harness is the evidence.
