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

__all__ = ["BUDGET_ABORT", "GATE_FAILURE", "SPEC_ERROR", "SUCCESS"]
