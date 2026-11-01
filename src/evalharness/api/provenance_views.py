"""View data for the run receipt and the two-run spec diff page.

The receipt answers what a run actually executed: the spec it snapshotted, the
harness revision, the candidate models, the judge configuration behind every
scored metric, the price table, the seed, the operator and the gates. The diff
page compares two runs on the same questions. Both pages read the repository
and return plain mappings, so the HTML route and the JSON route share one
contract.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from evalharness.core.judge_pins import (
    JudgeDrift,
    JudgePin,
    judge_drift,
    pins_for_run,
)
from evalharness.core.models import Check, RunRecord
from evalharness.core.spec_diff import (
    diff_run_snapshots,
    spec_diff_data,
)
from evalharness.storage.repository import Repository


class ProvenanceError(ValueError):
    """A receipt or diff request that cannot be answered."""


def receipt_data(repository: Repository, run_id: str) -> dict[str, Any]:
    """Return the receipt for one run as a JSON-ready mapping.

    Parameter:
        repository: the console repository.
        run_id: the run the receipt names.

    Raises:
        ProvenanceError: when the run does not exist.
    """
    run = _require_run(repository, run_id)
    spec = repository.get_spec(run.spec_id) or {}
    checks = _checks_for(repository, run.run_id)
    pins = pins_for_run(run, checks)
    drifts = judge_drift(run, checks)
    previous = _previous_run(repository, run)
    diff = (
        diff_run_snapshots(
            previous.spec_snapshot,
            run.spec_snapshot,
            previous.prices_snapshot,
            run.prices_snapshot,
        )
        if previous is not None
        else None
    )
    price_id, rates = _price_table(run.prices_snapshot)
    spec_sha = str(spec.get("sha") or "")
    return {
        "run_id": run.run_id,
        "spec_id": run.spec_id,
        "spec_name": _run_name(run),
        "status": run.status,
        "spec_sha": spec_sha,
        "spec_sha_recorded": bool(spec_sha),
        "harness_sha": run.harness_sha,
        "harness_sha_recorded": bool(run.harness_sha),
        "operator": run.operator,
        "operator_recorded": bool(run.operator),
        "seed": run.seed,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "candidates": [
            _candidate_data(candidate)
            for candidate in run.spec_snapshot.get("candidates") or []
        ],
        "judge_pins": [_pin_data(pin) for pin in pins],
        "price_table": {"id": price_id, "rates": rates},
        "price_table_recorded": bool(rates),
        "gates": [
            {
                "name": gate.name,
                "metric": gate.metric,
                "passed": gate.passed,
                "candidate": gate.candidate,
                "comparison": gate.comparison,
                "threshold": gate.threshold,
                "observed": gate.observed,
                "n": gate.n,
            }
            for gate in repository.list_gates(run.run_id)
        ],
        "previous": (
            _previous_data(previous) if previous is not None else None
        ),
        "diff": spec_diff_data(diff) if diff is not None else None,
        "drift": [_drift_data(drift) for drift in drifts],
        "receipt_url": f"/api/runs/{run.run_id}/receipt",
        "page_url": f"/runs/{run.run_id}/receipt",
        "run_url": f"/runs/{run.run_id}",
    }


def diff_data(
    repository: Repository, run_id: str, against: str = ""
) -> dict[str, Any]:
    """Return the two-run spec diff and judge drift for the page.

    Parameter:
        repository: the console repository.
        run_id: the current run, on the right of the diff.
        against: the baseline run id, on the left of the diff.

    Raises:
        ProvenanceError: when either run does not exist or against is empty.
    """
    current = _require_run(repository, run_id)
    if not against:
        raise ProvenanceError(
            "a diff needs ?against=<run id> naming the baseline run"
        )
    baseline = _require_run(repository, against)
    diff = diff_run_snapshots(
        baseline.spec_snapshot,
        current.spec_snapshot,
        baseline.prices_snapshot,
        current.prices_snapshot,
    )
    drifts = judge_drift(
        baseline,
        _checks_for(repository, baseline.run_id),
        current,
        _checks_for(repository, current.run_id),
    )
    return {
        "current": _run_side(current),
        "against": _run_side(baseline),
        "diff": spec_diff_data(diff),
        "drift": [_drift_data(drift) for drift in drifts],
        "diff_url": (
            f"/runs/{current.run_id}/diff?against={baseline.run_id}"
        ),
        "swap_url": (
            f"/runs/{baseline.run_id}/diff?against={current.run_id}"
        ),
    }


def _require_run(repository: Repository, run_id: str) -> RunRecord:
    """Return one run or raise the actionable not-found error."""
    run = repository.get_run(run_id)
    if run is None:
        raise ProvenanceError(
            f"run {run_id} does not exist; open the run list or start a run "
            "with evalh run <spec.yaml>"
        )
    return run


def _checks_for(repository: Repository, run_id: str) -> list[Check]:
    """Return every check a run stored, flattened across its cells."""
    by_cell = repository.list_checks_for_run(run_id)
    return [check for checks in by_cell.values() for check in checks]


def _run_name(run: RunRecord) -> str:
    """Return the run's spec name, falling back to the stored snapshot."""
    return run.spec_name or str((run.spec_snapshot or {}).get("name") or "")


def _previous_run(
    repository: Repository, current: RunRecord
) -> RunRecord | None:
    """Return the newest other run of the same spec, never the run itself."""
    others = [
        run for run in repository.list_runs() if run.run_id != current.run_id
    ]
    same_spec = next(
        (
            run
            for run in others
            if current.spec_id and run.spec_id == current.spec_id
        ),
        None,
    )
    if same_spec is not None:
        return same_spec
    name = _run_name(current)
    return next(
        (run for run in others if name and _run_name(run) == name),
        None,
    )


def _candidate_data(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return one candidate's recorded name, adapter and model."""
    model = str(candidate.get("model") or "")
    return {
        "name": str(candidate.get("name") or ""),
        "adapter": str(candidate.get("adapter") or ""),
        "model": model,
        "model_recorded": bool(model),
    }


def _pin_data(pin: JudgePin) -> dict[str, Any]:
    """Return one judge pin as a JSON-ready mapping."""
    return {
        "metric": pin.metric,
        "judge": pin.judge,
        "judge_label": pin.judge or "unknown judge",
        "adapter": pin.adapter,
        "model": pin.model,
        "options_digest": pin.options_digest,
        "temperature": pin.temperature,
        "config_hash": pin.config_hash,
        "source": pin.source,
        "last_used_at": pin.last_used_at,
    }


def _drift_data(drift: JudgeDrift) -> dict[str, Any]:
    """Return one judge drift as a JSON-ready mapping."""
    return {
        "kind": drift.kind,
        "judge": drift.judge,
        "metrics": list(drift.metrics),
        "hashes": list(drift.hashes),
        "runs": list(drift.runs),
        "reason": drift.reason,
    }


def _previous_data(previous: RunRecord) -> dict[str, Any]:
    """Return the link and identity of the run a receipt compares against."""
    return {
        "run_id": previous.run_id,
        "spec_name": _run_name(previous),
        "status": previous.status,
        "started_at": previous.started_at,
        "receipt_url": f"/runs/{previous.run_id}/receipt",
        "run_url": f"/runs/{previous.run_id}",
    }


def _run_side(run: RunRecord) -> dict[str, Any]:
    """Return the header facts for one side of the diff page."""
    return {
        "run_id": run.run_id,
        "spec_name": _run_name(run),
        "status": run.status,
        "seed": run.seed,
        "operator": run.operator,
        "operator_recorded": bool(run.operator),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "receipt_url": f"/runs/{run.run_id}/receipt",
        "run_url": f"/runs/{run.run_id}",
    }


def _price_table(
    snapshot: Mapping[str, Any],
) -> tuple[str, dict[str, dict[str, float]]]:
    """Return the price snapshot's fingerprint and flattened rates.

    The flattening mirrors core.compare so a receipt's fingerprint matches the
    one the compare page shows for the same run. A version 2 snapshot nests the
    table by provider; an older snapshot is already flat.
    """
    prices: dict[str, dict[str, float]]
    snapshot = dict(snapshot or {})
    if snapshot.get("version") == 2:
        providers = snapshot.get("providers") or {}
        prices = {
            (
                f"{provider}/{model}" if provider else str(model)
            ): {str(key): float(value) for key, value in dict(price).items()}
            for provider, models in providers.items()
            for model, price in models.items()
        }
    else:
        prices = {
            str(model): {
                str(key): float(value) for key, value in dict(price).items()
            }
            for model, price in snapshot.items()
        }
    identity = hashlib.sha256(
        json.dumps(prices, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return identity, prices
