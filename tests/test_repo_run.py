"""End-to-end repository source runs through the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.cli import main
from tests.helpers import base_spec, open_repository, run_id_from, write_spec

REPO_SOURCE = "evalharness.adapters.sources.repo:build"


def _repo_spec(workdir: Path, *, replicas: int = 1) -> dict:
    (workdir / "cases").mkdir()
    _write_cases(workdir, "What is 6 times 7?")
    spec = base_spec()
    spec["source"] = {
        "adapter": REPO_SOURCE,
        "options": {"repo": "tiny", "cases": "cases/questions.jsonl"},
    }
    spec["repos"] = {"tiny": {"root": "."}}
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["options"] = {}
    spec["replicas"] = replicas
    return spec


def _write_cases(workdir: Path, prompt: str) -> None:
    payload = {
        "case_id": "case-1",
        "prompt": prompt,
        "expected": {"answer": "42"},
        "tier": 1,
    }
    (workdir / "cases" / "questions.jsonl").write_text(
        json.dumps(payload) + "\n", encoding="utf-8"
    )


def test_a_repository_source_runs_and_records_its_snapshot(
    evalh_home, workdir, capsys
) -> None:
    spec = write_spec(workdir / "spec.yaml", _repo_spec(workdir))

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "status=ok" in captured.out
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        source = run.source_snapshot
        assert source["adapter"] == REPO_SOURCE
        assert source["case_count"] == 1
        assert source["repos"][0]["name"] == "tiny"
        assert len(source["repos"][0]["digest"]) == 64
        assert source["repos"][0]["root"] == "."
    finally:
        database.dispose()


def test_a_remote_repository_fetches_with_its_stored_credential(
    evalh_home, workdir, capsys, monkeypatch
) -> None:
    checkout = workdir / "checkout"
    (checkout / "cases").mkdir(parents=True)
    _write_cases(checkout, "What is 6 times 7?")
    calls: dict[str, object] = {}

    def fake_fetch(name, repo, *, home, credentials):
        calls.update(
            name=name, url=repo.url, credential=repo.credential, credentials=credentials
        )
        return checkout

    monkeypatch.setattr("evalharness.core.repo_snapshot.fetch_repo", fake_fetch)
    monkeypatch.setenv("GITHUB_TOKEN", "s3cret")
    assert (
        main(
            [
                "credentials",
                "add",
                "github",
                "--provider",
                "github",
                "--secret-env",
                "GITHUB_TOKEN",
            ]
        )
        == 0
    )
    capsys.readouterr()

    spec = base_spec()
    spec["source"] = {
        "adapter": REPO_SOURCE,
        "options": {"repo": "tiny", "cases": "cases/questions.jsonl"},
    }
    spec["source"]["credential"] = "github"
    spec["repos"] = {
        "tiny": {
            "url": "https://example.test/team/repo.git",
            "ref": "main",
        }
    }
    spec["credentials"].append({"name": "github", "provider": "github"})
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["options"] = {}
    path = write_spec(workdir / "remote.yaml", spec)

    code = main(["run", str(path)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert calls["credential"] == "github"
    assert calls["credentials"] == {"github": "s3cret"}
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        source = repository.get_run(run_id).source_snapshot
        assert source["repos"][0]["url"] == "https://example.test/team/repo.git"
        assert source["repos"][0]["root"] == ""
        assert source["repos"][0]["ref"] == "main"
    finally:
        database.dispose()


def test_resume_refuses_a_changed_repository_tree(
    evalh_home, workdir, capsys
) -> None:
    spec = write_spec(workdir / "spec.yaml", _repo_spec(workdir, replicas=2))
    assert main(["run", str(spec), "--cancel-after", "1"]) == 0
    run_id = run_id_from(capsys.readouterr().out)

    _write_cases(workdir, "What is 7 times 7?")

    code = main(["run", str(spec), "--resume", run_id])

    captured = capsys.readouterr()
    assert code == 2
    assert "spec error: repos.tiny=" in captured.err
    assert "repository content changed" in captured.err
