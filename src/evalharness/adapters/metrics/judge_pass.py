"""Judge-backed correctness: ask the named judge about the answer itself.

`metrics.citation_support` judges one citation at a time and needs citations.
This metric is the general case: the whole answer goes to the judge with the
case prompt, rubric and expected data, and the verdict becomes one check. It
is what lets a spec compare two judges, or a judge at one provider against a
judge at another, over the same candidate answer.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.core.models import CellRecord, Check, JudgeRequest, ScoreContext


class JudgePassMetric:
    """Pass when the named judge passes the candidate's answer."""

    def __init__(self, key: str, judge_name: str) -> None:
        self.key = key
        self._judge_name = judge_name

    async def check(self, cell: CellRecord, ctx: ScoreContext) -> list[Check]:
        """Ask the judge and turn its verdict into one check."""
        case = ctx.cases[cell.case_id]
        answer = ctx.redactor.redact(cell.answer)
        session = ctx.judges[self._judge_name]
        verdict = await session.verdict(
            JudgeRequest(
                case_id=case.case_id,
                prompt=case.prompt,
                answer=answer,
                rubric=case.rubric,
                expected=case.expected,
                evidence={"candidate": cell.candidate},
            )
        )
        return [
            Check(
                metric=self.key,
                passed=verdict.passed,
                score=verdict.score,
                reason=verdict.reason,
                evidence={
                    "judge": self._judge_name,
                    "answer": answer,
                    "verdict": verdict.evidence,
                },
                judge_config_hash=session.config_hash,
            )
        ]


def build(identity: str, options: Mapping[str, Any]) -> JudgePassMetric:
    """Build a judge-backed correctness metric from its options."""
    judge_name = options.get("judge")
    if not isinstance(judge_name, str) or not judge_name:
        raise ValueError("judge_pass needs options.judge naming a declared judge")
    return JudgePassMetric(identity, judge_name)
