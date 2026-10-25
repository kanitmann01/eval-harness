"""The public conformance suite and its runner.

Call ``run_suite(path, kind)`` for one adapter, or ``run_all()`` for every
first-party adapter in the manifest. A report carries one result per clause;
``ConformanceError`` names the clause and what the clause expected.
"""

from __future__ import annotations

from evalharness.conformance.runner import run_all, run_suite
from evalharness.conformance.suite import (
    CANDIDATE,
    CASES,
    JUDGE,
    KINDS,
    METRIC,
    SINK,
    SOURCE,
    ClauseResult,
    ConformanceCase,
    ConformanceContext,
    ConformanceError,
    ConformanceReport,
    cases_for,
    clause,
    run_case,
)

__all__ = [
    "CANDIDATE",
    "CASES",
    "JUDGE",
    "KINDS",
    "METRIC",
    "SINK",
    "SOURCE",
    "ClauseResult",
    "ConformanceCase",
    "ConformanceContext",
    "ConformanceError",
    "ConformanceReport",
    "cases_for",
    "clause",
    "run_all",
    "run_case",
    "run_suite",
]
