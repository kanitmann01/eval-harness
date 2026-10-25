"""The HTTP candidate contract, driven through the CLI and a real local server."""

from __future__ import annotations

import importlib.util
import json
import math
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from evalharness.cli import main
from evalharness.conformance import run_suite
from tests.helpers import base_spec, open_repository, run_id_from, write_spec
from tests.http_stub import default_response

HTTP = "evalharness.adapters.candidates.http:build"
SECRET = "sk-http-canary"


def _case(case_id: str, prompt: str = "What is 6 times 7?") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "prompt": prompt,
        "expected": {"answer": "42"},
        "tier": 1,
    }


def _spec(
    base_url: str,
    *,
    options: dict[str, Any] | None = None,
    cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    spec = base_spec()
    spec["candidates"] = [
        {
            "name": "http-agent",
            "adapter": HTTP,
            "model": "http-model",
            "options": {"base_url": base_url, "auth": "none", **(options or {})},
        }
    ]
    spec["credentials"] = []
    spec["prices"] = {"http-model": {"input": 0.15, "output": 0.60}}
    if cases is not None:
        spec["source"]["options"]["cases"] = cases
    return spec


def _run(evalh_home, workdir, capsys, spec: dict[str, Any]):
    path = write_spec(workdir / "http.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cells = repository.list_cells(run_id)
        checks = {
            cell.case_id: repository.list_checks(run_id, cell.cell_id)
            for cell in cells
        }
        run = repository.get_run(run_id)
    finally:
        database.dispose()
    assert run is not None
    return run, cells, checks, captured


def _store_credential(
    capsys, monkeypatch, name: str, secret: str = SECRET
) -> None:
    monkeypatch.setenv("HTTP_TEST_SECRET", secret)
    code = main(
        [
            "credentials",
            "add",
            name,
            "--provider",
            "custom",
            "--secret-env",
            "HTTP_TEST_SECRET",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err


def test_two_cells_map_usage_citations_and_artifacts(
    evalh_home, workdir, capsys, candidate_stub
):
    numbers = {"case-1": (100, 10, 1), "case-2": (200, 20, 3)}

    def responder(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        case_id = payload["case_id"]
        prompt_tokens, completion_tokens, tool_calls = numbers[case_id]
        return 200, {
            "answer": "42",
            "citations": [f"cite-{case_id}"],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
            "tool_calls": tool_calls,
            "artifacts": {"trace_id": f"trace-{case_id}"},
            "model": "remote-agent-v2",
        }

    candidate_stub.responder = responder
    spec = _spec(
        candidate_stub.base_url,
        cases=[
            _case("case-1", "What is 6 times 7?"),
            _case("case-2", "What is 7 times 6?"),
        ],
    )
    spec["candidates"][0]["options"]["prompt"] = "concise"
    spec["candidates"][0]["options"]["context"] = "policy"

    run, cells, checks, captured = _run(evalh_home, workdir, capsys, spec)

    assert run.status == "ok"
    by_case = {cell.case_id: cell for cell in cells}
    first = by_case["case-1"]
    assert (
        first.prompt_tokens,
        first.completion_tokens,
        first.tool_calls,
    ) == (100, 10, 1)
    assert first.usage_source == "provider"
    assert first.output["citations"] == ["cite-case-1"]
    assert first.output["artifacts"] == {"trace_id": "trace-case-1"}
    assert first.output["model"] == "remote-agent-v2"
    assert first.output["latency_ms"] >= 0
    assert all(check.passed for check in checks["case-1"])
    second = by_case["case-2"]
    assert (
        second.prompt_tokens,
        second.completion_tokens,
        second.tool_calls,
    ) == (200, 20, 3)
    assert second.output["citations"] == ["cite-case-2"]

    payloads = {
        request.payload["case_id"]: request
        for request in candidate_stub.requests
    }
    first_request = payloads["case-1"]
    assert first_request.path == "/run"
    assert first_request.payload == {
        "case_id": "case-1",
        "prompt": "What is 6 times 7?",
        "context": [
            {"chunk_id": "policy-1", "text": "Answer as a bare number."}
        ],
        "prompt_override": "Answer with digits only.",
        "seed": 7,
    }
    assert first_request.headers["Content-Type"] == "application/json"
    assert "Authorization" not in first_request.headers


def test_the_published_schema_matches_the_wire_request(
    evalh_home, workdir, capsys, candidate_stub
):
    contract_path = (
        Path(__file__).parents[1] / "contract" / "candidate-v1.schema.json"
    )
    schema = json.loads(contract_path.read_text(encoding="utf-8"))
    request = schema["$defs"]["request"]

    spec = _spec(candidate_stub.base_url, cases=[_case("case-1")])
    _run(evalh_home, workdir, capsys, spec)

    payload = candidate_stub.requests[0].payload
    assert set(payload) == set(request["properties"])
    assert set(request["required"]) == set(request["properties"])
    response = schema["$defs"]["response"]
    assert response["required"] == ["answer"]
    assert {"citations", "usage", "tool_calls", "artifacts", "model"} <= set(
        response["properties"]
    )


def test_missing_usage_is_recorded_as_estimated(
    evalh_home, workdir, capsys, candidate_stub
):
    candidate_stub.responder = lambda payload: (200, {"answer": "42"})
    spec = _spec(candidate_stub.base_url, cases=[_case("case-1")])
    spec["candidates"][0]["options"]["context"] = "policy"

    _, cells, _, _ = _run(evalh_home, workdir, capsys, spec)

    cell = cells[0]
    assert cell.usage_source == "estimated"
    prompt_chars = len("What is 6 times 7?") + len("Answer as a bare number.")
    assert cell.prompt_tokens == math.ceil(prompt_chars / 4)
    assert cell.completion_tokens == math.ceil(len("42") / 4)


def test_partial_usage_keeps_the_reported_count_and_estimates_the_rest(
    evalh_home, workdir, capsys, candidate_stub
):
    candidate_stub.responder = lambda payload: (
        200,
        {"answer": "42", "usage": {"prompt_tokens": 7}},
    )
    spec = _spec(candidate_stub.base_url, cases=[_case("case-1")])

    _, cells, _, _ = _run(evalh_home, workdir, capsys, spec)

    cell = cells[0]
    assert cell.usage_source == "estimated"
    assert cell.prompt_tokens == 7
    assert cell.completion_tokens == math.ceil(len("42") / 4)


def test_server_error_is_retried_twice_then_succeeds(
    evalh_home, workdir, capsys, candidate_stub
):
    attempts = {"count": 0}

    def responder(payload: dict[str, Any]) -> tuple[int, object]:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return 503, {"error": "temporarily unavailable"}
        return default_response(payload)

    candidate_stub.responder = responder
    spec = _spec(candidate_stub.base_url, cases=[_case("case-1")])

    _, cells, _, _ = _run(evalh_home, workdir, capsys, spec)

    assert len(candidate_stub.requests) == 3
    assert attempts["count"] == 3
    assert cells[0].status == "ok"
    assert cells[0].answer == "42"
    assert cells[0].usage_source == "provider"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"error": "invalid api key"}, "invalid api key"),
        ({"error": {"message": "key rejected"}}, "key rejected"),
        ({"message": "missing header"}, "missing header"),
        ("plain rejection", "plain rejection"),
        ([1, 2], "Unauthorized"),
        ({}, "Unauthorized"),
    ],
)
def test_auth_failure_is_a_cell_error_with_the_provider_message(
    evalh_home, workdir, capsys, candidate_stub, body, message
):
    candidate_stub.responder = lambda payload: (401, body)
    spec = _spec(candidate_stub.base_url, cases=[_case("case-1")])

    run, cells, _, captured = _run(evalh_home, workdir, capsys, spec)

    assert len(candidate_stub.requests) == 1
    assert cells[0].status == "error"
    assert cells[0].output["error_type"] == "HttpCandidateError"
    assert message in cells[0].output["error"]
    assert run.status == "partial"
    assert "status=partial" in captured.out


def test_a_wrong_answer_is_requested_exactly_once(
    evalh_home, workdir, capsys, candidate_stub
):
    candidate_stub.responder = lambda payload: (200, {"answer": "wrong"})
    spec = _spec(candidate_stub.base_url, cases=[_case("case-1")])

    _, cells, checks, _ = _run(evalh_home, workdir, capsys, spec)

    assert len(candidate_stub.requests) == 1
    assert cells[0].status == "ok"
    assert cells[0].answer == "wrong"
    assert checks["case-1"][0].passed is False
    assert cells[0].output["model"] == "http-model"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("<html>not json</html>", "not JSON"),
        ({"answer": 5}, "candidate-v1 at answer"),
    ],
)
def test_a_malformed_response_is_an_error_not_a_retry(
    evalh_home, workdir, capsys, candidate_stub, body, message
):
    candidate_stub.responder = lambda payload: (200, body)
    spec = _spec(candidate_stub.base_url, cases=[_case("case-1")])

    _, cells, _, _ = _run(evalh_home, workdir, capsys, spec)

    assert len(candidate_stub.requests) == 1
    assert cells[0].status == "error"
    assert cells[0].output["error_type"] == "HttpCandidateError"
    assert message in cells[0].output["error"]


def test_transport_failure_is_recorded_after_the_attempt_budget(
    evalh_home, workdir, capsys
):
    spec = _spec("http://127.0.0.1:1", cases=[_case("case-1")])

    run, cells, _, captured = _run(evalh_home, workdir, capsys, spec)

    assert cells[0].status == "error"
    assert cells[0].output["error_type"] == "TransportError"
    assert run.status == "partial"
    assert "status=partial" in captured.out


def test_missing_credential_with_bearer_fails_validation(
    evalh_home, workdir, capsys
):
    spec = _spec("http://127.0.0.1:8765", options={"auth": "bearer"})
    spec["credentials"] = [{"name": "http-key", "provider": "custom"}]
    spec["candidates"][0]["credential"] = "http-key"
    path = write_spec(workdir / "bearer.yaml", spec)

    code = main(["validate", str(path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "candidates[0].credential" in captured.err
    assert "http-agent" in captured.err
    assert "evalh credentials add" in captured.err


def test_bearer_without_a_credential_name_fails_validation(
    evalh_home, workdir, capsys
):
    spec = _spec("http://127.0.0.1:8765", options={"auth": "bearer"})
    path = write_spec(workdir / "bearer.yaml", spec)

    code = main(["validate", str(path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "candidates[0].credential" in captured.err


def test_bearer_auth_sends_the_stored_secret(
    evalh_home, workdir, capsys, monkeypatch, candidate_stub
):
    _store_credential(capsys, monkeypatch, "http-key")
    spec = _spec(
        candidate_stub.base_url,
        options={"auth": "bearer"},
        cases=[_case("case-1")],
    )
    spec["candidates"][0]["credential"] = "http-key"

    _run(evalh_home, workdir, capsys, spec)

    assert (
        candidate_stub.requests[0].headers["Authorization"]
        == f"Bearer {SECRET}"
    )


def test_header_auth_sends_the_secret_in_the_named_header(
    evalh_home, workdir, capsys, monkeypatch, candidate_stub
):
    _store_credential(capsys, monkeypatch, "http-key")
    spec = _spec(
        candidate_stub.base_url,
        options={"auth": "header", "header_name": "x-api-key"},
        cases=[_case("case-1")],
    )
    spec["candidates"][0]["credential"] = "http-key"

    _run(evalh_home, workdir, capsys, spec)

    headers = candidate_stub.requests[0].headers
    assert headers["x-api-key"] == SECRET
    assert "Authorization" not in headers


def test_bearer_auth_with_no_credential_fails_the_conformance_run(
    evalh_home, candidate_stub
):
    report = run_suite(
        HTTP,
        "candidate",
        options={"base_url": candidate_stub.base_url, "auth": "bearer"},
    )

    assert report.passed is False
    assert "requires a credential" in report.results[0].detail
    assert candidate_stub.requests == []


def test_http_candidate_passes_the_public_suite(evalh_home, candidate_stub):
    report = run_suite(
        HTTP,
        "candidate",
        options={"base_url": candidate_stub.base_url, "auth": "none"},
    )

    assert report.passed is True
    assert [result.clause for result in report.results] == [
        f"candidate/{number}" for number in range(1, 9)
    ]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"auth": "oauth"}, "options.auth must be one of"),
        ({"auth": "header"}, "options.header_name is required"),
    ],
)
def test_invalid_auth_options_fail_validation(
    evalh_home, workdir, capsys, options, message
):
    spec = _spec("http://127.0.0.1:8765", options=options)
    path = write_spec(workdir / "options.yaml", spec)

    code = main(["validate", str(path)])

    captured = capsys.readouterr()
    assert code == 2
    assert message in captured.err


def test_base_url_is_required(evalh_home, workdir, capsys):
    spec = _spec("")
    path = write_spec(workdir / "options.yaml", spec)

    code = main(["validate", str(path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "options.base_url is required" in captured.err


def _load_example_server() -> ModuleType:
    path = (
        Path(__file__).parents[1] / "examples" / "http_candidate_server.py"
    )
    spec = importlib.util.spec_from_file_location("http_candidate_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_spec_runs_end_to_end(evalh_home, workdir, capsys):
    module = _load_example_server()
    server = module.create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        demo = (
            Path(__file__).parents[1]
            / "examples"
            / "specs"
            / "http-candidate.yaml"
        )
        text = demo.read_text(encoding="utf-8").replace(
            "http://127.0.0.1:8765", base_url
        )
        path = workdir / "http-candidate.yaml"
        path.write_text(text, encoding="utf-8")

        code = main(["run", str(path)])

        captured = capsys.readouterr()
        assert code == 0, captured.err
        assert "status=ok" in captured.out
        run_id = run_id_from(captured.out)
        database, repository = open_repository()
        try:
            cells = repository.list_cells(run_id)
        finally:
            database.dispose()
        assert [cell.answer for cell in cells] == ["42", "42"]
        assert all(cell.usage_source == "provider" for cell in cells)
        assert all(cell.output["citations"] for cell in cells)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
