"""The two provider families, driven through the CLI and a real local server.

Every test here talks HTTP to a threaded stdlib server, never a mock transport.
The two provider adapters share one classification rule, so each error test is
parametrized over both families where the behaviour must be identical.
"""

from __future__ import annotations

import importlib.util
import math
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from evalharness.cli import main
from evalharness.conformance import run_suite
from evalharness.core.models import Price
from tests.helpers import (
    EXACT_MATCH,
    STUB,
    base_spec,
    open_repository,
    run_id_from,
    write_spec,
)

OPENAI_CANDIDATE = "evalharness.adapters.candidates.openai_compat:build"
ANTHROPIC_CANDIDATE = "evalharness.adapters.candidates.anthropic:build"
OPENAI_JUDGE = "evalharness.adapters.judges.openai_compat:build"
ANTHROPIC_JUDGE = "evalharness.adapters.judges.anthropic:build"
JUDGE_PASS = "evalharness.adapters.metrics.judge_pass:build"

OPENAI_MODEL = "gpt-4o-mini"
ANTHROPIC_MODEL = "claude-haiku-4-5"
OPENAI_PRICE = Price(input=0.15, output=0.60)
ANTHROPIC_PRICE = Price(input=1.00, output=5.00)
SECRET = "sk-provider-canary"

PROVIDER_MODELS = [
    (OPENAI_CANDIDATE, OPENAI_MODEL, "bearer", "openai-compatible"),
    (ANTHROPIC_CANDIDATE, ANTHROPIC_MODEL, "header", "anthropic"),
]


def _usd(price: Price, prompt: int, completion: int) -> float:
    return prompt / 1_000_000 * price.input + completion / 1_000_000 * price.output


def _store_credential(capsys, monkeypatch, name: str, provider: str, variable: str) -> None:
    monkeypatch.setenv(variable, SECRET)
    code = main(
        [
            "credentials",
            "add",
            name,
            "--provider",
            provider,
            "--secret-env",
            variable,
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err


def _two_provider_spec(base_url: str) -> dict[str, Any]:
    """Two candidates and two judges, one at each provider, two credentials."""
    spec = base_spec()
    spec["credentials"] = [
        {"name": "openai-key", "provider": "openai-compatible"},
        {"name": "anthropic-key", "provider": "anthropic"},
    ]
    spec["candidates"] = [
        {
            "name": "openai-candidate",
            "adapter": OPENAI_CANDIDATE,
            "credential": "openai-key",
            "model": OPENAI_MODEL,
            "options": {"base_url": base_url, "auth": "bearer"},
        },
        {
            "name": "anthropic-candidate",
            "adapter": ANTHROPIC_CANDIDATE,
            "credential": "anthropic-key",
            "model": ANTHROPIC_MODEL,
            "options": {
                "base_url": base_url,
                "auth": "header",
                "header_name": "x-api-key",
                "context": "policy",
            },
        },
    ]
    spec["judges"] = [
        {
            "name": "openai-judge",
            "adapter": OPENAI_JUDGE,
            "credential": "openai-key",
            "model": OPENAI_MODEL,
            "options": {"base_url": base_url, "auth": "bearer"},
        },
        {
            "name": "anthropic-judge",
            "adapter": ANTHROPIC_JUDGE,
            "credential": "anthropic-key",
            "model": ANTHROPIC_MODEL,
            "options": {
                "base_url": base_url,
                "auth": "header",
                "header_name": "x-api-key",
            },
        },
    ]
    spec["metrics"] = [
        {
            "key": "judge_openai",
            "adapter": JUDGE_PASS,
            "options": {"judge": "openai-judge"},
        },
        {
            "key": "judge_anthropic",
            "adapter": JUDGE_PASS,
            "options": {"judge": "anthropic-judge"},
        },
    ]
    spec["prices"] = {}
    spec["gates"] = []
    return spec


def _stored_two_provider_spec(
    capsys, monkeypatch, base_url: str
) -> dict[str, Any]:
    _store_credential(
        capsys, monkeypatch, "openai-key", "openai-compatible", "PROVIDER_OPENAI_KEY"
    )
    _store_credential(
        capsys, monkeypatch, "anthropic-key", "anthropic", "PROVIDER_ANTHROPIC_KEY"
    )
    return _two_provider_spec(base_url)


def _run_and_read(workdir, capsys, spec: dict[str, Any], name: str = "providers.yaml"):
    path = write_spec(workdir / name, spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, (captured.out, captured.err)
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        run = repository.get_run(run_id)
        cells = repository.list_cells(run_id)
        checks = {
            cell.cell_id: repository.list_checks(run_id, cell.cell_id)
            for cell in cells
        }
        costs = repository.list_provider_costs(run_id)
    finally:
        database.dispose()
    assert run is not None
    return run, cells, checks, costs, captured


def _one_candidate_spec(
    base_url: str,
    adapter: str,
    model: str,
    *,
    auth: str = "none",
    credential: str | None = None,
    judges: list[dict[str, Any]] | None = None,
    metrics: list[dict[str, Any]] | None = None,
    prices: dict[str, Any] | None = None,
    extra_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = base_spec()
    spec["candidates"] = [
        {
            "name": "provider-candidate",
            "adapter": adapter,
            "model": model,
            "options": {"base_url": base_url, "auth": auth, **(extra_options or {})},
        }
    ]
    spec["credentials"] = []
    if credential is not None:
        spec["candidates"][0]["credential"] = credential
    spec["judges"] = judges or []
    spec["metrics"] = metrics or [
        {"key": "exact_match", "adapter": EXACT_MATCH, "options": {}}
    ]
    spec["prices"] = prices if prices is not None else {}
    spec["gates"] = []
    return spec


def _validate(workdir, capsys, spec: dict[str, Any]) -> int:
    path = write_spec(workdir / "validate.yaml", spec)
    code = main(["validate", str(path)])
    return code


def test_two_providers_two_credentials_attribute_cost_per_provider_and_candidate(
    evalh_home, workdir, capsys, monkeypatch, provider_stub
):
    spec = _stored_two_provider_spec(capsys, monkeypatch, provider_stub.base_url)

    run, cells, checks, costs, captured = _run_and_read(workdir, capsys, spec)

    assert run.status == "ok"
    assert len(cells) == 2
    assert {cell.provider for cell in cells} == {"openai-compatible", "anthropic"}
    assert all(cell.usage_source == "provider" for cell in cells)
    assert all(cell.prompt_tokens == 11 and cell.completion_tokens == 5 for cell in cells)
    assert all(
        check.passed for cell in cells for check in checks[cell.cell_id]
    )

    judge_openai = _usd(OPENAI_PRICE, 11, 5)
    judge_anthropic = _usd(ANTHROPIC_PRICE, 11, 5)
    per_provider_judge = {
        "openai-compatible": judge_openai,
        "anthropic": judge_anthropic,
    }
    for cell in cells:
        assert set(cell.judge_provider_usage) == {
            "openai-compatible",
            "anthropic",
        }
        assert cell.judge_prompt_tokens == 22
        assert cell.judge_completion_tokens == 10
        assert cell.judge_cost_usd == pytest.approx(
            judge_openai + judge_anthropic
        )
        assert cell.answer_cost_usd == pytest.approx(
            _usd(
                OPENAI_PRICE if cell.provider == "openai-compatible" else ANTHROPIC_PRICE,
                11,
                5,
            )
        )
        for provider, usage in cell.judge_provider_usage.items():
            assert usage.cost_usd == pytest.approx(per_provider_judge[provider])

    rows = {row.provider: row for row in costs}
    assert set(rows) == {"openai-compatible", "anthropic"}
    # Every cell consults both judges, so each judge provider's row carries one
    # verdict from each of the two cells while n_cells stays the candidate cells
    # that provider actually ran.
    assert rows["openai-compatible"].n_cells == 1
    assert rows["openai-compatible"].judge_prompt_tokens == 22
    assert rows["openai-compatible"].judge_completion_tokens == 10
    assert rows["openai-compatible"].judge_cost_usd == pytest.approx(
        2 * judge_openai
    )
    assert rows["openai-compatible"].cost_usd == pytest.approx(
        _usd(OPENAI_PRICE, 11, 5) + 2 * judge_openai
    )
    assert rows["anthropic"].n_cells == 1
    assert rows["anthropic"].judge_prompt_tokens == 22
    assert rows["anthropic"].judge_cost_usd == pytest.approx(2 * judge_anthropic)
    assert rows["anthropic"].cost_usd == pytest.approx(
        _usd(ANTHROPIC_PRICE, 11, 5) + 2 * judge_anthropic
    )

    # The parts add up: the provider rows equal the run total, and the answer,
    # judge and tool columns equal the cell sums.
    assert sum(row.cost_usd for row in costs) == pytest.approx(run.cost_usd)
    assert sum(row.answer_cost_usd for row in costs) == pytest.approx(
        sum(cell.answer_cost_usd for cell in cells)
    )
    assert sum(row.judge_cost_usd for row in costs) == pytest.approx(
        sum(cell.judge_cost_usd for cell in cells)
    )
    assert sum(row.tool_cost_usd for row in costs) == pytest.approx(
        sum(cell.tool_cost_usd for cell in cells)
    )

    assert "provider openai-compatible:" in captured.out
    assert "provider anthropic:" in captured.out
    assert run.prices_snapshot["version"] == 2
    assert run.prices_snapshot["providers"]["openai-compatible"][OPENAI_MODEL][
        "input"
    ] == pytest.approx(0.15)
    assert run.prices_snapshot["providers"]["anthropic"][ANTHROPIC_MODEL][
        "input"
    ] == pytest.approx(1.00)


def test_both_auth_shapes_send_the_expected_headers(
    evalh_home, workdir, capsys, monkeypatch, provider_stub
):
    spec = _stored_two_provider_spec(capsys, monkeypatch, provider_stub.base_url)
    spec["candidates"][0]["options"]["headers"] = {
        "x-opencode-session": "test-session"
    }

    _run_and_read(workdir, capsys, spec)

    assert provider_stub.requests
    for request in provider_stub.requests:
        if request.path.endswith("/v1/messages"):
            assert request.headers["x-api-key"] == SECRET
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert "Authorization" not in request.headers
        else:
            assert request.headers["Authorization"] == f"Bearer {SECRET}"
            assert "x-api-key" not in request.headers
    custom = [
        request
        for request in provider_stub.requests
        if "x-opencode-session" in request.headers
    ]
    assert custom
    assert all(
        request.headers["x-opencode-session"] == "test-session"
        for request in custom
    )
    paths = {request.path for request in provider_stub.requests}
    assert paths == {"/chat/completions", "/v1/messages"}


@pytest.mark.parametrize(
    "adapter",
    [OPENAI_CANDIDATE, ANTHROPIC_CANDIDATE],
)
def test_rate_limit_retries_to_the_attempt_budget_then_records_a_cell_error(
    evalh_home, workdir, capsys, provider_stub, adapter
):
    provider_stub.path_responder = lambda path, payload: (
        429,
        {"error": "rate limit exceeded"},
    )
    model = OPENAI_MODEL if adapter == OPENAI_CANDIDATE else ANTHROPIC_MODEL
    spec = _one_candidate_spec(provider_stub.base_url, adapter, model)

    path = write_spec(workdir / "rate-limit.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err

    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id_from(captured.out))
        run = repository.get_run(run_id_from(captured.out))
    finally:
        database.dispose()
    assert len(provider_stub.requests) == 3
    assert cells[0].status == "error"
    assert cells[0].output["error_type"] == "TransportError"
    assert "rate limit exceeded" in cells[0].output["error"]
    assert run is not None and run.status == "partial"


def test_rate_limit_then_success_is_retried(
    evalh_home, workdir, capsys, provider_stub
):
    attempts = {"count": 0}

    def responder(path: str, payload: dict[str, Any]):
        attempts["count"] += 1
        if attempts["count"] < 3:
            return 429, {"error": "slow down"}
        return 200, {
            "choices": [{"message": {"content": "42"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        }

    provider_stub.path_responder = responder
    spec = _one_candidate_spec(provider_stub.base_url, OPENAI_CANDIDATE, OPENAI_MODEL)

    _, cells, checks, _, _ = _run_and_read(workdir, capsys, spec)

    assert len(provider_stub.requests) == 3
    assert cells[0].status == "ok"
    assert cells[0].answer == "42"
    assert checks[cells[0].cell_id][0].passed is True


@pytest.mark.parametrize(
    "adapter",
    [OPENAI_CANDIDATE, ANTHROPIC_CANDIDATE],
)
def test_an_auth_failure_is_one_request_and_a_redacted_cell_error(
    evalh_home, workdir, capsys, monkeypatch, provider_stub, adapter
):
    provider_stub.path_responder = lambda path, payload: (
        401,
        {"error": f"invalid key {SECRET}"},
    )
    model = OPENAI_MODEL if adapter == OPENAI_CANDIDATE else ANTHROPIC_MODEL
    _store_credential(
        capsys, monkeypatch, "openai-key", "openai-compatible", "PROVIDER_OPENAI_KEY"
    )
    spec = _one_candidate_spec(
        provider_stub.base_url,
        adapter,
        model,
        auth="bearer",
        credential="openai-key",
    )
    spec["credentials"] = [{"name": "openai-key", "provider": "openai-compatible"}]

    path = write_spec(workdir / "auth-failure.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err

    database, repository = open_repository()
    try:
        run_id = run_id_from(captured.out)
        cells = repository.list_cells(run_id)
        run = repository.get_run(run_id)
    finally:
        database.dispose()
    assert len(provider_stub.requests) == 1
    assert cells[0].status == "error"
    assert cells[0].output["error_type"] == "ProviderError"
    assert "invalid key [redacted:openai-key]" in cells[0].output["error"]
    assert SECRET not in cells[0].output["error"]
    assert run is not None and run.status == "partial"


def test_a_judge_auth_failure_is_a_cell_error_not_a_crash(
    evalh_home, workdir, capsys, provider_stub
):
    provider_stub.path_responder = lambda path, payload: (
        401,
        {"error": "judge key rejected"},
    )
    spec = base_spec()
    spec["candidates"] = [
        {
            "name": "stub",
            "adapter": STUB,
            "credential": "stub",
            "model": "stub-1",
            "options": {},
        }
    ]
    spec["judges"] = [
        {
            "name": "openai-judge",
            "adapter": OPENAI_JUDGE,
            "model": OPENAI_MODEL,
            "options": {"base_url": provider_stub.base_url, "auth": "none"},
        }
    ]
    spec["metrics"] = [
        {"key": "judge_pass", "adapter": JUDGE_PASS, "options": {"judge": "openai-judge"}}
    ]
    spec["prices"] = {"stub-1": {"input": 0.15, "output": 0.60}}
    spec["gates"] = []

    path = write_spec(workdir / "judge-failure.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err

    database, repository = open_repository()
    try:
        run_id = run_id_from(captured.out)
        cells = repository.list_cells(run_id)
        run = repository.get_run(run_id)
    finally:
        database.dispose()
    assert len(provider_stub.requests) == 1
    assert cells[0].status == "error"
    assert cells[0].output["error_type"] == "ProviderError"
    assert "judge key rejected" in cells[0].output["error"]
    assert run is not None and run.status == "partial"


@pytest.mark.parametrize(
    ("adapter", "kind", "last_clause"),
    [
        (OPENAI_CANDIDATE, "candidate", "candidate/8"),
        (ANTHROPIC_CANDIDATE, "candidate", "candidate/8"),
        (OPENAI_JUDGE, "judge", "judge/6"),
        (ANTHROPIC_JUDGE, "judge", "judge/6"),
    ],
)
def test_every_provider_adapter_passes_conformance_against_the_stub(
    evalh_home, provider_stub, adapter, kind, last_clause
):
    report = run_suite(
        adapter,
        kind,
        options={"base_url": provider_stub.base_url, "auth": "none"},
    )

    assert report.passed, [
        (result.clause, result.detail) for result in report.results if not result.ok
    ]
    assert report.results[-1].clause == last_clause


@pytest.mark.parametrize(
    ("adapter", "model", "body"),
    [
        (
            OPENAI_CANDIDATE,
            OPENAI_MODEL,
            {"choices": [{"message": {"content": "42"}}]},
        ),
        (
            ANTHROPIC_CANDIDATE,
            ANTHROPIC_MODEL,
            {"content": [{"type": "text", "text": "42"}]},
        ),
    ],
)
def test_missing_provider_usage_is_recorded_as_estimated(
    evalh_home, workdir, capsys, provider_stub, adapter, model, body
):
    provider_stub.path_responder = lambda path, payload: (200, body)
    spec = _one_candidate_spec(
        provider_stub.base_url,
        adapter,
        model,
        extra_options={"system": "Answer with a bare number."},
    )

    _, cells, _, _, _ = _run_and_read(workdir, capsys, spec)

    cell = cells[0]
    assert cell.status == "ok"
    assert cell.usage_source == "estimated"
    prompt_chars = len("Answer with a bare number.") + 1 + len("What is 6 times 7?")
    assert cell.prompt_tokens == math.ceil(prompt_chars / 4)
    assert cell.completion_tokens == math.ceil(len("42") / 4)


@pytest.mark.parametrize(
    ("adapter", "model", "body"),
    [
        (OPENAI_CANDIDATE, OPENAI_MODEL, {"choices": []}),
        (ANTHROPIC_CANDIDATE, ANTHROPIC_MODEL, {"content": []}),
    ],
)
def test_a_malformed_provider_response_is_one_error_not_a_retry(
    evalh_home, workdir, capsys, provider_stub, adapter, model, body
):
    provider_stub.path_responder = lambda path, payload: (200, body)
    spec = _one_candidate_spec(provider_stub.base_url, adapter, model)

    path = write_spec(workdir / "malformed.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err

    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id_from(captured.out))
    finally:
        database.dispose()
    assert len(provider_stub.requests) == 1
    assert cells[0].status == "error"
    assert cells[0].output["error_type"] == "ProviderError"
    assert "does not match the provider contract" in cells[0].output["error"]


def test_a_non_json_provider_body_is_one_error_not_a_retry(
    evalh_home, workdir, capsys, provider_stub
):
    provider_stub.path_responder = lambda path, payload: (200, "<html>nope</html>")
    spec = _one_candidate_spec(provider_stub.base_url, OPENAI_CANDIDATE, OPENAI_MODEL)

    path = write_spec(workdir / "not-json.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err

    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id_from(captured.out))
    finally:
        database.dispose()
    assert len(provider_stub.requests) == 1
    assert cells[0].status == "error"
    assert "not JSON" in cells[0].output["error"]


def test_a_wrong_answer_is_requested_once(
    evalh_home, workdir, capsys, provider_stub
):
    provider_stub.path_responder = lambda path, payload: (
        200,
        {
            "choices": [{"message": {"content": "wrong"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        },
    )
    spec = _one_candidate_spec(provider_stub.base_url, OPENAI_CANDIDATE, OPENAI_MODEL)

    _, cells, checks, _, _ = _run_and_read(workdir, capsys, spec)

    assert len(provider_stub.requests) == 1
    assert cells[0].status == "ok"
    assert cells[0].answer == "wrong"
    assert checks[cells[0].cell_id][0].passed is False


def test_a_transport_failure_is_recorded_after_the_attempt_budget(
    evalh_home, workdir, capsys
):
    spec = _one_candidate_spec("http://127.0.0.1:1", ANTHROPIC_CANDIDATE, ANTHROPIC_MODEL)

    path = write_spec(workdir / "transport.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err

    database, repository = open_repository()
    try:
        run_id = run_id_from(captured.out)
        cells = repository.list_cells(run_id)
        run = repository.get_run(run_id)
    finally:
        database.dispose()
    assert cells[0].status == "error"
    assert cells[0].output["error_type"] == "TransportError"
    assert run is not None and run.status == "partial"


def test_the_path_option_replaces_the_default_path(
    evalh_home, workdir, capsys, provider_stub
):
    spec = _one_candidate_spec(
        provider_stub.base_url,
        OPENAI_CANDIDATE,
        OPENAI_MODEL,
        extra_options={"path": "chat/completions/v2"},
    )

    _run_and_read(workdir, capsys, spec)

    assert provider_stub.requests[0].path == "/chat/completions/v2"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"passed": true, "reason": "ok", "score": 0.9}', True),
        ('{"passed": false, "reason": "wrong"}', False),
        ('```json\n{"passed": true, "reason": "fenced"}\n```', True),
        ("PASS", True),
        ("FAIL: missing a citation", False),
        ("the answer looks fine to me", False),
        ('{"passed": true}', True),
        ('{"passed": false, "reason": "no", "score": "high"}', False),
        ("[1, 2]", False),
    ],
)
def test_a_judge_verdict_is_parsed_from_json_or_a_pass_fail_marker(
    evalh_home, workdir, capsys, provider_stub, content, expected
):
    def responder(path: str, payload: dict[str, Any]):
        return 200, {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        }

    provider_stub.path_responder = responder
    spec = base_spec()
    spec["source"]["options"]["cases"][0]["rubric"] = "The answer must be 42."
    spec["candidates"] = [
        {
            "name": "stub",
            "adapter": STUB,
            "credential": "stub",
            "model": "stub-1",
            "options": {},
        }
    ]
    spec["judges"] = [
        {
            "name": "openai-judge",
            "adapter": OPENAI_JUDGE,
            "model": OPENAI_MODEL,
            "options": {
                "base_url": provider_stub.base_url,
                "auth": "none",
                "instructions": "Prefer a short reason.",
            },
        }
    ]
    spec["metrics"] = [
        {"key": "judge_pass", "adapter": JUDGE_PASS, "options": {"judge": "openai-judge"}}
    ]
    spec["prices"] = {"stub-1": {"input": 0.15, "output": 0.60}}
    spec["gates"] = []

    _, cells, checks, _, _ = _run_and_read(workdir, capsys, spec)

    check = checks[cells[0].cell_id][0]
    assert check.passed is expected
    assert check.judge_config_hash
    assert check.evidence["judge"] == "openai-judge"
    assert cells[0].judge_prompt_tokens == 11
    assert cells[0].judge_completion_tokens == 5
    assert set(cells[0].judge_provider_usage) == {"openai-compatible"}


def test_a_declared_model_with_no_price_fails_validation_naming_the_model(
    evalh_home, workdir, capsys, provider_stub
):
    spec = _one_candidate_spec(
        provider_stub.base_url,
        OPENAI_CANDIDATE,
        "not-a-priced-model",
        prices={},
    )

    code = _validate(workdir, capsys, spec)

    captured = capsys.readouterr()
    assert code == 2
    assert "prices.not-a-priced-model" in captured.err
    assert "no price" in captured.err


def test_a_judge_model_with_no_price_fails_validation(
    evalh_home, workdir, capsys, provider_stub
):
    spec = base_spec()
    spec["judges"] = [
        {
            "name": "openai-judge",
            "adapter": OPENAI_JUDGE,
            "model": "not-a-priced-judge",
            "options": {"base_url": provider_stub.base_url, "auth": "none"},
        }
    ]

    code = _validate(workdir, capsys, spec)

    captured = capsys.readouterr()
    assert code == 2
    assert "prices.not-a-priced-judge" in captured.err


def test_a_spec_provider_price_overrides_the_shipped_table(
    evalh_home, workdir, capsys, provider_stub
):
    spec = _one_candidate_spec(
        provider_stub.base_url, OPENAI_CANDIDATE, OPENAI_MODEL
    )
    spec["provider_prices"] = {
        "openai-compatible": {OPENAI_MODEL: {"input": 10.0, "output": 20.0}}
    }

    run, cells, _, _, _ = _run_and_read(workdir, capsys, spec)

    expected = 11 / 1_000_000 * 10.0 + 5 / 1_000_000 * 20.0
    assert cells[0].answer_cost_usd == pytest.approx(expected)
    assert run.prices_snapshot["providers"]["openai-compatible"][OPENAI_MODEL][
        "input"
    ] == pytest.approx(10.0)


def test_a_model_only_spec_price_overrides_the_provider_default(
    evalh_home, workdir, capsys, provider_stub
):
    spec = _one_candidate_spec(
        provider_stub.base_url, ANTHROPIC_CANDIDATE, ANTHROPIC_MODEL
    )
    spec["prices"] = {ANTHROPIC_MODEL: {"input": 2.0, "output": 4.0}}

    _, cells, _, _, _ = _run_and_read(workdir, capsys, spec)

    assert cells[0].answer_cost_usd == pytest.approx(
        11 / 1_000_000 * 2.0 + 5 / 1_000_000 * 4.0
    )


@pytest.mark.parametrize(
    ("adapter", "model", "auth", "message"),
    [
        (OPENAI_CANDIDATE, OPENAI_MODEL, "bearer", "candidates[0].credential"),
        (ANTHROPIC_CANDIDATE, ANTHROPIC_MODEL, "header", "candidates[0].credential"),
    ],
)
def test_a_provider_candidate_needs_a_stored_credential(
    evalh_home, workdir, capsys, adapter, model, auth, message
):
    spec = _one_candidate_spec(
        "http://127.0.0.1:8770",
        adapter,
        model,
        auth=auth,
        credential="declared-but-not-stored",
        extra_options={"header_name": "x-api-key"},
    )
    spec["credentials"] = [
        {"name": "declared-but-not-stored", "provider": "openai-compatible"}
    ]

    code = _validate(workdir, capsys, spec)

    captured = capsys.readouterr()
    assert code == 2
    assert message in captured.err
    assert "evalh credentials add" in captured.err


def test_a_provider_judge_needs_a_stored_credential(
    evalh_home, workdir, capsys
):
    spec = base_spec()
    spec["credentials"] = [
        {"name": "stub", "provider": "none"},
        {"name": "judge-key", "provider": "anthropic"},
    ]
    spec["judges"] = [
        {
            "name": "anthropic-judge",
            "adapter": ANTHROPIC_JUDGE,
            "credential": "judge-key",
            "model": ANTHROPIC_MODEL,
            "options": {"base_url": "http://127.0.0.1:8770", "auth": "header", "header_name": "x-api-key"},
        }
    ]

    code = _validate(workdir, capsys, spec)

    captured = capsys.readouterr()
    assert code == 2
    assert "judges[0].credential" in captured.err
    assert "evalh credentials add" in captured.err


def test_a_provider_judge_needs_a_declared_model(
    evalh_home, workdir, capsys
):
    spec = base_spec()
    spec["judges"] = [
        {
            "name": "openai-judge",
            "adapter": OPENAI_JUDGE,
            "options": {"base_url": "http://127.0.0.1:8770", "auth": "none"},
        }
    ]

    code = _validate(workdir, capsys, spec)

    captured = capsys.readouterr()
    assert code == 2
    assert "judges[0].model" in captured.err
    assert "declares no model" in captured.err


@pytest.mark.parametrize(
    ("adapter", "model", "options", "message"),
    [
        (OPENAI_CANDIDATE, OPENAI_MODEL, {"base_url": ""}, "options.base_url is required"),
        (OPENAI_CANDIDATE, OPENAI_MODEL, {"base_url": "http://x", "auth": "oauth"}, "options.auth must be one of"),
        (ANTHROPIC_CANDIDATE, ANTHROPIC_MODEL, {"base_url": "http://x", "auth": "header"}, "options.header_name is required"),
        (
            OPENAI_CANDIDATE,
            OPENAI_MODEL,
            {"base_url": "http://x", "headers": "nope"},
            "options.headers must be a mapping",
        ),
        (
            OPENAI_CANDIDATE,
            OPENAI_MODEL,
            {"base_url": "http://x", "headers": {"x-session": 7}},
            "options.headers must map string",
        ),
    ],
)
def test_invalid_provider_candidate_options_fail_validation(
    evalh_home, workdir, capsys, adapter, model, options, message
):
    spec = _one_candidate_spec("http://127.0.0.1:8770", adapter, model)
    spec["candidates"][0]["options"] = options

    code = _validate(workdir, capsys, spec)

    captured = capsys.readouterr()
    assert code == 2
    assert message in captured.err


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"base_url": "", "auth": "none"}, "options.base_url is required"),
        (
            {"base_url": "http://x", "auth": "oauth"},
            "options.auth must be one of",
        ),
        (
            {"base_url": "http://x", "auth": "header"},
            "options.header_name is required",
        ),
    ],
)
def test_invalid_provider_judge_options_fail_validation(
    evalh_home, workdir, capsys, options, message
):
    spec = base_spec()
    spec["judges"] = [
        {
            "name": "openai-judge",
            "adapter": OPENAI_JUDGE,
            "model": OPENAI_MODEL,
            "options": options,
        }
    ]

    code = _validate(workdir, capsys, spec)

    captured = capsys.readouterr()
    assert code == 2
    assert message in captured.err


@pytest.mark.parametrize(
    ("adapter", "kind"),
    [
        (OPENAI_CANDIDATE, "candidate"),
        (ANTHROPIC_CANDIDATE, "candidate"),
        (OPENAI_JUDGE, "judge"),
        (ANTHROPIC_JUDGE, "judge"),
    ],
)
def test_a_provider_adapter_with_bearer_auth_needs_a_credential_in_the_suite(
    evalh_home, provider_stub, adapter, kind
):
    report = run_suite(
        adapter,
        kind,
        options={"base_url": provider_stub.base_url, "auth": "bearer"},
    )

    assert report.passed is False
    assert "requires a credential" in report.results[0].detail
    assert provider_stub.requests == []


def test_the_shipped_price_table_prices_a_spec_with_no_prices_block(
    evalh_home, workdir, capsys, provider_stub
):
    spec = _one_candidate_spec(
        provider_stub.base_url, OPENAI_CANDIDATE, OPENAI_MODEL
    )
    del spec["prices"]

    run, cells, _, _, _ = _run_and_read(workdir, capsys, spec)

    assert cells[0].cost_usd == pytest.approx(_usd(OPENAI_PRICE, 11, 5))
    assert run.cost_usd > 0


def test_a_judge_path_without_a_leading_slash_is_normalised(
    evalh_home, workdir, capsys, provider_stub
):
    spec = base_spec()
    spec["candidates"] = [
        {
            "name": "stub",
            "adapter": STUB,
            "credential": "stub",
            "model": "stub-1",
            "options": {},
        }
    ]
    spec["judges"] = [
        {
            "name": "anthropic-judge",
            "adapter": ANTHROPIC_JUDGE,
            "model": ANTHROPIC_MODEL,
            "options": {
                "base_url": provider_stub.base_url,
                "auth": "none",
                "path": "v1/messages",
            },
        }
    ]
    spec["metrics"] = [
        {
            "key": "judge_pass",
            "adapter": JUDGE_PASS,
            "options": {"judge": "anthropic-judge"},
        }
    ]
    spec["prices"] = {"stub-1": {"input": 0.15, "output": 0.60}}
    spec["gates"] = []

    _, cells, checks, _, _ = _run_and_read(workdir, capsys, spec)

    assert provider_stub.requests[0].path == "/v1/messages"
    assert provider_stub.requests[0].headers["anthropic-version"] == "2023-06-01"
    assert checks[cells[0].cell_id][0].passed is True


def test_judge_pass_needs_a_declared_judge(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["metrics"] = [
        {"key": "judge_pass", "adapter": JUDGE_PASS, "options": {}}
    ]

    code = _validate(workdir, capsys, spec)

    captured = capsys.readouterr()
    assert code == 2
    assert "options.judge" in captured.err


def _load_provider_server() -> ModuleType:
    path = Path(__file__).parents[1] / "examples" / "provider_server.py"
    spec = importlib.util.spec_from_file_location("provider_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_demo_spec_runs_end_to_end_without_a_paid_credential(
    evalh_home, workdir, capsys
):
    module = _load_provider_server()
    server = module.create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        demo = Path(__file__).parents[1] / "examples" / "specs" / "providers.yaml"
        text = demo.read_text(encoding="utf-8").replace(
            "http://127.0.0.1:8770", base_url
        )
        path = workdir / "providers.yaml"
        path.write_text(text, encoding="utf-8")

        code = main(["run", str(path)])
        captured = capsys.readouterr()

        assert code == 0, captured.err
        assert "status=ok" in captured.out
        run_id = run_id_from(captured.out)
        database, repository = open_repository()
        try:
            cells = repository.list_cells(run_id)
            costs = repository.list_provider_costs(run_id)
            run = repository.get_run(run_id)
            credentials = repository.list_credentials()
            checks = {
                cell.cell_id: repository.list_checks(run_id, cell.cell_id)
                for cell in cells
            }
        finally:
            database.dispose()
        assert credentials == []
        assert len(cells) == 4
        assert all(cell.status == "ok" for cell in cells)
        assert all(
            check.passed
            for cell_checks in checks.values()
            for check in cell_checks
        )
        assert {row.provider for row in costs} == {"openai-compatible", "anthropic"}
        assert sum(row.cost_usd for row in costs) == pytest.approx(run.cost_usd)
        assert any(row.judge_cost_usd > 0 for row in costs)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
