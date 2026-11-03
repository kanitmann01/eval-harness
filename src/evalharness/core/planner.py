"""Expand a validated spec into deterministic cells."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from evalharness.core.context import RunContext
from evalharness.core.events import EventEmitter
from evalharness.core.models import Case, ContextChunk
from evalharness.core.repo_snapshot import RepoSnapshot
from evalharness.core.spec import ResolvedSpec
from evalharness.errors import SpecError

CELL_ID_LENGTH = 16
"""Hex characters kept from the cell id sha1."""


def cell_id_for(candidate: str, case_id: str, replica: int) -> str:
    """Return the stable cell id for a candidate, case and replica."""
    payload = f"{candidate}|{case_id}|{replica}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:CELL_ID_LENGTH]


@dataclass(frozen=True, slots=True)
class PlannedCell:
    """One planned unit of work: candidate x case x replica."""

    cell_id: str
    candidate: str
    case: Case
    replica: int
    variant: dict[str, object]


def context_digest(chunks: Iterable[ContextChunk]) -> str:
    """Return a stable digest of the selected context, ids and text included."""
    payload = json.dumps(
        [chunk.model_dump(mode="json") for chunk in chunks],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _variant(
    resolved: ResolvedSpec,
    candidate: str,
    case: Case,
    *,
    repos: Mapping[str, RepoSnapshot] | None,
    artifacts_root: Path | None,
    run_id: str,
) -> dict[str, object]:
    candidate_spec = resolved.candidates[candidate]
    name = resolved.candidate_contexts.get(candidate)
    selector = resolved.context_selectors.get(name) if name is not None else None
    if selector is not None:
        ctx = RunContext(
            run_id=run_id,
            seed=resolved.spec.seed,
            credential=None,
            events=EventEmitter(run_id),
            candidate=candidate,
            case_id=case.case_id,
            model=candidate_spec.model,
            repos=repos or {},
            artifacts_root=artifacts_root,
        )
        try:
            chunks = list(selector.select(case, ctx))
        except SpecError:
            raise
        except Exception as exc:  # selector adapters are external code
            raise SpecError(
                f"contexts.{name}.adapter",
                resolved.spec.contexts[name].adapter,  # type: ignore[union-attr]
                f"the context selector failed: {exc}",
                "fix the context options",
            ) from exc
    else:
        chunks = list(resolved.chunks[candidate])
    return {
        "prompt_variant": candidate_spec.options.get("prompt"),
        "prompt_override": resolved.prompt_overrides[candidate],
        "context": [chunk.model_dump(mode="json") for chunk in chunks],
        "context_name": name,
        "context_digest": context_digest(chunks),
        "context_truncated": False,
    }


def expand(
    resolved: ResolvedSpec,
    cases: Iterable[Case],
    *,
    repos: Mapping[str, RepoSnapshot] | None = None,
    artifacts_root: Path | None = None,
    run_id: str = "plan",
) -> list[PlannedCell]:
    """Expand candidates, cases and replicas into seeded, deterministic cells.

    A dynamic context selector runs once per candidate and case, before the
    cells are shuffled, so selection sees the case's own prompt and the run's
    prepared repositories rather than an executor-side afterthought.
    """
    materialized = list(cases)
    _unique_case_ids(materialized)
    cells: list[PlannedCell] = []
    for candidate in resolved.spec.candidates:
        for case in materialized:
            variant = _variant(
                resolved,
                candidate.name,
                case,
                repos=repos,
                artifacts_root=artifacts_root,
                run_id=run_id,
            )
            for replica in range(1, resolved.spec.replicas + 1):
                cells.append(
                    PlannedCell(
                        cell_id=cell_id_for(candidate.name, case.case_id, replica),
                        candidate=candidate.name,
                        case=case,
                        replica=replica,
                        variant=variant,
                    )
                )
    random.Random(resolved.spec.seed).shuffle(cells)
    return cells


def _unique_case_ids(cases: list[Case]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise SpecError(
                "source",
                case.case_id,
                "the source returned a duplicate case_id",
                "give every case a unique case_id",
            )
        seen.add(case.case_id)


def per_candidate_counts(cells: Iterable[PlannedCell]) -> dict[str, int]:
    """Count cells per candidate, preserving the order cells were seen."""
    counts: dict[str, int] = {}
    for cell in cells:
        counts[cell.candidate] = counts.get(cell.candidate, 0) + 1
    return counts
