"""HTTP seam tests for the credentials console.

Every test drives the application built by `create_app` through
`httpx.AsyncClient(transport=ASGITransport)`; the CLI is used only where the
ticket asks for equivalence. A local stub stands in for the provider, so no
test reaches the internet. A canary secret is submitted through both surfaces
and asserted absent from every response, redirect, log line and stored row.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.api.app import create_app
from evalharness.cli import main
from evalharness.core.config import Settings
from evalharness.core.vault import Vault, generate_master_key
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import TEST_MASTER_KEY, base_spec
from tests.test_api_console import Console, _client

CANARY = "CANARY-CREDENTIALS-13-SECRET"
"""A distinctive value no response, redirect or row may contain."""

TEXT = {"content-type": "text/plain"}


@pytest.fixture
def console(
    evalh_home: Path,
    workdir: Path,
    verify_stub: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Console]:
    """An application with a fresh database, specs directory and verify stub."""
    monkeypatch.setenv("EVALH_VERIFY_URL", verify_stub.base_url)
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


def _spec_text(
    *,
    candidate: str | None = None,
    judge: str | None = None,
) -> str:
    """Return a spec that needs no secret but names a credential where asked."""
    spec = base_spec()
    spec["credentials"] = []
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0].pop("credential", None)
    spec["candidates"][0]["options"] = {}
    if candidate is not None:
        spec["candidates"][0]["credential"] = candidate
    if judge is not None:
        spec["judges"] = [
            {
                "name": "judge-a",
                "adapter": "test:judge",
                "credential": judge,
                "options": {},
            }
        ]
        spec["metrics"][0]["options"] = {"judge": "judge-a"}
    return yaml.safe_dump(spec, sort_keys=False)


def _write_spec(console: Console, name: str, text: str) -> Path:
    """Write a spec file directly so the editor page can open it."""
    path = console.settings.specs_dir / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def _add(
    client: Any,
    *,
    name: str = "openai",
    provider: str = "openai",
    secret: str = "sk-canary-add",
) -> Any:
    """POST one credential to the JSON API."""
    return await client.post(
        "/api/credentials",
        json={"name": name, "provider": provider, "secret": secret},
    )


async def _validate(client: Any, text: str) -> Any:
    """POST one YAML document to the shared validation route."""
    return await client.post(
        "/api/specs/validate", content=text.encode("utf-8"), headers=TEXT
    )


async def test_add_and_list_round_trip(console: Console) -> None:
    async with _client(console.app) as client:
        created = await _add(client)
        assert created.status_code == 201
        payload = created.json()
        assert payload["name"] == "openai"
        assert payload["provider"] == "openai"
        assert payload["disabled"] is False
        assert payload["state"] == "unverified"
        assert payload["usable"] is False
        assert payload["last_ok_at"] is None
        assert payload["last_error"] is None
        assert payload["created_at"].endswith("+00:00")
        assert payload["fingerprint"].startswith("sha256:")
        assert "sk-canary-add" not in created.text

        listed = await client.get("/api/credentials")
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["name"] == "openai"
        assert rows[0]["fingerprint"] == payload["fingerprint"]
        assert "sk-canary-add" not in listed.text

        page = await client.get("/credentials")
        assert page.status_code == 200
        assert "openai" in page.text
        assert "unverified" in page.text
        assert "1 need attention" in page.text
        assert payload["fingerprint"] in page.text
        assert "sk-canary-add" not in page.text

    record = console.repository.list_credentials()[0]
    assert record.name == "openai"
    assert record.disabled is False
    assert not hasattr(record, "ciphertext")
    stored = console.repository.get_credential("openai")
    assert stored is not None
    assert b"sk-canary-add" not in stored.ciphertext
    raw = (Path(console.settings.home) / "harness.db").read_bytes()
    assert b"sk-canary-add" not in raw


def test_fingerprint_is_the_vault_prefix_and_hides_the_secret(console: Console) -> None:
    async def add_two() -> list[dict[str, Any]]:
        async with _client(console.app) as client:
            first = await _add(client, name="first", secret="same-secret")
            second = await _add(client, name="second", secret="same-secret")
        return [first.json(), second.json()]

    rows = asyncio.run(add_two())
    vault = Vault.from_text(TEST_MASTER_KEY)
    assert rows[0]["fingerprint"] == vault.fingerprint("same-secret")
    assert rows[1]["fingerprint"] == rows[0]["fingerprint"]
    assert "same-secret" not in rows[0]["fingerprint"]

    other = Vault.from_text(generate_master_key())
    console.repository.add_credential(
        "foreign", "openai", other.encrypt("another-secret")
    )

    async def list_rows() -> list[dict[str, Any]]:
        async with _client(console.app) as client:
            return (await client.get("/api/credentials")).json()

    listed = asyncio.run(list_rows())
    by_name = {row["name"]: row for row in listed}
    assert by_name["foreign"]["fingerprint"] is None
    assert by_name["first"]["fingerprint"] == vault.fingerprint("same-secret")


async def test_add_rejects_bad_values_without_echoing_the_secret(
    console: Console,
) -> None:
    async with _client(console.app) as client:
        assert (await _add(client)).status_code == 201

        duplicate = await _add(client, secret=CANARY)
        assert duplicate.status_code == 409
        error = duplicate.json()["errors"][0]
        assert error["field"] == "credentials.openai"
        assert "already exists" in error["reason"]
        assert CANARY not in duplicate.text

        bad_provider = await _add(
            client, name="other", provider="custom", secret=CANARY
        )
        assert bad_provider.status_code == 400
        error = bad_provider.json()["errors"][0]
        assert error["field"] == "provider"
        assert "openai" in error["fix"]
        assert CANARY not in bad_provider.text

        bad_name = await _add(client, name="../escape", secret=CANARY)
        assert bad_name.status_code == 400
        assert bad_name.json()["errors"][0]["field"] == "name"
        assert CANARY not in bad_name.text

        long_name = await _add(client, name="a" * 201, secret=CANARY)
        assert long_name.status_code == 400
        assert "longer than 200" in long_name.json()["errors"][0]["reason"]

        empty = await _add(client, name="empty", secret="")
        assert empty.status_code == 400
        assert empty.json()["errors"][0]["value"] == "<empty>"

        not_text = await _add(client, name="number", secret=42)  # type: ignore[arg-type]
        assert not_text.status_code == 400
        assert not_text.json()["errors"][0]["value"] == "<not text>"

        non_text_name = await _add(client, name=42)  # type: ignore[arg-type]
        assert non_text_name.status_code == 400
        assert non_text_name.json()["errors"][0]["value"] == "<not text>"

        missing = await client.post("/api/credentials", json={})
        assert missing.status_code == 400
        assert missing.json()["errors"][0]["field"] == "name"
        assert missing.json()["errors"][0]["value"] == "<missing>"

        broken = await client.post(
            "/api/credentials",
            content=('{"name": "x", "secret": "' + CANARY).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        assert broken.status_code == 400
        assert broken.json()["errors"][0]["field"] == "body"
        assert CANARY not in broken.text

        not_object = await client.post("/api/credentials", json=["x"])
        assert not_object.status_code == 400
        assert not_object.json()["errors"][0]["field"] == "body"


async def test_a_successful_test_updates_last_verified(
    console: Console, verify_stub: Any
) -> None:
    verify_stub.routes["/"] = (200, {"message": "fine"})
    async with _client(console.app) as client:
        assert (await _add(client, secret="sk-good")).status_code == 201
        tested = await client.post("/api/credentials/openai/test")
        assert tested.status_code == 200
        payload = tested.json()
        assert payload["verified"] is True
        assert payload["message"] == "fine"
        credential = payload["credential"]
        assert credential["state"] == "verified"
        assert credential["usable"] is True
        assert credential["last_ok_at"] is not None
        assert credential["last_error"] is None

        page = await client.get("/credentials")
        assert "verified" in page.text
        assert "fine" not in page.text

        listed = (await client.get("/api/credentials")).json()
        assert listed[0]["last_ok_at"] == credential["last_ok_at"]

    headers = {
        str(key).lower(): value
        for key, value in verify_stub.requests[-1]["headers"].items()
    }
    assert headers["authorization"] == "Bearer sk-good"
    record = console.repository.list_credentials()[0]
    assert record.last_ok_at is not None
    assert record.last_error is None


async def test_a_failed_test_stores_the_redacted_provider_message(
    console: Console, verify_stub: Any
) -> None:
    secret = "sk-bad-canary"
    verify_stub.routes["/"] = (
        401,
        {"error": {"message": f"Incorrect API key provided: {secret}"}},
    )
    async with _client(console.app) as client:
        assert (await _add(client, secret=secret)).status_code == 201
        tested = await client.post("/api/credentials/openai/test")
        assert tested.status_code == 200
        payload = tested.json()
        assert payload["verified"] is False
        assert payload["message"] == (
            "Incorrect API key provided: [redacted:openai]"
        )
        assert secret not in tested.text
        assert payload["credential"]["state"] == "unverified"
        assert payload["credential"]["last_ok_at"] is None

        page = await client.get("/credentials")
        assert "[redacted:openai]" in page.text
        assert secret not in page.text

        verify_stub.routes["/"] = (200, {"message": "fine"})
        again = await client.post("/api/credentials/openai/test")
        assert again.json()["verified"] is True
        assert again.json()["credential"]["last_error"] is None

    record = console.repository.list_credentials()[0]
    assert record.last_error is None
    assert record.last_ok_at is not None


async def test_a_huge_provider_error_is_stored_bounded(
    console: Console, verify_stub: Any
) -> None:
    """A provider that returns a whole HTML page must not fill the column."""
    verify_stub.routes["/"] = (500, {"error": {"message": "x" * 5000}})
    async with _client(console.app) as client:
        assert (await _add(client)).status_code == 201
        tested = await client.post("/api/credentials/openai/test")
        assert tested.status_code == 200
        rows = (await client.get("/api/credentials")).json()
        stored = next(row for row in rows if row["name"] == "openai")
        assert stored["last_error"] is not None
        assert len(stored["last_error"]) == 500


async def test_disable_and_enable_round_trip(
    console: Console, verify_stub: Any
) -> None:
    verify_stub.routes["/"] = (200, {"message": "fine"})
    async with _client(console.app) as client:
        assert (await _add(client)).status_code == 201
        assert (await client.post("/api/credentials/openai/test")).status_code == 200

        disabled = await client.post("/api/credentials/openai/disable")
        assert disabled.status_code == 200
        assert disabled.json()["disabled"] is True
        assert disabled.json()["state"] == "disabled"
        assert disabled.json()["usable"] is False
        page = await client.get("/credentials")
        assert "disabled" in page.text

        enabled = await client.post("/api/credentials/openai/enable")
        assert enabled.status_code == 200
        assert enabled.json()["disabled"] is False
        assert enabled.json()["state"] == "verified"
        page = await client.get("/credentials")
        assert "verified" in page.text

    record = console.repository.list_credentials()[0]
    assert record.disabled is False


async def test_delete_removes_it_and_the_cli_list_agrees(
    console: Console, capsys: pytest.CaptureFixture[str]
) -> None:
    async with _client(console.app) as client:
        assert (await _add(client)).status_code == 201
        deleted = await client.delete("/api/credentials/openai")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": "openai"}
        assert (await client.get("/api/credentials")).json() == []
    assert console.repository.get_credential("openai") is None

    assert main(["credentials", "list"]) == 0
    captured = capsys.readouterr()
    assert "no credentials stored" in captured.out


async def test_unknown_names_are_404_for_api_and_forms(console: Console) -> None:
    async with _client(console.app) as client:
        for method, path in (
            ("post", "/api/credentials/missing/test"),
            ("post", "/api/credentials/missing/disable"),
            ("post", "/api/credentials/missing/enable"),
        ):
            response = await getattr(client, method)(path)
            assert response.status_code == 404, path
            assert response.json()["errors"][0]["field"] == "credentials.missing"
        deleted = await client.delete("/api/credentials/missing")
        assert deleted.status_code == 404
        assert deleted.json()["errors"][0]["reason"] == "unknown credential"

        for path in (
            "/credentials/missing/test",
            "/credentials/missing/disable",
            "/credentials/missing/enable",
            "/credentials/missing/delete",
        ):
            response = await client.post(path)
            assert response.status_code == 404, path
            assert "does not exist" in response.text


async def test_editor_refuses_an_unverified_then_disabled_credential(
    console: Console, verify_stub: Any
) -> None:
    verify_stub.routes["/"] = (200, {"message": "fine"})
    text = _spec_text(candidate="shared")
    path = _write_spec(console, "shared", text)
    before_stat = path.stat().st_mtime_ns
    async with _client(console.app) as client:
        assert (await _add(client, name="shared")).status_code == 201

        refused = await _validate(client, text)
        assert refused.status_code == 400
        error = refused.json()["errors"][0]
        assert error["field"] == "candidates[0].credential"
        assert "never been verified" in error["reason"]
        assert "/credentials" in error["fix"]

        blocked = await client.post(
            "/specs/shared/edit", data={"yaml": text, "action": "save"}
        )
        assert blocked.status_code == 400
        assert "candidates[0].credential" in blocked.text
        assert "never been verified" in blocked.text
        assert path.stat().st_mtime_ns == before_stat

        page = await client.get("/specs/shared/edit")
        assert "not selectable until you test or enable it" in page.text
        assert "unverified" in page.text

        assert (await client.post("/api/credentials/shared/test")).json()[
            "verified"
        ] is True
        valid = await _validate(client, text)
        assert valid.status_code == 200
        assert valid.json()["valid"] is True

        assert (await client.post("/api/credentials/shared/disable")).status_code == 200
        disabled = await _validate(client, text)
        assert disabled.status_code == 400
        error = disabled.json()["errors"][0]
        assert error["field"] == "candidates[0].credential"
        assert "disabled" in error["reason"]
        assert "enable" in error["fix"]
        page = await client.get("/specs/shared/edit")
        assert "disabled" in page.text
        assert "not selectable until you test or enable it" in page.text

        assert (await client.post("/api/credentials/shared/enable")).status_code == 200
        saved = await client.post(
            "/specs/shared/edit", data={"yaml": text, "action": "save"}
        )
        assert saved.status_code == 303
        assert path.read_text(encoding="utf-8") == text


async def test_editor_refuses_a_disabled_judge_credential(
    console: Console, verify_stub: Any
) -> None:
    verify_stub.routes["/"] = (200, {"message": "fine"})
    text = _spec_text(judge="judge-key")
    async with _client(console.app) as client:
        assert (await _add(client, name="judge-key")).status_code == 201
        assert (
            await client.post("/api/credentials/judge-key/test")
        ).json()["verified"] is True
        assert (
            await client.post("/api/credentials/judge-key/disable")
        ).status_code == 200
        refused = await _validate(client, text)
        assert refused.status_code == 400
        error = refused.json()["errors"][0]
        assert error["field"] == "judges[0].credential"
        assert "disabled" in error["reason"]
        assert "enable" in error["fix"]

        assert (
            await client.post("/api/credentials/judge-key/enable")
        ).status_code == 200
        valid = await _validate(client, text)
        assert valid.status_code == 200


async def test_a_declared_credential_without_a_secret_still_validates(
    console: Console,
) -> None:
    spec = base_spec()
    text = yaml.safe_dump(spec, sort_keys=False)
    async with _client(console.app) as client:
        accepted = await _validate(client, text)
        assert accepted.status_code == 200
        assert accepted.json()["valid"] is True
    path = _write_spec(console, "declared", text)
    assert main(["validate", str(path)]) == 0


async def test_the_page_works_without_javascript(
    console: Console, verify_stub: Any
) -> None:
    verify_stub.routes["/"] = (200, {"message": "fine"})
    async with _client(console.app) as client:
        page = await client.get("/credentials")
        assert page.status_code == 200
        assert 'method="post"' in page.text
        assert 'name="name"' in page.text
        assert 'name="provider"' in page.text
        assert 'type="password"' in page.text
        for provider in ("openai", "anthropic", "azure-openai", "none"):
            assert f'value="{provider}"' in page.text
        for auth in ("Bearer token", "x-api-key", "api-key", "no auth header"):
            assert auth in page.text

        added = await client.post(
            "/credentials",
            data={
                "name": "form-key",
                "provider": "anthropic",
                "secret": "sk-form-canary",
            },
        )
        assert added.status_code == 303
        assert added.headers["location"] == "/credentials?added=form-key"
        assert "sk-form-canary" not in added.headers["location"]
        listing = await client.get(added.headers["location"])
        assert "Added <code>form-key</code>" in listing.text
        assert "sk-form-canary" not in listing.text

        invalid = await client.post(
            "/credentials",
            data={
                "name": "bad-key",
                "provider": "custom",
                "secret": "sk-form-canary",
            },
        )
        assert invalid.status_code == 400
        assert 'data-credential-error' in invalid.text
        assert "sk-form-canary" not in invalid.text

        duplicate = await client.post(
            "/credentials",
            data={
                "name": "form-key",
                "provider": "openai",
                "secret": "sk-form-canary",
            },
        )
        assert duplicate.status_code == 409
        assert "already exists" in duplicate.text
        assert "sk-form-canary" not in duplicate.text

        tested = await client.post("/credentials/form-key/test")
        assert tested.status_code == 303
        assert tested.headers["location"] == "/credentials?tested=form-key&ok=1"
        passed = await client.get(tested.headers["location"])
        assert "Test passed" in passed.text

        disabled = await client.post("/credentials/form-key/disable")
        assert disabled.headers["location"] == (
            "/credentials?changed=form-key&state=disabled"
        )
        assert "is now disabled" in (await client.get(disabled.headers["location"])).text

        enabled = await client.post("/credentials/form-key/enable")
        assert enabled.headers["location"] == (
            "/credentials?changed=form-key&state=enabled"
        )
        assert "is now enabled" in (await client.get(enabled.headers["location"])).text

        verify_stub.routes["/"] = (401, {"error": "rejected key"})
        failed = await client.post("/credentials/form-key/test")
        assert failed.headers["location"] == "/credentials?tested=form-key&ok=0"
        assert "Test failed" in (await client.get(failed.headers["location"])).text

        deleted = await client.post("/credentials/form-key/delete")
        assert deleted.headers["location"] == "/credentials?deleted=form-key"
        listing = await client.get(deleted.headers["location"])
        assert "Deleted <code>form-key</code>" in listing.text
        assert "No credentials stored" in listing.text


async def test_the_form_reports_a_missing_verify_url(console: Console) -> None:
    settings = replace(console.settings, verify_url=None)
    app = create_app(settings=settings, repository=console.repository)
    async with _client(app) as client:
        assert (await _add(client)).status_code == 201
        refused = await client.post("/credentials/openai/test")
        assert refused.status_code == 400
        assert "EVALH_VERIFY_URL" in refused.text
        assert "sk-canary-add" not in refused.text

        api = await client.post("/api/credentials/openai/test")
        assert api.status_code == 400
        assert api.json()["errors"][0]["field"] == "EVALH_VERIFY_URL"


async def test_vault_unavailable_is_a_page_and_a_json_error(console: Console) -> None:
    settings = replace(
        console.settings, master_key=None, master_key_file=None
    )
    app = create_app(settings=settings, repository=console.repository)
    async with _client(app) as client:
        page = await client.get("/credentials")
        assert page.status_code == 503
        assert "Credentials unavailable" in page.text
        assert "EVALH_MASTER_KEY" in page.text
        assert re.search(r"EVALH_MASTER_KEY=[A-Za-z0-9_\-]{43}=", page.text) is None

        listed = await client.get("/api/credentials")
        assert listed.status_code == 503
        assert listed.json()["errors"][0]["field"] == "EVALH_MASTER_KEY"

        added = await _add(client)
        assert added.status_code == 503
        tested = await client.post("/api/credentials/openai/test")
        assert tested.status_code == 503
        disabled = await client.post("/api/credentials/openai/disable")
        assert disabled.status_code == 503
        enabled = await client.post("/api/credentials/openai/enable")
        assert enabled.status_code == 503

        form = await client.post(
            "/credentials",
            data={"name": "x", "provider": "openai", "secret": "sk-x"},
        )
        assert form.status_code == 503
        form_test = await client.post("/credentials/x/test")
        assert form_test.status_code == 503


async def test_the_canary_never_surfaces_anywhere(
    console: Console,
    verify_stub: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    verify_stub.routes["/"] = (
        401,
        {"error": {"message": f"Incorrect API key provided: {CANARY}"}},
    )
    spec_text = _spec_text(candidate="api-canary")
    _write_spec(console, "canary", spec_text)
    form_payload = {"name": "form-canary", "provider": "openai", "secret": CANARY}
    bodies: list[str] = []
    locations: list[str] = []

    caplog.set_level(logging.INFO, logger="evalharness")
    async with _client(console.app) as client:
        form = await client.post("/credentials", data=form_payload)
        bodies.append(form.text)
        locations.append(form.headers.get("location", ""))

        api = await _add(client, name="api-canary", secret=CANARY)
        bodies.append(api.text)

        duplicate = await _add(client, name="api-canary", secret=CANARY)
        bodies.append(duplicate.text)
        form_duplicate = await client.post("/credentials", data=form_payload)
        bodies.append(form_duplicate.text)

        invalid = await client.post(
            "/credentials",
            data={
                "name": "bad-provider",
                "provider": "custom",
                "secret": CANARY,
            },
        )
        bodies.append(invalid.text)

        failed_api = await client.post("/api/credentials/api-canary/test")
        bodies.append(failed_api.text)
        failed_form = await client.post("/credentials/form-canary/test")
        bodies.append(failed_form.text)
        locations.append(failed_form.headers.get("location", ""))

        for path in (
            "/credentials",
            failed_form.headers["location"],
            "/api/credentials",
            "/specs/canary/edit",
        ):
            response = await client.get(path)
            bodies.append(response.text)

        refused = await _validate(client, spec_text)
        bodies.append(refused.text)
        assert refused.json()["errors"][0]["field"] == "candidates[0].credential"

        disabled = await client.post("/api/credentials/api-canary/disable")
        bodies.append(disabled.text)
        enabled = await client.post("/api/credentials/api-canary/enable")
        bodies.append(enabled.text)
        deleted = await client.delete("/api/credentials/api-canary")
        bodies.append(deleted.text)
        assert deleted.status_code == 200

        # The surviving row holds ciphertext and a redacted message only.
        stored = console.repository.get_credential("form-canary")
        assert stored is not None
        assert CANARY.encode("utf-8") not in stored.ciphertext
        assert stored.last_error == (
            "Incorrect API key provided: [redacted:form-canary]"
        )
        raw = (Path(console.settings.home) / "harness.db").read_bytes()
        assert CANARY.encode("utf-8") not in raw

        form_deleted = await client.post("/credentials/form-canary/delete")
        bodies.append(form_deleted.text)
        locations.append(form_deleted.headers.get("location", ""))

    for body in bodies:
        assert CANARY not in body
    for location in locations:
        assert CANARY not in location
    assert CANARY not in caplog.text
    assert console.repository.get_credential("api-canary") is None
    assert console.repository.get_credential("form-canary") is None
