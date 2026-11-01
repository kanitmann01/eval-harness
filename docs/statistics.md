# Statistics: intervals, pass@k and undefined

Every number the harness prints for a metric carries its sample size and its
uncertainty. This page defines the four figures on an aggregate row, the
per-tier rule, and what happens when there are no samples. The implementation
is [core/stats.py](../src/evalharness/core/stats.py) and
[core/scorer.py](../src/evalharness/core/scorer.py); both are pure functions of
the checks a run recorded.

## An aggregate row

One row is one (candidate, metric, tier) group:

| Field | Meaning |
| --- | --- |
| n | the number of checks behind the row, one per cell per metric |
| value | passed checks / n; None when n is zero |
| ci_low, ci_high | the 95 percent Wilson interval for value; None when n is zero |
| pass_at_k | the fraction of cases where at least one of k replicas passed; None unless replicas > 1 |
| pass_k | the fraction of cases where all k replicas passed; None unless replicas > 1 |

A run emits one overall row per candidate and metric with tier None, plus one
row per case tier that candidate actually ran. A tier is the case's declared
tier (default 1). The overall row is not the average of the tier rows; it is
computed over every check, so it can be weighted differently when tiers have
different case counts.

## The Wilson interval

For s passes out of n samples, with p = s / n and z = 1.96 (the standard
normal quantile for a two-sided 95 percent interval):

~~~text
denominator = 1 + z^2 / n
centre      = (p + z^2 / (2n)) / denominator
margin      = z * sqrt(p(1-p)/n + z^2 / (4n^2)) / denominator
interval    = (centre - margin, centre + margin), clamped to [0, 1]
~~~

Wilson is used instead of the normal approximation because it stays sensible
at the extremes and at small n, which is where most evaluation runs live. It
also never claims certainty: 8 of 10 passes is 0.4902 to 0.9433 at 95 percent,
and even 1 of 1 is only 0.2065 to 1.0.

The bounds are clamped, so a run of perfect passes reports a high bound of
exactly 1.0 rather than a number above it, and a run of total failures reports
a low bound of exactly 0.0. The clamp is a display and storage guarantee, not
a claim of certainty; the interval width carries the uncertainty.

z is a parameter with a default, so a later ticket can ask for a different
confidence level without a second implementation.

## Comparing two proportions

A comparison asks a different question from a single aggregate: does B beat A,
by how much, and is the difference larger than noise? The point delta is
always B minus A, so a positive number means B did better:

~~~text
delta = p_b - p_a
~~~

The 95 percent interval on that delta uses the Newcombe hybrid score method.
Each side gets its own Wilson bounds, then those bounds are combined:

~~~text
low  = delta - sqrt((p_b - low_b)^2  + (high_a - p_a)^2)
high = delta + sqrt((high_b - p_b)^2 + (p_a - low_a)^2)
~~~

The result is clamped to [-1, 1], because a difference of two proportions
cannot leave that range. For 8 of 10 against 10 of 10 the delta is 0.2 and the
interval is -0.1124 to 0.5098; because it contains zero, the comparison is
inconclusive. For 0 of 40 against 1 of 40 the delta is 0.025 and the interval
is -0.0650 to 0.1288, also inconclusive. The method is used because it keeps
the coverage close to nominal at the small sample sizes and extreme
proportions that evaluation runs produce.

The verdict has four values, decided once:

| Verdict | Rule |
| --- | --- |
| better | delta > 0 and the whole interval is above zero |
| worse | delta < 0 and the whole interval is below zero |
| inconclusive | the interval crosses zero (or exactly touches it) |
| undefined | either side has no samples, so there is no delta |

A side with n = 0 has no proportion, so it has no delta and no interval; the
comparison renders it as undefined, never as zero. Two proportions measured
under different judges are never called a win: the delta and interval are
still shown for context, but the verdict is inconclusive and the comparison is
flagged as a different judge. The implementation is
[difference_interval](../src/evalharness/core/stats.py) and
[delta_or_none](../src/evalharness/core/stats.py).

## Percentiles

`percentile(values, quantile)` answers a question about observed samples, not
a proportion: what value sits at a given position in the sample. It sorts the
values and linearly interpolates between the two order statistics around
`quantile * (len(values) - 1)`. A quantile outside [0, 1] or an empty sample
raises `ValueError` with the fix in the message; a percentile of no samples is
undefined, never a default.

The decision page uses p50 and p95 latency over ok cells only. An errored cell
has a wall time but it did not measure an answer, so including it would let a
crash look fast. With two samples the p95 is an interpolation between them,
not the larger value; that is the honest answer for a small sample.

## Required sample size

`required_sample_size(baseline, effect, power=0.8, alpha=0.05)` returns the
number of samples **per side** needed to detect `effect` in a two-proportion
comparison. It assumes two independent proportions, equal n on each side, and
a two-sided Wald test at `alpha`. The effect is the absolute difference:
`p2 = baseline + effect`, not a relative lift. `effect` may be negative when
the second side is expected to be worse.

With `p1 = baseline`, `p2 = baseline + effect`, `pbar = (p1 + p2) / 2`,
`z_alpha = NormalDist().inv_cdf(1 - alpha / 2)` and
`z_power = NormalDist().inv_cdf(power)`:

~~~text
n = ceil(
      (z_alpha * sqrt(2 * pbar * (1 - pbar))
       + z_power * sqrt(p1 * (1 - p1) + p2 * (1 - p2)))^2
      / effect^2
    )
~~~

The result is clamped to at least one sample per side. Worked values the tests
pin:

| baseline | effect | n per side |
| --- | --- | --- |
| 0.70 | +0.10 | 294 |
| 0.50 | +0.10 | 388 |
| 0.80 | +0.10 | 199 |
| 0.70 | -0.10 | 356 |
| 0.90 | +0.10 | 74 |

`evalh sample-size --baseline 0.70 --effect 0.10` prints the per-side n, the
total n and every assumption above. `--power` and `--alpha` override the
defaults, and `--json` returns the same fields. A baseline outside (0, 1), an
effect of zero, a target outside [0, 1], or power or alpha outside (0, 1) exits
2 with one actionable stderr line naming the value and the fix.

This is a planning number. It is not a promise that a significant result will
appear at that n, and it does not correct for multiple metrics: testing five
metrics at alpha 0.05 raises the chance that one crosses zero by luck.

## Change my mind

The compare verdict gets a derived line under the decision. It is built from
the primary comparable metric, which is the first metric with a defined
verdict:

* When the 95 percent interval already excludes zero, the line says so and
  states the current n on each side. No larger sample is needed to establish
  that direction.
* When the interval crosses zero and the observed delta is not zero, the line
  calls `required_sample_size(A value, delta)` and reports the per-side n
  needed at 80 percent power and a two-sided 0.05 alpha, plus the n each side
  actually has. The delta can be negative, in which case the second side is
  worse and the same two-sided formula applies.
* When the observed delta is exactly zero, no sample size makes the interval
  exclude zero, and the line says that plainly.
* When the delta is undefined, or the values fall outside [0, 1] so the
  two-proportion formula does not apply, the line says the number cannot be
  computed.

The line never changes the verdict and never turns an inconclusive result into
a win. It answers the next question a reader asks: "what would it take to
change my mind?"

## pass@k versus pass^k

These are two different claims and are never collapsed into one figure:

* **pass@k** is the fraction of cases where at least one of k replicas passed.
  It answers "can the candidate get this case right at all".
* **pass^k** is the fraction of cases where all k replicas passed. It answers
  "does the candidate get this case right every time", the reliability claim.

Both are proportions over **cases, not replicas**. A case contributes one
outcome built from its k replicas, so the denominator is the number of cases,
not the number of attempts. With replicas = 2 and one case that passed once,
pass@2 is 1.0 and pass^2 is 0.0 while the plain proportion is 0.5; all three
figures are true statements about different questions.

At k = 1 both functions collapse to the plain proportion over cases, which is
the identity the tests pin. The CLI does not print pass@k or pass^k when the
spec ran a single replica, because at k = 1 they would repeat the value column.

A case whose replicas are not all recorded (a cell errored or was cancelled)
is left out of pass@k and pass^k: it cannot honestly claim "at least one
passed" or fail "all passed". The plain proportion still counts the checks
that exist.

## Undefined, never zero or one

A proportion with no samples is undefined. Rendering it as 0.0 would claim
every sample failed; rendering it as 1.0 would claim every sample passed.
Both are lies, and both would pass a min gate silently.

So a group with cells but zero checks gets a row with n = 0, value None,
ci_low and ci_high None, and no pass@k or pass^k. The CLI prints undefined for
the value. The row is stored, not skipped, because its absence would look like
a run that never considered the metric.

A gate on a metric with no samples fails, with reason "no samples" in its
detail. A run with undefined aggregates and no gates still exits 0: undefined
is a fact about the data, not a harness failure.

A group with no cells at all produces no row. That case means the planner
never built a cell for the candidate, which is a different fact from "the
cells ran and produced nothing".

## Storage contract

The aggregates table stores one row per (run_id, candidate, metric, tier),
with tier NULL for the overall row and value nullable. Rows are replaced as a
group per run, so repeated scoring stays idempotent. The surrogate id exists
because a NULL cannot participate in a portable primary key.

Databases created by issue #2 had a primary key over (run_id, candidate,
metric) and a NOT NULL value, so they cannot hold a tier row or an undefined
value. [storage/migrations.py](../src/evalharness/storage/migrations.py) runs
on Database startup: it records a schema version, adds missing nullable
columns in place, and rebuilds only the aggregates table for the issue #2
shape, copying every existing row. No row is ever dropped.
