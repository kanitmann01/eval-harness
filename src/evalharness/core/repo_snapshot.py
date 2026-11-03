"""Local repository snapshots: content-addressed trees a source can read.

A snapshot is taken before the first cell and stored under
`$EVALH_HOME/repos/<digest>`. The digest covers every included file's blob
hash and path, so the same tree reuses the same directory. Only relative
paths, blob hashes and sizes enter the manifest; the database never holds
repository bytes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from evalharness.core.models import Case, ContextSpec, RepoSpec, Spec
from evalharness.core.repo_fetch import fetch_repo
from evalharness.errors import SpecError

DEFAULT_EXCLUDES: tuple[str, ...] = (
    "**/.env*",
    "**/*.pem",
    "**/*.key",
    "**/id_rsa*",
)
"""Secret-bearing paths excluded even when the spec does not list them."""

SNAPSHOT_MANIFEST = "manifest.json"
"""The manifest filename inside every snapshot directory."""

SNAPSHOT_TREE = "tree"
"""The copied file tree inside every snapshot directory."""


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    """One included file: its snapshot-relative path, hash and size."""

    path: str
    sha256: str
    bytes: int
    mode: int


@dataclass(frozen=True, slots=True)
class RepoSnapshot:
    """A prepared repository tree plus the facts a receipt records."""

    name: str
    root: str
    url: str | None
    ref: str | None
    commit: str | None
    digest: str
    files: tuple[SnapshotFile, ...]
    bytes: int
    skipped_binary: int
    dirty: bool | None
    tree: Path
    skipped: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def metadata(self) -> dict[str, Any]:
        """Return the receipt row for this repository, never a host path."""
        return {
            "name": self.name,
            "root": self.root,
            "url": self.url,
            "ref": self.ref,
            "commit": self.commit,
            "digest": self.digest,
            "files": len(self.files),
            "bytes": self.bytes,
            "skipped_binary": self.skipped_binary,
            "dirty": self.dirty,
        }


def prepare_repos(
    spec: Spec,
    *,
    base_dir: Path,
    home: Path,
    credentials: Mapping[str, str] | None = None,
) -> dict[str, RepoSnapshot]:
    """Snapshot every declared repository, or fail before any cell runs.

    `base_dir` resolves a local root the same way the spec file does. A remote
    URL is fetched shallowly into the cache first; `credentials` maps a
    declared credential name to its secret and is never written down.
    """
    snapshots: dict[str, RepoSnapshot] = {}
    for name, repo in spec.repos.items():
        if repo.url is not None:
            if repo.credential is None and spec.source.credential is not None:
                repo = repo.model_copy(
                    update={"credential": spec.source.credential}
                )
            checkout = fetch_repo(
                name,
                repo,
                home=home,
                credentials=credentials,
            )
            snapshots[name] = _snapshot_tree(
                name,
                repo,
                checkout,
                home,
                url=repo.url,
                recorded_root="",
            )
            continue
        snapshots[name] = snapshot_repo(
            name,
            repo,
            base_dir=base_dir,
            home=home,
        )
    return snapshots


def snapshot_repo(
    name: str,
    repo: RepoSpec,
    *,
    base_dir: Path,
    home: Path,
) -> RepoSnapshot:
    """Read one local root, copy its included files and return the snapshot."""
    root = (base_dir / repo.root).resolve()
    if not root.is_dir():
        raise SpecError(
            f"repos.{name}.root",
            repo.root,
            "the repository root does not exist or is not a directory",
            "fix the path or create the directory before loading the spec",
        )
    return _snapshot_tree(
        name,
        repo,
        root,
        home,
        url=None,
        recorded_root=repo.root,
    )


def _snapshot_tree(
    name: str,
    repo: RepoSpec,
    root: Path,
    home: Path,
    *,
    url: str | None,
    recorded_root: str,
) -> RepoSnapshot:
    """Copy one checkout into the content-addressed snapshot store."""
    files, skipped, total = _collect_files(root, repo, name)
    digest = _digest(files)
    tree = _store_tree(home, digest, root, files, skipped)
    commit, dirty = _git_metadata(root)
    return RepoSnapshot(
        name=name,
        root=recorded_root,
        url=url,
        ref=repo.ref,
        commit=commit,
        digest=digest,
        files=tuple(files),
        bytes=total,
        skipped_binary=sum(1 for item in skipped if item["reason"] == "binary"),
        dirty=dirty,
        tree=tree,
        skipped=tuple(skipped),
    )


def source_snapshot(
    adapter: str,
    repos: Mapping[str, RepoSnapshot],
    cases: Sequence[Case],
    contexts: Mapping[str, list[Any] | ContextSpec] | None = None,
) -> dict[str, Any]:
    """Build the JSON-safe provenance snapshot stored on the run."""
    recorded_cases = [
        {
            "case_id": case.case_id,
            "prompt_sha256": hashlib.sha256(case.prompt.encode("utf-8")).hexdigest(),
            "tier": case.tier,
        }
        for case in cases
    ]
    recorded_contexts = []
    for name, entry in sorted((contexts or {}).items()):
        if isinstance(entry, list):
            recorded_contexts.append(
                {
                    "name": name,
                    "chunks": [getattr(chunk, "chunk_id", "") for chunk in entry],
                }
            )
        else:
            recorded_contexts.append(
                {
                    "name": name,
                    "adapter": entry.adapter,
                    "options": dict(entry.options),
                    "chunks": [],
                }
            )
    return {
        "adapter": adapter,
        "repos": [repos[name].metadata() for name in sorted(repos)],
        "cases": recorded_cases,
        "case_count": len(recorded_cases),
        "contexts": recorded_contexts,
    }


SNAPSHOT_NAME = re.compile(r"^[0-9a-f]{64}$")
"""Only content-addressed snapshot directories are prunable."""


def list_snapshots(home: Path) -> list[Path]:
    """Return every content-addressed snapshot directory, sorted by digest."""
    root = home / "repos"
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and SNAPSHOT_NAME.fullmatch(path.name)
    )


def prune_snapshots(
    home: Path,
    referenced: Iterable[str],
    *,
    dry_run: bool = False,
) -> list[Path]:
    """Remove snapshots no recorded run references, or list them on a dry run.

    Deleting a referenced snapshot would break the cell evidence that names its
    digest, so the caller passes every digest the database still points at.
    """
    keep = set(referenced)
    removed: list[Path] = []
    for path in list_snapshots(home):
        if path.name in keep:
            continue
        removed.append(path)
        if not dry_run:
            shutil.rmtree(path)
    return removed


def referenced_digests(
    snapshots: Iterable[Mapping[str, Any]],
) -> set[str]:
    """Collect the repository digests recorded in run source snapshots."""
    digests: set[str] = set()
    for snapshot in snapshots:
        repos = snapshot.get("repos", [])
        if not isinstance(repos, list):
            continue
        for repo in repos:
            if isinstance(repo, Mapping) and isinstance(repo.get("digest"), str):
                digests.add(repo["digest"])
    return digests


def changed_repos(
    recorded: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    current: Mapping[str, RepoSnapshot] | Sequence[Mapping[str, Any]],
) -> list[tuple[str, str, str]]:
    """Return (name, recorded digest, current digest) for changed trees.

    Both arguments accept either the whole source snapshot or its repository
    rows, because a launch holds live snapshots while a run record holds JSON.
    """
    previous = _repo_digests(recorded)
    now = _repo_digests(current)
    changes = []
    for name in sorted(now):
        before = previous.get(name)
        after = now[name]
        if before is not None and before != after:
            changes.append((name, before, after))
    return changes


def _repo_digests(
    rows: Mapping[str, Any] | Sequence[Mapping[str, Any]] | Mapping[str, RepoSnapshot],
) -> dict[str, str]:
    """Read repository names and digests from a snapshot or its stored rows."""
    if isinstance(rows, Mapping):
        if "repos" in rows and isinstance(rows["repos"], (list, tuple)):
            return _repo_digests(rows["repos"])
        digests: dict[str, str] = {}
        for name, item in rows.items():
            if isinstance(item, RepoSnapshot):
                digests[str(name)] = item.digest
            elif isinstance(item, Mapping):
                digests[str(name)] = str(item.get("digest", ""))
        return digests
    return {
        str(row.get("name")): str(row.get("digest", ""))
        for row in rows
        if isinstance(row, Mapping)
    }


def _collect_files(
    root: Path,
    repo: RepoSpec,
    name: str,
) -> tuple[list[SnapshotFile], list[dict[str, str]], int]:
    """Walk the tree, apply includes/excludes and enforce every cap."""
    included: list[SnapshotFile] = []
    skipped: list[dict[str, str]] = []
    total = 0
    excludes = [*repo.exclude, *DEFAULT_EXCLUDES]
    root_resolved = root.resolve()
    walked: set[Path] = set()
    for current, dirs, files in os.walk(root, followlinks=repo.follow_symlinks):
        directory = Path(current)
        if repo.follow_symlinks:
            real = directory.resolve()
            if real in walked:
                dirs[:] = []
                continue
            walked.add(real)
        kept_dirs: list[str] = []
        for entry in sorted(dirs):
            path = directory / entry
            relative = path.relative_to(root).as_posix()
            if _matches(relative, excludes) or _matches(relative + "/", excludes):
                continue
            if path.is_symlink():
                if not repo.follow_symlinks:
                    skipped.append({"path": relative, "reason": "symlink"})
                    continue
                if not _within(path.resolve(), root_resolved):
                    raise SpecError(
                        f"repos.{name}.root",
                        relative,
                        "a symlinked directory escapes the repository root",
                        "remove the symlink or keep it inside the repository",
                    )
            kept_dirs.append(entry)
        dirs[:] = kept_dirs
        for entry in sorted(files):
            path = directory / entry
            relative = path.relative_to(root).as_posix()
            if _matches(relative, excludes):
                continue
            if not _matches(relative, repo.include):
                continue
            if path.is_symlink():
                if not repo.follow_symlinks:
                    skipped.append({"path": relative, "reason": "symlink"})
                    continue
                resolved = path.resolve()
                if not _within(resolved, root_resolved):
                    raise SpecError(
                        f"repos.{name}.root",
                        relative,
                        "a symlink escapes the repository root",
                        "remove the symlink or keep it inside the repository",
                    )
                path = resolved
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise SpecError(
                    f"repos.{name}.root",
                    relative,
                    f"a repository file could not be read: {exc}",
                    "fix the file permissions or exclude the path",
                ) from exc
            if size > repo.max_file_bytes:
                raise SpecError(
                    f"repos.{name}.max_file_bytes",
                    relative,
                    f"the file is {size} bytes, above the {repo.max_file_bytes} "
                    "byte cap",
                    "raise max_file_bytes or exclude the file",
                )
            if len(included) + 1 > repo.max_files:
                raise SpecError(
                    f"repos.{name}.max_files",
                    relative,
                    f"the repository has more than {repo.max_files} included files",
                    "raise max_files or narrow include/exclude",
                )
            try:
                payload = path.read_bytes()
            except OSError as exc:
                raise SpecError(
                    f"repos.{name}.root",
                    relative,
                    f"a repository file could not be read: {exc}",
                    "fix the file permissions or exclude the path",
                ) from exc
            if _is_binary(payload):
                if repo.binary == "error":
                    raise SpecError(
                        f"repos.{name}.binary",
                        relative,
                        "the repository contains a binary file and binary is 'error'",
                        "set binary to 'skip' or exclude the file",
                    )
                skipped.append({"path": relative, "reason": "binary"})
                continue
            total += len(payload)
            if total > repo.max_total_bytes:
                raise SpecError(
                    f"repos.{name}.max_total_bytes",
                    relative,
                    f"included files exceed the {repo.max_total_bytes} byte total cap",
                    "raise max_total_bytes or narrow include/exclude",
                )
            included.append(
                SnapshotFile(
                    path=relative,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    bytes=len(payload),
                    mode=stat.S_IMODE(path.stat().st_mode),
                )
            )
    included.sort(key=lambda item: item.path)
    skipped.sort(key=lambda item: item["path"])
    return included, skipped, total


def _digest(files: Iterable[SnapshotFile]) -> str:
    """Hash the sorted blob-hash and path lines into the snapshot digest."""
    lines = "".join(f"{item.sha256}  {item.path}\n" for item in files)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _store_tree(
    home: Path,
    digest: str,
    root: Path,
    files: Sequence[SnapshotFile],
    skipped: Sequence[Mapping[str, str]],
) -> Path:
    """Copy the included files into the content-addressed snapshot directory."""
    repos_root = home / "repos"
    repos_root.mkdir(parents=True, exist_ok=True)
    target = repos_root / digest
    manifest = target / SNAPSHOT_MANIFEST
    if manifest.is_file():
        try:
            recorded = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            recorded = {}
        if recorded.get("digest") == digest:
            return target / SNAPSHOT_TREE
        shutil.rmtree(target)
    elif target.exists():
        shutil.rmtree(target)
    temporary = repos_root / f".{digest}.{uuid4().hex}.tmp"
    tree = temporary / SNAPSHOT_TREE
    tree.mkdir(parents=True)
    try:
        for item in files:
            destination = tree / item.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = root / item.path
            shutil.copyfile(source, destination)
            try:
                os.chmod(destination, item.mode)
            except OSError:
                pass
        payload = {
            "digest": digest,
            "files": [
                {
                    "path": item.path,
                    "sha256": item.sha256,
                    "bytes": item.bytes,
                    "mode": item.mode,
                }
                for item in files
            ],
            "bytes": sum(item.bytes for item in files),
            "skipped": [dict(item) for item in skipped],
        }
        (temporary / SNAPSHOT_MANIFEST).write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        try:
            os.replace(temporary, target)
        except FileExistsError:
            shutil.rmtree(temporary)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target / SNAPSHOT_TREE


def _git_metadata(root: Path) -> tuple[str | None, bool | None]:
    """Return HEAD and dirty state when the root is inside a git worktree."""
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None, None
    if head.returncode != 0:
        return None, None
    if status.returncode != 0:
        return head.stdout.strip(), None
    return head.stdout.strip(), bool(status.stdout.strip())


def _is_binary(payload: bytes) -> bool:
    """Treat a NUL byte in the first block as the binary signal."""
    return b"\x00" in payload[:8192]


def _within(path: Path, root: Path) -> bool:
    """Return whether a resolved path stays inside a resolved root."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _matches(path: str, patterns: Iterable[str]) -> bool:
    """Match a snapshot path against glob patterns, with ** and basename rules."""
    for raw in patterns:
        pattern = raw.rstrip("/")
        if not pattern:
            continue
        if "/" not in pattern:
            if fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern):
                return True
        if re.fullmatch(_glob_regex(pattern), path):
            return True
    return False


def _glob_regex(pattern: str) -> str:
    """Translate a repository glob into an anchored regular expression."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if pattern[index : index + 2] == "**":
                if pattern[index + 2 : index + 3] == "/":
                    out.append("(?:.*/)?")
                    index += 3
                else:
                    out.append(".*")
                    index += 2
            else:
                out.append("[^/]*")
                index += 1
        elif character == "?":
            out.append("[^/]")
            index += 1
        elif character == "[":
            end = pattern.find("]", index + 1)
            if end == -1:
                out.append(r"\[")
                index += 1
            else:
                out.append(pattern[index : end + 1])
                index = end + 1
        else:
            out.append(re.escape(character))
            index += 1
    return "".join(out)
