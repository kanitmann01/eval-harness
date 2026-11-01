"""HTTP tests for the four step comparison wizard.

Every test drives the application through httpx; the hidden YAML draft is
scraped from the rendered form the same way a browser would carry it, so the
wizard is exercised as a stateless set of form posts.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from evalharness.api.app import create_app
from evalharness.api.spec_views import check_document
from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.test_api_console import Console, _client

INLINE_SOURCE = "evalharness.adapters.sources.inline:build"
STUB_CANDIDATE = "evalharness.adapters.candidates.stub:build"
EXACT_MATCH = "evalharness.adapters.metrics.exact_match:build"


@pytest.fixture
def wizard(evalh_home: Path, workdir: Path) -> Iterator[Console]:
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


def _draft(page: str) -> str:
    """Return the YAML carried by a page's hidden draft field."""
    match = re.search(r'name="draft" value="([^"]*)"', page)
    assert match is not None, "the page has no hidden draft field"
    return html.unescape(match.group(1))


def _shell(page: str, step: int) -> None:
    """Assert the step strip, the rail and the current step marker."""
    assert re.search(rf'data-step="{step}"[^>]*aria-current="step"', page), step
    assert page.count('aria-current="step"') == 1
    assert "spec-graph--rail" in page
    assert 'viewBox="0 0 320.0 ' in page
    assert "data-estimate-cells" in page


def _step_one(draft: str = "", **overrides: str) -> dict[str, str]:
    """Return a valid step 1 form posting, from an empty or carried draft."""
    form = {
        "form_step": "1",
        "draft": draft,
        "name": "math-check",
        "description": "Six times seven.",
        "seed": "7",
        "source_adapter": INLINE_SOURCE,
        "case_0_case_id": "case-1",
        "case_0_prompt": "What is 6 times 7?",
        "case_0_expected": "42",
        "case_0_rubric": "",
        "case_0_tier": "1",
        "case_1_case_id": "",
        "case_1_prompt": "",
        "case_1_expected": "",
        "case_1_rubric": "",
        "case_1_tier": "",
        "action": "continue",
    }
    form.update(overrides)
    return form


def _step_two(draft: str, **overrides: str) -> dict[str, str]:
    """Return a valid step 2 form posting that adds one prompt and one context."""
    form = {
        "form_step": "2",
        "draft": draft,
        "prompt_0_name": "concise",
        "prompt_0_text": "Be brief.",
        "prompt_1_name": "",
        "prompt_1_text": "",
        "context_0_name": "policy",
        "context_0_text": "Refunds within 30 days.\n\nNo cash refunds.",
        "context_1_name": "",
        "context_1_text": "",
        "credential_0_name": "",
        "credential_0_provider": "none",
        "action": "continue",
    }
    form.update(overrides)
    return form


def _step_three(draft: str, **overrides: str) -> dict[str, str]:
    """Return a valid step 3 form posting with two candidates."""
    form = {
        "form_step": "3",
        "draft": draft,
        "candidate_0_name": "stub-concise",
        "candidate_0_adapter": STUB_CANDIDATE,
        "candidate_0_model": "stub-1",
        "candidate_0_credential": "none",
        "candidate_0_prompt": "concise",
        "candidate_0_context": "none",
        "candidate_1_name": "stub-policy",
        "candidate_1_adapter": STUB_CANDIDATE,
        "candidate_1_model": "stub-1",
        "candidate_1_credential": "none",
        "candidate_1_prompt": "none",
        "candidate_1_context": "policy",
        "candidate_2_name": "",
        "candidate_2_adapter": STUB_CANDIDATE,
        "candidate_2_model": "",
        "candidate_2_credential": "none",
        "candidate_2_prompt": "none",
        "candidate_2_context": "none",
        "replicas": "1",
        "concurrency": "2",
        "action": "continue",
    }
    form.update(overrides)
    return form


def _step_four(draft: str, **overrides: str) -> dict[str, str]:
    """Return a valid step 4 form posting with a judge, two gates and a price."""
    form = {
        "form_step": "4",
        "draft": draft,
        "budget_usd": "2.00",
        "metric_0_key": "exact_match",
        "metric_0_adapter": EXACT_MATCH,
        "metric_1_key": "",
        "metric_1_adapter": EXACT_MATCH,
        "judge_0_metric": "quality",
        "judge_0_model": "",
        "judge_0_rubric": "Mentions 42\nIs brief",
        "judge_1_metric": "",
        "judge_1_model": "",
        "judge_1_rubric": "",
        "gate_0_metric": "quality",
        "gate_0_op": "at least",
        "gate_0_threshold": "1.0",
        "gate_1_metric": "exact_match",
        "gate_1_op": "at most",
        "gate_1_threshold": "1.0",
        "gate_2_metric": "",
        "gate_2_op": "at least",
        "gate_2_threshold": "",
        "price_0_model": "stub-1",
        "price_0_input": "0.15",
        "price_0_output": "0.60",
        "price_1_model": "",
        "price_1_input": "",
        "price_1_output": "",
        "action": "continue",
    }
    form.update(overrides)
    return form


async def _reach_step_four(client: Any) -> str:
    """Drive steps 1 to 3 and return the draft the step 4 form carries."""
    page = await client.post("/comparisons/new/1", data=_step_one())
    assert page.status_code == 200, page.text
    draft = _draft(page.text)
    page = await client.post("/comparisons/new/2", data=_step_two(draft))
    assert page.status_code == 200, page.text
    draft = _draft(page.text)
    page = await client.post("/comparisons/new/3", data=_step_three(draft))
    assert page.status_code == 200, page.text
    return _draft(page.text)


async def test_get_steps_and_navigation(wizard: Console) -> None:
    """Every entry point redirects or 404s, and the nav advertises the wizard."""
    async with _client(wizard.app) as client:
        entry = await client.get("/comparisons/new")
        assert entry.status_code == 303
        assert entry.headers["location"] == "/comparisons/new/1"

        page = await client.get("/comparisons/new/1")
        assert page.status_code == 200
        _shell(page.text, 1)
        assert ">Runs</a>" in page.text
        assert ">Specs</a>" in page.text
        assert ">Credentials</a>" in page.text
        assert ">New comparison</a>" in page.text
        assert "http://console.test/comparisons/new" in page.text
        assert ">Compare</a>" in page.text
        assert ">Launch</a>" in page.text
        assert 'class="btn btn-secondary btn-add"' in page.text
        assert '<span class="btn-add-glyph" aria-hidden="true">+</span>' in page.text
        assert 'name="action" value="add:case"' in page.text
        assert "Add case" in page.text
        assert "Add another" not in page.text

        for step in (2, 3, 4):
            fresh = await client.get(f"/comparisons/new/{step}")
            assert fresh.status_code == 303
            assert fresh.headers["location"] == "/comparisons/new/1"

        for bad in ("0", "5", "abc"):
            missing = await client.get(f"/comparisons/new/{bad}")
            assert missing.status_code == 404
        missing_post = await client.post(
            "/comparisons/new/5", data={"form_step": "5", "draft": ""}
        )
        assert missing_post.status_code == 404


async def test_the_first_step_opens_with_an_empty_seed(wizard: Console) -> None:
    """A fresh draft does not pin a seed the user never chose."""
    async with _client(wizard.app) as client:
        page = await client.get("/comparisons/new/1")
        assert page.status_code == 200
        draft = yaml.safe_load(_draft(page.text))
        assert "seed" not in draft
        assert re.search(r'<input[^>]*name="seed"[^>]*value=""', page.text)
        assert "Leave it empty for no seed." in page.text
        assert "1 case" in page.text
        assert "1 cases" not in page.text


async def test_the_step_strip_explains_every_step(wizard: Console) -> None:
    """Each step in the strip carries a visible one-line explanation."""
    descriptions = (
        "Name the comparison, pick the case source and write one row per case.",
        "Prompts, reference texts and credential declarations the candidates use.",
        "Each candidate is an adapter, a model and the context it runs with.",
        "The budget cap, the metrics, the judges, the gates and the prices.",
    )
    async with _client(wizard.app) as client:
        page = await client.get("/comparisons/new/1")
        assert page.status_code == 200
        assert page.text.count('class="field-hint step-help"') == 4
        for description in descriptions:
            assert description in page.text


async def test_the_estimate_rail_calls_its_numbers_estimates(wizard: Console) -> None:
    """The rail says the medians and the cost are estimates, not a quote."""
    async with _client(wizard.app) as client:
        page = await client.get("/comparisons/new/1")
        assert page.status_code == 200
        text = page.text
        assert "estimated medians" in text
        assert "fallback medians" not in text
        assert "Median tokens / cell" in text
        assert "Step 1 starts from one case and one candidate" in text
        assert "this estimate is for one cell" in text
        assert "not a quote" in text
        assert "%.6f" not in text
        assert "%.4f" not in text
        match = re.search(r'data-estimate-cost="([^"]*)"', text)
        assert match is not None
        assert match.group(1).startswith("$")
        assert match.group(1)[1:].replace(".", "", 1).isdigit()


async def test_the_buttons_name_the_next_and_previous_steps(wizard: Console) -> None:
    """Continue and Back carry the destination step title, and adders are short."""
    async with _client(wizard.app) as client:
        page = await client.post("/comparisons/new/1", data=_step_one())
        assert page.status_code == 200
        _shell(page.text, 2)
        assert "Continue to Choose candidates" in page.text
        assert "Back to What to vary" in page.text
        assert "Add prompt</button>" in page.text
        assert "Add context</button>" in page.text
        assert "Add credential</button>" in page.text
        assert "Add another" not in page.text


async def test_the_happy_path_writes_one_spec(wizard: Console) -> None:
    """A full run of the four steps writes one file and redirects to the list."""
    specs = wizard.settings.specs_dir
    async with _client(wizard.app) as client:
        page = await client.post("/comparisons/new/1", data=_step_one())
        assert page.status_code == 200
        _shell(page.text, 2)
        assert "Add context" in page.text
        assert "What to vary" in page.text
        draft_two = _draft(page.text)
        parsed = yaml.safe_load(draft_two)
        assert parsed["name"] == "math-check"
        assert parsed["source"]["options"]["cases"][0]["case_id"] == "case-1"
        assert "stub-1" in parsed["prices"]

        page = await client.post("/comparisons/new/2", data=_step_two(draft_two))
        assert page.status_code == 200
        _shell(page.text, 3)
        assert "Choose candidates" in page.text
        assert 'data-estimate-cells="1"' in page.text
        draft_three = _draft(page.text)
        parsed = yaml.safe_load(draft_three)
        assert parsed["prompts"] == {"concise": "Be brief."}
        assert len(parsed["contexts"]["policy"]) == 2

        page = await client.post("/comparisons/new/3", data=_step_three(draft_three))
        assert page.status_code == 200
        _shell(page.text, 4)
        assert "Create comparison" in page.text
        assert 'data-estimate-cells="2"' in page.text
        assert "data-estimate-cost" in page.text
        draft_four = _draft(page.text)
        assert not specs.exists() or not list(specs.glob("*.yaml"))

        page = await client.post("/comparisons/new/4", data=_step_four(draft_four))
        assert page.status_code == 303
        assert page.headers["location"] == "/comparisons?saved=math-check"

        written = sorted(specs.glob("*.yaml"))
        assert [path.name for path in written] == ["math-check.yaml"]
        text = written[0].read_text(encoding="utf-8")
        document = yaml.safe_load(text)
        assert document["budget_usd"] == 2.0
        assert document["seed"] == 7
        assert {metric["key"] for metric in document["metrics"]} == {
            "exact_match",
            "quality",
        }
        assert document["judges"][0]["name"] == "quality-judge"
        assert {gate["metric"] for gate in document["gates"]} == {
            "quality",
            "exact_match",
        }
        assert document["prices"]["stub-1"] == {"input": 0.15, "output": 0.6}
        check = check_document(text, wizard.repository)
        assert check.valid, str(check.errors[0]) if check.errors else ""

        listed = await client.get(page.headers["location"])
        assert listed.status_code == 303
        assert listed.headers["location"] == "/specs?saved=math-check"


async def test_step_one_reports_each_bad_field(wizard: Console) -> None:
    """A 400 keeps the submitted case text and names every bad field."""
    async with _client(wizard.app) as client:
        form = _step_one()
        form.update(
            {
                "name": "",
                "source_adapter": "bogus.source:build",
                "case_0_case_id": "",
                "case_0_prompt": "",
                "case_0_tier": "not-a-number",
            }
        )
        page = await client.post("/comparisons/new/1", data=form)
        assert page.status_code == 400
        assert 'id="name-error"' in page.text
        assert "the name is required" in page.text
        assert 'id="source_adapter-error"' in page.text
        assert "not a registered source" in page.text
        assert 'id="case_0_case_id-error"' in page.text
        assert 'id="case_0_prompt-error"' in page.text
        assert 'id="case_0_tier-error"' in page.text
        assert "Six times seven." in page.text
        assert 'value="42"' in page.text

        bad_slug = _step_one(name="Bad Name")
        page = await client.post("/comparisons/new/1", data=bad_slug)
        assert page.status_code == 400
        assert 'id="name-error"' in page.text
        assert "lower-case slug" in page.text


async def test_step_two_reports_each_bad_field(wizard: Console) -> None:
    """Prompts, contexts and credentials each report their own row errors."""
    async with _client(wizard.app) as client:
        page = await client.post("/comparisons/new/1", data=_step_one())
        draft = _draft(page.text)
        form = _step_two(draft)
        form.update(
            {
                "prompt_0_name": "",
                "prompt_1_name": "dup",
                "prompt_1_text": "First.",
                "prompt_2_name": "dup",
                "prompt_2_text": "Second.",
                "context_0_name": "policy",
                "context_0_text": "",
                "context_1_name": "",
                "context_1_text": "Orphan context.",
                "credential_0_name": "openai",
                "credential_0_provider": "bogus",
                "credential_1_name": "",
                "credential_1_provider": "openai",
            }
        )
        page = await client.post("/comparisons/new/2", data=form)
        assert page.status_code == 400
        assert 'id="prompt_0_name-error"' in page.text
        assert "Be brief." in page.text
        assert "declared twice" in page.text
        assert 'id="context_1_name-error"' in page.text
        assert "Orphan context." in page.text
        assert 'id="credential_0_provider-error"' in page.text
        assert 'id="credential_1_name-error"' in page.text


async def test_step_three_reports_each_bad_field(wizard: Console) -> None:
    """Candidates and the replica counts report inline errors."""
    async with _client(wizard.app) as client:
        page = await client.post("/comparisons/new/1", data=_step_one())
        draft_one = _draft(page.text)
        page = await client.post("/comparisons/new/2", data=_step_two(draft_one))
        draft_two = _draft(page.text)
        form = _step_three(
            draft_two,
            candidate_0_model="",
            candidate_1_name="",
            candidate_1_adapter="bogus.source:build",
            candidate_1_credential="ghost",
            candidate_1_prompt="ghost",
            candidate_1_context="ghost",
            replicas="0",
            concurrency="0",
        )
        page = await client.post("/comparisons/new/3", data=form)
        assert page.status_code == 400
        assert 'id="candidate_0_model-error"' in page.text
        assert "stub-concise" in page.text
        assert 'id="candidate_1_name-error"' in page.text
        assert 'id="candidate_1_credential-error"' in page.text
        assert 'id="candidate_1_prompt-error"' in page.text
        assert 'id="candidate_1_context-error"' in page.text
        assert 'id="replicas-error"' in page.text
        assert 'id="concurrency-error"' in page.text

        adapter = _step_three(
            draft_two, candidate_1_adapter="bogus.source:build"
        )
        page = await client.post("/comparisons/new/3", data=adapter)
        assert page.status_code == 400
        assert 'id="candidate_1_adapter-error"' in page.text

        prompt = _step_three(draft_two, candidate_1_prompt="ghost")
        page = await client.post("/comparisons/new/3", data=prompt)
        assert page.status_code == 400
        assert 'id="candidate_1_prompt-error"' in page.text

        context = _step_three(draft_two, candidate_1_context="ghost")
        page = await client.post("/comparisons/new/3", data=context)
        assert page.status_code == 400
        assert 'id="candidate_1_context-error"' in page.text

        unpriced = _step_three(draft_two, candidate_0_model="mystery-model")
        page = await client.post("/comparisons/new/3", data=unpriced)
        assert page.status_code == 400
        assert "mystery-model" in page.text


async def test_step_four_reports_validator_errors(wizard: Console) -> None:
    """Money, gate and price errors map back to their row fields."""
    async with _client(wizard.app) as client:
        draft_four = await _reach_step_four(client)

        page = await client.post(
            "/comparisons/new/4",
            data=_step_four(draft_four, budget_usd="-1", gate_0_threshold=""),
        )
        assert page.status_code == 400
        assert 'id="budget_usd-error"' in page.text
        assert "exact_match" in page.text

        page = await client.post(
            "/comparisons/new/4",
            data=_step_four(draft_four, gate_0_threshold="abc", budget_usd=""),
        )
        assert page.status_code == 400
        assert 'id="gate_0_threshold-error"' in page.text

        duplicated = _step_four(draft_four)
        duplicated["metric_1_key"] = "exact_match"
        page = await client.post("/comparisons/new/4", data=duplicated)
        assert page.status_code == 400
        assert "duplicate metric name" in page.text

        page = await client.post(
            "/comparisons/new/4", data=_step_four(draft_four, price_0_input="abc")
        )
        assert page.status_code == 400
        assert 'id="price_0_input-error"' in page.text

        page = await client.post(
            "/comparisons/new/4",
            data=_step_four(
                draft_four,
                metric_0_adapter="evalharness.adapters.metrics.missing:build",
            ),
        )
        assert page.status_code == 400
        assert 'id="metric_0_adapter-error"' in page.text


async def test_the_draft_round_trips_keys_no_step_owns(wizard: Console) -> None:
    """A sink and candidate options survive every step untouched."""
    async with _client(wizard.app) as client:
        page = await client.post("/comparisons/new/1", data=_step_one())
        document = yaml.safe_load(_draft(page.text))
        document["sink"] = {
            "adapter": "evalharness.adapters.sinks.json_sink:build",
            "options": {"path": "out.jsonl"},
        }
        document["candidates"][0]["options"] = {"temperature": 0.2}
        draft = yaml.safe_dump(document, sort_keys=False)

        page = await client.post("/comparisons/new/2", data=_step_two(draft))
        assert page.status_code == 200
        carried = yaml.safe_load(_draft(page.text))
        assert carried["sink"]["options"]["path"] == "out.jsonl"
        assert carried["candidates"][0]["options"]["temperature"] == 0.2
        draft = _draft(page.text)

        page = await client.post("/comparisons/new/3", data=_step_three(draft))
        assert page.status_code == 200
        draft = _draft(page.text)
        page = await client.post("/comparisons/new/4", data=_step_four(draft))
        assert page.status_code == 303

        written = wizard.settings.specs_dir / "math-check.yaml"
        saved = yaml.safe_load(written.read_text(encoding="utf-8"))
        assert saved["sink"]["options"]["path"] == "out.jsonl"
        assert saved["candidates"][0]["options"]["temperature"] == 0.2
        assert saved["seed"] == 7
        assert saved["name"] == "math-check"


async def test_add_and_remove_rows_without_javascript(wizard: Console) -> None:
    """The Add and Remove submit buttons change the draft server side."""
    async with _client(wizard.app) as client:
        page = await client.post(
            "/comparisons/new/1", data=_step_one(action="add:case")
        )
        assert page.status_code == 200
        assert 'name="case_1_case_id"' in page.text
        assert 'value="remove:case:0"' in page.text
        draft = _draft(page.text)
        assert len(yaml.safe_load(draft)["source"]["options"]["cases"]) == 1

        form = _step_one(draft)
        form.update(
            {
                "case_1_case_id": "case-2",
                "case_1_prompt": "What is 7 times 8?",
                "case_1_expected": "56",
                "case_1_tier": "2",
            }
        )
        page = await client.post("/comparisons/new/1", data=form)
        assert page.status_code == 200
        draft = _draft(page.text)
        assert len(yaml.safe_load(draft)["source"]["options"]["cases"]) == 2

        removing = _step_one(draft, action="remove:case:0")
        removing.update(
            {
                "case_1_case_id": "case-2",
                "case_1_prompt": "What is 7 times 8?",
                "case_1_expected": "56",
                "case_1_tier": "2",
            }
        )
        page = await client.post("/comparisons/new/1", data=removing)
        assert page.status_code == 200
        cases = yaml.safe_load(_draft(page.text))["source"]["options"]["cases"]
        assert [case["case_id"] for case in cases] == ["case-2"]

        page = await client.post(
            "/comparisons/new/2", data=_step_two(_draft(page.text), action="add:prompt")
        )
        assert page.status_code == 200
        assert 'name="prompt_1_name"' in page.text
        assert 'value="remove:prompt:0"' in page.text
        removing = _step_two(_draft(page.text), action="remove:prompt:0")
        page = await client.post("/comparisons/new/2", data=removing)
        assert page.status_code == 200
        assert yaml.safe_load(_draft(page.text))["prompts"] == {}

        none_expected = _step_one(_draft(page.text), case_0_expected="")
        page = await client.post("/comparisons/new/1", data=none_expected)
        assert page.status_code == 200
        cases = yaml.safe_load(_draft(page.text))["source"]["options"]["cases"]
        assert cases[0]["expected"] is None


async def test_back_and_a_completed_step_keep_the_draft(wizard: Console) -> None:
    """Back posts to the previous path and a strip button jumps to a done step."""
    async with _client(wizard.app) as client:
        draft_four = await _reach_step_four(client)

        back = await client.post(
            "/comparisons/new/3", data=_step_four(draft_four, action="back")
        )
        assert back.status_code == 200
        assert "Choose candidates" in back.text
        carried = yaml.safe_load(_draft(back.text))
        assert carried["budget_usd"] == 2.0
        assert len(carried["candidates"]) == 2

        jumped = await client.post(
            "/comparisons/new/4", data=_step_four(draft_four, action="goto:1")
        )
        assert jumped.status_code == 200
        assert "What to vary" in jumped.text
        assert 'value="math-check"' in jumped.text
        assert len(yaml.safe_load(_draft(jumped.text))["candidates"]) == 2


async def test_broken_drafts_and_missing_state(wizard: Console) -> None:
    """Bad YAML is a 400, a non-mapping is a 400, and no draft means step 1."""
    async with _client(wizard.app) as client:
        broken = await client.post(
            "/comparisons/new/1",
            data={"form_step": "1", "draft": "{{{", "action": "continue"},
        )
        assert broken.status_code == 400
        assert "did not parse" in broken.text

        listed = await client.post(
            "/comparisons/new/2",
            data={"form_step": "2", "draft": "- a list", "action": "continue"},
        )
        assert listed.status_code == 400
        assert "must be a YAML mapping" in listed.text

        missing = await client.post(
            "/comparisons/new/3",
            data={"form_step": "3", "draft": "", "action": "continue"},
        )
        assert missing.status_code == 303
        assert missing.headers["location"] == "/comparisons/new/1"


async def test_the_estimate_follows_the_candidate_count(wizard: Console) -> None:
    """The rail estimate changes when a second candidate joins the draft."""
    async with _client(wizard.app) as client:
        page = await client.post("/comparisons/new/1", data=_step_one())
        draft = _draft(page.text)
        page = await client.post("/comparisons/new/2", data=_step_two(draft))
        draft = _draft(page.text)
        one = await client.post("/comparisons/new/3", data=_step_three(draft))
        assert 'data-estimate-cells="2"' in one.text
        blank = await client.post(
            "/comparisons/new/3", data=_step_three(draft, concurrency="")
        )
        assert blank.status_code == 200
        assert 'data-estimate-cells="2"' in blank.text
        two = await client.post(
            "/comparisons/new/3",
            data=_step_three(
                draft, candidate_2_name="stub-third", candidate_2_model="stub-1"
            ),
        )
        assert two.status_code == 200
        assert 'data-estimate-cells="2"' not in two.text
        assert 'data-estimate-cells="3"' in two.text
