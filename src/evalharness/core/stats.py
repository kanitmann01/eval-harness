"""Pure statistics for aggregates: Wilson intervals, pass@k and pass^k.

Nothing here reads a database, a clock or the environment. The functions take
counts and return numbers, so a test can pin them against hand arithmetic.

Two vocabularies matter and are never collapsed:

* A proportion is a count over checks; one cell normally contributes one
  check per metric.
* pass@k and pass^k are proportions over cases, where every case carries k
  replica outcomes. Their denominator is cases, not replicas.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist

Z_95 = 1.96
"""The standard normal quantile for a two-sided 95 percent interval."""


def proportion(successes: int, n: int) -> float | None:
    """Return successes / n, or None when there are no samples.

    A proportion with n == 0 is undefined, never zero and never one: zero
    would claim every sample failed and one would claim every sample passed.

    Raises:
        ValueError: when a count is negative or successes exceeds n.
    """
    if n < 0 or successes < 0 or successes > n:
        raise ValueError(f"impossible proportion: {successes} passes out of {n}")
    if n == 0:
        return None
    return successes / n


def wilson_interval(
    successes: int,
    n: int,
    z: float = Z_95,
) -> tuple[float, float]:
    """Return the Wilson score interval for successes out of n, clamped to [0, 1].

    The interval is the 95 percent one when z keeps its default. It is
    narrower than the normal approximation and still behaves at the extremes,
    which is why the harness uses it for small runs.

    Raises:
        ValueError: when n == 0, because the interval is then undefined, or
            when the counts are impossible.
    """
    passed = proportion(successes, n)
    if passed is None:
        raise ValueError(
            "the Wilson interval needs at least one sample; n=0 is undefined"
        )
    denominator = 1.0 + z * z / n
    centre = (passed + z * z / (2 * n)) / denominator
    margin = (
        z
        * math.sqrt(passed * (1.0 - passed) / n + z * z / (4 * n * n))
        / denominator
    )
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def difference_interval(
    successes_a: int,
    n_a: int,
    successes_b: int,
    n_b: int,
    z: float = Z_95,
) -> tuple[float, float, float]:
    """Return (delta, low, high) for p_b - p_a using Newcombe's score method.

    Parameter:
        successes_a: passes on side A, out of n_a samples.
        successes_b: passes on side B, out of n_b samples.

    The delta is always B minus A, so a positive delta means B did better.
    The interval combines the two Wilson score intervals with Newcombe's
    hybrid score rule, which stays sensible at small n and at 0 or 1. It is
    clamped to [-1, 1] because a difference of proportions cannot leave it.

    Raises:
        ValueError: when either side has no samples, because a difference
            from an undefined proportion is undefined, or when a count is
            impossible.
    """
    passed_a = proportion(successes_a, n_a)
    passed_b = proportion(successes_b, n_b)
    if passed_a is None or passed_b is None:
        raise ValueError(
            "the difference interval needs at least one sample on each side; "
            "n=0 is undefined"
        )
    low_a, high_a = wilson_interval(successes_a, n_a, z)
    low_b, high_b = wilson_interval(successes_b, n_b, z)
    delta = passed_b - passed_a
    low = delta - math.sqrt(
        (passed_b - low_b) ** 2 + (high_a - passed_a) ** 2
    )
    high = delta + math.sqrt(
        (high_b - passed_b) ** 2 + (passed_a - low_a) ** 2
    )
    return (delta, max(-1.0, low), min(1.0, high))


def delta_or_none(
    successes_a: int,
    n_a: int,
    successes_b: int,
    n_b: int,
) -> float | None:
    """Return p_b - p_a, or None when either side has no samples.

    This is the point estimate without the interval. None is the honest answer
    when a side recorded no checks: there is no proportion to subtract, so the
    caller must render the delta as undefined instead of inventing zero.

    Raises:
        ValueError: when a count is negative or successes exceeds n.
    """
    passed_a = proportion(successes_a, n_a)
    passed_b = proportion(successes_b, n_b)
    if passed_a is None or passed_b is None:
        return None
    return passed_b - passed_a


def percentile(values: Sequence[float], quantile: float) -> float:
    """Return the quantile of values by linear interpolation.

    The method matches the common numpy default: sort the values, place the
    position at quantile * (n - 1) and interpolate between the two
    neighbours. The result is an observed quantile, not a fitted one, so a
    p95 latency never claims precision the sample does not have.

    Raises:
        ValueError: when values is empty or quantile is outside [0, 1].
    """
    if not values:
        raise ValueError("percentile needs at least one value; got an empty list")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError(f"quantile must be between 0 and 1, got {quantile}")
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def required_sample_size(
    baseline: float,
    effect: float,
    power: float = 0.8,
    alpha: float = 0.05,
) -> int:
    """Return the samples per side needed to detect an effect.

    Plans a two-sided test of two independent proportions with equal n:
    p1 = baseline and p2 = baseline + effect. The normal approximation is::

        n = (z(1 - alpha/2) * sqrt(2 * pbar * (1 - pbar))
             + z(power) * sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
            / effect ** 2

    with pbar = (p1 + p2) / 2. The result is rounded up, so it never
    understates the plan. It assumes the observed rates match the plan and
    ignores continuity correction; it is a planning figure, not a promise.

    Raises:
        ValueError: when baseline or baseline + effect is outside [0, 1],
            effect is zero, or power or alpha is outside (0, 1).
    """
    if not 0.0 <= baseline <= 1.0:
        raise ValueError(
            f"baseline must be a proportion between 0 and 1, got {baseline}; "
            "pass a rate such as 0.70"
        )
    if effect == 0.0:
        raise ValueError(
            "effect must not be zero; pass the smallest difference worth "
            "detecting, for example 0.10"
        )
    target = baseline + effect
    if not 0.0 <= target <= 1.0:
        raise ValueError(
            f"baseline + effect must stay between 0 and 1, got {target}; "
            "lower the effect or the baseline"
        )
    if not 0.0 < power < 1.0:
        raise ValueError(
            f"power must be between 0 and 1, got {power}; pass 0.80 for "
            "80 percent power"
        )
    if not 0.0 < alpha < 1.0:
        raise ValueError(
            f"alpha must be between 0 and 1, got {alpha}; pass 0.05 for a "
            "two-sided 95 percent interval"
        )
    normal = NormalDist()
    z_alpha = normal.inv_cdf(1.0 - alpha / 2.0)
    z_power = normal.inv_cdf(power)
    pooled = (baseline + target) / 2.0
    between = math.sqrt(2.0 * pooled * (1.0 - pooled))
    within = math.sqrt(
        baseline * (1.0 - baseline) + target * (1.0 - target)
    )
    numerator = (z_alpha * between + z_power * within) ** 2
    return max(1, math.ceil(numerator / (effect * effect)))


def pass_at_k(outcomes: list[bool], k: int) -> float | None:
    """Return the fraction of cases where at least one of k replicas passed.

    Parameter:
        outcomes: a flat, case-major list of replica results: the first k
            entries are one case, the next k the next case.

    The denominator is cases, not replicas, so this answers "the candidate
    got this case right at least once", not "what fraction of attempts
    passed". At k == 1 it equals the plain proportion over cases. Returns
    None when there are no cases.

    Raises:
        ValueError: when k is below one, or outcomes does not split into cases.
    """
    cases = _case_groups(outcomes, k)
    return proportion(sum(1 for case in cases if any(case)), len(cases))


def pass_k(outcomes: list[bool], k: int) -> float | None:
    """Return the fraction of cases where all k replicas passed.

    Parameter:
        outcomes: a flat, case-major list of replica results, as for pass_at_k.

    The denominator is cases, not replicas. At k == 1 it equals the plain
    proportion over cases, and therefore equals pass@k; at k > 1 it is the
    stricter reliability claim. Returns None when there are no cases.

    Raises:
        ValueError: when k is below one, or outcomes does not split into cases.
    """
    cases = _case_groups(outcomes, k)
    return proportion(sum(1 for case in cases if all(case)), len(cases))


def _case_groups(outcomes: list[bool], k: int) -> list[list[bool]]:
    """Split a flat, case-major outcome list into one group of k per case."""
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    if len(outcomes) % k:
        raise ValueError(
            f"{len(outcomes)} outcomes do not split into cases of k={k}"
        )
    return [outcomes[index : index + k] for index in range(0, len(outcomes), k)]
