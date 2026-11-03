"""Behaviour tests for judge pins, drift and the provenance pages."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

import pytest
from sqlalchemy import text

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.core.config import Settings
from evalharness.core.judge_pins import judge_drift, pins_for_run
from evalharness.core.judges import judge_config_hash
from evalharness.core.models import Check, JudgeSpec, RunRecord
from evalharness.storage.db import Database
from evalharness.storage.migrations import SCHEMA_VERSION
from evalharness.storage.models import RunRow
from evalharness.storage.repository import Repository
from tests.helpers import base_spec, write_spec
from tests.test_api_console import Console, _client, _seed

FINISHED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
STARTED = datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc)


def _run(
    spec: dict[str, Any],
    run_id: str = "run-1",
    *,
    finished: datetime | None = FINISHED,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        spec_name="spec",
        spec_snapshot=spec,
        started_at=STARTED,
        finished_at=finished,
    )


def _judged_spec(temperature: float = 0.2) -> dict[str, Any]:
    return {
        "metrics": [
            {"key": "citation_support", "options": {"judge": "strict"}},
            {"key": "exact_match", "options": {}},
        ],
        "judges": [
            {
                "name": "strict",
                "adapter": "judge:scripted",
                "model": "judge-1",
                "options": {"temperature": temperature, "instructions": "strict"},
            }
        ],
    }


def test_pins_fall_back_to_the_spec_and_carry_the_config_hash() -> None:
    spec = _judged_spec()

    pins = pins_for_run(_run(spec), [])

    assert len(pins) == 1
    pin = pins[0]
    assert pin.metric == "citation_support"
    assert pin.judge == "strict"
    assert pin.adapter == "judge:scripted"
    assert pin.model == "judge-1"
    assert pin.temperature == 0.2
    assert pin.options_digest
    assert pin.source == "spec"
    assert pin.last_used_at == FINISHED
    assert pin.config_hash == judge_config_hash(
        JudgeSpec(
            name="strict",
            adapter="judge:scripted",
            model="judge-1",
            options={"temperature": 0.2, "instructions": "strict"},
        )
    )


def test_stored_hashes_are_authoritative_and_flag_within_run_drift() -> None:
    checks = [
        Check(metric="citation_support", passed=True, judge_config_hash="hash-a"),
        Check(metric="citation_support", passed=True, judge_config_hash="hash-b"),
    ]

    pins = pins_for_run(_run(_judged_spec()), checks)

    assert [(pin.config_hash, pin.source) for pin in pins] == [
        ("hash-a", "checks"),
        ("hash-b", "checks"),
    ]
    drifts = judge_drift(_run(_judged_spec()), checks)
    assert len(drifts) == 1
    drift = drifts[0]
    assert drift.kind == "within_run"
    assert drift.judge == "strict"
    assert drift.metrics == ("citation_support",)
    assert drift.hashes == ("hash-a", "hash-b")
    assert drift.runs == ("run-1",)
    assert "not like-for-like" in drift.reason


def test_stored_hashes_without_a_declaration_still_pin_and_drift() -> None:
    checks = [
        Check(metric="m", passed=True, judge_config_hash="hash-a"),
        Check(metric="m", passed=True, judge_config_hash="hash-b"),
    ]

    pins = pins_for_run(_run({}), checks)
    assert len(pins) == 2
    assert {pin.judge for pin in pins} == {None}
    assert {pin.source for pin in pins} == {"checks"}

    drifts = judge_drift(_run({}), checks)
    assert len(drifts) == 1
    assert drifts[0].judge == "unknown judge"
    assert "not like-for-like" in drifts[0].reason


def test_compared_runs_with_different_judge_hashes_are_not_like_for_like() -> None:
    run_a = _run(_judged_spec(0.1), "run-a")
    run_b = _run(_judged_spec(0.2), "run-b")

    drifts = judge_drift(run_a, [], run_b, [])

    assert len(drifts) == 1
    drift = drifts[0]
    assert drift.kind == "across_runs"
    assert drift.judge == "strict"
    assert drift.metrics == ("citation_support",)
    assert len(drift.hashes) == 2
    assert drift.runs == ("run-a", "run-b")
    assert "not like-for-like" in drift.reason

    same = judge_drift(run_a, [], run_a, [])
    assert same == ()

    unjudged = _run({"metrics": [{"key": "exact_match", "options": {}}]}, "run-c")
    assert judge_drift(run_a, [], unjudged, []) == ()


def test_a_version_6_database_gains_the_operator_column(
    evalh_home: Any, workdir: Any
) -> None:
    url = "sqlite:///" + (workdir / "version-6.db").as_posix()
    database = Database(url)
    database.create_all()
    repository = Repository(database)
    repository.save_spec(
        "spec-old", "old-spec", "yaml", "sha", datetime.now(timezone.utc)
    )
    repository.create_run(
        RunRecord(
            run_id="run-old",
            spec_id="spec-old",
            status="ok",
            started_at=datetime.now(timezone.utc),
        )
    )
    with database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN operator")
        connection.execute(text("UPDATE schema_version SET version = 6"))
    database.dispose()

    reopened = Database(url)
    repository = Repository(reopened)
    try:
        old = repository.get_run("run-old")
        assert old is not None
        assert old.operator == ""
        repository.update_run(old.model_copy(update={"operator": "Ken"}))
        assert repository.get_run("run-old").operator == "Ken"
        with reopened.engine.begin() as connection:
            version = connection.exec_driver_sql(
                "SELECT MAX(version) FROM schema_version"
            ).scalar()
        assert version == SCHEMA_VERSION
    finally:
        reopened.dispose()


def _read_run(run_id: str) -> tuple[Repository, Database, RunRecord]:
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    run = repository.get_run(run_id)
    assert run is not None
    return repository, database, run


def test_operator_is_captured_and_trimmed_from_the_environment(
    evalh_home: Any,
    workdir: Any,
    capsys: Any,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EVALH_OPERATOR", "  Ken / FDE  ")
    spec = base_spec()
    spec["name"] = "operator-spec"
    run_id = _seed(capsys, write_spec(workdir / "operator.yaml", spec))

    repository, database, run = _read_run(run_id)
    try:
        assert run.operator == "Ken / FDE"
        assert repository.get_run(run_id).operator == "Ken / FDE"
    finally:
        database.dispose()


def test_operator_reads_as_empty_when_unset_or_null(
    evalh_home: Any,
    workdir: Any,
    capsys: Any,
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("EVALH_OPERATOR", raising=False)
    spec = base_spec()
    spec["name"] = "no-operator-spec"
    run_id = _seed(capsys, write_spec(workdir / "no-operator.yaml", spec))

    repository, database, run = _read_run(run_id)
    try:
        assert run.operator == ""
        assert repository.get_run(run_id).operator == ""
        with database.session() as session:
            row = session.get(RunRow, run_id)
            assert row is not None
            row.operator = None
        assert repository.get_run(run_id).operator == ""
    finally:
        database.dispose()


CITATION_SUPPORT = "evalharness.adapters.metrics.citation_support:build"
SCRIPTED_JUDGE = "evalharness.adapters.judges.scripted:build"


def _changed_spec() -> dict[str, Any]:
    """Return a spec whose prompts, candidates, prices and run fields moved."""
    spec = base_spec()
    spec["name"] = "changed-spec"
    spec["source"]["options"]["cases"] = [
        {
            "case_id": "case-1",
            "prompt": "What is 7 times 6?",
            "expected": {"answer": "42"},
            "tier": 1,
        },
        {
            "case_id": "case-2",
            "prompt": "What is 8 times 5?",
            "expected": {"answer": "40"},
            "tier": 2,
        },
    ]
    for candidate in spec["candidates"]:
        candidate["model"] = "stub-2"
    spec["prices"]["stub-2"] = {"input": 0.2, "output": 0.8}
    spec["seed"] = 8
    spec["budget_usd"] = 2.0
    spec["gates"] = [{"metric": "exact_match", "min": 0.5}]
    return spec


def _judge_spec(instructions: str) -> dict[str, Any]:
    """Return one cited candidate scored by a named scripted judge."""
    spec = base_spec()
    spec["name"] = "judge-spec"
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["name"] = "cited"
    spec["candidates"][0]["adapter"] = "test:cited"
    spec["candidates"][0]["options"] = {}
    spec["metrics"] = [
        {
            "key": "citation_support",
            "adapter": CITATION_SUPPORT,
            "options": {"judge": "quality-judge"},
        }
    ]
    spec["judges"] = [
        {
            "name": "quality-judge",
            "adapter": SCRIPTED_JUDGE,
            "model": "scripted-judge",
            "options": {
                "claims": ["6 times 7 is 42."],
                "instructions": instructions,
            },
        }
    ]
    spec["prices"]["scripted-judge"] = {"input": 1.0, "output": 1.0}
    spec["gates"] = []
    return spec


@pytest.fixture
def provenance(
    evalh_home: Any,
    workdir: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Console]:
    """Seed runs covering identical, changed, judged and unrelated receipts."""
    monkeypatch.delenv("EVALH_OPERATOR", raising=False)
    receipt = base_spec()
    receipt["name"] = "receipt-spec"
    receipt_path = write_spec(workdir / "receipt.yaml", receipt)
    receipt_a = _seed(capsys, receipt_path)
    receipt_b = _seed(capsys, receipt_path)

    baseline = base_spec()
    baseline["name"] = "changed-spec"
    changed_a = _seed(capsys, write_spec(workdir / "changed-a.yaml", baseline))
    changed_b = _seed(
        capsys, write_spec(workdir / "changed-b.yaml", _changed_spec())
    )

    judge_a = _seed(
        capsys,
        write_spec(workdir / "judge-a.yaml", _judge_spec("strict")),
    )
    judge_b = _seed(
        capsys,
        write_spec(workdir / "judge-b.yaml", _judge_spec("lenient")),
    )

    lonely = base_spec()
    lonely["name"] = "lonely-spec"
    lonely_id = _seed(capsys, write_spec(workdir / "lonely.yaml", lonely))

    repo_root = workdir / "repo-source"
    (repo_root / "cases").mkdir(parents=True)
    (repo_root / "cases" / "questions.jsonl").write_text(
        '{"case_id": "case-1", "prompt": "What is 6 times 7?", '
        '"expected": {"answer": "42"}}\n',
        encoding="utf-8",
    )
    repo_spec = base_spec()
    repo_spec["name"] = "repo-source-spec"
    repo_spec["repos"] = {"tiny": {"root": "repo-source"}}
    repo_spec["source"] = {
        "adapter": "evalharness.adapters.sources.repo:build",
        "options": {"repo": "tiny", "cases": "cases/questions.jsonl"},
    }
    repo_id = _seed(capsys, write_spec(workdir / "repo-source.yaml", repo_spec))

    monkeypatch.setenv("EVALH_OPERATOR", "  Ken / FDE  ")
    operator_spec = base_spec()
    operator_spec["name"] = "operator-api-spec"
    operator_id = _seed(
        capsys, write_spec(workdir / "operator-api.yaml", operator_spec)
    )
    monkeypatch.delenv("EVALH_OPERATOR", raising=False)

    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    app = create_app(settings=settings, repository=repository)
    yield Console(
        settings=settings,
        database=database,
        repository=repository,
        app=app,
        runs={
            "receipt_a": receipt_a,
            "receipt_b": receipt_b,
            "changed_a": changed_a,
            "changed_b": changed_b,
            "judge_a": judge_a,
            "judge_b": judge_b,
            "lonely": lonely_id,
            "repo": repo_id,
            "operator": operator_id,
        },
    )
    database.dispose()


async def test_receipt_json_reports_provenance_and_matches_compare_prices(
    provenance: Console,
) -> None:
    run_a = provenance.runs["receipt_a"]
    run_b = provenance.runs["receipt_b"]
    async with _client(provenance.app) as client:
        response = await client.get(f"/api/runs/{run_b}/receipt")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run_b
        assert data["spec_name"] == "receipt-spec"
        assert data["status"] == "ok"
        assert data["seed"] == 7
        assert data["operator"] == ""
        assert data["operator_recorded"] is False
        assert data["spec_sha"] == provenance.repository.get_spec(
            data["spec_id"]
        )["sha"]
        assert data["harness_sha"]
        assert {candidate["name"] for candidate in data["candidates"]} == {
            "stub-concise",
            "stub-explained",
        }
        assert {candidate["model"] for candidate in data["candidates"]} == {
            "stub-1"
        }
        assert data["previous"]["run_id"] == run_a
        assert data["diff"]["total"] == 0
        assert data["drift"] == []
        assert data["gates"] == []
        assert len(data["price_table"]["id"]) == 64
        assert any(
            key.split("/")[-1] == "stub-1"
            for key in data["price_table"]["rates"]
        )

        compare = await client.get(
            "/api/compare", params={"a": run_a, "b": run_b}
        )
        assert compare.status_code == 200
        assert compare.json()["a"]["price_table"]["id"] == data["price_table"][
            "id"
        ]


async def test_receipt_html_shows_hashes_operator_and_no_previous_run(
    provenance: Console,
) -> None:
    lonely = provenance.runs["lonely"]
    async with _client(provenance.app) as client:
        page = await client.get(f"/runs/{lonely}/receipt")
        assert page.status_code == 200
        text = page.text
        assert "Spec hash" in text
        assert "Harness revision" in text
        assert "not recorded" in text
        assert "no previous run" in text.lower()
        assert "Price table" in text
        assert "Judge pins" in text


async def test_receipt_records_the_repository_snapshot(
    provenance: Console,
) -> None:
    run_id = provenance.runs["repo"]
    async with _client(provenance.app) as client:
        data = (await client.get(f"/api/runs/{run_id}/receipt")).json()
        assert data["source_recorded"] is True
        source = data["source"]
        assert source["adapter"].endswith("sources.repo:build")
        assert source["case_count"] == 1
        assert source["cases"][0]["case_id"] == "case-1"
        assert len(source["cases"][0]["prompt_sha256"]) == 64
        repo = source["repos"][0]
        assert repo["name"] == "tiny"
        assert repo["root"] == "repo-source"
        assert len(repo["digest"]) == 64
        assert repo["files"] == 1
        assert repo["bytes"] > 0
        assert repo["commit"]

        page = await client.get(f"/runs/{run_id}/receipt")
        assert page.status_code == 200
        assert "Repository snapshots" in page.text
        assert repo["digest"][:12] in page.text
        assert "repo-source" in page.text


async def test_receipt_diff_lists_changed_categories_and_previous_link(
    provenance: Console,
) -> None:
    run_a = provenance.runs["changed_a"]
    run_b = provenance.runs["changed_b"]
    async with _client(provenance.app) as client:
        data = (await client.get(f"/api/runs/{run_b}/receipt")).json()
        diff = data["diff"]
        assert data["previous"]["run_id"] == run_a
        assert diff["total"] == sum(diff["counts"].values())
        for category in ("cases", "candidates", "prices", "run", "gates"):
            assert diff["counts"][category] > 0, category
        assert any(
            "cases[case-1]" in change["path"]
            for change in diff["changes"]
        )

        page = await client.get(f"/runs/{run_b}/receipt")
        assert page.status_code == 200
        assert "changed-spec" in page.text
        assert diff["headline"] in page.text
        assert f"/runs/{run_a}" in page.text
        assert diff["changes"][0]["summary"] in page.text


async def test_receipt_pins_the_judge_that_stored_each_check(
    provenance: Console,
) -> None:
    run_b = provenance.runs["judge_b"]
    async with _client(provenance.app) as client:
        data = (await client.get(f"/api/runs/{run_b}/receipt")).json()
    assert len(data["judge_pins"]) == 1
    pin = data["judge_pins"][0]
    assert pin["metric"] == "citation_support"
    assert pin["judge"] == "quality-judge"
    assert pin["judge_label"] == "quality-judge"
    assert pin["adapter"] == SCRIPTED_JUDGE
    assert pin["model"] == "scripted-judge"
    assert pin["source"] == "checks"
    assert len(pin["config_hash"]) == 64
    assert len(pin["options_digest"]) == 64
    assert pin["last_used_at"]


async def test_diff_page_puts_the_spec_diff_above_judge_drift(
    provenance: Console,
) -> None:
    run_a = provenance.runs["judge_a"]
    run_b = provenance.runs["judge_b"]
    async with _client(provenance.app) as client:
        page = await client.get(
            f"/runs/{run_b}/diff", params={"against": run_a}
        )
        assert page.status_code == 200
        text = page.text
        assert "Spec diff" in text
        assert "Judge drift" in text
        assert text.index("Spec diff") < text.index("Judge drift")
        assert "quality-judge" in text
        assert "not like-for-like" in text
        assert f"/runs/{run_a}" in text and f"/runs/{run_b}" in text
        assert f"/runs/{run_a}/receipt" in text

        same = await client.get(
            f"/runs/{provenance.runs['receipt_b']}/diff",
            params={"against": provenance.runs["receipt_a"]},
        )
        assert same.status_code == 200
        assert "No differences between the two snapshots." in same.text
        assert "No judge drift" in same.text


async def test_diff_page_marks_a_field_the_baseline_lacked_as_absent(
    provenance: Console,
) -> None:
    """A case the baseline did not declare reads absent, not as a recorded value."""
    async with _client(provenance.app) as client:
        page = await client.get(
            f"/runs/{provenance.runs['changed_b']}/diff",
            params={"against": provenance.runs["changed_a"]},
        )
        assert page.status_code == 200
        assert "absent" in page.text


async def test_receipt_and_diff_return_not_found_for_unknown_runs(
    provenance: Console,
) -> None:
    run = provenance.runs["receipt_a"]
    async with _client(provenance.app) as client:
        page = await client.get("/runs/missing-run/receipt")
        assert page.status_code == 404
        assert "That resource does not exist" in page.text

        missing = await client.get("/api/runs/missing-run/receipt")
        assert missing.status_code == 404
        assert "missing-run" in missing.json()["detail"]

        no_baseline = await client.get(f"/runs/{run}/diff")
        assert no_baseline.status_code == 404
        assert "?against" in no_baseline.text

        unknown_baseline = await client.get(
            f"/runs/{run}/diff", params={"against": "missing-run"}
        )
        assert unknown_baseline.status_code == 404
        assert "missing-run" in unknown_baseline.text


async def test_receipt_prefers_the_most_recent_run_of_the_same_spec_id(
    provenance: Console,
) -> None:
    run_b = provenance.runs["receipt_b"]
    lonely = provenance.runs["lonely"]
    stored = provenance.repository.get_run(lonely)
    assert stored is not None
    other = provenance.repository.get_run(run_b)
    assert other is not None
    provenance.repository.update_run(
        stored.model_copy(update={"spec_id": other.spec_id})
    )
    async with _client(provenance.app) as client:
        data = (await client.get(f"/api/runs/{run_b}/receipt")).json()
    assert data["previous"]["run_id"] == lonely


async def test_receipt_reads_an_older_flat_price_snapshot(
    provenance: Console,
) -> None:
    run_id = provenance.runs["receipt_b"]
    stored = provenance.repository.get_run(run_id)
    assert stored is not None
    provenance.repository.update_run(
        stored.model_copy(
            update={
                "prices_snapshot": {
                    "stub-1": {"input": 0.1, "output": 0.2}
                }
            }
        )
    )
    async with _client(provenance.app) as client:
        data = (await client.get(f"/api/runs/{run_id}/receipt")).json()
    assert data["price_table"]["rates"] == {
        "stub-1": {"input": 0.1, "output": 0.2}
    }
    assert len(data["price_table"]["id"]) == 64


async def test_receipt_shows_an_operator_when_the_environment_sets_one(
    provenance: Console,
) -> None:
    run_id = provenance.runs["operator"]
    async with _client(provenance.app) as client:
        data = (await client.get(f"/api/runs/{run_id}/receipt")).json()
        page = await client.get(f"/runs/{run_id}/receipt")
    assert data["operator"] == "Ken / FDE"
    assert data["operator_recorded"] is True
    assert "Ken / FDE" in page.text
