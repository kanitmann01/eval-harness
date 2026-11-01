"""HTTP tests for the structured comparison edit form.

The form is driven the way a browser would drive it: the fields are scraped
from the rendered page, changed, and posted back. No private helper is called.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from evalharness.api.app import create_app
from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import base_spec
from tests.test_api_console import Console, _client


@pytest.fixture
def editor(evalh_home: Path, workdir: Path) -> Iterator[Console]:
    """An application whose specs directory starts empty."""
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
    """Return a valid spec whose candidates name the no-credential choice."""
    document = spec if spec is not None else base_spec()
    for candidate in document["candidates"]:
        candidate.pop("credential", None)
    return yaml.safe_dump(document, sort_keys=False)


def _write(editor: Console, text: str, name: str = "smoke") -> Path:
    """Write a spec file into the console's specs directory."""
    root = editor.settings.specs_dir
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _digest(page: str) -> str:
    """Return the sha256 digest carried by the edit form."""
    match = re.search(r'name="digest" value="([0-9a-f]{64})"', page)
    assert match is not None, "the page has no sha256 digest field"
    return match.group(1)


def _form(page: str) -> dict[str, str]:
    """Scrape every named form control into a mapping, as a browser would."""
    values: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", page):
        name = re.search(r'\bname="([^"]+)"', tag)
        if name is None or 'type="submit"' in tag:
            continue
        value = re.search(r'\bvalue="([^"]*)"', tag)
        values[name.group(1)] = html.unescape(value.group(1)) if value else ""
    for opening, body in re.findall(
        r"(<textarea\b[^>]*>)(.*?)</textarea>", page, re.DOTALL
    ):
        name = re.search(r'\bname="([^"]+)"', opening)
        if name is not None:
            values[name.group(1)] = html.unescape(body)
    for opening, body in re.findall(
        r"(<select\b[^>]*>)(.*?)</select>", page, re.DOTALL
    ):
        name = re.search(r'\bname="([^"]+)"', opening)
        if name is None:
            continue
        selected = re.search(r'<option value="([^"]*)"[^>]*\bselected\b', body)
        options = re.findall(r'<option value="([^"]*)"', body)
        if selected is not None:
            values[name.group(1)] = html.unescape(selected.group(1))
        elif options:
            values[name.group(1)] = html.unescape(options[0])
    return values


async def _open(client: Any) -> str:
    """Fetch the edit page and return its body."""
    page = await client.get("/comparisons/smoke/edit")
    assert page.status_code == 200, page.text
    return page.text


async def test_the_form_shows_every_section_prefilled(editor: Console) -> None:
    """One page carries all four sections, the rail and a read-only name."""
    text = _yaml()
    path = _write(editor, text)
    async with _client(editor.app) as client:
        body = await _open(client)

    assert 'id="section-identity"' in body
    assert 'id="section-context"' in body
    assert 'id="section-candidates"' in body
    assert 'id="section-rules"' in body
    assert "Identity and cases" in body
    assert "Contexts and credentials" in body
    assert "Candidates" in body
    assert "Cost and rules" in body
    assert 'name="case_0_prompt"' in body
    assert "What is 6 times 7?" in body
    assert 'name="candidate_1_model"' in body
    assert 'name="price_0_input"' in body
    assert _digest(body) == hashlib.sha256(
        path.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    assert "data-estimate-cells" in body
    assert "estimated medians" in body
    assert "Step 1 starts from one case and one candidate" not in body
    assert 'class="spec-graph' in body
    assert "Spec graph" in body
    assert "/specs/smoke/edit" in body
    assert 'id="spec-name"' in body
    assert re.search(r'<input[^>]*id="spec-name"[^>]*readonly', body)
    assert "Renaming happens in the YAML editor" in body
    assert 'name="name"' not in body
    assert 'name="action" value="save"' in body
    assert 'name="action" value="save-run"' in body


async def test_unknown_and_unreadable_specs_use_the_404_page(
    editor: Console,
) -> None:
    """A missing, badly named or unparseable file is a 404 on both verbs."""
    _write(editor, "name: [unterminated", name="broken")
    async with _client(editor.app) as client:
        for url in (
            "/comparisons/missing/edit",
            "/comparisons/Bad-Name/edit",
            "/comparisons/broken/edit",
        ):
            assert (await client.get(url)).status_code == 404, url
            assert (
                await client.post(url, data={"digest": "0" * 64, "action": "save"})
            ).status_code == 404, url


async def test_a_spec_named_new_uses_the_structured_form(editor: Console) -> None:
    """A spec whose stem is new is not shadowed by the wizard entry route."""
    _write(editor, _yaml(), name="new")
    async with _client(editor.app) as client:
        page = await client.get("/comparisons/new/edit")
        assert page.status_code == 200
        assert "Identity and cases" in page.text


async def test_an_invalid_name_is_shown_beside_the_readonly_field(
    editor: Console,
) -> None:
    """A name the form cannot fix still names its error and the YAML escape."""
    spec = base_spec()
    spec["name"] = "Bad Name"
    path = _write(editor, _yaml(spec))
    before = path.read_text(encoding="utf-8")
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        form["action"] = "save"
        response = await client.post("/comparisons/smoke/edit", data=form)

    assert response.status_code == 400
    assert 'id="spec-name-error"' in response.text
    assert "YAML editor" in response.text
    assert path.read_text(encoding="utf-8") == before


async def test_save_writes_the_whole_spec_and_ignores_a_posted_name(
    editor: Console,
) -> None:
    """Save rewrites the file, keeps its stem and reports the save on the list."""
    original = yaml.safe_load(_yaml())
    path = _write(editor, _yaml())
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        form["description"] = "Edited by the form."
        form["case_0_expected"] = "43"
        form["name"] = "renamed"
        form["action"] = "save"
        saved = await client.post("/comparisons/smoke/edit", data=form)

        assert saved.status_code == 303
        assert saved.headers["location"] == "/specs?saved=smoke"
        assert not (path.parent / "renamed.yaml").exists()
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert document["name"] == "test-spec"
        assert document["description"] == "Edited by the form."
        assert document["source"]["options"]["cases"][0]["expected"] == {
            "answer": "43"
        }
        assert document["prompts"] == original["prompts"]
        assert [row["name"] for row in document["candidates"]] == [
            "stub-concise",
            "stub-explained",
        ]
        assert document["prices"]["stub-1"] == {"input": 0.15, "output": 0.6}

        listing = await client.get("/specs?saved=smoke")
        assert "Saved smoke.yaml." in listing.text
        assert 'class="status status-ok"' in listing.text


async def test_save_and_run_lands_on_the_launch_page_it_reads(
    editor: Console,
) -> None:
    """The redirect names the query parameter the launch route actually reads."""
    path = _write(editor, _yaml())
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        form["action"] = "save-run"
        saved = await client.post("/comparisons/smoke/edit", data=form)

        assert saved.status_code == 303
        assert saved.headers["location"] == "/launch?spec=smoke"
        launch = await client.get(saved.headers["location"])
        assert launch.status_code == 200
        assert '<option value="smoke.yaml" selected>' in launch.text
        assert "test-spec" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("action", ["save", "save-run"])
async def test_stale_digest_refuses_the_write(
    editor: Console, action: str
) -> None:
    """A file changed since load is never overwritten, by either save button."""
    path = _write(editor, _yaml())
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        newer = _yaml(base_spec() | {"description": "newer on disk"})
        _write(editor, newer)
        form["description"] = "typed but not written"
        form["action"] = action
        response = await client.post("/comparisons/smoke/edit", data=form)

        assert response.status_code == 409
        assert path.read_text(encoding="utf-8") == newer
        assert "changed on disk since you opened it" in response.text
        assert "Nothing was saved" in response.text
        assert "typed but not written" in response.text
        assert re.search(
            r'<button[^>]*name="action" value="save"[^>]*disabled', response.text
        )
        assert re.search(
            r'<button[^>]*name="action" value="save-run"[^>]*disabled',
            response.text,
        )

        again = _form(response.text)
        again["action"] = action
        retried = await client.post("/comparisons/smoke/edit", data=again)
        assert retried.status_code == 409
        assert path.read_text(encoding="utf-8") == newer


async def test_missing_digest_is_a_conflict(editor: Console) -> None:
    """A post without the digest cannot prove what it read, so it is refused."""
    path = _write(editor, _yaml())
    before = path.read_text(encoding="utf-8")
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        form.pop("digest")
        form["action"] = "save"
        response = await client.post("/comparisons/smoke/edit", data=form)

    assert response.status_code == 409
    assert path.read_text(encoding="utf-8") == before


async def test_a_vanished_file_is_a_404(editor: Console) -> None:
    """A spec deleted after load is not recreated by a post."""
    path = _write(editor, _yaml())
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        path.unlink()
        form["action"] = "save"
        assert (
            await client.post("/comparisons/smoke/edit", data=form)
        ).status_code == 404
        assert (await client.get("/comparisons/smoke/edit")).status_code == 404


async def test_validation_errors_rerender_with_typed_values(
    editor: Console,
) -> None:
    """A bad budget is a 400 with the field named, the text kept and no write."""
    path = _write(editor, _yaml())
    before = path.read_text(encoding="utf-8")
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        form["budget_usd"] = "-1"
        form["description"] = "Typed but invalid."
        form["action"] = "save"
        response = await client.post("/comparisons/smoke/edit", data=form)

        assert response.status_code == 400
        assert 'id="budget_usd-error"' in response.text
        assert "Fix:" in response.text
        assert "Typed but invalid." in response.text
        assert path.read_text(encoding="utf-8") == before
        assert _digest(response.text) == hashlib.sha256(
            before.encode("utf-8")
        ).hexdigest()

        retry = _form(response.text)
        retry["budget_usd"] = "2.00"
        retry["action"] = "save"
        saved = await client.post("/comparisons/smoke/edit", data=retry)
        assert saved.status_code == 303
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["budget_usd"] == 2.0


async def test_duplicate_prompt_names_are_reported(editor: Console) -> None:
    """A step 2 conflict is collected on the single page as a general error."""
    path = _write(editor, _yaml())
    before = path.read_text(encoding="utf-8")
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        form["prompt_1_name"] = form["prompt_0_name"]
        form["action"] = "save"
        response = await client.post("/comparisons/smoke/edit", data=form)

    assert response.status_code == 400
    assert "declared twice" in response.text
    assert path.read_text(encoding="utf-8") == before


async def test_candidate_errors_are_reported_on_the_single_page(
    editor: Console,
) -> None:
    """A step 3 conflict names its own field on the single page."""
    _write(editor, _yaml())
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        form["candidate_1_prompt"] = "missing"
        form["action"] = "save"
        response = await client.post("/comparisons/smoke/edit", data=form)

    assert response.status_code == 400
    assert 'id="candidate_1_prompt-error"' in response.text
    assert "Fix:" in response.text


async def test_add_and_remove_rows_render_without_writing(editor: Console) -> None:
    """The no-JS row buttons re-render the page and leave the file alone."""
    path = _write(editor, _yaml())
    before = path.read_text(encoding="utf-8")
    async with _client(editor.app) as client:
        form = _form(await _open(client))
        form["action"] = "add:case"
        added = await client.post("/comparisons/smoke/edit", data=form)
        assert added.status_code == 200
        assert 'name="case_1_case_id"' in added.text
        assert path.read_text(encoding="utf-8") == before

        removed_form = _form(added.text)
        removed_form["action"] = "remove:case:1"
        removed = await client.post("/comparisons/smoke/edit", data=removed_form)
        assert removed.status_code == 200
        assert 'name="case_1_case_id"' not in removed.text
        assert path.read_text(encoding="utf-8") == before
