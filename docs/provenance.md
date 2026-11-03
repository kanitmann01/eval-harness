# Provenance

A receipt answers the question a pass rate cannot: what did this run actually
execute? It names the spec snapshot and its hash, the harness revision, the
candidate models, the judge configuration behind every scored metric, the price
table, the seed, the operator, the timestamps and the gates. The diff page then
compares two runs on the same questions and says whether the comparison is
like-for-like.

Provenance is read-only. The pages read the existing run, spec and check
records; they never edit a run or rewrite a check. Everything comes from the
database snapshot written when the run started, not from the current spec file.

## Routes

| Route | What it returns |
| --- | --- |
| `GET /runs/{run_id}/receipt` | The HTML receipt. |
| `GET /api/runs/{run_id}/receipt` | The same receipt as JSON. |
| `GET /runs/{run_id}/diff?against={other_run_id}` | The HTML spec diff and judge drift. |

An unknown run id renders `not_found.html` with a 404. The JSON receipt returns
a 404 with `{"detail": "..."}`. A diff without `against=` is a 404 that names
the missing query parameter; an unknown baseline is the same 404 as an unknown
current run. There is no JSON diff route in this slice.

The receipt JSON is the source of truth for the page, so a script and a reader
see the same values. Its top-level keys are `run_id`, `spec_id`, `spec_name`,
`status`, `spec_sha`, `harness_sha`, `operator`, `seed`, `started_at`,
`finished_at`, `candidates`, `judge_pins`, `price_table`, `source`,
`source_recorded`, `gates`, `previous`, `diff`, `drift`, `receipt_url`,
`page_url` and `run_url`.
Each `*_recorded` companion key says whether a value came from storage or is
honestly absent.

## Receipt fields

| Field | Source | Meaning |
| --- | --- | --- |
| Spec hash | `repository.get_spec(run.spec_id)["sha"]` | The stored spec record's SHA-256. An older or its missing spec record reads `not recorded`. |
| Harness sha | `runs.harness_sha` | The harness revision that executed the run. |
| Candidate models | The spec snapshot's `candidates` | Each candidate name, adapter and model. A candidate without a model reads `not recorded`. |
| Judge pins | Stored checks, then the spec snapshot | The judge configuration behind every judge-scored metric. See [Judge pinning and drift](#judge-pinning-and-drift). |
| Price table | `runs.prices_snapshot` | The fingerprint and every flattened rate. The fingerprint is the SHA-256 of the sorted, compact-flattened table, so the same rates always produce the same id. A version 2 snapshot nests rates by provider and flattens to `provider/model`; an older snapshot is already flat. |
| Source snapshot | `runs.source_snapshot` | The source adapter, the case count, and for each repository its name, root, ref, git commit, content digest, file count, bytes, skipped-binary count and dirty flag. A run with no snapshot reads `not recorded`. |
| Seed | `runs.seed` | The seed the run executed. |
| Operator | `runs.operator` | Who asked for the run, or `not recorded`. |
| Started, finished | `runs.started_at`, `runs.finished_at` | UTC timestamps. A run still in flight reads `not finished`. |
| Gates | Stored gate results | Every gate with its metric, pass state, comparison, threshold, observed value and sample count. |
| Previous run and diff | The previous run of the same spec | The link, its facts and the structured diff from that run to this one. |

All timestamps use the console's `YYYY-MM-DD HH:MM:SS UTC` format. Values that
were never recorded say `not recorded`; the page does not invent a blank, a
zero or a placeholder.

## Operator

Set `EVALH_OPERATOR` in the environment that launches the run:

```bash
EVALH_OPERATOR="Ken / FDE" evalh run specs/answers.yaml
```

The value is stripped of surrounding whitespace and stored on the run at
launch. An unset or whitespace-only value stores an empty string and the
receipt says `not recorded`. There is deliberately no CLI flag: the operator is
ambient in the execution environment, so a console launch and a command line
launch of the same spec behave the same way.

The field is nullable in the database because runs created before version 7
have no value. Those rows read as an empty string, never as a failure.

## Previous run

The receipt compares this run with the most recent other run of the same spec:

1. the newest other run with the same non-empty `spec_id`; otherwise
2. the newest other run with the same `spec_name`, using the run's stored name
   and falling back to the spec snapshot's name.

A run never compares with itself. The command line mints a new `spec_id` for
every run, even when the same YAML file is executed twice, so the name fallback
is the normal path for repeated CLI runs. When there is no other run the
receipt says `no previous run` and shows no diff.

## Spec diff

`core.spec_diff.diff_run_snapshots` is a pure function over two spec snapshots
and two price snapshots. It returns structured changes, counts per category, a
total and a one-line headline. Missing keys read as empty, so a partial
snapshot still diffs, and identical snapshots produce
`No differences between the two snapshots.`

Every change carries a category, a path, the before and after values, and a
one-line summary. The categories and their paths are:

| Category | Example path | What it covers |
| --- | --- | --- |
| `source` | `source.repos[docs].digest` | The source adapter and each repository digest added, removed or changed. |
| `cases` | `source.options.cases[case-1].prompt` or `source.cases[case-1].prompt_sha256` | Case prompts added, removed or changed, matched by `case_id`. |
| `candidates` | `candidates[cand].model` | Candidate adapter and model swaps, plus whole-candidate adds and removes. |
| `judges` | `judges[j].options` | Judge adapter, model and option changes, and metric-to-judge assignment changes. |
| `prices` | `prices[p/m].input` | Per-model rate changes in either price snapshot shape, plus added and removed price rows. |
| `run` | `seed`, `replicas`, `budget_usd` | Seed, replica count, budget and gate arithmetic knobs. |
| `gates` | `gates[exact_match].min` | Gates matched by metric and candidate: adds, removes and changed limits. |

Ordering is stable: categories sort as source, cases, candidates, judges, prices,
run, gates, and paths sort within each category. The headline is the total plus the
non-zero category counts, for example `3 changes: 3 cases.`.

The diff page shows the spec diff first, because it answers what changed, and
the judge drift callout second, because drift decides whether the change can be
read as a like-for-like result.

## Judge pinning and drift

A judge pin identifies the configuration that scored one metric:

| Field | Meaning |
| --- | --- |
| `metric` | The scored metric, for example `citation_support`. |
| `judge`, `judge_label` | The declared judge name, or `unknown judge` when the run stored a hash without a declaration. |
| `adapter`, `model` | The judge adapter and model from the spec snapshot, when declared. |
| `options_digest` | The SHA-256 of the judge options, so two option sets are comparable without printing them. |
| `temperature` | The declared sampling temperature, when present. |
| `config_hash` | The judge configuration hash. |
| `source` | `checks` when it came from a stored check, `spec` when it was derived from the declaration. |
| `last_used_at` | The run's finish time, or its start time while still running. |

Stored hashes win. Every check already records the `judge_config_hash` that
produced it, so a metric's pin set is the distinct hashes on its checks; one
pin per distinct hash. When the run has no stored hash for a judge-backed
metric, the pin falls back to the spec snapshot: the declared judge is turned
into a `JudgeSpec` and hashed with the same `judge_config_hash` the executor
uses. Hashing is not reimplemented for the receipt.

Drift is reported in two shapes:

- **Within one run.** A metric used more than one configuration hash. The run
  is internally inconsistent, so its checks are not one measurement.
- **Across two compared runs.** Both runs declare the same judge name but their
  configuration hashes differ. Even if the metric name and score look alike,
  the judge changed.

Every drift carries `kind`, `judge`, `metrics`, `hashes`, `runs` and a
`reason` that ends with `not like-for-like.` The diff page renders each drift
as a warning callout above the run facts. When there is no drift the page says
`No judge drift`. A changed judge is never presented as a win: silence would
let a scorer swap masquerade as a quality improvement.

## Tests

The behaviour is covered from the outside: a real CLI seeds runs, then HTTP
requests read the receipt, the diff and the 404s. There is no direct assertion
on a private helper. See
[`tests/test_provenance.py`](../tests/test_provenance.py) and
[`tests/test_spec_diff.py`](../tests/test_spec_diff.py).
