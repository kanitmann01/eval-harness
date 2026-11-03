"""Dynamic context selection through the CLI: spec, planner, executor, record."""

from __future__ import annotations

import asyncio

import httpx

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.cli import main
from evalharness.core.config import Settings
from tests.helpers import base_spec, open_repository, run_id_from, write_spec

FAKE_CONTEXT = "test:context_fake"
BROKEN_CONTEXT = "test:context_broken"
STUB = "evalharness.adapters.candidates.stub:build"


def _two_case_spec() -> dict:
    spec = base_spec()
    spec["source"]["options"]["cases"] = [
        {
            "case_id": "case-a",
            "prompt": "What is 6 times 7?",
            "expected": {"answer": "42"},
            "tier": 1,
        },
        {
            "case_id": "case-b",
            "prompt": "What is 7 times 6?",
            "expected": {"answer": "42"},
            "tier": 2,
        },
    ]
    spec["contexts"] = {
        "graph": {"adapter": FAKE_CONTEXT, "options": {"delimiter": "-"}},
    }
    spec["candidates"] = [
        {
            "name": "stub-concise",
            "adapter": STUB,
            "credential": "stub",
            "model": "stub-1",
            "options": {"prompt": "concise", "context": "graph"},
        }
    ]
    spec["credentials"] = [{"name": "stub", "provider": "none"}]
    return spec


def test_a_context_adapter_selects_per_case_and_the_run_records_it(
    evalh_home, workdir, capsys
) -> None:
    spec = write_spec(workdir / "spec.yaml", _two_case_spec())

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        cells = repository.list_cells(run_id)
        contexts = run.source_snapshot["contexts"]
        recorded = {cell.case_id: cell for cell in cells}
        assert contexts == [
            {
                "name": "graph",
                "adapter": FAKE_CONTEXT,
                "options": {"delimiter": "-"},
                "chunks": [],
            }
        ]
        assert set(recorded) == {"case-a", "case-b"}
        first = recorded["case-a"].variant
        second = recorded["case-b"].variant
        assert first["context_name"] == "graph"
        assert first["context"][0]["chunk_id"] == "fake-case-a-0"
        assert second["context"][0]["chunk_id"] == "fake-case-b-0"
        assert first["context_digest"] != second["context_digest"]
        assert first["context"][0]["text"] == "What is 6 times 7?"
        artifacts = recorded["case-a"].output["artifacts"]
        assert artifacts["received"]["context_chunks"] == ["fake-case-a-0"]
    finally:
        database.dispose()


def test_an_adapter_without_select_is_a_spec_error(
    evalh_home, workdir, capsys
) -> None:
    spec_data = _two_case_spec()
    spec_data["contexts"] = {"graph": {"adapter": BROKEN_CONTEXT}}
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "contexts.graph.adapter" in captured.err
    assert "select" in captured.err


def test_a_selector_spec_error_is_passed_through_unchanged(
    evalh_home, workdir, capsys
) -> None:
    spec_data = _two_case_spec()
    spec_data["contexts"] = {"graph": {"adapter": "test:context_spec_error"}}
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "contexts.graph.options.repo" in captured.err
    assert "not snapshotted" in captured.err


def test_a_selector_that_raises_is_wrapped_as_a_spec_error(
    evalh_home, workdir, capsys
) -> None:
    spec_data = _two_case_spec()
    spec_data["contexts"] = {"graph": {"adapter": "test:context_exploding"}}
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "contexts.graph.adapter" in captured.err
    assert "selector exploded" in captured.err


def test_dynamic_context_needs_a_candidate_that_accepts_injection(
    evalh_home, workdir, capsys
) -> None:
    spec_data = _two_case_spec()
    spec_data["candidates"] = [
        {
            "name": "no-context",
            "adapter": "test:no_context",
            "credential": "stub",
            "model": "stub-1",
            "options": {"context": "graph"},
        }
    ]
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "candidates[0].options.context" in captured.err
    assert "cannot accept context injection" in captured.err


def test_a_malformed_context_entry_names_the_field(
    evalh_home, workdir, capsys
) -> None:
    spec_data = _two_case_spec()
    spec_data["contexts"] = {"graph": {"options": {}}}
    spec = write_spec(workdir / "spec.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "contexts.graph.adapter" in captured.err

def test_the_run_page_shows_the_context_each_candidate_received(
    evalh_home, workdir, capsys
) -> None:
    spec = write_spec(workdir / "spec.yaml", _two_case_spec())
    assert main(["run", str(spec)]) == 0
    run_id = run_id_from(capsys.readouterr().out)
    database, repository = open_repository()
    try:
        app = create_app(settings=Settings.from_env(), repository=repository)

        async def fetch() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://console.test",
            ) as client:
                return await client.get(f"/runs/{run_id}")

        response = asyncio.run(fetch())
        assert response.status_code == 200
        text = response.text
        assert "Context" in text
        assert "fake-case-a-0" in text
        assert "fake-case-b-0" in text
        assert "docs/fake.md:1-1" in text
    finally:
        database.dispose()
