"""HTTP API seam tests for the compare view.

Every test seeds real runs through `evalharness.cli.main` and reads them through
`httpx.AsyncClient` against the ASGI application. The console fixture, `_seed`
and `_client` are reused from the read-only console tests.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.api.compare_views import decision_view
from evalharness.core.compare import (
    BETTER,
    UNDEFINED,
    WORSE,
    CompareSide,
    Comparison,
    JudgeIdentity,
    MetricComparison,
    MetricSide,
    PriceTable,
)
from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import EXACT_MATCH, base_spec, write_spec

CITATION_SUPPORT = "evalharness.adapters.metrics.citation_support:build"
SCRIPTED_JUDGE = "evalharness.adapters.judges.scripted:build"
from tests.test_api_console import (  # noqa: F401 - console is a fixture
    Console,
    _client,
    _seed,
    console,
)


def _exact_row(data: dict[str, Any]) -> dict[str, Any]:
    """Return the exact_match row from a comparison response."""
    return next(item for item in data["metrics"] if item["metric"] == "exact_match")


async def test_two_runs_compare_with_deltas_intervals_and_cost(
    console: Console,
) -> None:
    run_a = console.runs["alpha"]
    run_b = console.runs["beta"]
    async with _client(console.app) as client:
        page = await client.get("/compare", params={"a": run_a, "b": run_b})
        assert page.status_code == 200
        assert "alpha-spec" in page.text and "beta-spec" in page.text
        assert "exact_match" in page.text
        assert "Cost per correct answer" in page.text
        assert "95% interval" in page.text
        assert "A is better on exact_match" in page.text
        assert "Swap A and B" in page.text
        assert "Spec graphs" in page.text
        assert page.text.count('class="spec-graph spec-graph--pair"') == 2
        assert 'class="graph-pair"' in page.text
        assert "Side A · baseline: all candidates" in page.text
        assert "Side B · candidate: all candidates" in page.text
        data = (
            await client.get("/api/compare", params={"a": run_a, "b": run_b})
        ).json()
        assert f'title="{data["a"]["price_table"]["id"]}"' in page.text
        assert f'title="{data["b"]["price_table"]["id"]}"' in page.text
        assert "price table fingerprint" in page.text
        assert "A cell is one case" in page.text
        assert "a cell can fail a check and still run ok" in page.text
        assert 'viewBox="0 0 640.0 ' in page.text
        # Both sides keep their box labels and use the short headings.
        assert 'class="sg-label"' in page.text
        assert ">Prompts</text>" in page.text and ">Gates</text>" in page.text
        assert ">Prompts and contexts</text>" not in page.text
        assert ">Gates and verdict</text>" not in page.text
        assert 'id="graph-a-desc"' in page.text and 'id="graph-b-desc"' in page.text
        assert "<colgroup>" in page.text
        assert 'class="col-judge"' in page.text
        assert 'class="num col-lg cost-cell" data-label="Cost / correct" title=' in (
            page.text
        )
        assert 'class="mono metric-name" title="exact_match"' in page.text
        # One judge on both sides reads as two lines, not four.
        assert page.text.count('class="judge-name"') == 1
        assert page.text.count('class="judge-detail"') == 1
        assert 'class="judge-detail" title=' in page.text

        response = await client.get(
            "/api/compare", params={"a": run_a, "b": run_b}
        )
        assert response.status_code == 200
        data = response.json()

        assert data["mode"] == "runs"
        assert data["a"]["run_id"] == run_a
        assert data["a"]["candidate"] is None
        assert data["a"]["n_cells"] == 4
        assert data["a"]["n_checks"] == 4
        assert data["a"]["n_passed"] == 4
        assert data["b"]["n_cells"] == 1
        assert data["b"]["n_checks"] == 1
        assert data["b"]["n_passed"] == 0

        row = _exact_row(data)
        assert row["a"]["n"] == 4
        assert row["a"]["passed"] == 4
        assert row["a"]["value"] == 1.0
        assert row["b"]["n"] == 1
        assert row["b"]["passed"] == 0
        assert row["b"]["value"] == 0.0
        assert row["delta"] == pytest.approx(-1.0)
        assert row["interval_low"] == pytest.approx(-1.0)
        assert row["interval_high"] == pytest.approx(-0.06749, abs=5e-4)
        assert row["verdict"] == "worse"
        assert row["comparable"] is True
        assert row["different_judge"] is False

        # Cost per correct answer is total cost divided by passed checks.
        cost_a = sum(
            cell.cost_usd for cell in console.repository.list_cells(run_a)
        )
        assert data["a"]["cost_usd"] == pytest.approx(cost_a)
        assert data["a"]["cost_per_correct"] == pytest.approx(cost_a / 4)
        assert data["b"]["cost_per_correct"] is None
        assert row["a"]["cost_per_correct"] == pytest.approx(cost_a / 4)
        assert row["b"]["cost_per_correct"] is None

        # The price table is named by an identity over its content.
        assert data["same_price_table"] is True
        assert data["a"]["price_table"]["id"] == data["b"]["price_table"]["id"]
        assert "stub-1" in data["a"]["price_table"]["prices"]

        # The one-sided case is listed, not dropped and not a tie.
        one_sided = {item["case_id"]: item for item in data["one_sided_cases"]}
        assert set(one_sided) == {"case-2"}
        entry = one_sided["case-2"]
        assert entry["side"] == "a"
        assert entry["count"] == 2
        assert all(
            cell["url"].startswith(f"/runs/{run_a}/cells/")
            for cell in entry["cells"]
        )
        assert "case-2" in page.text
        assert "Cases on only one side" in page.text

        # HTML and JSON agree on the values they both show.
        assert f"{row['a']['value'] * 100:.1f}%" in page.text
        assert f"{row['b']['value'] * 100:.1f}%" in page.text
        assert f"{row['delta'] * 100:+.1f} pp" in page.text


async def test_reverse_comparison_calls_b_better_and_lists_b_only_cases(
    console: Console,
) -> None:
    run_a = console.runs["beta"]
    run_b = console.runs["alpha"]
    async with _client(console.app) as client:
        data = (
            await client.get(
                "/api/compare", params={"a": run_a, "b": run_b}
            )
        ).json()
        page = await client.get("/compare", params={"a": run_a, "b": run_b})
    row = _exact_row(data)
    assert row["delta"] == pytest.approx(1.0)
    assert row["interval_low"] == pytest.approx(0.06749, abs=5e-4)
    assert row["interval_high"] == pytest.approx(1.0)
    assert row["verdict"] == "better"
    assert [(item["case_id"], item["side"]) for item in data["one_sided_cases"]] == [
        ("case-2", "b")
    ]
    assert "B better" in page.text
    assert "B is better on exact_match" in page.text


async def test_candidate_mode_compares_two_candidates_in_one_run(
    console: Console,
) -> None:
    run_id = console.runs["alpha"]
    params = {
        "run": run_id,
        "candidate_a": "stub-concise",
        "candidate_b": "cited-stub",
    }
    async with _client(console.app) as client:
        page = await client.get("/compare", params=params)
        assert page.status_code == 200
        assert "stub-concise" in page.text and "cited-stub" in page.text
        assert "candidate mode" in page.text

        data = (await client.get("/api/compare", params=params)).json()

    assert data["mode"] == "candidates"
    assert data["a"]["candidate"] == "stub-concise"
    assert data["b"]["candidate"] == "cited-stub"
    assert data["a"]["n_cells"] == 2
    assert data["b"]["n_cells"] == 2
    assert data["one_sided_cases"] == []
    row = _exact_row(data)
    assert row["a"]["n"] == 2 and row["b"]["n"] == 2
    assert row["delta"] == pytest.approx(0.0)
    assert row["interval_low"] < 0 < row["interval_high"]
    assert row["verdict"] == "inconclusive"
    assert row["a"]["cost_per_correct"] == pytest.approx(
        data["a"]["cost_usd"] / 2
    )


def _ten_cases() -> list[dict[str, Any]]:
    """Return ten cases that all expect the same scripted answer."""
    return [
        {
            "case_id": f"case-{index}",
            "prompt": "What is 6 times 7?",
            "expected": {"answer": "42"},
            "tier": 1,
        }
        for index in range(1, 11)
    ]


def _judged_spec(
    name: str,
    judge_name: str,
    answer: str,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a one-candidate spec whose metric names a declared judge."""
    spec = base_spec()
    spec["name"] = name
    spec["source"]["options"]["cases"] = cases
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["name"] = "judged-stub"
    spec["candidates"][0]["options"] = {"answer": answer}
    spec["metrics"] = [
        {
            "key": "exact_match",
            "adapter": EXACT_MATCH,
            "options": {"judge": judge_name},
        }
    ]
    spec["judges"] = [
        {
            "name": judge_name,
            "adapter": "test:judge",
            "options": {"variant": judge_name},
        }
    ]
    spec["gates"] = []
    return spec


def _citation_spec(
    name: str,
    judge_name: str,
    claims: list[str],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a one-candidate spec scored by a real scripted judge."""
    spec = _judged_spec(name, judge_name, "42", cases)
    spec["candidates"][0]["name"] = "cited"
    spec["candidates"][0]["adapter"] = "test:cited"
    spec["candidates"][0]["options"] = {}
    spec["metrics"] = [
        {
            "key": "citation_support",
            "adapter": CITATION_SUPPORT,
            "options": {"judge": judge_name},
        }
    ]
    spec["judges"] = [
        {
            "name": judge_name,
            "adapter": SCRIPTED_JUDGE,
            "model": "scripted-judge",
            "options": {"claims": claims, "instructions": judge_name},
        }
    ]
    spec["prices"]["scripted-judge"] = {"input": 1.0, "output": 1.0}
    return spec


def _stamp_judge_hash(
    repository: Repository, run_id: str, config_hash: str
) -> None:
    """Write a judge configuration hash onto every stored check of a run."""
    for cell in repository.list_cells(run_id):
        checks = repository.list_checks(run_id, cell.cell_id)
        if checks:
            repository.record_checks(
                run_id,
                cell.cell_id,
                [
                    check.model_copy(update={"judge_config_hash": config_hash})
                    for check in checks
                ],
            )


@pytest.fixture
def judged(
    evalh_home: Any,
    workdir: Any,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[dict[str, Any]]:
    """Seed two ten-case runs scored under two different judge configurations."""
    cases = _ten_cases()
    run_a = _seed(
        capsys,
        write_spec(
            workdir / "judge-a.yaml",
            _judged_spec("judge-a-spec", "judge-a", "wrong", cases),
        ),
    )
    run_b = _seed(
        capsys,
        write_spec(
            workdir / "judge-b.yaml",
            _judged_spec("judge-b-spec", "judge-b", "42", cases),
        ),
    )
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    _stamp_judge_hash(repository, run_a, "check-hash-a")
    _stamp_judge_hash(repository, run_b, "check-hash-b")
    app = create_app(settings=settings, repository=repository)
    yield {
        "app": app,
        "repository": repository,
        "runs": {"a": run_a, "b": run_b},
    }
    database.dispose()


async def test_different_judges_are_flagged_and_never_called_a_win(
    judged: dict[str, Any],
) -> None:
    run_a = judged["runs"]["a"]
    run_b = judged["runs"]["b"]
    async with _client(judged["app"]) as client:
        page = await client.get("/compare", params={"a": run_a, "b": run_b})
        assert page.status_code == 200
        assert "Different judge" in page.text
        assert "not a win" in page.text
        assert "Not comparable" in page.text
        assert "verdict-better" not in page.text
        assert ">A · judge-a<" in page.text
        assert ">B · judge-b<" in page.text
        assert page.text.count('class="judge-name"') == 2

        data = (
            await client.get("/api/compare", params={"a": run_a, "b": run_b})
        ).json()

    row = _exact_row(data)
    assert row["a"]["n"] == 10 and row["a"]["passed"] == 0
    assert row["b"]["n"] == 10 and row["b"]["passed"] == 10
    assert row["delta"] == pytest.approx(1.0)
    # The interval excludes zero, so with one judge this would be a win.
    assert row["interval_low"] > 0.5
    assert row["verdict"] == "inconclusive"
    assert row["comparable"] is False
    assert row["different_judge"] is True
    assert data["cross_judge_metrics"] == ["exact_match"]

    judge_a = row["a"]["judge"]
    judge_b = row["b"]["judge"]
    assert judge_a["name"] == "judge-a"
    assert judge_b["name"] == "judge-b"
    assert judge_a["adapter"] == "test:judge"
    assert judge_a["model"] is None
    assert judge_a["config_hash"] == "check-hash-a"
    assert judge_b["config_hash"] == "check-hash-b"
    assert judge_a["hash_source"] == "checks"


async def test_a_side_with_no_samples_renders_undefined(console: Console) -> None:
    run_a = console.runs["alpha"]
    run_b = console.runs["gamma"]
    async with _client(console.app) as client:
        page = await client.get("/compare", params={"a": run_a, "b": run_b})
        assert page.status_code == 200
        assert "Undefined" in page.text
        assert "undefined" in page.text
        assert "No comparable samples" in page.text

        data = (
            await client.get("/api/compare", params={"a": run_a, "b": run_b})
        ).json()

    row = _exact_row(data)
    assert row["a"]["n"] == 4
    assert row["b"]["n"] == 0
    assert row["b"]["value"] is None
    assert row["b"]["ci_low"] is None
    assert row["b"]["cost_per_correct"] is None
    assert row["delta"] is None
    assert row["interval_low"] is None
    assert row["interval_high"] is None
    assert row["verdict"] == "undefined"
    assert row["comparable"] is False
    assert "contains" in data["one_sided_metrics"]
    assert "exact_match" in data["one_sided_metrics"]
    assert data["comparable_metrics"] == []


async def test_unknown_and_incomplete_comparisons_are_actionable_404s(
    console: Console,
) -> None:
    run_a = console.runs["alpha"]
    async with _client(console.app) as client:
        unknown_page = await client.get(
            "/compare", params={"a": run_a, "b": "missing"}
        )
        assert unknown_page.status_code == 404
        assert "does not exist" in unknown_page.text
        assert "evalh run spec.yaml" in unknown_page.text

        unknown_api = await client.get(
            "/api/compare", params={"a": run_a, "b": "missing"}
        )
        assert unknown_api.status_code == 404
        assert "does not exist" in unknown_api.json()["detail"]

        missing_b = await client.get("/compare", params={"a": run_a})
        assert missing_b.status_code == 404
        assert "add ?b=" in missing_b.text

        self_compare = await client.get("/compare", params={"run": run_a})
        assert self_compare.status_code == 404
        assert "candidate_a" in self_compare.text

        half_candidate = await client.get(
            "/compare", params={"run": run_a, "candidate_a": "stub-concise"}
        )
        assert half_candidate.status_code == 404
        assert "candidate_b" in half_candidate.text

        no_run = await client.get(
            "/api/compare",
            params={"candidate_a": "stub-concise", "candidate_b": "cited-stub"},
        )
        assert no_run.status_code == 404
        assert "needs a run" in no_run.json()["detail"]

        unknown_candidate = await client.get(
            "/api/compare",
            params={
                "run": run_a,
                "candidate_a": "nobody",
                "candidate_b": "stub-concise",
            },
        )
        assert unknown_candidate.status_code == 404
        detail = unknown_candidate.json()["detail"]
        assert "not part of run" in detail
        assert "stub-concise" in detail

        no_query = await client.get("/api/compare")
        assert no_query.status_code == 404
        assert "needs two run ids" in no_query.json()["detail"]


async def test_compare_empty_state_explains_what_is_needed(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        page = await client.get("/compare")
    assert page.status_code == 200
    assert "Nothing to compare yet" in page.text
    assert "Pick two of the 3 runs in the menus above" in page.text
    assert "Select run A" in page.text
    assert "Select run B" in page.text
    for name in ("alpha-spec", "beta-spec", "gamma-spec"):
        assert name in page.text


async def test_compare_with_no_runs_explains_how_to_start(
    evalh_home: Any,
) -> None:
    app = create_app()
    try:
        async with _client(app) as client:
            page = await client.get("/compare")
            assert page.status_code == 200
            assert "No runs yet" in page.text
            assert "evalh run spec.yaml" in page.text
            api = await client.get("/api/compare")
            assert api.status_code == 404
            assert "needs two run ids" in api.json()["detail"]
    finally:
        app.state.database.dispose()


@pytest.fixture
def judged_citations(
    evalh_home: Any,
    workdir: Any,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[dict[str, Any]]:
    """Seed two six-case runs scored by two real scripted judge configurations."""
    cases = [
        {
            "case_id": f"case-{index}",
            "prompt": "What is 6 times 7?",
            "expected": {"answer": "42"},
            "tier": 1,
        }
        for index in range(1, 7)
    ]
    run_a = _seed(
        capsys,
        write_spec(
            workdir / "cite-a.yaml",
            _citation_spec("cite-a-spec", "judge-a", ["policy"], cases),
        ),
    )
    run_b = _seed(
        capsys,
        write_spec(
            workdir / "cite-b.yaml",
            _citation_spec("cite-b-spec", "judge-b", ["absent"], cases),
        ),
    )
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    repository = Repository(database)
    app = create_app(settings=settings, repository=repository)
    yield {
        "app": app,
        "repository": repository,
        "runs": {"a": run_a, "b": run_b},
    }
    database.dispose()


async def test_real_judge_hashes_and_judge_cost_flow_into_the_comparison(
    judged_citations: dict[str, Any],
) -> None:
    """Issue #7 stores the hash and judge cost; the comparison reads both."""
    run_a = judged_citations["runs"]["a"]
    run_b = judged_citations["runs"]["b"]
    cells_a = judged_citations["repository"].list_cells(run_a)
    async with _client(judged_citations["app"]) as client:
        data = (
            await client.get(
                "/api/compare", params={"a": run_a, "b": run_b}
            )
        ).json()

    row = next(
        item for item in data["metrics"] if item["metric"] == "citation_support"
    )
    assert row["a"]["n"] == 6 and row["a"]["passed"] == 6
    assert row["b"]["n"] == 6 and row["b"]["passed"] == 0
    assert row["a"]["judge"]["name"] == "judge-a"
    assert row["b"]["judge"]["name"] == "judge-b"
    assert row["a"]["judge"]["adapter"] == SCRIPTED_JUDGE
    assert row["a"]["judge"]["hash_source"] == "checks"
    assert row["b"]["judge"]["hash_source"] == "checks"
    assert (
        row["a"]["judge"]["config_hash"] != row["b"]["judge"]["config_hash"]
    )
    assert row["different_judge"] is True
    assert row["verdict"] == "inconclusive"

    # The compare cost per correct answer includes the judge spend issue #7
    # stores on every cell, because it divides the total cell cost.
    judge_spend = sum(cell.judge_cost_usd for cell in cells_a)
    total_cost = sum(cell.cost_usd for cell in cells_a)
    assert judge_spend > 0
    assert data["a"]["cost_usd"] == pytest.approx(total_cost)
    assert data["a"]["cost_per_correct"] == pytest.approx(total_cost / 6)
    assert "scripted-judge" in data["a"]["price_table"]["prices"]

def _decision_judge() -> JudgeIdentity:
    """Return a deterministic judge identity for the decision view tests."""
    return JudgeIdentity(
        name=None,
        adapter=None,
        model=None,
        config_hash="hash",
        source="spec",
    )


def _decision_side(price_id: str = "price-a") -> CompareSide:
    """Return one side with ten checks for the decision view tests."""
    return CompareSide(
        run_id="run-a",
        spec_name="spec",
        status="ok",
        candidate=None,
        cost_usd=0.01,
        n_cells=10,
        n_ok=10,
        n_checks=10,
        n_passed=8,
        cost_per_correct=0.00125,
        price_table=PriceTable(
            id=price_id, prices={"stub-1": {"input": 0.15, "output": 0.6}}
        ),
        candidates=("stub",),
    )


def _decision_metric(
    metric: str,
    verdict: str,
    *,
    delta: float = 0.2,
    comparable: bool = True,
) -> MetricComparison:
    """Return one metric row with ten samples on each side."""
    side = MetricSide(
        n=10,
        passed=8,
        value=0.8,
        ci_low=0.5,
        ci_high=0.95,
        cost_per_correct=0.00125,
        judge=_decision_judge(),
    )
    return MetricComparison(
        metric=metric,
        a=side,
        b=side,
        delta=delta,
        interval_low=0.05,
        interval_high=0.4,
        verdict=verdict,
        reason="reason",
        comparable=comparable,
        different_judge=False,
    )


def _decision_comparison(
    metrics: list[MetricComparison],
    *,
    b_price: str = "price-a",
) -> Comparison:
    """Return a comparison with the given rows and price identities."""
    return Comparison(
        mode="runs",
        a=_decision_side(),
        b=_decision_side(b_price),
        metrics=tuple(metrics),
        one_sided_cases=(),
        comparable_metrics=tuple(item.metric for item in metrics),
        cross_judge_metrics=(),
        one_sided_metrics=(),
    )


def test_decision_view_handles_mixed_metrics_and_missing_metrics() -> None:
    """The decision sentence is honest when metrics disagree or are absent."""
    mixed = decision_view(
        _decision_comparison(
            [
                _decision_metric("exact_match", BETTER),
                _decision_metric("contains", WORSE, delta=-0.2),
            ]
        )
    )
    assert mixed.tone == "mixed"
    assert "better on exact_match" in mixed.sentence
    assert "worse on contains" in mixed.sentence
    assert mixed.support[0][0] == "A"

    # A defensive shape: a comparable row with an undefined verdict falls to
    # the mixed branch, and the empty side of the sentence says "none".
    defensive = decision_view(
        _decision_comparison(
            [
                _decision_metric("exact_match", BETTER),
                _decision_metric("contains", UNDEFINED),
            ]
        )
    )
    assert defensive.tone == "mixed"
    assert "worse on none" in defensive.sentence

    absent = decision_view(_decision_comparison([]))
    assert (absent.tone, absent.label) == ("undefined", "No metrics")

    prices = decision_view(
        _decision_comparison([_decision_metric("exact_match", BETTER)], b_price="price-b")
    )
    assert "different price tables" in prices.caveat
    assert "Small samples" not in prices.caveat