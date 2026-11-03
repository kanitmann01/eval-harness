"""Run the public conformance suite against a dotted adapter path."""

from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from evalharness.adapters.manifest import MANIFEST, conformance_options
from evalharness.conformance.suite import (
    CANDIDATE,
    CONTEXT,
    JUDGE,
    METRIC,
    SINK,
    SOURCE,
    ConformanceContext,
    ConformanceReport,
    cases_for,
    run_case,
)
from evalharness.core.config import Settings
from evalharness.core.registry import resolve
from evalharness.core.repo_snapshot import RepoSnapshot

_IDENTITIES: dict[str, str] = {
    CANDIDATE: "conformance-candidate",
    JUDGE: "conformance-judge",
    METRIC: "conformance_metric",
    SOURCE: "source",
    SINK: "sink",
    CONTEXT: "context",
}
"""The identity the suite passes to each kind's factory."""


def _default_base() -> Path:
    return Settings.from_env().home / "conformance"


@contextmanager
def _scratch(base: Path | None) -> Iterator[Path]:
    root = Path(base) if base is not None else _default_base()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"case-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _default_options(kind: str, scratch: Path) -> dict[str, object]:
    if kind == SOURCE:
        return {
            "cases": [
                {
                    "case_id": "conformance-case-1",
                    "prompt": "What is 6 times 7?",
                    "expected": {"answer": "42"},
                },
                {
                    "case_id": "conformance-case-2",
                    "prompt": "What is 7 times 6?",
                    "expected": {"answer": "42"},
                },
            ]
        }
    if kind == SINK:
        return {"artifacts_dir": str(scratch / "artifacts")}
    if kind == CONTEXT:
        return {"max_chunks": 6, "max_bytes": 12000}
    return {}


def run_suite(
    adapter_path: str,
    kind: str,
    *,
    options: Mapping[str, object] | None = None,
    workdir: Path | None = None,
) -> ConformanceReport:
    """Run every clause for one adapter and return its report.

    Parameters
    ----------
    adapter_path:
        A registered name or a 'module:factory' dotted path.
    kind:
        One of 'candidate', 'judge', 'metric', 'source' or 'sink'.
    options:
        Adapter options the suite builds with; kind defaults are merged under
        these, so a caller can override a default without losing the rest.
    workdir:
        Base directory for scratch artifacts. Defaults to
        EVALH_HOME/conformance.
    """
    cases = cases_for(kind)
    factory = resolve(adapter_path)
    with _scratch(workdir) as scratch:
        merged = _default_options(kind, scratch)
        repos: dict[str, RepoSnapshot] = {}
        artifacts_root: Path | None = None
        setup = getattr(factory, "CONFORMANCE_SETUP", None)
        if setup is not None:
            setup_result = setup(scratch)
            if len(setup_result) == 3:
                setup_options, repos, artifacts_root = setup_result
            else:
                setup_options, repos = setup_result
            merged.update(setup_options)
        if options is not None:
            merged.update(dict(options))
        context = ConformanceContext(
            adapter_path=adapter_path,
            kind=kind,
            identity=_IDENTITIES[kind],
            options=merged,
            workdir=scratch,
            factory=factory,
            repos=repos,
            artifacts_root=artifacts_root,
        )
        results = [run_case(case, context) for case in cases]
    return ConformanceReport(
        adapter=adapter_path,
        kind=kind,
        passed=all(result.ok for result in results),
        results=results,
    )


def run_all(*, workdir: Path | None = None) -> list[ConformanceReport]:
    """Run the full suite for every first-party adapter in the manifest."""
    return [
        run_suite(
            entry.adapter,
            entry.kind,
            options=conformance_options(entry),
            workdir=workdir,
        )
        for entry in MANIFEST
    ]
