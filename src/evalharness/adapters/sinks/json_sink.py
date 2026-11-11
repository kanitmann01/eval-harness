"""JSON artifact sink: files on disk, only paths in the database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from evalharness.core.artifacts import run_artifact_dir
from evalharness.core.models import CellRecord, Check, RunRecord
from evalharness.errors import SpecError


class JsonSink:
    """Write one directory per run and one directory per cell."""

    def __init__(self, artifacts_dir: Path) -> None:
        self._root = artifacts_dir

    def open_run(self, run: RunRecord) -> None:
        """Create the run's artifact directory."""
        run_artifact_dir(self._root, run.run_id, create=True)

    def record_cell(self, cell: CellRecord, checks: list[Check]) -> None:
        """Write the cell, its checks and its answer under its recorded path."""
        if not cell.artifact_dir:
            raise SpecError(
                "cell.artifact_dir",
                cell.artifact_dir,
                "the sink cannot record cell "
                f"{cell.cell_id!r} without an artifact directory",
                "set CellRecord.artifact_dir with "
                "evalharness.core.artifacts.cell_artifact_dir before calling the sink",
            )
        directory = Path(cell.artifact_dir)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "cell": cell.model_dump(mode="json"),
            "checks": [check.model_dump(mode="json") for check in checks],
        }
        (directory / "cell.json").write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )
        (directory / "answer.txt").write_text(cell.answer, encoding="utf-8")

    def close_run(self, run: RunRecord) -> None:
        """Write the final run record into the run directory."""
        directory = run_artifact_dir(self._root, run.run_id, create=True)
        (directory / "run.json").write_text(
            json.dumps(run.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )


def build(identity: str, options: Mapping[str, Any]) -> JsonSink:
    """Build a JSON sink rooted at the artifacts_dir option."""
    return JsonSink(Path(str(options.get("artifacts_dir", "."))))
