# Decision: which candidate to trust in one run

The decision surface answers one question about one run: given the checks,
cost and latency it recorded, which candidate should a reader trust, and which
candidates are still defensible? It does not compare two runs. That is the
[compare surface](console.md), with its own judges and price tables.

Implementation: [core/decision.py](../src/evalharness/core/decision.py) ranks
the run; [api/decision_views.py](../src/evalharness/api/decision_views.py)
shapes the page and JSON;
[api/routes/decision.py](../src/evalharness/api/routes/decision.py) serves
both. The statistics are defined in [statistics.md](statistics.md).

## Routes

| Route | Returns |
| --- | --- |
| GET /runs/{run_id}/decision | the server-rendered page |
| GET /api/runs/{run_id}/decision | the JSON report |

An unknown run id returns 404. The page renders the not-found state with
`run <id> does not exist`; the JSON body is
`{"detail": "run <id> does not exist"}`.

## What one candidate reports

Every candidate in the run gets one report row:

* **checks**: every check its cells recorded, pooled. A metric that records
  more than one check per cell contributes more than one check, so it carries
  more weight in the pass rate. The page states this caveat.
* **pass rate**: passed checks divided by recorded checks, with a Wilson 95
  percent interval. No checks means undefined, not zero.
* **cost per correct check**: the candidate's whole recorded cell cost divided
  by its passing checks. Nothing passed means undefined. The cell cost includes
  answer, judge and tool spend, because that is what the run actually paid.
* **latency**: p50 and p95 wall time over ok cells only. Errored cells are
  counted in `n_errored` and excluded from the percentiles, because a crash
  did not measure an answer.
* **cells**: total, ok and errored counts.
* **dominance**: the conservative calls defined below.

## Ranking

Candidates are sorted best first:

1. defined pass rate before undefined, then pass rate descending;
2. defined cost per correct before undefined, then cost ascending;
3. defined p95 before undefined, then p95 ascending;
4. candidate name, for a deterministic tie.

Rank is 1-based. Ranking orders the table; it does not by itself claim one
candidate dominates another.

## Conservative dominance

Candidate A dominates candidate B only when **all** of these hold:

* A's Wilson lower bound is greater than B's Wilson upper bound, so the pass
  rate intervals do not overlap;
* A's cost per correct check is less than or equal to B's;
* A's p95 latency is less than or equal to B's;
* all five measures (pass rate, both interval bounds, cost per correct, p95)
  are defined for both candidates.

An undefined measure blocks dominance on both sides. A missing number is not
a good number, and a candidate that crashed or recorded no checks never gets to
win on cost or speed. Dominance is not transitive in general, because the
three measures can trade off.

The **Pareto frontier** is every candidate with all measures defined and no
dominator, in rank order. A candidate can be off the frontier even when no
single other candidate dominates it, if one of its measures is undefined.

## JSON shape

```json
{
  "run_id": "3f2a9c0d4e5f6a7b",
  "primary_metrics": ["exact_match"],
  "pareto_frontier": ["cited-stub"],
  "candidates": [
    {
      "candidate": "cited-stub",
      "rank": 1,
      "n_checks": 4,
      "passed": 4,
      "pass_rate": 1.0,
      "ci_low": 0.5101,
      "ci_high": 1.0,
      "cost_usd": 0.0021,
      "cost_per_correct": 0.000525,
      "n_cells": 4,
      "n_ok": 4,
      "n_errored": 0,
      "p50_latency_ms": 12.0,
      "p95_latency_ms": 18.0,
      "dominates": [],
      "dominated_by": [],
      "on_pareto_frontier": true
    }
  ]
}
```

Every value that is not measurable is `null`, never zero: `pass_rate`,
`ci_low`, `ci_high`, `cost_per_correct`, `p50_latency_ms` and
`p95_latency_ms`. `dominates` and `dominated_by` list candidate names,
sorted. `pareto_frontier` is in rank order. `primary_metrics` is the sorted
set of metric keys declared in the run's spec snapshot; when the spec declares
none, it falls back to the observed check metrics.

## The page

The page leads with the run id, status, spec name and primary metrics. The
ranked table is the accessible data source: it carries every candidate, its
interval, cost per correct, p50 and p95 latency, cell counts and the dominance
call. Empty states render: a run with no cells shows "No cells recorded" and
still names the primary metrics; a single candidate gets rank 1 and is on the
frontier when its measures are defined.

An inline SVG scatter plots **every candidate with a defined pass rate and cost
per correct check**, including dominated ones:

* the x axis is pass rate, 0 to 100 percent, left to right;
* the y axis is cost per correct check, rising from the cheapest at the bottom
  to the most expensive at the top, so lower is better;
* a filled marker and label is on the Pareto frontier; a hollow, muted marker
  is dominated or has an undefined measure such as p95 latency.

A candidate with no pass rate or no cost per correct check cannot be placed on
the axes, so the page lists it under "Not plotted" with the reason. Nothing is
silently dropped. The scatter is decoration for sighted readers; the ranked
table above it is the accessible table for the same data, and the SVG carries
a title and description.

The page does not edit `console.css`, does not use client-side script, and
uses the existing dollar, percent, interval and timestamp filters so a value is
formatted the same way everywhere in the console.
