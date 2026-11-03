"""Artifact graph context selector: answer chunks from a closed run.

The selector reads one run directory under `RunContext.artifacts_root` and
never the database or a live repository. Nodes are the chunk ids each recorded
answer cited in `variant.context`; edges are co-citation inside one answer.
One chunk is emitted per non-empty answer: its text is the answer, its score is
lexical overlap with the case prompt plus a co-citation boost shared with the
other answers in the same run, and its graph nodes are the cited chunk ids.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from evalharness.core.context import RunContext
from evalharness.core.models import Case, ContextChunk
from evalharness.errors import SpecError

KNOWN_OPTIONS = frozenset({"run", "max_chunks", "max_bytes", "exclude_candidate"})
"""Every option this selector understands; a typo is an error, not a default."""

DEFAULT_MAX_CHUNKS = 6
MAX_CHUNKS_LIMIT = 50
DEFAULT_MAX_BYTES = 12_000
MAX_BYTES_LIMIT = 2_000_000

_COCITATION_WEIGHT = 0.5
_WORD = re.compile(r"[a-z0-9]+")

# Characters that would let the run option escape the artifacts root. A run id
# is one directory name, not a path: the selector must read exactly one run.
_PATH_CHARACTERS = ("/", "\\", ":")


@dataclass(frozen=True, slots=True)
class _Answer:
    """One recorded answer with the chunk ids it cited."""

    cell_id: str
    candidate: str
    text: str
    cited: tuple[str, ...]


class ArtifactGraphSelector:
    """Select one chunk per answer from a closed run's recorded artifacts."""

    def __init__(
        self,
        identity: str,
        *,
        run: str,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        exclude_candidate: str | None = None,
    ) -> None:
        self.name = identity
        self._run = run
        self._max_chunks = max_chunks
        self._max_bytes = max_bytes
        self._exclude_candidate = exclude_candidate

    def select(self, case: Case, ctx: RunContext) -> list[ContextChunk]:
        """Return this run's answer chunks for one case, deterministically."""
        run_field = f"contexts.{self.name}.options.run"
        if ctx.artifacts_root is None:
            raise SpecError(
                f"contexts.{self.name}.artifacts",
                self._run,
                "the run context has no artifacts root",
                "set EVALH_ARTIFACTS_DIR to the artifacts directory",
            )
        run_dir = Path(ctx.artifacts_root) / self._run
        run_path = run_dir / "run.json"
        if not run_path.is_file():
            raise SpecError(
                run_field,
                self._run,
                "the run has no artifacts",
                "name an existing run",
            )
        run_payload = _read_json(run_path, run_field, self._run, "the run artifact")
        if run_payload.get("status") == "running":
            raise SpecError(
                run_field,
                self._run,
                "the run is not closed",
                "wait for the run to close or name a closed run",
            )
        recorded = run_payload.get("run_id")
        run_id = recorded if isinstance(recorded, str) and recorded else self._run

        answers = self._answers(run_dir, run_field)
        citation_counts = Counter(
            chunk_id for answer in answers for chunk_id in answer.cited
        )
        other_answers = max(1, len(answers) - 1)
        scored = [
            (
                _score(answer, case.prompt, citation_counts, other_answers),
                answer,
            )
            for answer in answers
        ]
        scored.sort(key=lambda item: (-item[0], _chunk_id(run_id, item[1].cell_id)))

        selected: list[ContextChunk] = []
        seen: set[str] = set()
        used_bytes = 0
        for score, answer in scored:
            chunk_id = _chunk_id(run_id, answer.cell_id)
            if chunk_id in seen:
                continue
            if len(selected) >= self._max_chunks:
                break
            size = len(answer.text.encode("utf-8"))
            if used_bytes + size > self._max_bytes:
                continue
            seen.add(chunk_id)
            used_bytes += size
            selected.append(
                ContextChunk(
                    chunk_id=chunk_id,
                    text=answer.text,
                    score=score,
                    graph_nodes=list(answer.cited),
                )
            )
        return selected

    def _answers(self, run_dir: Path, field: str) -> list[_Answer]:
        answers: list[_Answer] = []
        for entry in sorted(run_dir.iterdir(), key=lambda item: item.name):
            if not entry.is_dir():
                continue
            payload = _read_json(
                entry / "cell.json", field, entry.name, "the cell artifact"
            )
            cell = payload.get("cell")
            if not isinstance(cell, dict):
                raise SpecError(
                    field,
                    entry.name,
                    "the cell artifact has no cell object",
                    "repair the artifact or rerun the run",
                )
            candidate = cell.get("candidate")
            if (
                self._exclude_candidate is not None
                and candidate == self._exclude_candidate
            ):
                continue
            text = _answer_text(cell, entry, field)
            if not text.strip():
                continue
            cell_id = cell.get("cell_id")
            if not isinstance(cell_id, str) or not cell_id:
                cell_id = entry.name
            answers.append(
                _Answer(
                    cell_id=cell_id,
                    candidate=candidate if isinstance(candidate, str) else "",
                    text=text,
                    cited=_cited_ids(cell.get("variant")),
                )
            )
        return answers


def build(identity: str, options: Mapping[str, Any]) -> ArtifactGraphSelector:
    """Build a selector for the closed run the `run` option names."""
    unknown = sorted(set(options) - KNOWN_OPTIONS)
    if unknown:
        raise SpecError(
            f"contexts.{identity}.options.{unknown[0]}",
            unknown[0],
            "unknown option",
            "use run, max_chunks, max_bytes or exclude_candidate",
        )
    if "run" not in options:
        raise SpecError(
            f"contexts.{identity}.options.run",
            None,
            "the run option is required",
            "set run to a run id under the artifacts directory",
        )
    run = _run_id(identity, options["run"])
    exclude = options.get("exclude_candidate")
    if exclude is not None and not isinstance(exclude, str):
        raise SpecError(
            f"contexts.{identity}.options.exclude_candidate",
            exclude,
            "exclude_candidate must be a string",
            "name a candidate or remove the option",
        )
    return ArtifactGraphSelector(
        identity,
        run=run,
        max_chunks=_option_int(
            identity, options, "max_chunks", DEFAULT_MAX_CHUNKS, MAX_CHUNKS_LIMIT
        ),
        max_bytes=_option_int(
            identity, options, "max_bytes", DEFAULT_MAX_BYTES, MAX_BYTES_LIMIT
        ),
        exclude_candidate=exclude or None,
    )


def conformance_setup(
    scratch: Path,
) -> tuple[dict[str, object], dict[str, object], Path]:
    """Create a closed run with two answers for the public conformance suite."""
    artifacts = scratch / "artifacts"
    run_id = "conformance-run"
    directory = artifacts / run_id
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(
        json.dumps({"run_id": run_id, "status": "ok"}), encoding="utf-8"
    )
    cells = (
        ("alpha", "refund policy within 30 days", ("chunk-1", "chunk-2")),
        ("beta", "shipping policy", ("chunk-2",)),
    )
    for cell_id, answer, cited in cells:
        cell_dir = directory / cell_id
        cell_dir.mkdir()
        cell = {
            "cell_id": cell_id,
            "candidate": "conformance",
            "case_id": "conformance-case",
            "replica": 1,
            "answer": answer,
            "output": {},
            "variant": {"context": [{"chunk_id": item} for item in cited]},
        }
        (cell_dir / "cell.json").write_text(
            json.dumps({"cell": cell, "checks": []}), encoding="utf-8"
        )
        (cell_dir / "answer.txt").write_text(answer, encoding="utf-8")
    return {"run": run_id}, {}, artifacts


# The conformance runner reads this attribute from the resolved factory to give
# a context selector the artifacts a real launch would have produced.
build.CONFORMANCE_SETUP = conformance_setup


def _run_id(identity: str, value: Any) -> str:
    field = f"contexts.{identity}.options.run"
    if not isinstance(value, str) or not value.strip():
        raise SpecError(
            field,
            value,
            "the run must be a non-empty run id",
            "set run to a run id under the artifacts directory",
        )
    if value in {".", ".."} or ".." in value or any(
        character in value for character in _PATH_CHARACTERS
    ):
        raise SpecError(
            field,
            value,
            "the run must be a bare run id, not a path",
            "name the run directory exactly, without separators, dots or a drive",
        )
    return value


def _option_int(
    identity: str,
    options: Mapping[str, Any],
    name: str,
    default: int,
    high: int,
) -> int:
    raw = options.get(name, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= high:
        raise SpecError(
            f"contexts.{identity}.options.{name}",
            raw,
            f"{name} must be an integer from 1 to {high}",
            f"set {name} to an integer between 1 and {high}",
        )
    return raw


def _read_json(path: Path, field: str, value: str, what: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecError(
            field,
            value,
            f"{what} is corrupt: {exc}",
            "repair the artifact or rerun the run",
        ) from exc
    if not isinstance(payload, dict):
        raise SpecError(
            field,
            value,
            f"{what} is not a JSON object",
            "repair the artifact or rerun the run",
        )
    return payload


def _answer_text(cell: Mapping[str, Any], entry: Path, field: str) -> str:
    text = cell.get("answer", "")
    if not isinstance(text, str):
        raise SpecError(
            field,
            entry.name,
            "the cell answer is not a string",
            "repair the artifact or rerun the run",
        )
    if text.strip():
        return text
    answer_path = entry / "answer.txt"
    if not answer_path.is_file():
        return text
    try:
        return answer_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(
            field,
            entry.name,
            f"the cell answer is unreadable: {exc}",
            "repair the artifact or rerun the run",
        ) from exc


def _cited_ids(variant: Any) -> tuple[str, ...]:
    if not isinstance(variant, dict):
        return ()
    entries = variant.get("context")
    if not isinstance(entries, list):
        return ()
    found: set[str] = set()
    for item in entries:
        if isinstance(item, dict):
            chunk_id = item.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id:
                found.add(chunk_id)
    return tuple(sorted(found))


def _score(
    answer: _Answer,
    prompt: str,
    citation_counts: Counter[str],
    other_answers: int,
) -> float:
    lexical = _lexical(answer.text, prompt)
    boost = 0.0
    for chunk_id in answer.cited:
        shared = citation_counts[chunk_id] - 1
        if shared > 0:
            boost += min(1.0, shared / other_answers)
    if answer.cited:
        boost /= len(answer.cited)
    return lexical + _COCITATION_WEIGHT * boost


def _lexical(text: str, prompt: str) -> float:
    prompt_terms = set(_WORD.findall(prompt.lower()))
    if not prompt_terms:
        return 0.0
    answer_terms = set(_WORD.findall(text.lower()))
    return len(prompt_terms & answer_terms) / len(prompt_terms)


def _chunk_id(run_id: str, cell_id: str) -> str:
    return f"run:{run_id}/{cell_id}#answer"