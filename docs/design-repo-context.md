# Design: repo attach and graph-aware context (0.4 candidates)

Status: proposed. Answers the two TODOs in [ux-flow.md](ux-flow.md)
section 12. The spec remains the source of truth.

## 1. Problem and user stories

The harness only sees inputs that fit inside the spec. Cases live in
`source.options.cases`, contexts are inline chunk lists, and everything else is
the candidate's business (D8). That fails when the agent under test works on a
repository:

- Cases, fixtures and prompts live in the repo they test. Copying them into a
  spec creates a second copy that drifts: a repo prompt fix is invisible to the
  eval, a spec case fix is invisible to review.
- A run cannot name the tree it read. The spec sha pins the spec, not the code,
  so two green runs can differ without explanation.
- Graph retrieval lives inside the candidate: comparing it with a fixed set
  means two builds and no chunk evidence.

| # | User story | Today | Cost |
| --- | --- | --- | --- |
| U1 | Cases and fixtures live next to the code and are reviewed in the same PR. | Paste cases, copy fixtures. | Manual refresh per change, drift, `expected` lost. |
| U2 | A reviewer can name the tree a green run executed. | Receipt names spec, models, prices; not the repo. | Run cannot be reproduced or defended. |
| U3 | Graph context versus hand-picked context is one variable. | Retrieval is inside the candidate. | No chunk provenance; wrong attribution. |
| U4 | Yesterday's eval reruns with the same cases and chunks. | Resume trusts the spec sha only. | A source reading a tree drifts silently. |

The adapter reads content; it never runs, imports or tests repository code.

## 2. What does not change

Spec is the source of truth; core imports protocols and the registry only; the
console stays server-rendered; exit codes stay 0/1/2/3; no new Python
dependency (`ast` and regex for graphs, the documented `git` binary for remote
fetch).

## 3. Spec surface

Three optional additions, all additive inside `spec_version: 1`: a top-level
`repos` map, `source.credential`, and a mapping form for a `contexts` entry.
`provider_prices` set the precedent. An older binary rejects the new keys with
exit 2, which is safe: it refuses instead of ignoring auth.

### 3.1 `repos`

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `root` | string | `"."` | Relative; absolute paths, `~`, drive letters and NUL rejected. Directory at load. Exactly one of `root`, `url`. |
| `url` | string | unset | `https` only, no userinfo, no query. |
| `ref` | string | unset | Branch, tag or commit; needs `url` or a Git checkout. |
| `credential` | string | unset | Only with `url`; must be stored, not merely declared. Provider `git`. |
| `include`, `exclude` | globs | `["**/*"]`; `.git/`, `node_modules/`, `.venv/`, `__pycache__/`, `*.pyc` | Snapshot-relative POSIX paths; `exclude` wins. |
| `max_files` | int >= 1 | `2000` | Exceeding refuses the snapshot; never truncates silently. |
| `max_file_bytes` | int >= 1024 | `1000000` | Larger text file skipped with a warning; a cases or fixtures file is an error. |
| `max_total_bytes` | int >= 1024 | `20000000` | Checked before copying. |
| `follow_symlinks` | bool | `false` | Off skips every symlink; on still skips links escaping `root`. |
| `binary` | `skip`/`error` | `skip` | NUL byte in the first 8 KiB; `error` names the first path. |

Relative roots keep the spec portable and mean no absolute host path reaches
the run snapshot, a cell, an event or HTML.

### 3.2 Repo source

`source` gains `credential` (only with `url`). The adapter is
`evalharness.adapters.sources.repo:build`; its options are:

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `repo` | string | required | Name under `repos`. |
| `cases` | string | required | Snapshot-relative. `.jsonl` is one Case per line; `.yaml` is a list or `{cases: [...]}`. |
| `fixtures` | string | unset | Snapshot-relative golden directory. |
| `case_limit` | int >= 1 | `500` | Refuses more cases. |
| `inline_fixtures`, `inline_max_bytes` | bool, int >= 0 | `false`, `65536` | Inline a fixture up to the cap into `case.metadata["fixtures"][i]["text"]`. |

A case may list `metadata.fixtures: ["golden/refund.json"]`. The source checks
each path inside the snapshot and writes
`case.metadata["fixtures"] = [{"path", "sha256", "bytes", "text"?}]`, so a
judge or metric needs no filesystem access.

### 3.3 Contexts

An entry is either the existing chunk list or a mapping with `adapter` and
`options`; mixing `chunks` and `adapter` is an error.

`repo_graph` options:

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `repo` | string | required | Name under `repos`. Selectors read the source snapshot, or snapshot a local tree with the same helper; they never authenticate. A remote non-source repo is refused. |
| `rank` | `lexical`/`pagerank` | `lexical` | Path and symbol overlap; pagerank adds import in-degree. `embeddings` refused. |
| `seed_chunks`, `expand` | int >= 1, int >= 0 | `3`, `2` | Files seeded, then import hops. |
| `chunk_lines` | int >= 8 | `80` | Window around the best matching line. |
| `max_chunks`, `max_bytes` | int >= 1, int >= 512 | `8`, `24000` | Hard caps; deterministic truncation, whole lines only. |
| `include_tests` | bool | `false` | Excludes `tests/` and `test_*` when false. |

`artifact_graph:build` takes `run` (required), `max_chunks` (6),
`max_bytes` (12000) and `exclude_candidate`. It reads a closed run's immutable
artifacts: nodes are cited chunk ids and case ids, edges are co-citation in one
answer. No live database read.

Chunks carry optional `path`, `start_line`, `end_line`, `blob_sha256`,
`score` and `graph_nodes`. Ids are
`repo:<name>/<path>#L<start>-L<end>` or `run:<run_id>/<cell_id>#answer`.

### 3.4 Worked example

```yaml
spec_version: 1
name: repo-context-ab
description: Graph context versus a hand-picked set on the payments repo.
seed: 7
repos:
  payments:
    root: ../payments-service
    include: ["src/**", "evals/**"]
source:
  adapter: evalharness.adapters.sources.repo:build
  options: {repo: payments, cases: evals/cases.jsonl, fixtures: evals/fixtures}
prompts:
  v4: Answer from the supplied context only. Cite chunk ids.
contexts:
  hand-picked:
    - {chunk_id: policy/refunds#L1-L40, text: "Refunds within 30 days of delivery."}
  graph:
    adapter: evalharness.adapters.contexts.repo_graph:build
    options: {repo: payments, rank: lexical, max_chunks: 8}
candidates:
  - {name: rag-hand, adapter: "evalharness.adapters.candidates.stub:build",
     model: stub-1, options: {prompt: v4, context: hand-picked}}
  - {name: rag-graph, adapter: "evalharness.adapters.candidates.stub:build",
     model: stub-1, options: {prompt: v4, context: graph}}
replicas: 2
metrics:
  - {key: exact_match, adapter: "evalharness.adapters.metrics.exact_match:build"}
gates: [{metric: exact_match, min: 0.90}]
prices: {stub-1: {input: 0.15, output: 0.60}}
```

The stub ignores context text, so this proves the plumbing, not quality.

### 3.5 Actionable errors

| Field | Reason | Fix |
| --- | --- | --- |
| `repos.payments.root` | An absolute root would enter every run snapshot. | Use `../payments-service`. |
| `repos.payments.credential` | Not stored in the vault. | `evalh credentials add github-read --provider git`. |
| `contexts.graph.options.repo` | Selectors cannot authenticate. | Attach it as source or clone locally. |
| `contexts.graph.options.rank` | No embedding ranker in this build. | Use `lexical` or `pagerank`. |

## 4. Protocol decision

**Repo attach is a Source.** It produces the case set, the whole contract:
`load(spec, ctx) -> Iterable[Case]`. It inherits source/1..3 and the manifest
gate. The gap is auth: `SourceSpec` has no credential and `service._load_cases`
passes `credential=None, NoOpRedactor()`. The design adds `source.credential`
and resolves the handle per launch, like a candidate credential.

**Graph context is a new `ContextSelector`.** Against the five:

| Protocol | Why not |
| --- | --- |
| Candidate | Selection hides inside the answer; the variant records no chunks. |
| Source | Loads cases once; selection is per case. Chunks in `Case.metadata` would make the planner reinterpret adapter data. |
| Judge | Sees the answer after execution; cannot inject. |
| Metric | Sees the cell after execution; cannot inject. |
| Sink | Writes output only. |

```python
class ContextSelector(Protocol):
    """Select the chunks one case receives from a declared context source."""

    name: str

    def select(self, case: Case, ctx: RunContext) -> list[ContextChunk]:
        """Return chunks for one case, deterministically for the same seed."""
```

Synchronous like Source, because the planner is synchronous; a factory builds
the index once. `RunContext` already carries the seed, redactor and one
credential.

Cost: one protocol, one conformance kind (`context`), one manifest subpackage,
and a per-case planner. Core imports only the protocol.

## 5. Data flow and provenance

1. **Validate.** `Spec` gains `repos`; `SourceSpec` gains `credential`;
   `contexts` becomes `dict[str, ContextSpec | list[ContextChunk]]`. Checks cover
   repo names, root/url exclusivity, relative roots, URL shape, credential
   usability, adapter paths and option types. No repo I/O yet.
2. **Snapshot.** On `load` (run, `--dry-run`, console preview) the source writes
   `$EVALH_HOME/repos/<digest>/`: `manifest.json` (path, sha256, bytes, mode,
   skipped, ref, commit, dirty) and `tree/` (included files). Digest hashes
   sorted `<blob-sha>  <path>` lines; the same digest reuses the directory.
3. **Load and plan.** Read cases from the snapshot, validate Cases, resolve
   fixtures, emit `source_loaded`. `expand` then selects per
   `(context_name, case_id)`, caches, and writes `context`, `context_name`,
   `context_digest`, `context_truncated` into the variant.
   `context_digest` hashes sorted `chunk_id|blob_sha256` lines.
4. **Execute.** `RunContext.chunks` comes from `cell.variant["context"]`, not
   `resolved.chunks`. Today it is the latter; that line must change or recorded
   and injected context can disagree.
5. **Score and store.** Metrics see the cell and case; chunk ids are citable
   evidence. The run gains `source_snapshot`; the cell already stores `variant`;
   `cell.json` already includes it.

Spec and prices sit in `runs.spec_snapshot` and `runs.prices_snapshot`; repo
identity, case digests, fixture stats and context stats sit in
`runs.source_snapshot`; selected chunks sit in `cells.variant`; repo content
sits in the snapshot tree. The DB stores digests and paths, never repo bytes.

A spec sha cannot see a repo edit. On resume the harness compares each recorded
repo digest with the digest it resolves now. A mismatch refuses the resume:
exit 2, field `repos.payments`, "the repository content changed since this run
was launched", fix "restore the tree or start a new run" ([runs.md](runs.md)).

## 6. Secrets and redaction

The token is a vault secret: resolved into a `CredentialHandle`, never dumped,
replaced by `[redacted:<name>]` everywhere.

| Surface | Rule |
| --- | --- |
| Spec, artifacts | No token; URL userinfo and query rejected. The manifest stores the sanitized URL, relative root and hashes only. |
| Vault | Only place at rest. Provider `git`, verified by one `git ls-remote` with a 5 s timeout. |
| Git | Token via `GIT_ASKPASS` in a 0700 temp dir, never argv; `GIT_TERMINAL_PROMPT=0`, `http.followRedirects=false`, no hooks, no submodules; helper removed after. |
| Errors, events, logs | Git stderr redacted and the URL sanitized before any `SpecError` or event; the source context carries the real redactor, not `NoOpRedactor`. |
| Cells, HTML | Repo text and relative paths only. The card shows name, file count, digest prefix; never an absolute root. |

Repository content is not vault content. A committed `.env` is a secret the
redactor cannot know, so the snapshot excludes `**/.env*`, `**/*.pem`,
`**/*.key` and `**/id_rsa*` by default and records each exclusion: a filename
policy, not a content scanner.

## 7. Storage and schema

Schema version 8. One nullable column through the generic nullable pass, no
rebuild:

```text
runs.source_snapshot  JSON  NULL
```

Version 7 is the head. A version 8 database keeps every row and reads a missing
snapshot as `{}`; the receipt says "not recorded" rather than inventing one.

```json
{"adapter": "evalharness.adapters.sources.repo:build",
 "repos": [{"name": "payments", "root": "../payments-service", "url": null,
            "ref": null, "commit": null, "digest": "ab12...", "files": 412,
            "bytes": 3810000, "skipped_binary": 3, "dirty": true}],
 "cases": [{"case_id": "refund-1", "prompt_sha256": "9f...", "tier": 1}],
 "case_count": 40,
 "contexts": [{"name": "graph", "options_digest": "ef56...", "chunk_max": 8,
               "truncated_cases": 1, "selection_ms": 812}]}
```

Snapshots sit under `$EVALH_HOME/repos/`, not the per-run artifacts root,
because they are shared and content-addressed. A referenced snapshot is pinned;
0.4 has no automatic GC. Removing one breaks cell evidence, so deletion is an
explicit command with a dry run in 0.4.1.

## 8. Failure modes

Except a refused resume, all are detected before the first cell: exit 2, no new
run row.

| Failure | Detection | Surface | Fix |
| --- | --- | --- | --- |
| Missing root, or a file | Snapshot | `repos.<name>.root`, value, "does not exist" or "is a file, not a directory". | Clone it or fix the path. |
| Auth failure 401/403, or `git` missing | Fetch, one attempt | `repos.<name>.credential` or `.url`, "the remote rejected authentication (HTTP 401)" or "git is not on PATH"; token never echoed. | Check the token, or install Git. |
| Rate limit 429 | Fetch, one retry honouring `Retry-After` up to 60 s | `repos.<name>.url`, host, retry seconds. | Wait or use a local clone. |
| Ref not found | Fetch | `repos.<name>.ref`, value, "the ref is not in the remote". | Fetch it or use a commit. |
| Huge repo | Caps | `max_files`/`max_total_bytes`, observed count, "refuses to truncate silently". | Raise the cap or narrow `include`. |
| Binary, oversize file or escaping symlink | Walk | `skip` records and continues; `error` names the first path; a cases or fixtures file over the cap errors; escaping links are skipped with a warning. | Choose the policy or raise the cap. |
| Cases missing/malformed or duplicate ids | Load, planner | `source.options.cases` with path, line, parser message; duplicate ids use the existing `source` error. | Fix the file. |
| Selector empty or raises | Plan | Warning `context_empty` with count, or `contexts.<name>.options` with the case id and adapter message. | Widen `include` or fix the option. |
| Digest changed on resume | Resume | `repos.<name>`, "the repository content changed". | Start a new run. |
| Artifact run missing | Plan | `contexts.<name>.options.run`, id, "the run has no artifacts". | Name an existing run. |

Partial context is not a failure. `max_chunks` or `max_bytes` truncation records
`context_truncated: true` and an event with the dropped count. Whether that
matters is a metric's question.

## 9. Observability, cost, latency

Events: `repo_snapshot`, `source_loaded`, `context_planned`,
`context_truncated`, each with counts and milliseconds. Payloads are redacted
and carry no absolute path.

The receipt gains `source` (the snapshot plus `source_recorded`); the diff gains
a `source` category before `cases`. `_case_changes` reads only
`source.options.cases` today, so it must fall back to `source_snapshot.cases`
or a changed case list shows "No differences".

Cost is zero model tokens. Disk is capped per snapshot plus a depth-1 partial
clone per remote URL.

| Stage | Target | Enforcement |
| --- | --- | --- |
| Local snapshot, 500 files / 10 MB | 2 s | Content caps, no time truncation. |
| Remote fetch | 30 s typical, 60 s hard | Process timeout; failure, not partial snapshot. |
| Graph build, 2000 files | 5 s | File cap; cached per repo digest. |
| Selection, then the whole plan | 50 ms per case-context; 10 s for 40 cases x 2 contexts | Chunk and byte caps; selection errors fail before spend. |

Wall-clock never decides which chunks are selected. A slow machine produces the
same variant as a fast one.

## 10. Conformance and tests

| Adapter | Kind | Suite options |
| --- | --- | --- |
| `adapters.sources.repo:build` | source | `tests/fixtures/repos/tiny` |
| `adapters.contexts.repo_graph:build` | context | same tree, `max_chunks: 4` |
| `adapters.contexts.artifact_graph:build` | context | checked-in artifacts fixture |

`_PACKAGE_KINDS` gains `contexts: context`. Append-only clauses `context/1..5`:
returns `ContextChunk` instances; ids unique and non-empty; equal results for
the same case and seed; `max_chunks` and `max_bytes` respected; the case is not
mutated and a `../` path or escaping symlink is not read. Source gains
`source/4` (same digest twice) and `source/5` (no absolute host path in case
metadata).

Tests, through the CLI, HTTP API or fixture tree: spec errors
(`test_spec_repo.py`); snapshot, binary, symlink, caps, fixtures
(`test_repo_source.py`); selection, truncation, empty match
(`test_repo_context.py`); receipt, diff, resume refusal
(`test_repo_provenance.py`); a canary token absent from every surface after a
forced 401 (`test_repo_secrets.py`); the version 7 migration and the core import
scan (`test_migrations.py`, `test_architecture.py`).

The fixture tree is committed, so CI needs no network and no `git`. Remote fetch
is opt-in (`EVALH_GIT_CONFORMANCE=1`) against a stdlib 401-then-200 server plus
a local repository. The 100 percent coverage gate still applies.

## 11. Rollout, compatibility, open questions

1. **0.4.0a, repo attach.** `repos`, `source.credential`, repo source, snapshot
   store, schema 8, receipt and diff. Local roots only.
2. **0.4.0b, protocol and remote.** `ContextSelector`, conformance kind,
   per-case planner and executor, `repo_graph`, console card, remote fetch with
   `git` and the failure surfaces.
3. **0.4.1, artifact graph and hygiene.** `artifact_graph`, snapshot GC, an
   optional fixture handle for metrics if inlining proves blunt.

Compatibility: old specs parse unchanged; old binaries refuse new specs with
exit 2; old runs receipt "not recorded"; version 7 upgrades in place; downgrade
unsupported.

Open questions:

1. Top-level `repos` versus `source.options`; the map is shared state.
2. Plan-time selection is exact but adds setup latency; moving it to execution
   would weaken the snapshot.
3. The `git` binary is an external prerequisite; a pure-Python client costs a
   dependency. Fixture access and snapshot GC need separate decisions.

## 12. Compromises the architecture forced

- **D8 is partly reversed.** Repo reading returns as an adapter: eval inputs
  must be harness-owned. Core still scans nothing.
- **Source has no credential or redactor.** `_load_cases` passes `None` and
  `NoOpRedactor()`; private repos need both plumbed through CLI, console preview
  and resume.
- **Context is candidate-level.** `expand` builds one variant per candidate and
  the executor reads `resolved.chunks`; both change, and the shared variant dict
  must become per-cell (or stay immutable).
- **Cell ids ignore context.** `sha1(candidate|case_id|replica)` stays stable,
  so repo and context digests must live in `source_snapshot` and the variant.
- **One source, one diff.** Two repositories cannot both contribute cases; repo
  cases are invisible to the diff until `source_snapshot.cases` lands.
- **No directory picker.** Server-rendered HTML means a typed relative path and
  a field error; an upload would duplicate the snapshot and risk logs.
