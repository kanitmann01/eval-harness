"""HTTP API seam tests for the spec editor.

Every test drives the application built by `create_app` through
`httpx.AsyncClient(transport=ASGITransport)`; the CLI is used only where the
ticket asks for equivalence or end-to-end proof, so no server and no network.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.cli import main
from evalharness.core.config import Settings
from evalharness.core.models import Check
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import base_spec, write_spec
from tests.test_api_console import Console, _client, _seed

TEXT = {"content-type": "text/plain"}
"""The content type the editor's script and the tests use for raw YAML."""


class _LatencyMetric:
    """A metric whose key contains latency, so a latency gate produces a warning."""

    def __init__(self, key: str) -> None:
        self.key = key

    def check(self, cell: Any, ctx: Any) -> list[Check]:
        """Return no checks; the editor only builds metrics during validation."""
        return []


def build_latency_metric(identity: str, options: Any) -> _LatencyMetric:
    """Build the latency-named metric through the production registry."""
    return _LatencyMetric(identity)


@pytest.fixture
def editor(
    evalh_home: Path,
    workdir: Path,
) -> Iterator[Console]:
    """An application whose specs directory is a fresh workspace directory."""
    settings = replace(Settings.from_env(), specs_dir=workdir / "specs")
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    app = create_app(settings=settings, repository=repository)
    yield Console(
        settings=settings,
        database=database,
        repository=repository,
        app=app,
        runs={},
    )
    database.dispose()


def _yaml(spec: dict[str, Any] | None = None) -> str:
    """Return a spec document as YAML text."""
    return yaml.safe_dump(spec if spec is not None else base_spec(), sort_keys=False)


async def _put(client: Any, name: str, text: str) -> Any:
    """PUT one YAML document to the spec route."""
    return await client.put(
        f"/api/specs/{name}",
        content=text.encode("utf-8"),
        headers=TEXT,
    )


async def _validate(client: Any, text: str) -> Any:
    """POST one YAML document to the JSON validation route."""
    return await client.post(
        "/api/specs/validate",
        content=text.encode("utf-8"),
        headers=TEXT,
    )


async def _put_document(app: Any, name: str, text: str) -> Any:
    """PUT one document through a fresh in-process client."""
    async with _client(app) as client:
        return await _put(client, name, text)


async def _validate_document(app: Any, text: str) -> Any:
    """Validate one document through a fresh in-process client."""
    async with _client(app) as client:
        return await _validate(client, text)


def test_specs_dir_defaults_next_to_the_database(workdir: Path) -> None:
    database = workdir / "state" / "harness.db"
    home = workdir / "home"
    env = {
        "EVALH_DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "EVALH_HOME": str(home),
    }
    assert Settings.from_env(env).specs_dir == database.parent / "specs"

    explicit = dict(env, EVALH_SPECS_DIR=str(workdir / "elsewhere"))
    assert Settings.from_env(explicit).specs_dir == workdir / "elsewhere"

    for url in ("sqlite://", "postgresql://localhost/harness"):
        fallback = Settings.from_env(
            {"EVALH_DATABASE_URL": url, "EVALH_HOME": str(home)}
        )
        assert fallback.specs_dir == home / "specs"


async def test_empty_specs_directory_is_an_empty_state(editor: Console) -> None:
    specs_dir = editor.settings.specs_dir
    assert not specs_dir.exists()
    async with _client(editor.app) as client:
        assert (await client.get("/api/specs")).json() == []
        page = await client.get("/specs")
        assert page.status_code == 200
        assert "No spec files yet" in page.text
        assert str(specs_dir) in page.text
    assert not specs_dir.exists()


async def test_round_trip_edit_validate_and_save(editor: Console) -> None:
    text = _yaml()
    path = editor.settings.specs_dir / "smoke.yaml"
    async with _client(editor.app) as client:
        created = await _put(client, "smoke", text)
        assert created.status_code == 200
        payload = created.json()
        assert payload["created"] is True
        assert payload["name"] == "smoke"
        assert payload["cells"] == 2
        assert payload["cases"] == 1
        assert payload["cells_per_candidate"] == {
            "stub-concise": 1,
            "stub-explained": 1,
        }
        assert payload["cost_estimate"]["cost_usd"] > 0
        assert payload["cost_estimate"]["observed"] is False
        assert payload["cost_estimate"]["median_prompt_tokens"] == 500
        assert payload["warnings"] == []
        assert payload["errors"] == []
        assert path.read_text(encoding="utf-8") == text

        rows = (await client.get("/api/specs")).json()
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "smoke"
        assert row["path"] == str(path)
        assert row["valid"] is True
        assert row["spec_name"] == "test-spec"
        assert row["candidates"] == 2
        assert row["cells"] == 2
        assert row["size"] == len(text.encode("utf-8"))
        assert row["modified"].endswith("+00:00")
        assert row["error"] is None

        detail = (await client.get("/api/specs/smoke")).json()
        assert detail["yaml"] == text
        assert detail["valid"] is True
        assert detail["cells"] == 2
        assert detail["summary"]["spec_name"] == "test-spec"
        assert [item["name"] for item in detail["summary"]["candidates"]] == [
            "stub-concise",
            "stub-explained",
        ]
        assert detail["summary"]["metrics"] == ["exact_match"]
        assert detail["summary"]["replicas"] == 1

        same_file = await client.get("/api/specs/smoke.yaml")
        assert same_file.status_code == 200
        assert same_file.json()["name"] == "smoke"

        page = await client.get("/specs/smoke/edit")
        assert page.status_code == 200
        assert "data-spec-editor" in page.text
        assert "stub-concise" in page.text
        assert "Check" in page.text and "Save" in page.text
        assert "Cells per candidate" in page.text
        assert ">Comparisons</a>" in page.text

        spec = base_spec()
        spec["source"]["options"]["cases"].append(
            {
                "case_id": "case-2",
                "prompt": "And 7 times 6?",
                "expected": {"answer": "42"},
                "tier": 2,
            }
        )
        larger = _yaml(spec)
        checked = await _validate(client, larger)
        assert checked.status_code == 200
        assert checked.json()["cases"] == 2
        assert checked.json()["cells"] == 4

        updated = await _put(client, "smoke", larger)
        assert updated.status_code == 200
        assert updated.json()["created"] is False
        assert path.read_text(encoding="utf-8") == larger

        saved = await client.post(
            "/specs/smoke/edit", data={"yaml": text, "action": "save"}
        )
        assert saved.status_code == 303
        assert saved.headers["location"] == "/specs?saved=smoke"
        assert path.read_text(encoding="utf-8") == text
        listing = await client.get("/specs", params={"saved": "smoke"})
        assert "Saved" in listing.text and "smoke.yaml" in listing.text
        assert sorted(item.name for item in path.parent.iterdir()) == ["smoke.yaml"]


async def test_list_rows_open_the_structured_form(editor: Console) -> None:
    """The list row action goes to the structured form, not the raw editor."""
    async with _client(editor.app) as client:
        assert (await _put(client, "smoke", _yaml())).status_code == 200
        page = await client.get("/specs")
        assert "/comparisons/smoke/edit" in page.text
        assert "/specs/smoke/edit" not in page.text


async def test_invalid_spec_names_the_field_and_is_never_written(
    editor: Console,
) -> None:
    text = _yaml()
    async with _client(editor.app) as client:
        assert (await _put(client, "smoke", text)).status_code == 200
        path = editor.settings.specs_dir / "smoke.yaml"
        before = path.read_text(encoding="utf-8")
        before_stat = path.stat()

        invalid = text.replace("budget_usd: 1.0", "budget_usd: -1")
        response = await _put(client, "smoke", invalid)
        assert response.status_code == 400
        payload = response.json()
        assert payload["valid"] is False
        assert payload["cells"] is None
        error = payload["errors"][0]
        assert error["field"] == "budget_usd"
        assert error["value"] == "-1"
        assert "greater than or equal to 0" in error["reason"]
        assert error["fix"] == "see docs/spec-v1.md"

        assert path.read_text(encoding="utf-8") == before
        assert path.stat().st_mtime_ns == before_stat.st_mtime_ns

        checked = await _validate(client, invalid)
        assert checked.status_code == 400
        assert checked.json()["errors"][0]["field"] == "budget_usd"

        refused = await client.post(
            "/specs/smoke/edit", data={"yaml": invalid, "action": "save"}
        )
        assert refused.status_code == 400
        assert "budget_usd" in refused.text
        assert "Fix:" in refused.text
        assert "disabled" in refused.text
        assert path.read_text(encoding="utf-8") == before
        assert path.stat().st_mtime_ns == before_stat.st_mtime_ns
        assert sorted(item.name for item in path.parent.iterdir()) == ["smoke.yaml"]


async def test_cell_count_and_estimate_match_the_cli_dry_run(
    editor: Console,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _yaml()
    dry_spec = write_spec(workdir / "dry.yaml", base_spec())
    assert main(["run", str(dry_spec), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "cells: 2" in output
    assert "  stub-concise: 1 cells" in output
    assert "  stub-explained: 1 cells" in output
    cost_line = next(
        line for line in output.splitlines() if line.startswith("estimated cost:")
    )
    cli_cost = float(cost_line.split("$")[1].split()[0])

    async with _client(editor.app) as client:
        assert (await _put(client, "smoke", text)).status_code == 200
        detail = (await client.get("/api/specs/smoke")).json()
    assert detail["cells"] == 2
    assert detail["cells_per_candidate"] == {
        "stub-concise": 1,
        "stub-explained": 1,
    }
    assert round(detail["cost_estimate"]["cost_usd"], 6) == round(cli_cost, 6)


def test_estimate_uses_medians_from_an_earlier_run(
    editor: Console,
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _yaml()
    assert asyncio.run(_put_document(editor.app, "smoke", text)).status_code == 200
    before = asyncio.run(_validate_document(editor.app, text)).json()
    assert before["cost_estimate"]["observed"] is False

    run_id = _seed(capsys, editor.settings.specs_dir / "smoke.yaml")
    assert run_id

    after = asyncio.run(_validate_document(editor.app, text)).json()
    assert after["valid"] is True
    assert after["cost_estimate"]["observed"] is True
    assert after["cost_estimate"]["median_prompt_tokens"] == 12
    assert after["cost_estimate"]["median_completion_tokens"] == 8
    assert after["cost_estimate"]["cost_usd"] < before["cost_estimate"]["cost_usd"]
    assert after["cells"] == 2


async def test_traversal_absolute_and_bad_names_are_refused(editor: Console) -> None:
    text = _yaml()
    async with _client(editor.app) as client:
        for name in (
            "..%2F..%2Fescape",
            "%2Ftmp%2Fevil",
            "Bad-Name",
            "..hidden",
            "%2E%2E",
        ):
            response = await _put(client, name, text)
            assert response.status_code == 400, name
            assert response.json()["errors"][0]["field"] == "name"
            assert (
                "slug inside the specs directory"
                in response.json()["errors"][0]["reason"]
            )
        assert not editor.settings.specs_dir.exists()
        outside = await client.get("/api/specs/%2E%2E%2Fescape")
        assert outside.status_code == 400
        assert outside.json()["errors"][0]["field"] == "name"


async def test_non_yaml_and_malformed_bodies_are_refused(editor: Console) -> None:
    async with _client(editor.app) as client:
        broken = await _validate(client, "name: [unterminated")
        assert broken.status_code == 400
        assert broken.json()["errors"][0]["field"] == "spec"
        assert "YAML" in broken.json()["errors"][0]["reason"]

        listing = await _validate(client, "- just\n- a list\n")
        assert listing.status_code == 400
        assert listing.json()["errors"][0]["field"] == "spec"
        assert "mapping" in listing.json()["errors"][0]["reason"]

        not_a_string = await client.post(
            "/api/specs/validate", json={"yaml": 42}
        )
        assert not_a_string.status_code == 400
        assert not_a_string.json()["errors"][0]["field"] == "yaml"

        invalid_json = await client.post(
            "/api/specs/validate",
            content=b"{",
            headers={"content-type": "application/json"},
        )
        assert invalid_json.status_code == 400
        assert invalid_json.json()["errors"][0]["field"] == "yaml"

        envelope = await client.post("/api/specs/validate", json={"yaml": _yaml()})
        assert envelope.status_code == 200
        assert envelope.json()["valid"] is True

        bad_put = await client.put("/api/specs/smoke", json={"yaml": 42})
        assert bad_put.status_code == 400
        assert bad_put.json()["errors"][0]["field"] == "yaml"
        assert not editor.settings.specs_dir.exists()


async def test_duplicate_starts_a_new_file(editor: Console) -> None:
    text = _yaml()
    async with _client(editor.app) as client:
        assert (await _put(client, "smoke", text)).status_code == 200
        first = await client.post("/api/specs/smoke/duplicate")
        assert first.status_code == 201
        assert first.json()["name"] == "smoke-copy"
        assert first.json()["duplicate_of"] == "smoke"
        assert first.json()["path"].endswith("smoke-copy.yaml")
        copy = editor.settings.specs_dir / "smoke-copy.yaml"
        assert copy.read_text(encoding="utf-8") == text

        second = await client.post("/api/specs/smoke/duplicate")
        assert second.json()["name"] == "smoke-copy-2"

        page = await client.post("/specs/smoke/duplicate")
        assert page.status_code == 303
        assert page.headers["location"] == "/specs/smoke-copy-3/edit?copied=smoke"
        third = editor.settings.specs_dir / "smoke-copy-3.yaml"
        assert third.read_text(encoding="utf-8") == text
        opened = await client.get(
            "/specs/smoke-copy-3/edit", params={"copied": "smoke"}
        )
        assert "Duplicate of" in opened.text

        missing = await client.post("/api/specs/missing/duplicate")
        assert missing.status_code == 404


async def test_check_button_works_without_javascript(editor: Console) -> None:
    text = _yaml()
    async with _client(editor.app) as client:
        assert (await _put(client, "smoke", text)).status_code == 200
        valid = await client.post(
            "/specs/smoke/edit", data={"yaml": text, "action": "check"}
        )
        assert valid.status_code == 200
        assert "No validation errors" in valid.text
        assert "data-cells" in valid.text
        assert ">2<" in valid.text

        invalid = await client.post(
            "/specs/smoke/edit",
            data={"yaml": "spec_version: 1\n", "action": "check"},
        )
        assert invalid.status_code == 200
        assert "field-error" in invalid.text
        assert 'data-field="name"' in invalid.text
        assert "Field required" in invalid.text
        assert "Fix:" in invalid.text
        assert "disabled" in invalid.text


async def test_the_list_marks_a_broken_file_invalid(editor: Console) -> None:
    specs_dir = editor.settings.specs_dir
    specs_dir.mkdir(parents=True)
    (specs_dir / "broken.yaml").write_text("spec_version: 1\n", encoding="utf-8")
    (specs_dir / "Bad-Name.yaml").write_text(_yaml(), encoding="utf-8")
    async with _client(editor.app) as client:
        rows = (await client.get("/api/specs")).json()
        assert [row["name"] for row in rows] == ["broken"]
        assert rows[0]["name"] == "broken"
        assert rows[0]["valid"] is False
        assert rows[0]["error"]["field"] == "name"
        assert rows[0]["error"]["reason"] == "Field required"
        assert rows[0]["cells"] is None
        page = await client.get("/specs")
        assert "invalid" in page.text
        assert "Field required" in page.text
        opened = await client.get("/specs/broken/edit")
        assert opened.status_code == 200
        assert 'data-field="name"' in opened.text
        assert "disabled" in opened.text


async def test_source_errors_are_field_named(editor: Console) -> None:
    spec = base_spec()
    spec["source"] = {"adapter": "test:failing_source", "options": {}}
    async with _client(editor.app) as client:
        failed = await _validate(client, _yaml(spec))
        assert failed.status_code == 400
        assert failed.json()["errors"][0]["field"] == "source"
        assert "source exploded" in failed.json()["errors"][0]["reason"]

        spec["source"] = {"adapter": "test:empty_source", "options": {}}
        empty = await _validate(client, _yaml(spec))
        assert empty.status_code == 400
        assert empty.json()["errors"][0]["field"] == "source"
        assert "no cases" in empty.json()["errors"][0]["reason"]


async def test_validation_reports_warnings(editor: Console) -> None:
    spec = base_spec()
    spec["concurrency"] = 2
    spec["metrics"] = [
        {"key": "latency", "adapter": "tests.test_api_specs:build_latency_metric"}
    ]
    spec["gates"] = [{"metric": "latency", "max": 0.5}]
    async with _client(editor.app) as client:
        checked = await _validate(client, _yaml(spec))
        assert checked.status_code == 200
        assert checked.json()["warnings"] == [
            "latency gate with concurrency > 1: latency measures queueing, "
            "not the model; use concurrency 1 for latency comparisons"
        ]
        page = await client.post(
            "/specs/smoke/edit", data={"yaml": _yaml(spec), "action": "check"}
        )
        assert page.status_code == 200
        assert "concurrency 1 for latency comparisons" in page.text


async def test_unknown_names_are_404_where_the_file_is_missing(editor: Console) -> None:
    async with _client(editor.app) as client:
        assert (await client.get("/specs/missing/edit")).status_code == 404
        assert (await client.get("/specs/Bad-Name/edit")).status_code == 404
        assert (await client.post("/specs/missing/duplicate")).status_code == 404
        assert (await client.post("/specs/Bad-Name/duplicate")).status_code == 404
        failed_save = await client.post(
            "/specs/Bad-Name/edit", data={"yaml": "", "action": "check"}
        )
        assert failed_save.status_code == 404
        assert (await client.get("/api/specs/missing")).status_code == 404
        assert (await client.get("/api/specs/Bad-Name")).status_code == 400
        assert (await client.post("/api/specs/missing/duplicate")).status_code == 404
        assert (await client.post("/api/specs/Bad-Name/duplicate")).status_code == 400


def test_the_cli_runs_a_spec_saved_by_the_editor(
    editor: Console,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert asyncio.run(_put_document(editor.app, "saved", _yaml())).status_code == 200
    run_id = _seed(capsys, editor.settings.specs_dir / "saved.yaml")
    cells = editor.repository.list_cells(run_id)
    assert len(cells) == 2
    assert {cell.status for cell in cells} == {"ok"}
    run = editor.repository.get_run(run_id)
    assert run is not None
    assert run.status == "ok"
    assert run.spec_snapshot["name"] == "test-spec"


async def test_specs_page_shows_a_friendly_specs_path(editor: Console) -> None:
    specs_dir = editor.settings.specs_dir
    specs_dir.mkdir(parents=True)
    (specs_dir / "smoke.yaml").write_text(_yaml(), encoding="utf-8")
    relative = specs_dir.resolve().relative_to(Path.cwd().resolve()).as_posix()
    async with _client(editor.app) as client:
        page = await client.get("/specs")
        assert f'<code title="{specs_dir}">{relative}</code>' in page.text
        assert f">{specs_dir}<" not in page.text


async def test_specs_path_falls_back_to_the_directory_name_outside_the_cwd(
    editor: Console,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs_dir = editor.settings.specs_dir
    specs_dir.mkdir(parents=True)
    (specs_dir / "smoke.yaml").write_text(_yaml(), encoding="utf-8")
    elsewhere = workdir / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    async with _client(editor.app) as client:
        page = await client.get("/specs")
        assert f'<code title="{specs_dir}">{specs_dir.name}</code>' in page.text
