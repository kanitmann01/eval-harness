"""The remote fetch boundary: cache reuse, credentials and actionable failures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from evalharness.core.models import RepoSpec
from evalharness.core.repo_fetch import clear_cache, fetch_repo
from evalharness.errors import SpecError


def _repo(url: str | None = "https://example.test/team/repo.git", **fields) -> RepoSpec:
    data = {"url": url, **fields} if url is not None else {"root": ".", **fields}
    return RepoSpec.model_validate(data)


def _successful(
    calls: list[tuple[list[str], dict[str, object]]], origin: list[str]
):
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((args, kwargs))
        if "init" in args:
            Path(args[3], ".git").mkdir(parents=True, exist_ok=True)
        if "get-url" in args:
            return subprocess.CompletedProcess(args, 0, origin[0] + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    return run


def test_fetch_repo_refuses_a_repository_without_a_url(tmp_path: Path) -> None:
    with pytest.raises(SpecError) as caught:
        fetch_repo("tiny", _repo(None), home=tmp_path)

    assert caught.value.field == "repos.tiny.url"


def test_fetch_repo_refuses_a_missing_credential_secret(tmp_path: Path) -> None:
    with pytest.raises(SpecError) as caught:
        fetch_repo("tiny", _repo(credential="github"), home=tmp_path)

    assert caught.value.field == "repos.tiny.credential"
    assert "secret is not available" in caught.value.reason


def test_fetch_repo_initialises_the_cache_and_passes_the_secret_in_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        "evalharness.core.repo_fetch.subprocess.run",
        _successful(calls, ["https://example.test/team/repo.git"]),
    )

    checkout = fetch_repo(
        "tiny",
        _repo(credential="github", ref="main"),
        home=tmp_path,
        credentials={"github": "s3cret"},
    )

    assert checkout.is_dir()
    assert checkout.parent.name == "remotes"
    assert any("init" in args for args, _ in calls)
    assert any("remote" in args and "add" in args for args, _ in calls)
    fetch_call = next(
        kwargs for args, kwargs in calls if "fetch" in args and "--force" in args
    )
    environment = fetch_call["env"]
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_ALLOW_PROTOCOL"] == "https"
    assert environment["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert environment["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert "s3cret" not in environment["GIT_CONFIG_VALUE_0"]


def test_fetch_repo_reuses_a_matching_cache_without_rewriting_the_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        "evalharness.core.repo_fetch.subprocess.run",
        _successful(calls, ["https://example.test/team/repo.git"]),
    )
    home = tmp_path
    first = fetch_repo("tiny", _repo(), home=home)
    calls.clear()

    second = fetch_repo("tiny", _repo(), home=home)

    assert second == first
    assert not any("add" in args or "set-url" in args for args, _ in calls)


def test_fetch_repo_repairs_a_cache_whose_origin_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    origin = ["https://example.test/old.git"]
    monkeypatch.setattr(
        "evalharness.core.repo_fetch.subprocess.run",
        _successful(calls, origin),
    )
    home = tmp_path
    checkout = fetch_repo("tiny", _repo(), home=home)
    calls.clear()
    origin[0] = "https://example.test/new.git"

    fetch_repo("tiny", _repo(), home=home)

    assert checkout.is_dir()
    assert any("set-url" in args for args, _ in calls)


def test_fetch_repo_reports_a_git_failure_with_the_last_stderr_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, 128, "", "fatal: not found\n")

    monkeypatch.setattr("evalharness.core.repo_fetch.subprocess.run", failing)

    with pytest.raises(SpecError) as caught:
        fetch_repo("tiny", _repo(), home=tmp_path)

    assert "could not be fetched: fatal: not found" in caught.value.reason


def test_fetch_repo_redacts_the_secret_from_a_git_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if "init" in args:
            Path(args[3], ".git").mkdir(parents=True, exist_ok=True)
        if "fetch" not in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 128, "", "denied s3cret\n")

    monkeypatch.setattr("evalharness.core.repo_fetch.subprocess.run", failing)

    with pytest.raises(SpecError) as caught:
        fetch_repo(
            "tiny",
            _repo(credential="github"),
            home=tmp_path,
            credentials={"github": "s3cret"},
        )

    assert "s3cret" not in caught.value.reason
    assert "<redacted>" in caught.value.reason


def test_fetch_repo_reports_a_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def timing_out(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(args, 120)

    monkeypatch.setattr("evalharness.core.repo_fetch.subprocess.run", timing_out)

    with pytest.raises(SpecError) as caught:
        fetch_repo("tiny", _repo(), home=tmp_path)

    assert "did not finish within 120 seconds" in caught.value.reason


def test_fetch_repo_reports_git_being_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("git")

    monkeypatch.setattr("evalharness.core.repo_fetch.subprocess.run", missing)

    with pytest.raises(SpecError) as caught:
        fetch_repo("tiny", _repo(), home=tmp_path)

    assert "git could not be run" in caught.value.reason


def test_fetch_repo_reports_a_failure_without_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, 7, "", "")

    monkeypatch.setattr("evalharness.core.repo_fetch.subprocess.run", failing)

    with pytest.raises(SpecError) as caught:
        fetch_repo("tiny", _repo(), home=tmp_path)

    assert "git exited with 7" in caught.value.reason


def test_fetch_repo_treats_a_broken_origin_lookup_as_an_empty_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def broken_lookup(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(args)
        if "get-url" in args:
            raise OSError("cannot read")
        if "init" in args:
            Path(args[3], ".git").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("evalharness.core.repo_fetch.subprocess.run", broken_lookup)
    checkout = fetch_repo("tiny", _repo(), home=tmp_path)
    calls.clear()

    fetch_repo("tiny", _repo(), home=tmp_path)

    assert checkout.is_dir()
    assert any("set-url" in args for args in calls)


def test_clear_cache_removes_fetched_checkouts_and_tolerates_a_missing_root(
    tmp_path: Path,
) -> None:
    clear_cache(tmp_path)
    checkout = tmp_path / "repos" / "remotes" / "abc"
    checkout.mkdir(parents=True)

    clear_cache(tmp_path)

    assert not (tmp_path / "repos" / "remotes").exists()
