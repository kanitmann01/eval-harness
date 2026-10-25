"""Unit tests for the pure statistics behind the aggregates."""

from __future__ import annotations

import pytest

from evalharness.core.stats import (
    Z_95,
    pass_at_k,
    pass_k,
    proportion,
    wilson_interval,
)


def test_wilson_interval_matches_hand_computed_bounds():
    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=5e-5)
    assert high == pytest.approx(0.9433, abs=5e-5)


def test_wilson_interval_clamps_at_zero_and_one():
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert high == pytest.approx(0.2775, abs=1e-3)
    low, high = wilson_interval(10, 10)
    assert low == pytest.approx(0.7225, abs=1e-3)
    assert high == 1.0


def test_wilson_interval_uses_the_95_percent_constant_by_default():
    assert Z_95 == 1.96
    assert wilson_interval(5, 10) == wilson_interval(5, 10, 1.96)
    wider = wilson_interval(5, 10, 2.576)
    assert wider[0] < wilson_interval(5, 10)[0]
    assert wider[1] > wilson_interval(5, 10)[1]


def test_wilson_interval_needs_a_sample():
    with pytest.raises(ValueError) as raised:
        wilson_interval(0, 0)
    assert "n=0 is undefined" in str(raised.value)
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_proportion_is_undefined_without_samples():
    assert proportion(0, 0) is None
    assert proportion(1, 2) == 0.5
    assert proportion(2, 2) == 1.0
    assert proportion(0, 2) == 0.0


def test_proportion_rejects_impossible_counts():
    with pytest.raises(ValueError) as raised:
        proportion(3, 2)
    assert "3 passes out of 2" in str(raised.value)
    with pytest.raises(ValueError):
        proportion(-1, 2)


def test_pass_at_k_and_pass_k_collapse_to_the_proportion_at_k_one():
    outcomes = [True, False, True, False, True]
    expected = proportion(3, 5)
    assert pass_at_k(outcomes, 1) == expected
    assert pass_k(outcomes, 1) == expected


def test_pass_at_k_and_pass_k_are_different_claims():
    # Two cases with two replicas each: the first case passed once, the
    # second case never passed.
    outcomes = [True, False, False, False]
    assert pass_at_k(outcomes, 2) == 0.5
    assert pass_k(outcomes, 2) == 0.0
    assert pass_at_k([True, True], 2) == 1.0
    assert pass_k([True, True], 2) == 1.0


def test_pass_at_k_and_pass_k_are_undefined_without_cases():
    assert pass_at_k([], 2) is None
    assert pass_k([], 2) is None


def test_pass_at_k_rejects_ragged_or_impossible_k():
    with pytest.raises(ValueError) as raised:
        pass_at_k([True, False, True], 2)
    assert "do not split" in str(raised.value)
    with pytest.raises(ValueError) as raised:
        pass_k([True], 0)
    assert "at least 1" in str(raised.value)
