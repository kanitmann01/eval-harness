"""Traversal-safe resolution of the artifact files a cell stored."""

from __future__ import annotations

from pathlib import Path

ARTIFACT_FILES = ("cell.json", "answer.txt")
"""The only file names a cell artifact link may serve."""


def artifact_file(directory: Path | None, name: str, root: Path) -> Path | None:
    """Return an allowlisted file inside the configured artifacts root.

    Parameter:
        directory: the directory the database stored for the cell.
        name: one of ARTIFACT_FILES; anything else is refused.
        root: the artifacts directory the serving process is configured with.
    """
    if directory is None or name not in ARTIFACT_FILES:
        return None
    target = (directory / name).resolve()
    inside = target.is_relative_to(root.resolve())
    return target if inside and target.is_file() else None
