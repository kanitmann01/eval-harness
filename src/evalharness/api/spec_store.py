"""The specs directory: slug resolution, listing and atomic writes.

The spec file is the only source of truth. This module owns the one place the
console writes to disk: a name is checked as a slug and the resolved path is
checked to stay inside the configured root, so neither a traversal nor an
absolute path can escape.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from evalharness.errors import SpecError

SPEC_SUFFIX = ".yaml"
"""Every spec file ends with this suffix; the name is the stem."""

SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")
"""A spec name: lower-case alphanumerics, dot, dash and underscore."""


@dataclass(frozen=True, slots=True)
class SpecFile:
    """One file in the specs directory with its filesystem metadata."""

    name: str
    path: Path
    size: int
    modified: datetime


class SpecStore:
    """Read and write spec files under one root directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, name: str) -> Path:
        """Return the file for a spec name, refusing anything outside the root.

        The extension is always `.yaml`; `smoke` and `smoke.yaml` name the same
        file. The resolved path is checked against the resolved root before the
        name is accepted, so the directory boundary does not depend on the slug
        pattern alone.
        """
        stem = spec_stem(name)
        resolved = (self.root / f"{stem}{SPEC_SUFFIX}").resolve()
        inside = self.root.resolve() in resolved.parents
        if not SLUG_PATTERN.fullmatch(stem) or not inside:
            raise SpecError(
                "name",
                name,
                "a spec name must be a slug inside the specs directory",
                "use lower-case letters, digits, dot, dash or underscore, "
                "starting with a letter or digit",
            )
        return resolved

    def exists(self, name: str) -> bool:
        """Return whether a spec file with this name is present."""
        return self.path_for(name).is_file()

    def list(self) -> list[SpecFile]:
        """Return every spec file, ordered by name; an absent root is empty."""
        if not self.root.is_dir():
            return []
        paths = sorted(
            path
            for path in self.root.glob(f"*{SPEC_SUFFIX}")
            if path.is_file() and SLUG_PATTERN.fullmatch(path.stem)
        )
        return [_spec_file(path) for path in paths]

    def file(self, name: str) -> SpecFile:
        """Return one spec file and its metadata."""
        return _spec_file(self.path_for(name))

    def read(self, name: str) -> str:
        """Read one spec file as UTF-8 text."""
        return self.path_for(name).read_text(encoding="utf-8")

    def write(self, name: str, text: str) -> Path:
        """Write one spec file atomically and return its path.

        The text goes to a temporary file in the same directory, is flushed and
        fsynced, and only then replaces the target with `os.replace`, so a
        reader never sees a half-written spec.
        """
        path = self.path_for(name)
        self.root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=self.root,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path


def spec_stem(name: str) -> str:
    """Return the file stem for a spec name, dropping one `.yaml` suffix."""
    return name[: -len(SPEC_SUFFIX)] if name.endswith(SPEC_SUFFIX) else name


def copy_name(store: SpecStore, name: str) -> str:
    """Return the first free `<name>-copy`, `<name>-copy-2`, ... name."""
    base = f"{name}-copy"
    candidate = base
    index = 1
    while store.exists(candidate):
        index += 1
        candidate = f"{base}-{index}"
    return candidate


def _spec_file(path: Path) -> SpecFile:
    """Build the metadata record for one spec file."""
    stat = path.stat()
    return SpecFile(
        name=path.name[: -len(SPEC_SUFFIX)],
        path=path,
        size=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )
