"""One path rule for the artifacts tree: root, run id, cell id."""

from __future__ import annotations

from pathlib import Path


def run_artifact_dir(
    artifacts_root: Path, run_id: str, *, create: bool = False
) -> Path:
    """Return the run's artifact directory, optionally creating it.

    Args:
        artifacts_root: The root every artifact stays under.
        run_id: The run whose directory is wanted.
        create: Create the directory on disk when true.
    """
    directory = artifacts_root / run_id
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def cell_artifact_dir(
    artifacts_root: Path,
    run_id: str,
    cell_id: str,
    *,
    create: bool = False,
) -> Path:
    """Return the cell's artifact directory, optionally creating it.

    Args:
        artifacts_root: The root every artifact stays under.
        run_id: The run that owns the cell.
        cell_id: The cell whose directory is wanted.
        create: Create the directory on disk when true.
    """
    directory = run_artifact_dir(artifacts_root, run_id) / cell_id
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory
