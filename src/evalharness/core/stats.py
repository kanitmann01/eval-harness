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
