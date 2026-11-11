"""HTTP seam tests for the demo library and its copy-on-first-use specs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from evalharness.api.app import create_app
from evalharness.core.config import Settings
from evalharness.demos.catalog import bundled_text, find_demo


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console.test"
    )


@pytest.fixture
def demo_app(evalh_home: Path) -> Iterator[Any]:
    del evalh_home
    yield create_app()


async def test_demo_library_lists_every_scenario_and_the_pipeline(
    demo_app: Any,
) -> None:
    async with _client(demo_app) as client:
        response = await client.get("/demos")
    assert response.status_code == 200
    body = response.text
    assert "Demo library" in body
    assert "Offline walkthrough" in body
    assert "spends no tokens" in body
    for stage in ("Spec", "Cases", "Cells", "Checks", "Aggregates", "Gates", "Verdict"):
        assert stage in body
    for title in (
        "Prompt choice",
        "Context grounding",
        "Model and cost tradeoff",
        "A failed candidate",
    ):
        assert title in body
    assert "Run demo" in body
    assert "Open spec" in body
    assert "demo-prompt-choice.yaml" in body
    assert "spec copied" not in body


async def test_run_demo_copies_the_spec_and_redirects_to_the_replay_trace(
    demo_app: Any,
) -> None:
    async with _client(demo_app) as client:
        response = await client.post("/demos/prompt-choice/run")
    assert response.status_code == 303
    location = response.headers["location"]
    assert "/runs/" in location
    assert location.endswith("/trace?replay=1")

    spec = Path(Settings.from_env().specs_dir) / "demo-prompt-choice.yaml"
    assert spec.is_file()
    assert spec.read_text(encoding="utf-8") == bundled_text(find_demo("prompt-choice") or pytest.fail("missing demo"))

    async with _client(demo_app) as client:
        library = await client.get("/demos")
    assert "spec copied" in library.text


async def test_run_demo_replays_the_same_recorded_run(demo_app: Any) -> None:
    async with _client(demo_app) as client:
        first = await client.post("/demos/context-grounding/run")
        second = await client.post("/demos/context-grounding/run")
        runs = (await client.get("/api/runs")).json()

    assert first.status_code == 303
    assert second.status_code == 303
    assert second.headers["location"] == first.headers["location"]
    matching = [
        run for run in runs if run["spec_name"] == "demo-context-grounding"
    ]
    assert len(matching) == 1


async def test_open_demo_copies_the_spec_and_lands_in_the_editor(
    demo_app: Any,
) -> None:
    async with _client(demo_app) as client:
        response = await client.post("/demos/context-grounding/open")
    assert response.status_code == 303
    assert response.headers["location"].endswith(
        "/specs/demo-context-grounding/edit?copied=demo"
    )
    assert (Path(Settings.from_env().specs_dir) / "demo-context-grounding.yaml").is_file()

    async with _client(demo_app) as client:
        editor = await client.get(response.headers["location"])
    assert editor.status_code == 200
    assert "demo-context-grounding" in editor.text


async def test_unknown_demo_is_the_console_404(demo_app: Any) -> None:
    async with _client(demo_app) as client:
        listing = await client.post("/demos/nope/run")
        opening = await client.post("/demos/nope/open")
    assert listing.status_code == 404
    assert "does not exist" in listing.text
    assert opening.status_code == 404
    assert "does not exist" in opening.text


async def test_run_demo_refuses_a_spec_edited_to_a_live_provider(
    demo_app: Any,
) -> None:
    scenario = find_demo("model-tradeoff") or pytest.fail("missing demo")
    specs = Path(Settings.from_env().specs_dir)
    specs.mkdir(parents=True, exist_ok=True)
    text = bundled_text(scenario).replace(
        "evalharness.adapters.candidates.stub:build",
        "evalharness.adapters.candidates.openai:build",
    )
    (specs / f"{scenario.spec_name}.yaml").write_text(text, encoding="utf-8")

    async with _client(demo_app) as client:
        response = await client.post("/demos/model-tradeoff/run")
        runs = (await client.get("/api/runs")).json()

    assert response.status_code == 400
    assert "now names a live provider" in response.text
    assert "Run demo never spends" in response.text
    assert runs == []


async def test_an_unlaunchable_existing_demo_spec_returns_the_reason(
    demo_app: Any,
) -> None:
    specs = Path(Settings.from_env().specs_dir)
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "demo-model-tradeoff.yaml").write_text(
        "name: broken\n", encoding="utf-8"
    )
    async with _client(demo_app) as client:
        response = await client.post("/demos/model-tradeoff/run")
    assert response.status_code == 400
    body = response.text
    assert "The demo did not launch" in body
    assert "spec_version" in body