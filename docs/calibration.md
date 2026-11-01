# Judge calibration

Calibration answers one question: when a stored check says pass or fail, does a
person who reads the same answer agree? It does not retrain a judge and it does
not edit the stored check. It records a second, human opinion next to the check
and reports how often the two agree.

## What a human label is

A human label is one person's verdict on one stored check. It is keyed by the
run, the cell and the metric, so it always points at exactly one recorded
verdict. Saving the same check again updates the existing label instead of
adding another opinion; clearing it removes the label and returns the check to
unlabelled.

Labels live in the `human_labels` table, created by the normal schema
`create_all` path. They are not part of the spec snapshot and do not change
run cost, status or aggregates. A label whose cell or metric is no longer
stored is stale and is ignored by the report rather than counted against the
run.

The review table is available on the calibration page for any completed run,
including a run with zero labels. The page is server-rendered and every control
is a plain form, so it works without JavaScript.

## Agreement math

Only labelled checks enter the rate. For a run with `n` labelled checks and
`a` agreements:

- agreement rate = `a / n`
- the interval is the two-sided Wilson score interval at 95% (`z = 1.96`)
  over the same `a` and `n`

With zero labelled checks the rate, the interval and the last-validated stamp
are all empty (JSON `null`, the page shows a dash) instead of a misleading
zero. A run with checks but no labels therefore reports `labelled: 0` and the
number of stored checks as `unlabelled`.

The report also groups labelled checks by the judge configuration hash stored
on each check. A judge whose model or options changed produces a different
hash, so its agreement history is a separate row. Deterministic metrics have no
hash and appear as one "Deterministic metric" row.

## Last validated stamp

Each label records the UTC time it was saved. The summary panel's "Last human
label" is the newest label time in the run; each judge row shows the newest
label time inside that configuration. Clearing a label removes its stamp. There
is no separate "validated" state: a saved label is a recorded human verdict,
and the stamp only says when it was last refreshed.

## Disagreement workflow

A disagreement is a labelled check where the human verdict differs from the
stored verdict. The page lists every disagreement with the case, candidate,
metric, both verdicts and the human note, and links to the cell evidence at
`/runs/{run_id}/cells/{cell_id}`. Read the answer and raw evidence, then either
save a corrected human verdict (which moves the check into agreement or removes
the label) or leave the disagreement in place as a recorded review finding.

Disagreements do not fail a gate and do not change the run status. They are a
review signal for the judge configuration, not a second gate.

## Operator

The optional `EVALH_OPERATOR` environment variable names the person saving a
label. Leading and trailing whitespace is stripped. When it is unset or blank,
the label stores `not recorded` and the page shows that value. The operator is
part of the label, not of the check.

```bash
EVALH_OPERATOR="alice" python -m evalharness serve
```

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/runs/{run_id}/calibration` | HTML agreement page, per-judge table, review and disagreement panels |
| GET | `/api/runs/{run_id}/calibration` | JSON report with counts, interval, judges, checks and disagreements |
| POST | `/runs/{run_id}/calibration/labels` | Save or update `{cell_id, metric, verdict=pass|fail, note}` |
| POST | `/runs/{run_id}/calibration/labels/clear` | Clear `{cell_id, metric}`; clearing an absent label still succeeds |

Both POST routes redirect with `303` to `/runs/{run_id}/calibration` on
success. An unknown run is a `404` using the console's not-found page (JSON
errors return `{"detail": "run <id> does not exist"}`). A cell that is not part
of the run, a metric with no stored check, or a verdict other than `pass` or
`fail` is a `400` that names the field, the value and the fix.

## JSON shape

The JSON report always includes `run_id`, `labelled`, `unlabelled`,
`agreements`, `agreement_rate`, `ci_low`, `ci_high`, an `interval`
object with `low` and `high`, `last_validated_at`, `judges`,
`disagreements` and `checks`. Timestamps are ISO 8601 UTC strings or `null`.
Each check and disagreement row carries the stored and human verdicts, the
judge name and model when the spec still declares them, the judge configuration
hash, the operator, the note and the `cell_url`. A missing value is `null`,
never a zero.

## Local checks

```bash
python -m pytest tests/test_calibration.py tests/test_api_calibration.py -x --no-cov
python -m pytest
```

The first command exercises the report math, the label store and the routes
without the coverage gate. The second is the repository gate and must finish at
100.00% line coverage with no failures.
