# Rewrite brief 02: Your Demo Is Not Evidence

## Job
Corporate engineering blog for eval-harness 0.4. 1,500 to 1,750 words. Deliver a .docx with Figure 1.
Audience: engineering leaders and senior engineers who ship LLM systems. Technical, but no hand-holding.
Voice: first-person plural, from the person who has to make the release call. Do not name the role. Dry wit, short sentences, one or two earned aphorisms. Wildean in posture, not Victorian costume.

## Thesis
A demo proves a thing can happen once. A release needs to know how often, at what cost, under which code, and whether it will happen again. An eval harness is the instrument that turns a demo claim into evidence. The product is the harness, not any one context strategy.

## Story (max 350 words)
A client asks which of three context configurations to ship. Three arms, eight tasks. `bare` scores 4/8 (0/4 code, 4/4 policy, $0.0081797). `map` scores 4/8 (4/4 code, 0/4 policy, $0.0003042). `map_rules` scores 8/8 (both, $0.0003042). Two arms tie with opposite failures; cost is 27 times apart. The narrow answer was `map_rules`. The important finding is that the table could not say why, could not name the repository tree, judge version, price snapshot, or interval. That is the turn. Do not spend the rest of the piece arguing map versus bare. Do not say map is better than context. The story is about a question a scoreboard cannot answer.
Key line: "A score without provenance is an opinion with a decimal point."

## Why now (about 350 words)
Models, prompts, context, judges, and repositories change independently. A demo tests one arrangement; a release tests many. Ad hoc scripts rot; results cannot be compared; cost is invisible; a judge swap looks like progress. The dangerous idea: most teams do not have an evaluation problem, they have a demo problem wearing evaluation's clothes. A number you cannot reproduce is not a number.

## What it is (about 450 words)
One YAML spec is the source of truth: cases, repositories, candidates (adapter, model, prompt variant, context), metrics, prices, gates. Planner expands into cells (candidate x case x replica, deterministic ids, seeded order). Six protocols: Candidate, Judge, Metric, Source, Sink, ContextSelector. Repository snapshot is content-addressed; context selection is recorded; scorer produces checks and aggregates with Wilson intervals and pass@k/pass^k; gates decide; receipt records. The diagram goes after the run sequence.

## Why ours is production infrastructure (about 350 words)
Four USPs, each a short paragraph:
1. Runs like a service: CLI and HTTP, one run id, bounded concurrency, retries with an attempt budget, budget guard, cancel between cells, resume skips ok cells; PostgreSQL or SQLite; compose.
2. Treats inputs as evidence: spec and price snapshots, judge pins, repo digest/commit/dirty flag, context chunk ids, receipt, diff, resume refuses a changed tree.
3. Honest about uncertainty: 95 percent Wilson intervals, pass@k and pass^k, cost per correct answer and per correct check, "No measurable difference" when intervals overlap, gates and exit codes 0/1/2/3.
4. Built to be trusted: BYOK encrypted vault, one container per person, redactor over logs/events/artifacts/cells/HTML, 100 percent line coverage, parity test, conformance suite, smoke test, server-rendered console with no Node.
Do not claim "production ready" as a certification. Say "built like release infrastructure."

## Run sequence (about 300 words)
Validate, snapshot the repository, plan and select context, execute, score, record the receipt, read. Explain judge cost attribution. Keep it concrete.

## Close (about 150 words)
Start with twenty cases, one candidate, one gate. Keep the receipt. Add a second candidate when the first number is boring. End on "A demo is a claim. The harness is the evidence."

## Wilde constraints
- Having something to say: every paragraph advances the thesis. No inventory for its own sake.
- Misbehave: one or two surprising sentences, no fake erudition.
- Dangerous idea: demo culture is the enemy of evidence; a score without provenance is an opinion.
- Endless rereading: aphorisms must be earned, not decorative.
- Revision: cut 10 percent after the draft.
- Masks: use the client exchange; no named client.
- Commonplace book: use concrete field notes, not generic claims.
- Wit and irony: dry, not slapstick. Never joke at the client's expense.
- Simplicity: short words, short sentences; technical terms only where needed.

## Hard constraints
No em dashes. American spelling. No invented results. No named client. No "Forward Deployed Engineer." 1,500 to 1,750 words total. Figure 1 caption. The story numbers and product facts must match the repository.