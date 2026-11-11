"""Judge calibration: compare stored metric verdicts with human labels.

The module takes records the caller loaded, never a database, and returns one
plain report the HTML page and the JSON route both render. Labels are matched
by run, cell and metric; a label whose check is no longer stored is stale and
is ignored rather than counted against the run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from evalharness.core.models import CellRecord, Check, RunRecord
from evalharness.core.spec_projection import ProjectedJudge, project_document
from evalharness.core.stats import proportion, wilson_interval


class HumanLabelLike(Protocol):
    """The fields a human label must expose to the calibration report."""

    run_id: str
    cell_id: str
    metric: str
    passed: bool
    note: str
    operator: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CalibrationCheck:
    """One stored check with its judge identity and optional human label."""

    run_id: str
    cell_id: str
    case_id: str
    candidate: str
    metric: str
    passed: bool
    reason: str
    judge_config_hash: str | None
    judge_name: str | None
    judge_model: str | None
    label: HumanLabelLike | None

    @property
    def agreement(self) -> bool | None:
        """Return whether the human agreed, or None without a label."""
        if self.label is None:
            return None
        return self.label.passed == self.passed

    @property
    def human_passed(self) -> bool | None:
        """Return the human verdict, or None when the check is unlabelled."""
        return None if self.label is None else self.label.passed

    @property
    def human_note(self) -> str | None:
        """Return the human note, or None when the check is unlabelled."""
        return None if self.label is None else self.label.note

    @property
    def operator(self) -> str | None:
        """Return who saved the label, or None when the check is unlabelled."""
        return None if self.label is None else self.label.operator

    @property
    def cell_url(self) -> str:
        """Return the console link to the cell behind this check."""
        return f"/runs/{self.run_id}/cells/{self.cell_id}"


@dataclass(frozen=True, slots=True)
class JudgeCalibration:
    """The labelled checks that share one judge configuration hash."""

    name: str | None
    model: str | None
    config_hash: str | None
    labelled: int
    agreements: int
    agreement_rate: float | None
    last_validated_at: datetime | None


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """The whole calibration view of one run."""

    run_id: str
    labelled: int
    unlabelled: int
    agreements: int
    agreement_rate: float | None
    interval_low: float | None
    interval_high: float | None
    last_validated_at: datetime | None
    judges: tuple[JudgeCalibration, ...]
    disagreements: tuple[CalibrationCheck, ...]
    checks: tuple[CalibrationCheck, ...]


def calibration_report(
    run: RunRecord,
    cells: list[CellRecord],
    checks_by_cell: dict[str, list[Check]],
    labels: list[HumanLabelLike],
) -> CalibrationReport:
    """Build the agreement report from stored records and human labels.

    A check without a label is unlabelled; a label without a stored check is
    stale and contributes nothing. Zero labelled checks yield a null rate and
    a null interval instead of dividing by zero.
    """
    labels_by_check = {
        (label.cell_id, label.metric): label for label in labels
    }
    rows = _review_rows(run, cells, checks_by_cell, labels_by_check)
    labelled_rows = [row for row in rows if row.label is not None]
    agreements = sum(1 for row in labelled_rows if row.agreement)
    agreement_rate = proportion(agreements, len(labelled_rows))
    if labelled_rows:
        interval_low, interval_high = wilson_interval(
            agreements, len(labelled_rows)
        )
    else:
        interval_low, interval_high = None, None
    last_validated_at = max(
        (row.label.created_at for row in labelled_rows if row.label is not None),
        default=None,
    )
    return CalibrationReport(
        run_id=run.run_id,
        labelled=len(labelled_rows),
        unlabelled=len(rows) - len(labelled_rows),
        agreements=agreements,
        agreement_rate=agreement_rate,
        interval_low=interval_low,
        interval_high=interval_high,
        last_validated_at=last_validated_at,
        judges=_judge_breakdown(rows),
        disagreements=tuple(
            row for row in labelled_rows if row.agreement is False
        ),
        checks=tuple(rows),
    )


def _review_rows(
    run: RunRecord,
    cells: list[CellRecord],
    checks_by_cell: dict[str, list[Check]],
    labels_by_check: dict[tuple[str, str], HumanLabelLike],
) -> list[CalibrationCheck]:
    """Flatten cells and checks into one deterministic review order."""
    rows: list[CalibrationCheck] = []
    ordered_cells = sorted(
        cells, key=lambda item: (item.candidate, item.case_id, item.cell_id)
    )
    judged = project_document(run.spec_snapshot or {}).judged_metrics
    for cell in ordered_cells:
        checks = sorted(
            checks_by_cell.get(cell.cell_id, []), key=lambda item: item.metric
        )
        for check in checks:
            judge_name, judge_model = _judge_identity(
                judged, check.metric, check.judge_config_hash
            )
            rows.append(
                CalibrationCheck(
                    run_id=run.run_id,
                    cell_id=cell.cell_id,
                    case_id=cell.case_id,
                    candidate=cell.candidate,
                    metric=check.metric,
                    passed=check.passed,
                    reason=check.reason,
                    judge_config_hash=check.judge_config_hash,
                    judge_name=judge_name,
                    judge_model=judge_model,
                    label=labels_by_check.get((cell.cell_id, check.metric)),
                )
            )
    return rows


def _judge_identity(
    judged: Mapping[str, ProjectedJudge],
    metric: str,
    config_hash: str | None,
) -> tuple[str | None, str | None]:
    """Return the declared judge name and model for one stored check.

    A deterministic check has no hash and no declaration, so both values stay
    None. A judged check whose declaration has left the snapshot keeps its
    hash and groups without a name rather than disappearing.
    """
    if config_hash is None:
        return None, None
    judge = judged.get(metric)
    if judge is None:
        return None, None
    return judge.name, judge.model


def _judge_breakdown(
    rows: list[CalibrationCheck],
) -> tuple[JudgeCalibration, ...]:
    """Group review rows by judge configuration and compute each rate."""
    groups: dict[
        tuple[str | None, str | None, str | None], list[CalibrationCheck]
    ] = {}
    for row in rows:
        key = (row.judge_config_hash, row.judge_name, row.judge_model)
        groups.setdefault(key, []).append(row)
    judges = []
    for (config_hash, name, model), items in groups.items():
        labelled = [item for item in items if item.label is not None]
        agreements = sum(1 for item in labelled if item.agreement)
        judges.append(
            JudgeCalibration(
                name=name,
                model=model,
                config_hash=config_hash,
                labelled=len(labelled),
                agreements=agreements,
                agreement_rate=proportion(agreements, len(labelled)),
                last_validated_at=max(
                    (
                        item.label.created_at
                        for item in labelled
                        if item.label is not None
                    ),
                    default=None,
                ),
            )
        )
    return tuple(
        sorted(
            judges,
            key=lambda judge: (
                judge.config_hash is None,
                judge.config_hash or "",
                judge.name or "",
                judge.model or "",
            ),
        )
    )
