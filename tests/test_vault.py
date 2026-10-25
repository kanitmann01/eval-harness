"""Vault bootstrap, encryption at rest and credential metadata storage."""

from __future__ import annotations

import base64
import io
import re
import sqlite3
import sys

import pytest
from cryptography.fernet import InvalidToken

from evalharness.cli import main
from evalharness.core.config import Settings
from evalharness.core.models import utc_now
from evalharness.core.vault import (
    MasterKeyError,
    Vault,
    VaultCredentialStore,
    generate_master_key,
    open_vault,
)
from evalharness.errors import SpecError
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import TEST_MASTER_KEY, base_spec, write_spec


def _add_secret(capsys, monkeypatch, name: str, secret: str, provider="openai"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(secret + "\n"))
    code = main(
        ["credentials", "add", name, "--provider", provider, "--secret-stdin"]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return captured


def test_missing_master_key_refuses_and_prints_it_once(
    evalh_home, capsys, monkeypatch
):
    monkeypatch.delenv("EVALH_MASTER_KEY", raising=False)
    monkeypatch.delenv("EVALH_MASTER_KEY_FILE", raising=False)

    code = main(["credentials", "list"])

    captured = capsys.readouterr()
    assert code == 2
    assert "shown once" in captured.err
    keys = re.findall(r"EVALH_MASTER_KEY=([A-Za-z0-9_\-]{43}=)", captured.err)
    assert len(keys) == 1
    assert captured.err.count(f"EVALH_MASTER_KEY={keys[0]}") == 1
    assert len(base64.urlsafe_b64decode(keys[0])) == 32
    assert not evalh_home.exists()

    monkeypatch.setenv("EVALH_MASTER_KEY", keys[0])
    assert main(["credentials", "list"]) == 0
    second = capsys.readouterr()
    assert "no credentials stored" in second.out
    assert "generated one" not in second.err


@pytest.mark.parametrize(
    "value",
    [
        "not-base64",
        base64.urlsafe_b64encode(b"too-short").decode("ascii"),
    ],
)
def test_malformed_master_key_names_the_variable(
    evalh_home, capsys, monkeypatch, value
):
    monkeypatch.setenv("EVALH_MASTER_KEY", value)

    code = main(["credentials", "list"])

    captured = capsys.readouterr()
    assert code == 2
    assert "EVALH_MASTER_KEY" in captured.err
    assert "32 bytes" in captured.err
    assert "generated one" not in captured.err


def test_malformed_master_key_file_names_the_file_variable(
    evalh_home, capsys, monkeypatch, workdir
):
    monkeypatch.delenv("EVALH_MASTER_KEY")
    key_file = workdir / "master.key"
    key_file.write_text("still-not-base64", encoding="utf-8")
    monkeypatch.setenv("EVALH_MASTER_KEY_FILE", str(key_file))

    code = main(["credentials", "list"])

    captured = capsys.readouterr()
    assert code == 2
    assert "EVALH_MASTER_KEY_FILE" in captured.err
    assert "32 bytes" in captured.err


def test_master_key_file_is_read(evalh_home, capsys, monkeypatch, workdir):
    monkeypatch.delenv("EVALH_MASTER_KEY")
    key_file = workdir / "master.key"
    key_file.write_text(TEST_MASTER_KEY + "\n", encoding="utf-8")
    monkeypatch.setenv("EVALH_MASTER_KEY_FILE", str(key_file))

    code = main(["credentials", "list"])

    captured = capsys.readouterr()
    assert code == 0
    assert "no credentials stored" in captured.out


def test_unreadable_master_key_file_is_actionable(
    evalh_home, capsys, monkeypatch, workdir
):
    monkeypatch.delenv("EVALH_MASTER_KEY")
    monkeypatch.setenv("EVALH_MASTER_KEY_FILE", str(workdir / "absent.key"))

    code = main(["credentials", "list"])

    captured = capsys.readouterr()
    assert code == 2
    assert "EVALH_MASTER_KEY_FILE" in captured.err
    assert "could not be read" in captured.err


def test_vault_round_trip_fingerprint_and_wrong_key():
    vault = Vault.from_text(generate_master_key())
    ciphertext = vault.encrypt("sk-canary")
    assert ciphertext != b"sk-canary"
    assert vault.decrypt(ciphertext) == "sk-canary"
    assert vault.fingerprint("sk-canary") == vault.fingerprint("sk-canary")
    assert vault.fingerprint("sk-canary") != vault.fingerprint("other")
    assert "sk-canary" not in vault.fingerprint("sk-canary")
    with pytest.raises(InvalidToken):
        Vault.from_text(generate_master_key()).decrypt(ciphertext)


def test_open_vault_reports_missing_key_as_generated():
    with pytest.raises(MasterKeyError) as raised:
        open_vault(Settings.from_env({}))
    assert raised.value.generated_key is not None
    assert "no master key is configured" in str(raised.value)


def test_vault_credential_store_decrypts_lazily(workdir):
    database = Database(f"sqlite:///{(workdir / 'store.db').as_posix()}")
    database.create_all()
    repository = Repository(database)
    vault = Vault.from_text(TEST_MASTER_KEY)
    repository.add_credential("stored", "openai", vault.encrypt("sk-stored"))

    store = VaultCredentialStore(vault, repository)
    handle = store.get("stored")
    assert handle is not None
    assert handle.name == "stored"
    assert handle.provider == "openai"
    assert handle.secret == "sk-stored"
    assert store.names() == ["stored"]
    assert store.get("missing") is None

    rotated = VaultCredentialStore(Vault.from_text(generate_master_key()), repository)
    with pytest.raises(SpecError) as raised:
        rotated.get("stored")
    assert "current master key" in str(raised.value)
    database.dispose()


def test_repository_credential_metadata_and_errors(workdir):
    database = Database(f"sqlite:///{(workdir / 'repo.db').as_posix()}")
    database.create_all()
    repository = Repository(database)
    assert repository.list_credentials() == []

    record = repository.add_credential(
        "one", "openai", b"ciphertext", created_at=utc_now()
    )
    assert record.name == "one"
    assert repository.get_credential("missing") is None
    stored = repository.get_credential("one")
    assert stored is not None
    assert stored.ciphertext == b"ciphertext"

    with pytest.raises(SpecError) as duplicate:
        repository.add_credential("one", "openai", b"other")
    assert "already exists" in str(duplicate.value)

    verified = repository.test_credential("one", tested_at=utc_now())
    assert verified.last_ok_at is not None
    assert verified.last_error is None

    failed = repository.test_credential("one", error="provider said no")
    assert failed.last_error == "provider said no"
    assert failed.last_ok_at is not None

    with pytest.raises(SpecError):
        repository.test_credential("missing")
    with pytest.raises(SpecError):
        repository.delete_credential("missing")

    repository.delete_credential("one")
    assert repository.list_credentials() == []
    database.dispose()


def test_database_dump_contains_no_key_material(
    evalh_home, capsys, monkeypatch
):
    canary = "sk-canary-raw-dump"
    _add_secret(capsys, monkeypatch, "openai", canary)

    database_path = evalh_home / "harness.db"
    raw = database_path.read_bytes()
    assert canary.encode("utf-8") not in raw

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "select ciphertext from credentials where name = ?", ("openai",)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert canary.encode("utf-8") not in bytes(row[0])


def test_run_without_master_key_refuses(evalh_home, workdir, capsys, monkeypatch):
    monkeypatch.delenv("EVALH_MASTER_KEY", raising=False)
    monkeypatch.delenv("EVALH_MASTER_KEY_FILE", raising=False)
    spec = write_spec(workdir / "spec.yaml", base_spec())

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "EVALH_MASTER_KEY=" in captured.err
    assert not evalh_home.exists()


def test_rotated_master_key_refuses_the_run(
    evalh_home, workdir, capsys, monkeypatch
):
    _add_secret(capsys, monkeypatch, "shared", "sk-rotated")
    monkeypatch.setenv("EVALH_MASTER_KEY", generate_master_key())
    spec_data = base_spec()
    spec_data["credentials"] = []
    spec_data["candidates"] = [spec_data["candidates"][0]]
    spec_data["candidates"][0]["credential"] = "shared"
    spec = write_spec(workdir / "rotated.yaml", spec_data)

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 2
    assert "cannot be decrypted" in captured.err


def test_credentials_with_unwritable_home_is_a_spec_error(
    evalh_home, capsys
):
    evalh_home.write_text("not a directory", encoding="utf-8")

    code = main(["credentials", "list"])

    captured = capsys.readouterr()
    assert code == 2
    assert "EVALH_HOME" in captured.err
