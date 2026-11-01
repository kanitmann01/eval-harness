"""Contract tests for the console microcopy, money and time formatters.

These are pure unit tests over the display helpers plus static-text checks for
the shared stylesheet and script. Browser behaviour is covered by the HTTP
console tests; this file keeps the formatting contract cheap to pin.
"""

from __future__ import annotations

from datetime import datetime

from evalharness.api.templating import format_time, format_usd


def test_usd_keeps_three_significant_digits_below_a_cent() -> None:
    """A real sub-cent cost stays readable without scientific notation."""
    assert format_usd(0.000066) == "$0.0000660"
    assert format_usd(0.00069) == "$0.000690"
    assert format_usd(0.000026) == "$0.0000260"
    assert format_usd(0.0000066) == "$0.00000660"


def test_usd_rounds_to_cents_at_or_above_a_cent() -> None:
    """Ordinary money uses two decimals once it reaches a cent."""
    assert format_usd(0.01) == "$0.01"
    assert format_usd(1.234) == "$1.23"
    assert format_usd(1234.5) == "$1,234.50"
    assert format_usd(0) == "$0.00"
    assert format_usd(None) == "-"


def test_timestamp_is_explicitly_utc() -> None:
    """Every stored timestamp renders in UTC and says so."""
    assert format_time(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02 03:04:05 UTC"
    assert format_time(None) == "-"
