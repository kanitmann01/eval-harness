"""Behaviour tests for content-addressed repository snapshots."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from evalharness.core import repo_snapshot as repo_module
from evalharness.core.models import RepoSpec, SourceSpec, Spec
from evalharness.core.repo_snapshot import (
    _matches,
    changed_repos,
    prepare_repos,
    snapshot_repo,
    source_snapshot,
)
from evalharness.core.models import Case
from evalharness.errors import SpecError


def _repo(root: Path, **overrides: object) -> RepoSpec:
    return RepoSpec(root=str(root), **overrides)


def _spec(repo_root: str, **overrides: object) -> Spec:
    return Spec(
        spec_version=1,
        name="repo-spec",
        source=SourceSpec(adapter="evalharness.adapters.sources.repo:build"),
        repos={"tiny": RepoSpec(root=repo_root, **overrides)},
        candidates=[
            {
                "name": "c",
                "adapter": "evalharness.adapters.candidates.stub:build",
                "model": "stub-1",
            }
        ],
        metrics=[
            {
                "key": "m",
                "adapter": "evalharness.adapters.metrics.exact_match:build",
            }
        ],
    )


def _tree(root: Path) -> None:
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    (root / "sub" / "b.py").write_text("print('b')\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("x\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00\x01")


def test_snapshot_is_content_addressed_and_records_only_relative_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _tree(root)
    home = tmp_path / "home"

    first = snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)
    second = snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)

    assert first.digest == second.digest
    assert [item.path for item in first.files] == [
        "a.txt",
        "sub/b.py",
    ]
    assert first.skipped_binary == 1
    assert first.bytes == sum(item.bytes for item in first.files)
    assert first.metadata() == {
        "name": "tiny",
        "root": str(root),
        "url": None,
        "ref": None,
        "commit": first.commit,
        "digest": first.digest,
        "files": 2,
        "bytes": first.bytes,
        "skipped_binary": 1,
        "dirty": first.dirty,
    }
    assert str(tmp_path) not in json.dumps(first.metadata())
    manifest = json.loads(
        (home / "repos" / first.digest / "manifest.json").read_text("utf-8")
    )
    assert manifest["digest"] == first.digest
    assert [row["path"] for row in manifest["files"]] == ["a.txt", "sub/b.py"]
    assert manifest["skipped"] == [{"path": "binary.bin", "reason": "binary"}]
    assert (first.tree / "a.txt").read_text("utf-8") == "alpha\n"


def test_prepare_repos_rejects_a_remote_url(tmp_path: Path) -> None:
    spec = _spec("repo")
    spec.repos["tiny"] = RepoSpec(url="https://example.com/a.git")

    with pytest.raises(SpecError) as error:
        prepare_repos(spec, base_dir=tmp_path, home=tmp_path / "home")

    assert error.value.field == "repos.tiny.url"
    assert "0.4.0a" in error.value.reason


def test_prepare_repos_snapshots_each_local_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")

    repos = prepare_repos(_spec("repo"), base_dir=tmp_path, home=tmp_path / "home")

    assert set(repos) == {"tiny"}
    assert repos["tiny"].root == "repo"


def test_every_cap_fails_before_a_snapshot_is_written(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("abc", encoding="utf-8")
    (root / "b.txt").write_text("de", encoding="utf-8")
    home = tmp_path / "home"

    cases = [
        ({"max_files": 1}, "repos.tiny.max_files"),
        ({"max_file_bytes": 2}, "repos.tiny.max_file_bytes"),
        ({"max_total_bytes": 3}, "repos.tiny.max_total_bytes"),
        ({"binary": "error"}, "repos.tiny.binary"),
    ]
    (root / "binary.bin").write_bytes(b"\x00")
    for overrides, field in cases:
        with pytest.raises(SpecError) as error:
            snapshot_repo("tiny", _repo(root, **overrides), base_dir=tmp_path, home=home)
        assert error.value.field == field
        assert not (home / "repos").exists() or not list((home / "repos").iterdir())


def test_glob_patterns_match_without_leaving_the_tree(tmp_path: Path) -> None:
    assert _matches("sub/file.py", ["sub/*"])
    assert _matches("a/b/file.py", ["**/*.py"])
    assert _matches("file.py", ["**/*.py"])
    assert _matches("file.py", ["**.py"])
    assert _matches("a.txt", ["?.txt"])
    assert _matches("a.txt", ["[ab].txt"])
    assert _matches("a[0-9.txt", ["a[0-9.txt"])
    assert _matches("sub/file.py", ["sub/**.py"])
    assert _matches("sub/a.py", ["sub/?.py"])
    assert _matches("sub/a.py", ["sub/[ab].py"])
    assert _matches("sub/a[0-9.py", ["sub/a[0-9.py"])
    assert not _matches("a.txt", [""])
    assert not _matches("a.txt", ["sub/"])
    assert not _matches("a.txt", ["~x"])
    assert not _matches("node_modules/x.js", ["node_modules/"])
    assert _matches(".env", ["**/.env*"])


def test_include_and_exclude_narrow_the_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _tree(root)
    (root / "sub" / "c.md").write_text("c\n", encoding="utf-8")
    home = tmp_path / "home"

    snapshot = snapshot_repo(
        "tiny",
        _repo(root, include=["**/*.py"], exclude=["sub/"]),
        base_dir=tmp_path,
        home=home,
    )

    assert snapshot.files == ()


def _fake_symlinks(
    monkeypatch: pytest.MonkeyPatch,
    names: set[str],
    resolved: dict[str, Path] | None = None,
) -> None:
    """Pretend the named paths are symlinks on platforms that forbid creating them."""
    real_is_symlink = Path.is_symlink
    real_resolve = Path.resolve

    def is_symlink(self: Path) -> bool:
        return self.name in names or real_is_symlink(self)

    def resolve(self: Path, strict: bool = False) -> Path:
        if resolved and self.name in resolved:
            return resolved[self.name]
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    monkeypatch.setattr(Path, "resolve", resolve)


def test_symlinks_are_skipped_or_refused_by_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    (root / "link.txt").write_text("inside\n", encoding="utf-8")
    (root / "linkdir").mkdir()
    home = tmp_path / "home"
    _fake_symlinks(monkeypatch, {"link.txt", "linkdir"})

    skipped = snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)
    assert skipped.skipped == (
        {"path": "link.txt", "reason": "symlink"},
        {"path": "linkdir", "reason": "symlink"},
    )

    followed = snapshot_repo(
        "tiny",
        _repo(root, follow_symlinks=True),
        base_dir=tmp_path,
        home=home / "follow",
    )
    assert [item.path for item in followed.files] == ["a.txt", "link.txt"]

    _fake_symlinks(monkeypatch, {"link.txt", "linkdir"}, {"link.txt": outside})
    with pytest.raises(SpecError) as error:
        snapshot_repo(
            "tiny",
            _repo(root, follow_symlinks=True),
            base_dir=tmp_path,
            home=home / "escape-file",
        )
    assert "a symlink escapes" in error.value.reason

    _fake_symlinks(monkeypatch, {"link.txt", "linkdir"}, {"linkdir": tmp_path})
    with pytest.raises(SpecError) as error:
        snapshot_repo(
            "tiny",
            _repo(root, follow_symlinks=True),
            base_dir=tmp_path,
            home=home / "escape-dir",
        )
    assert "symlinked directory escapes" in error.value.reason


def test_a_followed_directory_cycle_is_walked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")

    def walk(
        *args: object, **kwargs: object
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        yield str(root), ["loop"], ["a.txt"]
        yield str(root), [], ["a.txt"]

    monkeypatch.setattr(repo_module.os, "walk", walk)
    snapshot = snapshot_repo(
        "tiny",
        _repo(root, follow_symlinks=True),
        base_dir=tmp_path,
        home=tmp_path / "home",
    )

    assert [item.path for item in snapshot.files] == ["a.txt"]


def test_a_snapshot_rebuilds_when_its_manifest_is_missing_or_broken(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    home = tmp_path / "home"
    first = snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)
    target = home / "repos" / first.digest
    manifest = target / "manifest.json"

    manifest.write_text("{", encoding="utf-8")
    second = snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)
    assert second.digest == first.digest
    assert json.loads(manifest.read_text("utf-8"))["digest"] == first.digest

    manifest.unlink()
    third = snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)
    assert third.digest == first.digest
    assert manifest.is_file()


def test_source_snapshot_records_cases_and_contexts() -> None:
    cases = [
        Case(case_id="one", prompt="a", tier=1),
        Case(case_id="two", prompt="b", tier=2),
    ]
    data = source_snapshot("adapter", {}, cases, {"policy": []})
    assert data["adapter"] == "adapter"
    assert data["repos"] == []
    assert data["case_count"] == 2
    assert data["cases"][0]["case_id"] == "one"
    assert len(data["cases"][0]["prompt_sha256"]) == 64
    assert data["contexts"] == [{"name": "policy", "chunks": []}]


def test_changed_repos_accepts_the_snapshot_or_its_rows(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    home = tmp_path / "home"
    before = snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)
    recorded = source_snapshot("adapter", {"tiny": before}, [])
    (root / "a.txt").write_text("changed\n", encoding="utf-8")
    after = snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)

    assert changed_repos(recorded, {"tiny": after}) == [
        ("tiny", before.digest, after.digest)
    ]
    assert changed_repos(recorded["repos"], {"tiny": after}) == [
        ("tiny", before.digest, after.digest)
    ]
    assert changed_repos(recorded, {"tiny": before}) == []
    assert changed_repos(
        {"tiny": before.metadata()}, {"tiny": after.metadata()}
    ) == [
        ("tiny", before.digest, after.digest)
    ]
    assert changed_repos(recorded, [after.metadata()]) == [
        ("tiny", before.digest, after.digest)
    ]
    assert changed_repos({}, {"tiny": before}) == []


def test_git_metadata_handles_a_missing_git_and_failed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("a\n", encoding="utf-8")

    def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        raise OSError("git is not installed")

    monkeypatch.setattr("evalharness.core.repo_snapshot.subprocess.run", missing)
    snapshot = snapshot_repo(
        "tiny", _repo(root), base_dir=tmp_path, home=tmp_path / "home"
    )
    assert snapshot.commit is None and snapshot.dirty is None

    def undecodable(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad output")

    monkeypatch.setattr(
        "evalharness.core.repo_snapshot.subprocess.run", undecodable
    )
    snapshot = snapshot_repo(
        "tiny", _repo(root), base_dir=tmp_path, home=tmp_path / "home-utf8"
    )
    assert snapshot.commit is None and snapshot.dirty is None

    def failed(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if args[-1] == "HEAD":
            return subprocess.CompletedProcess(args, 1, "", "no head")
        return subprocess.CompletedProcess(args, 1, "", "no status")

    monkeypatch.setattr("evalharness.core.repo_snapshot.subprocess.run", failed)
    snapshot = snapshot_repo(
        "tiny", _repo(root), base_dir=tmp_path, home=tmp_path / "home2"
    )
    assert snapshot.commit is None and snapshot.dirty is None

    def head_only(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if args[-1] == "HEAD":
            return subprocess.CompletedProcess(args, 0, "abc\n", "")
        return subprocess.CompletedProcess(args, 1, "", "no status")

    monkeypatch.setattr("evalharness.core.repo_snapshot.subprocess.run", head_only)
    snapshot = snapshot_repo(
        "tiny", _repo(root), base_dir=tmp_path, home=tmp_path / "home3"
    )
    assert snapshot.commit == "abc" and snapshot.dirty is None

    def both(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if args[-1] == "HEAD":
            return subprocess.CompletedProcess(args, 0, "abc\n", "")
        return subprocess.CompletedProcess(args, 0, " M a.txt\n", "")

    monkeypatch.setattr("evalharness.core.repo_snapshot.subprocess.run", both)
    snapshot = snapshot_repo(
        "tiny", _repo(root), base_dir=tmp_path, home=tmp_path / "home4"
    )
    assert snapshot.commit == "abc" and snapshot.dirty is True


def test_snapshot_repo_requires_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(SpecError) as error:
        snapshot_repo(
            "tiny",
            _repo(tmp_path / "missing"),
            base_dir=tmp_path,
            home=tmp_path / "home",
        )
    assert error.value.field == "repos.tiny.root"
    assert "does not exist" in error.value.reason


def test_a_file_stat_error_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    real_stat = Path.stat

    def stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name == "a.txt":
            raise OSError("denied")
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", stat)
    with pytest.raises(SpecError) as error:
        snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=tmp_path / "home")
    assert error.value.field == "repos.tiny.root"
    assert "could not be read" in error.value.reason


def test_a_file_read_error_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    real_read = Path.read_bytes

    def read(self: Path) -> bytes:
        if self.name == "a.txt":
            raise OSError("denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(SpecError) as error:
        snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=tmp_path / "home")
    assert error.value.field == "repos.tiny.root"
    assert "could not be read" in error.value.reason


def test_a_chmod_failure_does_not_fail_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")

    def chmod(path: object, mode: int) -> None:
        raise OSError("denied")

    monkeypatch.setattr(repo_module.os, "chmod", chmod)
    snapshot = snapshot_repo(
        "tiny", _repo(root), base_dir=tmp_path, home=tmp_path / "home"
    )
    assert [item.path for item in snapshot.files] == ["a.txt"]


def test_a_lost_replace_race_cleans_the_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    home = tmp_path / "home"

    def replace(source: object, destination: object) -> None:
        raise FileExistsError

    monkeypatch.setattr(repo_module.os, "replace", replace)
    snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)

    assert not list((home / "repos").glob(".*.tmp"))


def test_a_copy_failure_cleans_the_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    home = tmp_path / "home"

    def copy(source: object, destination: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo_module.shutil, "copyfile", copy)
    with pytest.raises(RuntimeError):
        snapshot_repo("tiny", _repo(root), base_dir=tmp_path, home=home)

    assert not list((home / "repos").glob(".*.tmp"))
