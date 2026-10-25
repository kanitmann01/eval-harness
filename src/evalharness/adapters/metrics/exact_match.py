"""Rule-based exact-match metric against a case's expected answer."""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.core.models import CellRecord, Check, ScoreContext


class ExactMatchMetric:
    """Pass when the answer equals the expected value after stripping space."""

    def __init__(self, key: str, expected_key: str = "answer") -> None:
        self.key = key
        self._expected_key = expected_key

    async def check(self, cell: CellRecord, ctx: ScoreContext) -> list[Check]:
        """Compare the recorded answer with the case's ground truth."""
        case = ctx.cases[cell.case_id]
        expected = str((case.expected or {}).get(self._expected_key, "")).strip()
        actual = cell.answer.strip()
        passed = actual == expected
        return [
            Check(
                metric=self.key,
                passed=passed,
                score=1.0 if passed else 0.0,
                reason=f"expected {expected!r}, got {actual!r}",
                evidence={"expected": expected, "actual": actual},
            )
        ]


def build(identity: str, options: Mapping[str, Any]) -> ExactMatchMetric:
    """Build an exact-match metric named by the spec's metric key."""
    return ExactMatchMetric(
        identity, expected_key=str(options.get("expected_key", "answer"))
    )
