"""Repository graph context selector: deterministic chunks from a snapshot.

The selector reads only the prepared snapshot a run already holds in
RunContext.repos, never the developer's working tree. It windows every
eligible text file, ranks windows by lexical overlap with the case prompt,
optionally folds in a file-level PageRank over import edges, then expands
around the seeds with adjacent windows and directly related files. Same case
and context always produce the same chunks.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Iterator, Mapping

from evalharness.core.context import RunContext
from evalharness.core.models import Case, ContextChunk, RepoSpec
from evalharness.core.repo_snapshot import RepoSnapshot, SnapshotFile, snapshot_repo
from evalharness.errors import SpecError

KNOWN_OPTIONS = frozenset(
    {
        "repo",
        "rank",
        "seed_chunks",
        "expand",
        "chunk_lines",
        "max_chunks",
        "max_bytes",
        "include_tests",
    }
)
"""Every option this selector understands; a typo is an error, not a default."""

RANKS = frozenset({"lexical", "pagerank"})
"""The ranking modes this build supports."""

DEFAULT_SEED_CHUNKS = 3
DEFAULT_EXPAND = 2
DEFAULT_CHUNK_LINES = 80
DEFAULT_MAX_CHUNKS = 8
DEFAULT_MAX_BYTES = 24_000

_PAGERANK_DAMPING = 0.85
_PAGERANK_ITERATIONS = 30

_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_PY_FROM = re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\b", re.MULTILINE)
_PY_IMPORT = re.compile(
    r"^\s*import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)", re.MULTILINE
)
_QUOTED_IMPORT = re.compile(
    r"""(?:\bfrom|\brequire\s*\(|\bimport\s*\(?)\s*["']([^"'\n]+)["']"""
)
_C_INCLUDE = re.compile(r"""#\s*include\s*[<"]([^">]+)[">]""")
_INCLUDE_WORD = re.compile(r"""\binclude\s+["']([^"']+)["']""")

_EXTENSIONS = (
    "",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".yaml",
    ".yml",
    ".h",
    ".hpp",
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".kt",
    ".md",
    ".txt",
)
"""Suffixes a bare import or include mention may resolve to."""

_INDEX_NAMES = ("__init__.py", "index.js", "index.ts", "index.tsx", "mod.rs")
"""Directory entry files a package import may resolve to."""


@dataclass(frozen=True, slots=True)
class _Window:
    """One line window before scoring; ordinal is its position in the file."""

    chunk_id: str
    path: str
    ordinal: int
    start_line: int
    end_line: int
    blob_sha256: str
    text: str
    score: float = 0.0


class RepoGraphSelector:
    """Select line windows from one prepared repository snapshot per case."""

    def __init__(
        self,
        identity: str,
        *,
        repo: str,
        rank: str,
        seed_chunks: int,
        expand: int,
        chunk_lines: int,
        max_chunks: int,
        max_bytes: int,
        include_tests: bool,
    ) -> None:
        self.name = identity
        self._repo = repo
        self._rank = rank
        self._seed_chunks = seed_chunks
        self._expand = expand
        self._chunk_lines = chunk_lines
        self._max_chunks = max_chunks
        self._max_bytes = max_bytes
        self._include_tests = include_tests

    def select(self, case: Case, ctx: RunContext) -> list[ContextChunk]:
        """Return ranked chunks for one case from the declared snapshot."""
        snapshot = ctx.repos.get(self._repo)
        if snapshot is None:
            raise SpecError(
                f"contexts.{self.name}.options.repo",
                self._repo,
                "the repository was not snapshotted for this selection",
                "run through 'evalh run' so the spec's repositories are prepared",
            )
        files = self._read_snapshot(snapshot)
        chunks_by_path = {
            entry.path: _windows(self._repo, entry, text, self._chunk_lines)
            for entry, text in files
        }
        chunks_by_path = {
            path: windows for path, windows in chunks_by_path.items() if windows
        }
        windows = [
            window for path in sorted(chunks_by_path) for window in chunks_by_path[path]
        ]
        texts = {entry.path: text for entry, text in files}
        graph = _file_graph(texts)
        ranks = _pagerank(sorted(texts), graph) if self._rank == "pagerank" else {}
        prompt_tokens = _tokens(case.prompt)
        rescored = [
            replace(
                window,
                score=_overlap(prompt_tokens, window.text)
                + 0.5 * ranks.get(window.path, 0.0),
            )
            for window in windows
        ]
        chunks_by_path = {}
        for window in rescored:
            chunks_by_path.setdefault(window.path, []).append(window)
        related = _related_files(sorted(texts), graph)
        selected = _seed(rescored, self._seed_chunks, self._max_chunks)
        selected = _expand(
            selected,
            chunks_by_path,
            related,
            self._expand,
            self._max_chunks,
        )
        return self._emit(selected, chunks_by_path, related)

    def _read_snapshot(
        self, snapshot: RepoSnapshot
    ) -> list[tuple[SnapshotFile, str]]:
        """Read every eligible text file from the snapshot, sorted by path."""
        readable: list[tuple[SnapshotFile, str]] = []
        for entry in sorted(snapshot.files, key=lambda item: item.path):
            if not self._include_tests and _is_test_path(entry.path):
                continue
            text = _read_file(snapshot, entry.path)
            if text is None:
                continue
            readable.append((entry, text))
        return readable

    def _emit(
        self,
        selected: list[_Window],
        chunks_by_path: Mapping[str, list[_Window]],
        related: Mapping[str, set[str]],
    ) -> list[ContextChunk]:
        """Apply the byte cap and turn the chosen windows into core records."""
        selected_ids = {window.chunk_id for window in selected}
        chunks: list[ContextChunk] = []
        used = 0
        for window in selected:
            size = len(window.text.encode("utf-8"))
            if used + size > self._max_bytes:
                continue
            used += size
            nodes = sorted(
                {
                    neighbour.chunk_id
                    for neighbour in _neighbours(window, chunks_by_path, related)
                }
                & selected_ids
            )
            chunks.append(
                ContextChunk(
                    chunk_id=window.chunk_id,
                    text=window.text,
                    path=window.path,
                    start_line=window.start_line,
                    end_line=window.end_line,
                    blob_sha256=window.blob_sha256,
                    score=window.score,
                    graph_nodes=nodes,
                )
            )
        return chunks


def build(identity: str, options: Mapping[str, Any]) -> RepoGraphSelector:
    """Build a repo graph selector from the documented options."""
    unknown = sorted(set(options) - KNOWN_OPTIONS)
    if unknown:
        raise SpecError(
            f"contexts.{identity}.options.{unknown[0]}",
            unknown[0],
            "unknown context selector option",
            f"use one of: {', '.join(sorted(KNOWN_OPTIONS))}",
        )
    repo = options.get("repo")
    if not isinstance(repo, str) or not repo:
        raise SpecError(
            f"contexts.{identity}.options.repo",
            repo,
            "the repo_graph selector needs a non-empty repository name",
            "name a repository declared under repos:",
        )
    rank = options.get("rank", "lexical")
    if not isinstance(rank, str) or rank not in RANKS:
        raise SpecError(
            f"contexts.{identity}.options.rank",
            rank,
            "rank must be 'lexical' or 'pagerank'",
            "set rank to lexical or pagerank",
        )
    include_tests = options.get("include_tests", False)
    if not isinstance(include_tests, bool):
        raise SpecError(
            f"contexts.{identity}.options.include_tests",
            include_tests,
            "include_tests must be true or false",
            "set include_tests to true or false",
        )
    return RepoGraphSelector(
        identity,
        repo=repo,
        rank=rank,
        seed_chunks=_option_int(
            identity, options, "seed_chunks", DEFAULT_SEED_CHUNKS, 1, 10
        ),
        expand=_option_int(identity, options, "expand", DEFAULT_EXPAND, 0, 10),
        chunk_lines=_option_int(
            identity, options, "chunk_lines", DEFAULT_CHUNK_LINES, 1, 400
        ),
        max_chunks=_option_int(
            identity, options, "max_chunks", DEFAULT_MAX_CHUNKS, 1, 50
        ),
        max_bytes=_option_int(
            identity, options, "max_bytes", DEFAULT_MAX_BYTES, 1, 2_000_000
        ),
        include_tests=include_tests,
    )


def conformance_setup(
    scratch: Path,
) -> tuple[dict[str, object], dict[str, RepoSnapshot]]:
    """Create a tiny repository snapshot and the options that read it."""
    root = scratch / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "policy.py").write_text(
        "REFUND_WINDOW_DAYS = 30\n\n"
        "def refund_window() -> int:\n"
        "    return REFUND_WINDOW_DAYS\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "Refund policy: a refund is available within 30 days.\n",
        encoding="utf-8",
    )
    snapshot = snapshot_repo(
        "conformance",
        RepoSpec(root="repo"),
        base_dir=scratch,
        home=scratch / "home",
    )
    return {"repo": "conformance"}, {"conformance": snapshot}


# The conformance runner reads this attribute from the resolved factory to give
# the selector the snapshot a real launch would have prepared.
build.CONFORMANCE_SETUP = conformance_setup


def _option_int(
    identity: str,
    options: Mapping[str, Any],
    name: str,
    default: int,
    low: int,
    high: int,
) -> int:
    """Read a bounded integer option or raise an actionable SpecError."""
    value = options.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise SpecError(
            f"contexts.{identity}.options.{name}",
            value,
            f"the option must be an integer between {low} and {high}",
            f"set {name} to an integer between {low} and {high}",
        )
    return value


def _read_file(snapshot: RepoSnapshot, relative: str) -> str | None:
    """Read one snapshot file as UTF-8, or None when unsafe or unreadable."""
    target = _inside(snapshot, relative)
    if target is None:
        return None
    try:
        payload = target.read_bytes()
    except OSError:
        return None
    if b"\x00" in payload[:8192]:
        return None
    return payload.decode("utf-8", errors="replace")


def _inside(snapshot: RepoSnapshot, relative: str) -> Path | None:
    """Resolve a manifest path and refuse anything that escapes the tree."""
    if not relative or "\x00" in relative:
        return None
    normalized = relative.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (
        normalized.startswith("/")
        or normalized.startswith("~")
        or ".." in parts
        or PureWindowsPath(relative).is_absolute()
        or PureWindowsPath(relative).drive
    ):
        return None
    target = (snapshot.tree / normalized).resolve()
    try:
        target.relative_to(snapshot.tree.resolve())
    except ValueError:
        return None
    return target


def _is_test_path(path: str) -> bool:
    """Return whether a path is test material excluded by default."""
    parts = path.split("/")
    if "tests" in parts[:-1]:
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def _windows(
    repo: str,
    entry: SnapshotFile,
    text: str,
    chunk_lines: int,
) -> list[_Window]:
    """Split one file into non-overlapping line windows."""
    lines = text.splitlines()
    windows: list[_Window] = []
    for offset in range(0, len(lines), chunk_lines):
        start = offset + 1
        end = min(offset + chunk_lines, len(lines))
        windows.append(
            _Window(
                chunk_id=f"repo:{repo}/{entry.path}#L{start}-L{end}",
                path=entry.path,
                ordinal=len(windows),
                start_line=start,
                end_line=end,
                blob_sha256=entry.sha256,
                text="\n".join(lines[offset:end]),
            )
        )
    return windows


def _tokens(text: str) -> set[str]:
    """Return the distinct lowercase word tokens of a string."""
    return {token.lower() for token in _TOKEN.findall(text) if len(token) > 1}


def _overlap(prompt_tokens: set[str], text: str) -> float:
    """Return the fraction of prompt tokens present in a window."""
    if not prompt_tokens:
        return 0.0
    return len(prompt_tokens & _tokens(text)) / len(prompt_tokens)


def _file_graph(texts: Mapping[str, str]) -> dict[str, set[str]]:
    """Build the directed file graph from import, require and include mentions."""
    paths = sorted(texts)
    lookup = {path: path for path in paths}
    suffixes = _suffix_index(paths)
    graph: dict[str, set[str]] = {path: set() for path in paths}
    for path in paths:
        for raw, python in _imports(texts[path]):
            target = _resolve_import(raw, path, python, lookup, suffixes)
            if target is not None and target != path:
                graph[path].add(target)
    return graph


def _suffix_index(paths: Iterable[str]) -> dict[str, str]:
    """Map every path suffix to one deterministic repository path."""
    index: dict[str, str] = {}
    for path in sorted(paths):
        parts = path.split("/")
        for position in range(len(parts)):
            index.setdefault("/".join(parts[position:]), path)
    return index


def _imports(text: str) -> Iterator[tuple[str, bool]]:
    """Yield (mention, is_python) pairs from every supported import shape."""
    for match in _PY_FROM.finditer(text):
        yield match.group(1), True
    for match in _PY_IMPORT.finditer(text):
        for part in match.group(1).split(","):
            yield part.strip(), True
    for match in _QUOTED_IMPORT.finditer(text):
        yield match.group(1), False
    for match in _C_INCLUDE.finditer(text):
        yield match.group(1), False
    for match in _INCLUDE_WORD.finditer(text):
        yield match.group(1), False


def _resolve_import(
    raw: str,
    importer: str,
    python: bool,
    lookup: Mapping[str, str],
    suffixes: Mapping[str, str],
) -> str | None:
    """Resolve one mention to a repository path, or None when it is external."""
    mention = raw.strip()
    if not mention:
        return None
    if python:
        base = mention.replace(".", "/")
    else:
        base = mention
        if mention.startswith("."):
            base = posixpath.join(posixpath.dirname(importer), mention)
        base = base.lstrip("/")
    for candidate in _candidates(posixpath.normpath(base)):
        resolved = lookup.get(candidate) or suffixes.get(candidate)
        if resolved is not None:
            return resolved
    return None


def _candidates(base: str) -> Iterator[str]:
    """Yield the file names a bare mention may stand for."""
    for extension in _EXTENSIONS:
        yield base + extension
    stripped = base.rstrip("/")
    for name in _INDEX_NAMES:
        yield f"{stripped}/{name}"


def _pagerank(paths: Iterable[str], graph: Mapping[str, set[str]]) -> dict[str, float]:
    """Return a deterministic file-level PageRank over the import graph."""
    nodes = sorted(paths)
    total = len(nodes)
    if total == 0:
        return {}
    rank = {path: 1.0 / total for path in nodes}
    outgoing = {path: sorted(graph.get(path, set())) for path in nodes}
    dangling = [path for path in nodes if not outgoing[path]]
    for _ in range(_PAGERANK_ITERATIONS):
        share = 0.0
        for path in dangling:
            share += rank[path]
        updated = {
            path: (1.0 - _PAGERANK_DAMPING) / total
            + _PAGERANK_DAMPING * share / total
            for path in nodes
        }
        for path in nodes:
            targets = outgoing[path]
            if not targets:
                continue
            amount = _PAGERANK_DAMPING * rank[path] / len(targets)
            for target in targets:
                updated[target] += amount
        rank = updated
    return rank


def _related_files(
    paths: Iterable[str], graph: Mapping[str, set[str]]
) -> dict[str, set[str]]:
    """Return sibling and import-adjacent files for every path."""
    related: dict[str, set[str]] = {path: set() for path in paths}
    groups: dict[str, list[str]] = {}
    for path in sorted(related):
        groups.setdefault(posixpath.dirname(path), []).append(path)
    for group in groups.values():
        if len(group) < 2:
            continue
        for path in group:
            related[path].update(item for item in group if item != path)
    for source, targets in graph.items():
        for target in targets:
            related[source].add(target)
            related[target].add(source)
    return related


def _seed(
    windows: list[_Window], seed_chunks: int, max_chunks: int
) -> list[_Window]:
    """Pick the highest-scoring windows, deterministic on a score tie."""
    ordered = sorted(windows, key=lambda window: (-window.score, window.chunk_id))
    return ordered[: min(seed_chunks, max_chunks)]


def _expand(
    selected: list[_Window],
    chunks_by_path: Mapping[str, list[_Window]],
    related: Mapping[str, set[str]],
    hops: int,
    max_chunks: int,
) -> list[_Window]:
    """Add adjacent windows and related-file windows for a number of hops."""
    seen = {window.chunk_id for window in selected}
    frontier = list(selected)
    for _ in range(hops):
        candidates: dict[str, _Window] = {}
        for window in frontier:
            for neighbour in _neighbours(window, chunks_by_path, related):
                if neighbour.chunk_id not in seen:
                    candidates.setdefault(neighbour.chunk_id, neighbour)
        ordered = sorted(
            candidates.values(), key=lambda item: (-item.score, item.chunk_id)
        )
        frontier = []
        for window in ordered:
            if len(selected) >= max_chunks:
                break
            selected.append(window)
            seen.add(window.chunk_id)
            frontier.append(window)
        if not frontier:
            break
    return selected


def _neighbours(
    window: _Window,
    chunks_by_path: Mapping[str, list[_Window]],
    related: Mapping[str, set[str]],
) -> list[_Window]:
    """Return adjacent windows plus the best window of each related file."""
    neighbours: list[_Window] = []
    same = chunks_by_path.get(window.path, [])
    if window.ordinal > 0:
        neighbours.append(same[window.ordinal - 1])
    if window.ordinal + 1 < len(same):
        neighbours.append(same[window.ordinal + 1])
    for path in sorted(related.get(window.path, set())):
        candidates = chunks_by_path.get(path)
        if candidates:
            neighbours.append(
                min(candidates, key=lambda item: (-item.score, item.chunk_id))
            )
    return neighbours
