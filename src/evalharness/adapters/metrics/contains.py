"""Rule-based containment metric against a case's expected answer."""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.core.models import CellRecord, Check, ScoreContext


class ContainsMetric:
    """Pass when the answer contains the expected value as a substring."""

    def __init__(self, key: str, expected_key: str = "answer") -> None:
        self.key = key
        self._expected_key = expected_key

    def check(self, cell: CellRecord, ctx: ScoreContext) -> list[Check]:
        """Check that the expected substring appears in the answer."""
        case = ctx.cases[cell.case_id]
        expected = str((case.expected or {}).get(self._expected_key, "")).strip()
        actual = cell.answer.strip()
        passed = bool(expected) and expected in actual
        return [
            Check(
                metric=self.key,
                passed=passed,
                score=1.0 if passed else 0.0,
                reason=f"expected to contain {expected!r}, got {actual!r}",
                evidence={"expected": expected, "actual": actual},
            )
        ]


def build(identity: str, options: Mapping[str, Any]) -> ContainsMetric:
    """Build a containment metric named by the spec's metric key."""
    return ContainsMetric(
        identity, expected_key=str(options.get("expected_key", "answer"))
    )
