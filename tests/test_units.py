"""Unit tests for the public core utilities and storage primitives."""

from __future__ import annotations

import asyncio
import runpy
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from evalharness.adapters.metrics.contains import build as build_contains
from evalharness.adapters.sinks.json_sink import build as build_json_sink
from evalharness.cli import main
from evalharness.core.config import Settings, harness_revision
from evalharness.core.context import (
    CredentialHandle,
    EnvCredentialStore,
    InMemoryCredentialStore,
    RunContext,
    credential_env_var,
)
from evalharness.core.cost import Price, estimate_cost, split_cost, usage_cost
from evalharness.core.events import EventEmitter
from evalharness.core.models import (
    Aggregate,
    CandidateOutput,
    Case,
    CellRecord,
    Check,
    ContextChunk,
    CredentialSpec,
    GateSpec,
    RunRecord,
    ScoreContext,
    Spec,
    utc_now,
)
from evalharness.core.planner import cell_id_for
from evalharness.core.redaction import NoOpRedactor, SecretRedactor
from evalharness.core.registry import build, register, resolve
from evalharness.core.scorer import aggregate_cells, evaluate_gates
from evalharness.core.spec import load_spec
from evalharness.errors import RegistryError, SpecError
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository
from tests.helpers import base_spec


def _sample_factory(identity: str, options: dict) -> tuple[str, dict]:
    return identity, dict(options)


def test_register_resolve_and_build():
    register("test:unit_factory", _sample_factory)
    register("test:unit_factory", _sample_factory)
    assert resolve("test:unit_factory") is _sample_factory
    assert build("test:unit_factory", "identity", {"a": 1}) == ("identity", {"a": 1})


def test_register_duplicate_name_fails():
    register("test:unit_duplicate", _sample_factory)
    with pytest.raises(RegistryError) as raised:
        register("test:unit_duplicate", lambda identity, options: None)
    assert "already registered" in str(raised.value)


def test_resolve_unregistered_short_name():
    with pytest.raises(RegistryError) as raised:
        resolve("definitely-not-registered")
    assert "not a registered name" in str(raised.value)


def test_resolve_needs_module_and_attribute():
    with pytest.raises(RegistryError):
        resolve(":attr")
    with pytest.raises(RegistryError):
        resolve("module:")


def test_resolve_missing_module():
    with pytest.raises(RegistryError) as raised:
        resolve("definitely_missing_module_xyz:factory")
    assert "could not be imported" in str(raised.value)


def test_resolve_missing_attribute():
    with pytest.raises(RegistryError) as raised:
        resolve("evalharness.core.registry:missing_attribute")
    assert "no attribute" in str(raised.value)


def test_resolve_non_callable():
    with pytest.raises(RegistryError) as raised:
        resolve("evalharness.core.registry:_FACTORIES")
    assert "not callable" in str(raised.value)


def test_error_strings_name_field_and_fix():
    spec_error = SpecError("field", "value", "reason", "do this")
    assert str(spec_error) == "field='value': reason. Fix: do this"
    registry_error = RegistryError("path", "reason", "do this")
    assert str(registry_error) == "'path': reason. Fix: do this"


def test_noop_and_secret_redaction():
    noop = NoOpRedactor()
    assert noop.redact("secret") == "secret"
    assert noop.redact_data({"a": "secret"}) == {"a": "secret"}

    redactor = SecretRedactor({"alpha": "longsecret", "beta": "secret", "empty": ""})
    assert redactor.redact("longsecret and secret") == (
        "[redacted:alpha] and [redacted:beta]"
    )
    redacted = redactor.redact_data(
        {"text": "secret", "items": ["longsecret", 1, ("secret",)]}
    )
    assert redacted == {
        "text": "[redacted:beta]",
        "items": ["[redacted:alpha]", 1, ("[redacted:beta]",)],
    }


def test_event_emitter_sequence_subscription_and_redaction():
    appended = []
    listener_events = []
    listener = listener_events.append
    emitter = EventEmitter(
        "run-events",
        append=appended.append,
        redactor=SecretRedactor({"canary": "SECRET"}),
        start_seq=4,
    )
    assert emitter.seq == 4
    emitter.subscribe(listener)
    event = emitter.emit("kind", {"value": "SECRET", "nested": [{"x": "SECRET"}]})
    assert event.seq == 5
    assert event.payload == {
        "value": "[redacted:canary]",
        "nested": [{"x": "[redacted:canary]"}],
    }
    assert appended == [event]
    assert listener_events == [event]
    emitter.unsubscribe(listener)
    emitter.unsubscribe(listener)
    emitter.emit("next")
    assert listener_events == [event]
    assert emitter.seq == 6


def test_event_emitter_without_append():
    emitter = EventEmitter("run-no-append")
    event = emitter.emit("kind")
    assert event.payload == {}
    assert emitter.seq == 1


def test_settings_defaults_and_overrides(monkeypatch):
    for key in (
        "EVALH_HOME",
        "EVALH_DATABASE_URL",
        "EVALH_ARTIFACTS_DIR",
        "EVALH_MASTER_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    defaults = Settings.from_env({})
    assert defaults.home == Path.home() / ".evalharness"
    assert defaults.database_url.endswith("harness.db")
    assert defaults.artifacts_dir == defaults.home / "artifacts"
    assert defaults.master_key is None
    assert defaults.master_key_file is None
    assert defaults.verify_url is None

    overridden = Settings.from_env(
        {
            "EVALH_HOME": str(Path("somewhere")),
            "EVALH_DATABASE_URL": "sqlite:///db.sqlite3",
            "EVALH_ARTIFACTS_DIR": str(Path("somewhere") / "artifacts"),
            "EVALH_MASTER_KEY": "master-key",
            "EVALH_MASTER_KEY_FILE": "master.key",
            "EVALH_VERIFY_URL": "http://verify.invalid",
        }
    )
    assert overridden.home == Path("somewhere")
    assert overridden.database_url == "sqlite:///db.sqlite3"
    assert overridden.master_key == "master-key"
    assert overridden.master_key_file == "master.key"
    assert overridden.verify_url == "http://verify.invalid"
    assert harness_revision({"EVALH_HARNESS_SHA": "abc123"}) == "abc123"
    assert harness_revision({}).startswith("evalharness/")


def test_credential_store_environment_and_handle_redaction(monkeypatch):
    handle = CredentialHandle(name="key", provider="openai", secret="s3cr3t")
    assert "s3cr3t" not in repr(handle)
    assert "s3cr3t" not in handle.model_dump_json()

    memory = InMemoryCredentialStore(
        {"b": CredentialHandle(name="b"), "a": CredentialHandle(name="a")}
    )
    assert memory.names() == ["a", "b"]
    assert memory.get("a") is not None
    assert memory.get("missing") is None

    assert credential_env_var("my-key.1") == "EVALH_CREDENTIAL_MY_KEY_1"

    explicit = EnvCredentialStore.from_spec(
        [CredentialSpec(name="my-key", provider="custom")],
        {"EVALH_CREDENTIAL_MY_KEY": "from-mapping"},
    )
    resolved = explicit.get("my-key")
    assert resolved is not None
    assert resolved.provider == "custom"
    assert resolved.secret == "from-mapping"

    monkeypatch.setenv("EVALH_CREDENTIAL_STUB", "from-environment")
    environment = EnvCredentialStore.from_spec([CredentialSpec(name="stub")])
    environment_handle = environment.get("stub")
    assert environment_handle is not None
    assert environment_handle.secret == "from-environment"

    context = RunContext(
        run_id="run",
        seed=7,
        credential=handle,
        events=EventEmitter("run"),
        prompt_variant="concise",
        prompt_override="say it",
        chunks=[ContextChunk(chunk_id="c1", text="t")],
        candidate="candidate-a",
        case_id="case-1",
        replica=2,
        model="stub-1",
    )
    assert context.artifact_metadata() == {
        "candidate": "candidate-a",
        "case_id": "case-1",
        "replica": 2,
        "prompt_variant": "concise",
        "context_chunks": ["c1"],
    }


def test_cost_math_and_estimate():
    price = Price(input=1.0, output=2.0, tool=0.5)
    assert usage_cost(
        price, prompt_tokens=1_000_000, completion_tokens=1_000_000, tool_calls=2
    ) == 4.0
    output = CandidateOutput(
        answer="a",
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
        tool_calls=2,
    )
    split = split_cost(price, output)
    assert split.answer_usd == 2.0
    assert split.tool_usd == 1.0
    assert split.judge_usd == 0.0
    assert split.total_usd == 3.0

    spec = Spec.model_validate(base_spec())
    estimate = estimate_cost(
        spec, {"stub-1": price}, {"stub-concise": 2, "stub-explained": 1}
    )
    assert estimate.cells == 3
    assert estimate.prompt_tokens == 3 * 500
    assert estimate.completion_tokens == 3 * 200
    assert estimate.cost_usd == pytest.approx(3 * (500 * 1.0 + 200 * 2.0) / 1_000_000)


def _aggregate(
    candidate: str = "cand", value: float | None = 1.0, n: int = 1
) -> Aggregate:
    """Build one overall aggregate row for gate tests."""
    return Aggregate(run_id="run", candidate=candidate, metric="m", n=n, value=value)


def test_scorer_aggregates_and_gate_bounds():
    assert aggregate_cells("run", {}, [], {}, []) == []
    rows = [_aggregate(), _aggregate(candidate="other", value=0.0)]
    failing_max = evaluate_gates([GateSpec(metric="m", min=0.5, max=0.9)], rows[:1])[0]
    assert failing_max.passed is False
    assert (failing_max.comparison, failing_max.threshold) == ("<=", 0.9)
    failing_min = evaluate_gates([GateSpec(metric="m", min=0.5, max=1.0)], [
        _aggregate(value=0.4)
    ])[0]
    assert failing_min.passed is False
    assert (failing_min.comparison, failing_min.threshold) == (">=", 0.5)
    passing_min = evaluate_gates([GateSpec(metric="m", min=0.5)], rows[:1])[0]
    assert passing_min.passed is True
    assert (passing_min.comparison, passing_min.threshold) == (">=", 0.5)
    passing_max = evaluate_gates([GateSpec(metric="m", max=1.0)], rows[:1])[0]
    assert passing_max.passed is True
    assert (passing_max.comparison, passing_max.threshold) == ("<=", 1.0)
    candidate = evaluate_gates(
        [GateSpec(metric="m", candidate="other", min=0.5)], rows
    )[0]
    assert candidate.passed is False
    assert (candidate.observed, candidate.n) == (0.0, 1)
    run_wide = evaluate_gates([GateSpec(metric="m", min=0.5)], rows)[0]
    assert (run_wide.observed, run_wide.n) == (0.5, 2)
    missing_candidate = evaluate_gates(
        [GateSpec(metric="m", candidate="absent", min=0.5)], rows
    )[0]
    assert missing_candidate.passed is False
    assert (missing_candidate.observed, missing_candidate.n) == (None, 0)
    empty = evaluate_gates([GateSpec(metric="missing", min=0.1)], [])[0]
    assert empty.passed is False
    assert empty.detail["n"] == 0
    assert empty.detail["value"] is None
    assert empty.detail["reason"] == "no samples"
    empty_max = evaluate_gates([GateSpec(metric="missing", max=0.5)], [])[0]
    assert (empty_max.comparison, empty_max.threshold) == ("<=", 0.5)


def test_scorer_seeds_an_undefined_row_from_cells_without_checks():
    cases = {"case-1": Case(case_id="case-1", prompt="p", tier=2)}
    cells = [
        CellRecord(
            cell_id="cell",
            run_id="run",
            candidate="cand",
            case_id="case-1",
            replica=1,
        )
    ]
    rows = aggregate_cells("run", cases, cells, {}, ["exact_match"], replicas=2)
    assert [(row.tier, row.n, row.value) for row in rows] == [
        (None, 0, None),
        (2, 0, None),
    ]
    assert all(row.ci_low is None and row.pass_at_k is None for row in rows)


def test_contains_metric_passes_and_fails():
    metric = build_contains("contains", {})
    context = ScoreContext(
        run_id="run",
        candidate="candidate",
        cases={
            "case": Case(case_id="case", prompt="p", expected={"answer": "42"}),
        },
    )
    passing = CellRecord(
        cell_id="cell",
        run_id="run",
        candidate="candidate",
        case_id="case",
        replica=1,
        answer="the answer is 42",
    )
    assert asyncio.run(metric.check(passing, context))[0].passed is True
    failing = passing.model_copy(update={"answer": "nope"})
    assert asyncio.run(metric.check(failing, context))[0].passed is False


def test_json_sink_derives_artifact_directory(workdir):
    sink = build_json_sink("sink", {"artifacts_dir": str(workdir)})
    run = RunRecord(run_id="run-sink", started_at=utc_now())
    sink.open_run(run)
    cell = CellRecord(
        cell_id="cell-sink",
        run_id="run-sink",
        candidate="candidate",
        case_id="case",
        replica=1,
        answer="42",
    )
    sink.record_cell(cell, [])
    sink.close_run(run)
    assert (workdir / "run-sink" / "cell-sink" / "cell.json").is_file()
    assert (workdir / "run-sink" / "run.json").is_file()


def test_load_spec_returns_the_model(workdir):
    path = workdir / "load.yaml"
    path.write_text(
        "spec_version: 1\nname: loaded\nsource: {adapter: x:y}\n"
        "candidates:\n  - {name: c, adapter: x:y, credential: c, model: m}\n"
        "metrics:\n  - {key: m, adapter: x:y}\n",
        encoding="utf-8",
    )
    spec = load_spec(path)
    assert spec.name == "loaded"
    assert spec.spec_version == 1


def test_planner_cell_id_is_stable_and_short():
    cell_id = cell_id_for("candidate", "case", 1)
    assert cell_id == cell_id_for("candidate", "case", 1)
    assert len(cell_id) == 16
    assert cell_id != cell_id_for("candidate", "case", 2)


def test_repository_rolls_back_a_failed_session(workdir):
    database = Database(f"sqlite:///{(workdir / 'rollback.db').as_posix()}")
    database.create_all()
    repository = Repository(database)
    repository.save_spec("spec-rollback", "name", "yaml", "sha", utc_now())
    run = RunRecord(
        run_id="run-rollback", spec_id="spec-rollback", started_at=utc_now()
    )
    repository.create_run(run)
    with pytest.raises(IntegrityError):
        repository.create_run(run)
    assert repository.get_run("run-rollback") is not None
    repository.record_aggregates([])
    database.dispose()


def test_module_entrypoint_runs(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evalharness", "--help"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("evalharness", run_name="__main__")
    assert raised.value.code == 0
    assert "usage: evalh" in capsys.readouterr().out


def test_main_with_explicit_none_argv(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evalh", "--help"])
    assert main(None) == 0
    assert "usage: evalh" in capsys.readouterr().out
