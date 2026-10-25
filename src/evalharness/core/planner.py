"""Expand a validated spec into deterministic cells."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Iterable

from evalharness.core.models import Case
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


def _variant(resolved: ResolvedSpec, candidate: str) -> dict[str, object]:
    candidate_spec = resolved.candidates[candidate]
    return {
        "prompt_variant": candidate_spec.options.get("prompt"),
        "prompt_override": resolved.prompt_overrides[candidate],
        "context": [chunk.model_dump() for chunk in resolved.chunks[candidate]],
    }


def expand(resolved: ResolvedSpec, cases: Iterable[Case]) -> list[PlannedCell]:
    """Expand candidates, cases and replicas into seeded, deterministic cells."""
    materialized = list(cases)
    _unique_case_ids(materialized)
    cells: list[PlannedCell] = []
    variants = {
        candidate.name: _variant(resolved, candidate.name)
        for candidate in resolved.spec.candidates
    }
    for candidate in resolved.spec.candidates:
        for case in materialized:
            for replica in range(1, resolved.spec.replicas + 1):
                cells.append(
                    PlannedCell(
                        cell_id=cell_id_for(candidate.name, case.case_id, replica),
                        candidate=candidate.name,
                        case=case,
                        replica=replica,
                        variant=variants[candidate.name],
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
