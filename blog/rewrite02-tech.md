## What an eval harness is

An eval harness is the instrument that answers the release question: how often, at what cost, under which code, and would it happen again. It starts with one YAML spec, which is the source of truth. The spec names the cases and the repositories they come from, the candidates (adapter, model, prompt variant, context), the metrics, the prices, and the gates. A run snapshots the spec and the resolved price table, so an old run keeps its old meaning after the file moves on. The spec holds no secret, only the name of a credential the vault resolves.

The planner expands that spec into cells, one per candidate times case times replica. Cell ids are deterministic and the order is seeded, so the same spec produces the same matrix twice. A cell is the unit of execution, cost and evidence. Before cell one, a repository source writes a snapshot of the tree under a content digest, with the commit and a dirty flag when it can see them. A resume compares that digest and refuses a changed tree, because a number from a different tree is a different number.

A context selector runs once per candidate and case, before the cells are shuffled. The cell keeps the chunk ids it was given and a digest of the selection, so an answer can be traced to the text that produced it.

Six protocols bound the system: Candidate, Judge, Metric, Source, Sink, ContextSelector. Core imports those protocols and a registry, never a concrete adapter. That is what lets a RAG pipeline, an HTTP endpoint and a provider API sit in one matrix without core knowing which is which. The harness is the product. A pipeline is a candidate.

## Built like release infrastructure

It runs like a service rather than a script. The CLI and the HTTP API drive the same orchestration, a run keeps one id through cancel and resume, concurrency is bounded, retries stop at the attempt budget the candidate declares, and a budget guard stops the run when spend crosses the cap. A cancel is honored between cells, so work in flight is recorded rather than lost. Storage is PostgreSQL or SQLite, and the compose file brings the harness and the database up together.

It treats inputs as evidence. The run stores the spec and price snapshots, pins every judge configuration with a hash, and records the repository digest, commit and dirty flag on the run, and the context chunk ids each cell received. The receipt collects those facts and the diff compares them with the previous run, so a judge swap shows up as drift instead of progress. If the tree moved, the old run does not continue.

It is honest about uncertainty. Every rate carries its n and a 95 percent Wilson interval, replicas add pass@k and pass^k, the decision view puts cost per correct check beside the rate, and the compare view adds cost per correct answer. When the interval on the delta crosses zero, the compare page calls it inconclusive: no measurable difference, and no winner from a decimal. Gates read the aggregates, and the exit code is the contract: 0 pass, 1 gate failure, 2 spec error, 3 budget abort.

It is built to be trusted, which is not the same as certified. Keys are BYOK, encrypted at rest, and redacted from logs, events, artifacts, cells and HTML. One container belongs to one person. The suite holds a 100 percent line coverage gate and a parity test that replays a recorded three-arm scoreboard; conformance runs per adapter kind, and a smoke script drives the running service. The console is server-rendered: no Node build, no CDN, no client framework. We call that built like release infrastructure, and we mean the properties, not a badge.

## How a run goes

Validate: the spec is parsed, checked against the schema, and refused before any spend if a field is wrong. Errors name the field, the value and the fix. Snapshot the repository, when the spec names one. Plan and select: the planner builds the matrix and each context selector fills its case's chunks. Execute: cells go out under bounded concurrency, retries and the budget guard, and each candidate returns an answer with its usage. Score: metrics turn cells into checks; a judge-backed metric awaits its judge, and the verdict's tokens and cost are charged to the cell that triggered it, separate from the answer cost and rolled up per provider. Record: aggregates, gates, events and artifacts, the facts the receipt is built from; the receipt is a read over those rows, not a second write. Read: the console, where the heatmap, context panel, decision view and receipt draw from the same rows, or the JSON API, or the CLI table.

The order matters because each step can refuse. A bad spec exits 2 before a run exists. A snapshot over its caps exits 2 before a cell. A resume with a moved tree exits 2 with the repository name and the fix. Budget exits 3. A refused launch leaves no run and no receipt, only an exit code.

Figure 1 puts the whole run on one page.

[[FIGURE 1]]
Figure 1. One run, end to end.

Which leaves the practical question: how small can the first honest run be.
