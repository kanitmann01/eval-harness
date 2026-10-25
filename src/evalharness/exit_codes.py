"""Exit codes are the automation contract for every harness command."""

from __future__ import annotations

SUCCESS = 0
"""The run completed and every gate passed."""

GATE_FAILURE = 1
"""The run completed but at least one gate failed."""

SPEC_ERROR = 2
"""The spec is invalid or could not be loaded; nothing was executed."""

BUDGET_ABORT = 3
"""The run stopped because the budget was exhausted."""


def for_run(*, status: str, gate_failed: bool) -> int:
    """Return the exit code for a finished run.

    The precedence is deliberate. A budget abort is a spend stop, not a
    quality verdict, so it wins over a gate. A gate failure wins over a
    cancelled run's default of zero: cancellation alone is a user decision, but
    a failed threshold on the cells that did run is still a failing verdict.
    A cancelled run whose gates pass exits zero, and cell errors never change
    the code by themselves.

    Parameter:
        status: the run status the executor recorded.
        gate_failed: whether any evaluated gate failed.
    """
    if status == "budget_exceeded":
        return BUDGET_ABORT
    if gate_failed:
        return GATE_FAILURE
    return SUCCESS


__all__ = ["BUDGET_ABORT", "GATE_FAILURE", "SPEC_ERROR", "SUCCESS", "for_run"]
