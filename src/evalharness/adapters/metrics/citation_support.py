"""Judge-backed citation support: every citation must support the answer.

The metric asks the judge the spec names whether each citation the candidate
reported actually supports the answer. It returns one check, passing only when
there is at least one citation and every citation is supported. The evidence
lists every citation with the judge's verdict and reason, and the check
records the judge configuration hash it was produced under.
"""

from __future__ import annotations

from typing import Any, Mapping

from evalharness.core.models import CellRecord, Check, JudgeRequest, ScoreContext


class CitationSupportMetric:
    """Pass when the named judge supports every citation on the cell."""

    def __init__(self, key: str, judge_name: str) -> None:
        self.key = key
        self._judge_name = judge_name

    async def check(self, cell: CellRecord, ctx: ScoreContext) -> list[Check]:
        """Judge each citation and combine the verdicts into one check."""
        case = ctx.cases[cell.case_id]
        answer = ctx.redactor.redact(cell.answer)
        citations = [
            ctx.redactor.redact(str(citation)) for citation in _citations(cell)
        ]
        session = ctx.judges[self._judge_name]
        judged: list[dict[str, Any]] = []
        for citation in citations:
            verdict = await session.verdict(
                JudgeRequest(
                    case_id=case.case_id,
                    prompt=case.prompt,
                    answer=citation,
                    rubric=case.rubric,
                    expected=case.expected,
                    evidence={"citation": citation, "answer": answer},
                )
            )
            judged.append(
                {
                    "citation": citation,
                    "passed": verdict.passed,
                    "reason": verdict.reason,
                }
            )
        passed = bool(judged) and all(item["passed"] for item in judged)
        return [
            Check(
                metric=self.key,
                passed=passed,
                score=1.0 if passed else 0.0,
                reason=_reason(judged),
                evidence={
                    "judge": self._judge_name,
                    "answer": answer,
                    "citations": judged,
                },
                judge_config_hash=session.config_hash,
            )
        ]


def _citations(cell: CellRecord) -> list[str]:
    """Return the citations the candidate recorded on this cell."""
    value = cell.output.get("citations", [])
    if not isinstance(value, list):
        return []
    return [str(citation) for citation in value]


def _reason(judged: list[dict[str, Any]]) -> str:
    """Explain an empty citation list, a clean pass, or the failing citations."""
    if not judged:
        return "the answer cites no sources"
    failed = sum(1 for item in judged if not item["passed"])
    if failed:
        return f"{failed} of {len(judged)} citation(s) do not support the answer"
    return f"all {len(judged)} citation(s) support the answer"


def build(identity: str, options: Mapping[str, Any]) -> CitationSupportMetric:
    """Build a citation metric bound to the judge named in its options."""
    judge_name = options.get("judge")
    if not isinstance(judge_name, str) or not judge_name:
        raise ValueError("citation_support needs options.judge naming a declared judge")
    return CitationSupportMetric(identity, judge_name)
