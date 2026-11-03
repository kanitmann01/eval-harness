"""Fetch a declared remote repository into the snapshot cache.

The fetch is deliberately narrow: HTTPS only (the spec validator enforces it),
one ref, shallow, no submodules and no hooks. A credential is passed to git as
an HTTP header through the process environment, never as a command argument, so
it cannot appear in a process listing or an error message.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Mapping

from evalharness.core.models import RepoSpec
from evalharness.errors import SpecError

GIT_TIMEOUT_SECONDS = 120
"""A fetch that takes longer than this is treated as a network failure."""


def fetch_repo(
    name: str,
    repo: RepoSpec,
    *,
    home: Path,
    credentials: Mapping[str, str] | None = None,
) -> Path:
    """Fetch the repository at its declared URL and return a clean checkout.

    The cache lives under `<home>/repos/remotes/<key>` and is reused across
    runs. The checkout is forced to the fetched commit and cleaned, so a file
    left by a previous fetch cannot leak into the new snapshot.
    """
    if repo.url is None:
        raise SpecError(
            f"repos.{name}.url",
            None,
            "the repository has no URL to fetch",
            "set url to an https repository URL",
        )
    secret = ""
    if repo.credential is not None:
        secret = (credentials or {}).get(repo.credential, "")
        if not secret:
            raise SpecError(
                f"repos.{name}.credential",
                repo.credential,
                "the credential is needed to fetch this repository but its "
                "secret is not available",
                "add it with 'evalh credentials add' or remove credential for "
                "a public repository",
            )
    cache = _cache_path(home, repo.url, repo.ref)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        if not (cache / ".git").is_dir():
            _run_git(["init", "-q", str(cache)], name, repo.url, "")
            _run_git(
                ["-C", str(cache), "remote", "add", "origin", repo.url],
                name,
                repo.url,
                "",
            )
        elif _remote_url(cache) != repo.url:
            _run_git(
                ["-C", str(cache), "remote", "set-url", "origin", repo.url],
                name,
                repo.url,
                "",
            )
        target = repo.ref or "HEAD"
        _run_git(
            [
                "-C",
                str(cache),
                "fetch",
                "--force",
                "--depth=1",
                "origin",
                target,
            ],
            name,
            repo.url,
            secret,
        )
        _run_git(
            ["-C", str(cache), "checkout", "--force", "--detach", "FETCH_HEAD"],
            name,
            repo.url,
            "",
        )
        _run_git(
            ["-C", str(cache), "clean", "-fdx"],
            name,
            repo.url,
            "",
        )
    except OSError as exc:
        raise SpecError(
            f"repos.{name}.url",
            repo.url,
            f"git could not be run: {exc}",
            "install git and make sure it is on PATH, or set root to a local path",
        ) from exc
    return cache


def _cache_path(home: Path, url: str, ref: str | None) -> Path:
    """Return a stable cache directory for one URL and ref."""
    key = hashlib.sha256(f"{url}\0{ref or ''}".encode("utf-8")).hexdigest()[:24]
    return home / "repos" / "remotes" / key


def _remote_url(cache: Path) -> str:
    """Read the cache's origin URL without raising for a broken checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cache), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _run_git(
    args: list[str],
    name: str,
    url: str,
    secret: str,
) -> None:
    """Run one git command, translating a failure into an actionable error."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_ALLOW_PROTOCOL"] = "https"
    if secret:
        token = base64.b64encode(f"x-access-token:{secret}".encode("utf-8"))
        environment.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": "Authorization: Basic "
                + token.decode("ascii"),
            }
        )
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise SpecError(
            f"repos.{name}.url",
            url,
            f"git did not finish within {GIT_TIMEOUT_SECONDS} seconds",
            "check the network, the ref, and the repository permissions",
        ) from exc
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout).strip().splitlines()
    message = detail[-1] if detail else f"git exited with {result.returncode}"
    if secret:
        message = message.replace(secret, "<redacted>")
    raise SpecError(
        f"repos.{name}.url",
        url,
        f"the remote repository could not be fetched: {message}",
        "check the URL, ref and credential, or point root at a local checkout",
    )


def clear_cache(home: Path) -> None:
    """Remove every fetched checkout; used by tests and manual cleanup."""
    root = home / "repos" / "remotes"
    if root.is_dir():
        shutil.rmtree(root)
