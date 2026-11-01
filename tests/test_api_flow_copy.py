"""Cross-page copy checks for the console flow.

The three editing surfaces (the wizard, the structured edit form and the raw
YAML editor) and the estimate labels must say the same thing on every page that
mentions them. Each test drives the ASGI application in process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import base_spec, write_spec
from tests.test_api_console import Console, _client


@pytest.fixture
def flow_console(
    evalh_home: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Console]:
    """Return an app whose specs directory starts empty."""
    specs = workdir / "specs"
    specs.mkdir()
    monkeypatch.setenv("EVALH_SPECS_DIR", str(specs))
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
        runs={},
    )
    database.dispose()


async def test_the_specs_page_names_the_three_editing_surfaces(
    flow_console: Console,
) -> None:
    """Specs explains the wizard, structured edit and raw YAML, and what Compare is."""
    async with _client(flow_console.app) as client:
        page = await client.get("/specs")
    assert page.status_code == 200
    text = page.text
    assert "<h1>Specs</h1>" in text
    assert "Three ways to edit a spec" in text
    assert "wizard" in text.lower()
    assert "structured edit" in text.lower()
    assert "Raw YAML" in text
    assert "Compare does not edit a spec" in text
    assert "run ids" in text
    assert "No comparisons yet" not in text


async def test_the_specs_empty_state_states_the_count_and_the_next_action(
    flow_console: Console,
) -> None:
    """An empty directory says how many specs it holds and how to add the first."""
    async with _client(flow_console.app) as client:
        page = await client.get("/specs")
    assert page.status_code == 200
    assert "No spec files yet" in page.text
    assert "The directory holds <strong>0 specs</strong>" in page.text
    assert "new comparison wizard" in page.text
    assert "PUT /api/specs/" in page.text


async def test_the_wizard_says_which_surface_it_is(flow_console: Console) -> None:
    """The wizard writes a new file; edits and comparisons happen elsewhere."""
    async with _client(flow_console.app) as client:
        page = await client.get("/comparisons/new/1")
    assert page.status_code == 200
    text = page.text
    assert "this wizard writes a new spec file" in text
    assert "Structured edit" in text
    assert "Raw YAML" in text
    assert "Compare reads two run ids" in text
    assert "never this draft" in text
    assert "Add another" not in text


async def test_the_launch_page_labels_its_numbers_as_estimates(
    flow_console: Console,
) -> None:
    """Launch explains the per-cell estimate and never presents it as a bill."""
    spec = base_spec()
    spec["name"] = "smoke"
    write_spec(flow_console.settings.specs_dir / "smoke.yaml", spec)
    async with _client(flow_console.app) as client:
        page = await client.get("/launch")
    assert page.status_code == 200
    text = page.text
    assert "estimated medians" in text
    assert "fallback medians" not in text
    assert "median prompt tokens" in text
    assert "median completion tokens" in text
    assert "This is an estimate only" in text
    assert "bills the tokens it actually uses" in text
    assert "preselects a spec that validates" in text
    assert 'value="smoke.yaml" selected' in text
    assert "%.4f" not in text
    assert "%.6f" not in text
