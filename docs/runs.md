# Runs: budget, cancel and resume

A run is one identity: the `run_id` created at launch, with the spec snapshot,
price snapshot, seed and cells attached to it. Everything below keeps that
identity. A cancel or a budget abort closes the run; a resume continues the same
run in place. `evalh run SPEC` starts a run, `evalh run SPEC --resume RUN_ID`
continues one, and `evalh run SPEC --dry-run` only previews it.

## Budget

The spec declares `budget_usd`, USD for the whole run. Two checks enforce it.

### Launch check

Before any cell runs, `evalh run` estimates the run cost with
`core.cost.estimate_cost` and compares it with the cap.

- The median prompt and completion tokens come from the most recent run of the
  same spec sha when that run has at least one `ok` cell. Without such a run the
  fallback is 500 prompt and 200 completion tokens per cell
  (`DEFAULT_MEDIAN_PROMPT_TOKENS`, `DEFAULT_MEDIAN_COMPLETION_TOKENS`).
- If the estimate is above the cap the run is refused: exit code 2, a spec error
  naming `budget_usd`, the estimate and the cap, and nothing is recorded (no run
  row, no cells).
- `--override-budget USD` sets the cap for this one run. The run stores it in
  `runs.budget_usd` and `runs.budget_override_usd`, and a `budget_overridden`
  event records the spec cap, the effective cap and the estimate.
- `--dry-run` executes nothing, so it never refuses. It prints the estimate and,
  when the estimate is over the spec cap, a line saying a real run would be
  refused until the budget is raised or overridden.

### Mid-run guard

The guard runs once before the first cell (so a resumed budget-aborted run does
not spend again) and after every finished cell.

- Spend is the sum of the `cost_usd` of every cell this run has, including cells
  an earlier attempt completed.
- Once spend is strictly greater than the cap, the executor stops scheduling new
  cells, records every unstarted cell with `status=cancelled` and an output of
  `{"reason": "budget exceeded: spent $X of $Y"}`, keeps completed cells, sets
  `runs.status=budget_exceeded`, stores the observed spend and the cap on the
  run, and appends `budget_exceeded`.
- The exit code is 3 (`exit_codes.BUDGET_ABORT`).

## Cancel

`CancellationToken` (`core/cancel.py`) is the cooperative flag. It is checked
between cells, never mid-cell: work already in flight finishes and is recorded.

- SIGINT (Ctrl-C): the CLI installs a handler for the duration of the run that
  cancels the token with reason `interrupted (SIGINT)`, and restores the previous
  handler when execution returns.
- `--cancel-after N` cancels once N cells have finished (an `ok` or `error`
  cell counts). N=0 cancels before the first cell. On a resume the count covers
  cells finished by this invocation; skipped `ok` cells are not counted. Tests
  drive this flag instead of a signal because it is deterministic.
- Unstarted cells are stored with `status=cancelled` and the reason in their
  output. The run is closed with `runs.status=cancelled` and `finished_at`, the
  sink is closed, and `run_cancelled` is appended before `run_finished`.
- Aggregates and gates are computed only from completed cells. A cancelled cell
  has no checks, so it contributes nothing to a proportion.
- Exit code: 0 unless a gate failed (1) or the budget was also exceeded (3).

Cancel is cell-granular. With `concurrency > 1` some in-flight cells may still
finish after the token trips, and which of them finish is not deterministic. Use
concurrency 1 when the count matters.

## Resume

`evalh run SPEC --resume RUN_ID` continues an existing run:

- The run is loaded with its spec snapshot, seed, prices and cells.
- A cell already `ok` in this run is skipped: not started, not charged again, its
  stored checks still count.
- Every other cell (cancelled, error, never started) runs, into the same
  `run_id`, with the same deterministic cell ids.
- Spend, aggregates and gates are recomputed over the whole run after the
  remainder finishes. Aggregate rows and gate rows are replaced, and the run's
  `cost_usd`, `n_cells` and `n_ok` describe the whole matrix.
- Events keep one monotonic sequence for the run id. The resume appends
  `run_started` (with `n_skipped`) and `run_resumed` after the last existing seq.
- A resumed run's aggregates equal those of an uninterrupted run of the same
  spec; `tests/test_cli_budget_cancel_resume.py` asserts it.

Refused resumes, each a spec error (exit 2):

| Case | Field | Fix |
| --- | --- | --- |
| No run with that id in this database | `--resume` | pass the id printed by an earlier run |
| Run already finished `ok` | `--resume` | start a new run |
| Spec seed differs from the run's seed | `seed` | restore the seed or start a new run |
| Spec file sha differs from the snapshot sha | `spec` | restore the file or start a new run |

`--override-budget USD` also works with `--resume`, which is how a run that
aborted on budget continues: without it the guard trips again before the first
cell.

A resume replays the current spec's matrix. The spec sha pins that matrix for the
built-in sources (inline, and the JSONL file source); a source that reads data
outside the spec must keep its case ids stable, or the "same aggregates"
guarantee does not hold.

## Exit codes

| Situation | Run status | Exit |
| --- | --- | --- |
| Every cell finished, gates passed | `ok` | 0 |
| Every cell finished, a gate failed | `ok` | 1 |
| Some cells errored, gates passed | `partial` | 0 |
| Some cells errored, a gate failed | `partial` | 1 |
| Cancelled, gates passed | `cancelled` | 0 |
| Cancelled, a gate failed | `cancelled` | 1 |
| Budget exceeded mid-run | `budget_exceeded` | 3 |
| Invalid spec, refused launch or refused resume | no run, or unchanged | 2 |
| Bad command line | no run | 2 |

Precedence: a budget abort wins over a gate failure, and a gate failure wins over
the cancelled default of zero.

## The one-run-at-a-time rule

A run id is driven by exactly one `evalh run` invocation at a time.

- Starting a run creates a new run id. Resuming continues an existing one in
  place; it never forks a second record for the same work.
- The CLI takes no lock. Two processes driving the same run id would interleave
  cells, budgets and event sequences, so callers serialise.
- Only a run that is not `ok` may be resumed. A process that crashed leaves
  `status=running`, which is resumable.
- A finished run's cells, aggregates and gates are rewritten only by its own
  resume. Nobody appends cells to an `ok` run.

The console implements one process-wide launch lock on top of this rule: one
run at a time per container, a second launch is refused with 409 rather than
queued, and the lock is released on finish, cancel and error. See
[console.md](console.md#one-run-at-a-time). The cancel token contract above is
unchanged; the console sets the token with the reason
`cancelled from the console`.

<!-- BEGIN issue #8: gate aggregates and their storage. Keep this block
     delimited so later runs tickets can add sections around it. -->

## Gates

A gate is a threshold on an **aggregate**, never on a raw check. The run
finishes and is scored first, then every gate is evaluated against the aggregate
rows issue #4 stores (n, value and the 95 percent Wilson interval). The exit
codes above are produced by that evaluation.

### Declaring a gate

A gate names a metric declared in the spec and sets `min`, `max`, or both.
`candidate` is optional; omit it for the run-wide figure.

~~~yaml
gates:
  - metric: exact_match
    min: 0.90
  - metric: citation_support
    candidate: rag-v4
    min: 0.85
  - metric: cost_usd_per_case
    max: 0.02
~~~

- Without `candidate`, the gate reads the **run-wide** value: every candidate's
  overall row pooled by its n, so it is the proportion over every check in the
  run, not an average of candidate averages.
- With `candidate`, the gate reads that candidate's overall row only. The
  candidate must be declared under `candidates:`, otherwise validation exits 2
  before anything runs. Two gates may target the same metric when they target
  different candidates, but one metric and candidate pair may have only one gate.
- `min` compares with `>=`, `max` with `<=`. With both, both must hold. The
  stored comparison and threshold are the bound that decided the outcome (min
  first when both reject), which is what a red build should display.
- A gate on an undefined aggregate (n = 0, value None) fails, with
  `reason: "no samples"` in its detail. A run with undefined aggregates and no
  gates still exits 0: undefined is a fact about the data, not a harness failure.

### Stored gate results

Each outcome is written to the `gates` table and printed after the aggregate
table:

~~~text
gate exact_match: pass observed=100.0% >= 90.0% n=1
gate exact_match[rag-v4]: FAIL observed=62.5% >= 85.0% n=8
~~~

The stored columns are `metric`, `candidate` (NULL for run-wide),
`comparison`, `threshold`, `observed`, `n`, `passed` and `detail`. The last
is the human-readable blob carried over from earlier tickets. Issue #8 added the
arithmetic columns as nullable columns through
[`storage/migrations.py`](../src/evalharness/storage/migrations.py), so a
database created by issue #4 keeps every old gate and gains them in place. That
migration is schema version 3; version 2 added the budget override from the
previous section.

### One exit-code decision

The state x exit-code matrix above is computed in exactly one place:
`exit_codes.for_run(status, gate_failed)`. Budget abort wins, then a failed
gate, then success. The CLI does not branch on a run status itself, so a new
status cannot silently acquire a new exit code. Gate failure always exits 1;
there is no flag to turn it off. `--cancel-after` and SIGINT still exit 0 when
their gates pass.

Gate evaluation is exercised end to end with no paid credential by
[`examples/specs/gated.yaml`](../examples/specs/gated.yaml) (exit 0) and
[`examples/specs/gated-failing.yaml`](../examples/specs/gated-failing.yaml)
(exit 1). CI asserts both codes. Every run opens the credential vault, so the
master key from [credentials.md](credentials.md) must be in the environment;
CI generates an ephemeral one for its disposable database. That key is not a
paid credential.

<!-- END issue #8: gate aggregates and their storage. -->

