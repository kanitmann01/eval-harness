"""JSON shapes for the calibration report."""

from __future__ import annotations

from evalharness.api.templating import iso_or_none
from typing import Any

from evalharness.core.calibration import (
    CalibrationCheck,
    CalibrationReport,
    JudgeCalibration,
)


def calibration_data(report: CalibrationReport) -> dict[str, Any]:
    """Render one calibration report as JSON-safe data.

    A missing rate, interval or stamp becomes null rather than zero, matching
    the console's existing JSON convention.
    """
    return {
        "run_id": report.run_id,
        "labelled": report.labelled,
        "unlabelled": report.unlabelled,
        "agreements": report.agreements,
        "agreement_rate": report.agreement_rate,
        "ci_low": report.interval_low,
        "ci_high": report.interval_high,
        "interval": {"low": report.interval_low, "high": report.interval_high},
        "last_validated_at": iso_or_none(report.last_validated_at),
        "judges": [_judge_data(judge) for judge in report.judges],
        "disagreements": [
            _check_data(row) for row in report.disagreements
        ],
        "checks": [_check_data(row) for row in report.checks],
    }


def _judge_data(judge: JudgeCalibration) -> dict[str, Any]:
    """Render one judge configuration's agreement summary."""
    return {
        "name": judge.name,
        "model": judge.model,
        "config_hash": judge.config_hash,
        "labelled": judge.labelled,
        "agreements": judge.agreements,
        "agreement_rate": judge.agreement_rate,
        "last_validated_at": iso_or_none(judge.last_validated_at),
    }


def _check_data(row: CalibrationCheck) -> dict[str, Any]:
    """Render one stored check with its judge and human verdicts."""
    return {
        "run_id": row.run_id,
        "cell_id": row.cell_id,
        "cell_url": row.cell_url,
        "case_id": row.case_id,
        "candidate": row.candidate,
        "metric": row.metric,
        "judge_name": row.judge_name,
        "judge_model": row.judge_model,
        "judge_config_hash": row.judge_config_hash,
        "judge_passed": row.passed,
        "judge_reason": row.reason,
        "human_passed": row.human_passed,
        "human_note": row.human_note,
        "operator": row.operator,
        "agreement": row.agreement,
        "labelled_at": iso_or_none(
            None if row.label is None else row.label.created_at
        ),
    }


