"""Inline source adapter: cases written directly in the spec options."""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.core.context import RunContext
from evalharness.core.models import Case, Spec


class InlineSource:
    """Return the case list declared under source.options.cases."""

    def __init__(self, cases: list[Case]) -> None:
        self._cases = cases

    def load(self, spec: Spec, ctx: RunContext) -> list[Case]:
        """Return a copy of the declared cases."""
        return list(self._cases)


def build(identity: str, options: Mapping[str, Any]) -> InlineSource:
    """Build an inline source from the cases option."""
    raw_cases = list(options.get("cases") or [])
    cases = [Case.model_validate(case) for case in raw_cases]
    return InlineSource(cases)
