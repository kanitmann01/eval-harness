"""Credential CLI: add, list, test, delete, spec validation and redaction."""

from __future__ import annotations

import getpass
import io
import json
import sys
from pathlib import Path

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.adapters.verify.http_verifier import HttpVerifier
from evalharness.cli import main
from tests.helpers import (
    base_spec,
    open_repository,
    run_id_from,
    write_spec,
)


def _add(
    capsys,
    monkeypatch,
    *,
    name: str = "openai",
    secret: str = "sk-live-canary",
    provider: str = "openai",
    env_var: str | None = None,
) -> None:
    if env_var is None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(secret + "\n"))
        args = ["credentials", "add", name, "--provider", provider, "--secret-stdin"]
    else:
        monkeypatch.setenv(env_var, secret)
        args = [
            "credentials",
            "add",
            name,
            "--provider",
            provider,
            "--secret-env",
            env_var,
        ]
    code = main(args)
    captured = capsys.readouterr()
    assert code == 0, captured.err


def test_add_and_list_round_trip(evalh_home, capsys, monkeypatch):
    _add(capsys, monkeypatch, secret="sk-live-canary")

    code = main(["credentials", "list"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openai" in captured.out
    assert "created" in captured.out
    assert "last_verified" in captured.out
    assert "sk-live-canary" not in captured.out + captured.err

    database, repository = open_repository()
    try:
        records = repository.list_credentials()
        assert [record.name for record in records] == ["openai"]
        assert records[0].provider == "openai"
        assert records[0].last_ok_at is None
        assert records[0].last_error is None
    finally:
        database.dispose()


def test_add_reads_the_secret_from_the_environment(
    evalh_home, capsys, monkeypatch
):
    _add(
        capsys,
        monkeypatch,
        name="env-key",
        secret="sk-from-env",
        provider="anthropic",
        env_var="PROVIDER_SECRET",
    )

    database, repository = open_repository()
    try:
        record = repository.list_credentials()[0]
        assert record.name == "env-key"
        assert record.provider == "anthropic"
    finally:
        database.dispose()


def test_add_prompts_without_echoing(evalh_home, capsys, monkeypatch):
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "sk-prompted")

    code = main(["credentials", "add", "prompted", "--provider", "openai"])

    captured = capsys.readouterr()
    assert code == 0
    assert "added credential prompted" in captured.out
    assert "sk-prompted" not in captured.out + captured.err


def test_add_rejects_an_empty_secret(evalh_home, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))

    code = main(
        ["credentials", "add", "empty", "--provider", "openai", "--secret-stdin"]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "secret is empty" in captured.err


def test_add_rejects_a_missing_environment_variable(
    evalh_home, capsys, monkeypatch
):
    monkeypatch.delenv("ABSENT_SECRET", raising=False)

    code = main(
        [
            "credentials",
            "add",
            "absent",
            "--provider",
            "openai",
            "--secret-env",
            "ABSENT_SECRET",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "ABSENT_SECRET" in captured.err
    assert "unset or empty" in captured.err


def test_add_rejects_a_duplicate_name(evalh_home, capsys, monkeypatch):
    _add(capsys, monkeypatch)

    monkeypatch.setattr(sys, "stdin", io.StringIO("other-secret\n"))
    code = main(
        ["credentials", "add", "openai", "--provider", "openai", "--secret-stdin"]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "credentials.openai" in captured.err
    assert "already exists" in captured.err


def test_add_rejects_two_secret_sources(evalh_home, capsys):
    code = main(
        [
            "credentials",
            "add",
            "both",
            "--provider",
            "openai",
            "--secret-stdin",
            "--secret-env",
            "KEY",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "not allowed with argument" in captured.err


def test_test_command_verifies_and_records_last_ok(
    evalh_home, capsys, monkeypatch, verify_stub
):
    _add(capsys, monkeypatch, secret="sk-good")
    verify_stub.routes["/"] = (200, {"ok": True})

    code = main(
        ["credentials", "test", "openai", "--verify-url", verify_stub.base_url]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "verified: credential accepted" in captured.out
    headers = {
        str(key).lower(): value
        for key, value in verify_stub.requests[-1]["headers"].items()
    }
    assert headers["authorization"] == "Bearer sk-good"

    database, repository = open_repository()
    try:
        record = repository.list_credentials()[0]
        assert record.last_ok_at is not None
        assert record.last_error is None
    finally:
        database.dispose()


def test_rejected_key_exits_one_and_records_the_provider_message(
    evalh_home, capsys, monkeypatch, verify_stub
):
    _add(capsys, monkeypatch, secret="sk-bad")
    verify_stub.routes["/"] = (
        401,
        {"error": {"message": "Incorrect API key provided: sk-bad"}},
    )

    code = main(
        ["credentials", "test", "openai", "--verify-url", verify_stub.base_url]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "rejected:" in captured.out
    assert "Incorrect API key provided" in captured.out
    assert "sk-bad" not in captured.out
    assert "[redacted:openai]" in captured.out

    database, repository = open_repository()
    try:
        record = repository.list_credentials()[0]
        assert record.last_ok_at is None
        assert record.last_error == (
            "Incorrect API key provided: [redacted:openai]"
        )
    finally:
        database.dispose()


def test_test_command_rejects_an_unknown_name(evalh_home, capsys):
    code = main(["credentials", "test", "missing"])

    captured = capsys.readouterr()
    assert code == 2
    assert "unknown credential" in captured.err


def test_test_command_needs_a_verification_url(
    evalh_home, capsys, monkeypatch
):
    _add(capsys, monkeypatch)

    code = main(["credentials", "test", "openai"])

    captured = capsys.readouterr()
    assert code == 2
    assert "EVALH_VERIFY_URL" in captured.err


def test_test_command_uses_the_environment_url(
    evalh_home, capsys, monkeypatch, verify_stub
):
    _add(capsys, monkeypatch, secret="sk-env-url")
    verify_stub.routes["/"] = (200, {"ok": True})
    monkeypatch.setenv("EVALH_VERIFY_URL", verify_stub.base_url)

    code = main(["credentials", "test", "openai"])

    captured = capsys.readouterr()
    assert code == 0
    assert "verified" in captured.out


def test_transport_failure_exits_one_and_records_last_error(
    evalh_home, capsys, monkeypatch, verify_stub
):
    _add(capsys, monkeypatch, secret="sk-transport")
    verify_stub.routes["/"] = (200, None)

    code = main(
        ["credentials", "test", "openai", "--verify-url", verify_stub.base_url]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "verification request failed" in captured.out

    database, repository = open_repository()
    try:
        record = repository.list_credentials()[0]
        assert record.last_error is not None
        assert record.last_error.startswith("verification request failed")
    finally:
        database.dispose()


def test_header_and_no_auth_providers(
    evalh_home, capsys, monkeypatch, verify_stub
):
    verify_stub.routes["/"] = (200, {"ok": True})
    _add(
        capsys,
        monkeypatch,
        name="claude",
        secret="sk-ant",
        provider="anthropic",
    )
    assert (
        main(
            ["credentials", "test", "claude", "--verify-url", verify_stub.base_url]
        )
        == 0
    )
    capsys.readouterr()
    headers = {
        str(key).lower(): value
        for key, value in verify_stub.requests[-1]["headers"].items()
    }
    assert headers["x-api-key"] == "sk-ant"
    assert "authorization" not in headers

    _add(
        capsys,
        monkeypatch,
        name="local",
        secret="ignored",
        provider="none",
    )
    assert (
        main(
            ["credentials", "test", "local", "--verify-url", verify_stub.base_url]
        )
        == 0
    )
    capsys.readouterr()
    headers = {
        str(key).lower(): value
        for key, value in verify_stub.requests[-1]["headers"].items()
    }
    assert "authorization" not in headers
    assert "x-api-key" not in headers


async def test_http_verifier_reports_provider_messages(verify_stub):
    verify_stub.routes.update(
        {
            "/text": (401, "the key is wrong"),
            "/list": (400, []),
            "/error-string": (401, {"error": "bad key"}),
            "/openai": (401, {"error": {"message": "Incorrect API key"}}),
            "/error-dict": (401, {"error": {}}),
            "/message": (429, {"message": "slow down"}),
            "/empty": (500, {}),
            "/ok": (200, "{}"),
            "/ok-message": (200, {"message": "fine"}),
        }
    )
    async def result(path: str):
        verifier = HttpVerifier(verify_stub.url(path), timeout_s=2.0)
        return await verifier.verify("openai", "s")

    assert (await result("/text")).message == "the key is wrong"
    assert (await result("/list")).message == "Bad Request"
    assert (await result("/error-string")).message == "bad key"
    assert (await result("/openai")).message == "Incorrect API key"
    assert (await result("/error-dict")).message == "Unauthorized"
    assert (await result("/message")).message == "slow down"
    assert (await result("/empty")).message == "Internal Server Error"
    accepted = await result("/ok")
    assert accepted.ok is True
    assert accepted.message == "credential accepted"
    messaged = await result("/ok-message")
    assert messaged.ok is True
    assert messaged.message == "fine"


def test_delete_with_yes_removes_the_credential(evalh_home, capsys, monkeypatch):
    _add(capsys, monkeypatch)

    code = main(["credentials", "delete", "openai", "--yes"])

    captured = capsys.readouterr()
    assert code == 0
    assert "deleted credential openai" in captured.out
    assert main(["credentials", "list"]) == 0
    assert "no credentials stored" in capsys.readouterr().out


def test_delete_asks_for_confirmation(evalh_home, capsys, monkeypatch):
    _add(capsys, monkeypatch)

    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
    assert main(["credentials", "delete", "openai"]) == 0
    assert "not deleted" in capsys.readouterr().out

    assert main(["credentials", "list"]) == 0
    assert "openai" in capsys.readouterr().out

    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))
    assert main(["credentials", "delete", "openai"]) == 0
    assert "deleted credential openai" in capsys.readouterr().out


def test_delete_unknown_name_is_a_spec_error(evalh_home, capsys):
    code = main(["credentials", "delete", "missing", "--yes"])

    captured = capsys.readouterr()
    assert code == 2
    assert "unknown credential" in captured.err


def test_credentials_without_a_subcommand_prints_help(evalh_home, capsys):
    code = main(["credentials"])

    captured = capsys.readouterr()
    assert code == 0
    assert "usage: evalh credentials" in captured.out


def test_spec_accepts_a_vault_credential_and_rejects_an_unknown_one(
    evalh_home, workdir, capsys, monkeypatch
):
    _add(capsys, monkeypatch, name="shared", secret="sk-shared")
    spec_data = base_spec()
    spec_data["credentials"] = []
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["credential"] = "shared"
    spec_data["judges"] = [
        {
            "name": "judge-a",
            "adapter": "test:judge",
            "credential": "shared",
            "options": {},
        }
    ]
    spec_data["metrics"][0]["options"] = {"judge": "judge-a"}
    spec = write_spec(workdir / "vault-credential.yaml", spec_data)

    assert main(["validate", str(spec)]) == 0
    assert "spec valid" in capsys.readouterr().out

    spec_data["candidates"][0]["credential"] = "absent"
    missing = write_spec(workdir / "missing-credential.yaml", spec_data)
    code = main(["validate", str(missing)])

    captured = capsys.readouterr()
    assert code == 2
    assert "candidates[0].credential" in captured.err
    assert "unknown credential" in captured.err
    assert "evalh credentials add" in captured.err


def test_vault_secret_is_redacted_from_logs_events_artifacts_and_cells(
    evalh_home, workdir, capsys, monkeypatch
):
    canary = "CANARY-VAULT-6-SECRET"
    _add(capsys, monkeypatch, name="shared", secret=canary)
    spec_data = base_spec()
    spec_data["credentials"] = []
    spec_data["candidates"] = [
        {
            "name": "echo",
            "adapter": "test:credential_echo",
            "credential": "shared",
            "model": "stub-1",
            "options": {},
        }
    ]
    spec = write_spec(workdir / "canary.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert canary not in captured.out
    assert canary not in captured.err
    assert "[redacted:shared]" in captured.err
    run_id = run_id_from(captured.out)

    database, repository = open_repository()
    try:
        cell = repository.list_cells(run_id)[0]
        assert cell.answer == "[redacted:shared]"
        assert canary not in json.dumps(cell.output)
        assert cell.output["artifacts"]["credential_name"] == "shared"
        assert cell.output["artifacts"]["provider"] == "openai"
        assert cell.output["artifacts"]["secret_length"] == len(canary)

        artifact_dir = Path(cell.artifact_dir)
        for name in ("cell.json", "answer.txt"):
            text = (artifact_dir / name).read_text(encoding="utf-8")
            assert canary not in text
            assert "[redacted:shared]" in text

        events = repository.list_events(run_id)
        assert canary not in json.dumps(
            [event.model_dump(mode="json") for event in events]
        )
        note = [event for event in events if event.kind == "candidate_note"]
        assert note[0].payload["credential"] == "[redacted:shared]"

        checks = repository.list_checks(run_id, cell.cell_id)
        assert canary not in json.dumps(
            [check.model_dump(mode="json") for check in checks]
        )
        assert checks[0].evidence["actual"] == "[redacted:shared]"
    finally:
        database.dispose()

    raw = (evalh_home / "harness.db").read_bytes()
    assert canary.encode("utf-8") not in raw
