# Redo technical brief (0.4)

You are writing part of a corporate engineering blog for the eval-harness project at D:\projects\eval-harness. Work only in that repo. Do not edit anything outside blog/.

## Role and voice
Write as a forward deployed engineer explaining the system to a technical peer. Do not name the role. First person plural where natural. Plain, concrete, low-jargon. Short paragraphs. No em dashes. American spelling. No invented results. Read blog/eval-harness-blog.md first for voice.

## Task
Rewrite only these three sections for the new 0.4 version:
1. "What an eval harness actually is"
2. "How a run actually goes"
3. "What changed in practice"

The old version describes five protocols and no repository input. The new version must describe six protocols and the 0.4 context/repository layer without becoming a changelog. Keep it simple: a reader should finish with a mental model of spec -> cells -> adapters -> checks -> aggregates -> receipt, plus where the repo snapshot and context selection sit.

## Facts you may use (verified; do not add others)
- One declarative YAML spec is the source of truth: source of cases, repos, candidates (provider, model, prompt variant, context), metrics, per-model prices, optional gates. No secrets; credentials are BYOK in an encrypted vault.
- Planner expands the spec into cells: candidate x case x replica, deterministic ids, seeded order. Executor: bounded concurrency, retries transport failures up to the candidate's declared max_attempts, budget guard after each cell, cancel between cells, resume skips ok cells and keeps one run id.
- Six protocols: Candidate, Judge, Metric, Source, Sink, ContextSelector. Core imports the protocols and a registry only.
  - Candidate: answer, citations, tool calls, tokens, latency, usage source, artifacts; never pass/fail.
  - Judge: verdict with reason, evidence, its own tokens and cost.
  - Metric: checks from a cell.
  - Source: the case set.
  - Sink: writes results elsewhere; JSON sink writes artifacts, DB stores paths.
  - ContextSelector: selects the chunks one case receives; synchronous; deterministic for the same seed; selection runs once per candidate and case; the cell records chunk ids, selector name and digest.
- Repository snapshots: local root or HTTPS URL, optional ref and credential, include/exclude globs, caps, symlink and binary policy. Before the first cell, copies included files into a content-addressed tree under $EVALH_HOME/repos/<digest>, with a manifest; digest reuse; a repository source reads cases and fixtures from the snapshot, so case file and code cannot drift apart silently. Run provenance records adapter, repository rows (root, url, ref, commit, digest, files, bytes, skipped, dirty), case prompt hashes and context metadata. Receipt gains a Source panel; run diff gains a source category. Resume refuses a repository whose digest changed (exit 2, field repos.<name>).
- Context selectors: contexts.<name> is a fixed chunk list or a mapping with adapter and options. repo_graph windows a prepared snapshot, ranks by lexical overlap and optional import-edge PageRank, expands around seeds, filters tests, caps chunks and bytes. artifact_graph reads a closed run's answers and co-citations. Context conformance context/1..context/5. The run page has a Context panel with selector, digest, chunk count, bytes and first chunks per candidate.
- Storage: PostgreSQL/SQLite specs, runs, cells, checks, gates, events, source snapshot; artifacts on disk; repo snapshots under $EVALH_HOME/repos/<digest>; DB stores digests and paths, never repo bytes; encrypted vault BYOK; one redactor wraps logs, events, artifacts, cell records and HTML.
- Aggregates: n, value, 95 percent Wilson interval, pass@k and pass^k when replicas > 1; no samples means undefined, not zero. Gates are thresholds on aggregates after the run.
- Receipt: run/spec ids, spec hash, harness revision, operator, seed, UTC timestamps, candidates, judge pins with config hashes, price snapshot fingerprint, gates, diff to previous run, Source panel. Read-only, from the run's snapshot.
- Console: server-rendered; run list, candidate by case heatmap, per-cell evidence, compare with deltas and cost per correct answer, decision view, receipt, calibration, source and context panels.
- Exit codes: 0 for a run whose gates pass, even when some cells failed or the run was cancelled; 1 gate failure; 2 spec error; 3 budget abort.
- 0.4.0 is "The Context release" (2026-11-03). evalh repos gc [--dry-run] removes snapshots no recorded run references. Database schema v8 adds runs.source_snapshot.
- Do not claim the three-arm fixture used repo_graph or a live market; it used fixed chunk lists. Do not claim embeddings; repo_graph supports lexical or PageRank, and embeddings are refused.
- Exact claims only. If unsure, leave it out.

## Structure and length
- "What an eval harness actually is": about 480-540 words. Spec, cells, six protocols, repo snapshot, context selector, diagram placeholder, adapter seam (HTTP candidate), receipt. Put the line [[FIGURE 1]] on its own line after the paragraph that explains the six protocols and the repo/context inputs, then a caption line: "Figure 1. One run, end to end." The diagram will be replaced later.
- "How a run actually goes": about 380-430 words. Validate and snapshot; load cases from the snapshot; plan and select context per candidate/case; execute with bounded concurrency, retries, budget, cancel, resume; score and attribute judge cost; aggregates, gates, storage, redaction, source/context panels; exit codes.
- "What changed in practice": about 330-380 words. Intervals, cost per correct answer, judge pin/drift, receipt, CI gate, parity test. Then the 0.4 additions: Source panel with digest/commit/files/bytes/skipped/dirty, source category in the diff, Context panel with selector/digest/chunks, resume refusal on a changed digest, repos gc. Keep this to one tight paragraph, not a bullet list.
- End the last section with a sentence that hands off to the closing section, which will say: start with twenty cases, one gate, keep the receipt.

## Output
Write the three sections, with their headings, to blog/redo-tech.md. Do not write the opening story or the closing section. Then reply with a 3-sentence summary and the word count.