"""Validation tests: every error names the field, value and fix before a run."""

from __future__ import annotations

import copy

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.cli import main
from tests.helpers import JSON_SINK, STUB_FIXED_PROMPT, base_spec, write_spec


def _failure(evalh_home, workdir, capsys, spec_or_text):
    path = workdir / "bad.yaml"
    if isinstance(spec_or_text, str):
        path.write_text(spec_or_text, encoding="utf-8")
    else:
        write_spec(path, spec_or_text)
    code = main(["validate", str(path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "spec error:" in captured.err
    assert "Fix:" in captured.err
    return captured.err


def test_unknown_candidate_adapter(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["adapter"] = "nope:missing"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].adapter" in error
    assert "nope:missing" in error


def test_unknown_credential(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["credential"] = "missing-key"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].credential" in error
    assert "unknown credential" in error
    assert "stub" in error


def test_missing_price(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["prices"] = {}
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "prices.stub-1" in error
    assert "no price" in error


def test_prompt_override_refused(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["adapter"] = STUB_FIXED_PROMPT
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].options.prompt" in error
    assert "cannot accept a prompt override" in error


def test_unknown_prompt_variant(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["options"]["prompt"] = "poetic"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].options.prompt" in error
    assert "unknown prompt variant" in error


def test_prompt_variant_must_be_string(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["options"]["prompt"] = 3
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].options.prompt" in error
    assert "must be a string" in error


def test_context_injection_refused(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["adapter"] = "test:no_context"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].options.context" in error
    assert "cannot accept context injection" in error


def test_unknown_context(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["options"]["context"] = "missing"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].options.context" in error
    assert "unknown context" in error


def test_malformed_inline_context(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["options"]["context"] = {
        "chunks": [{"chunk_id": "only-id"}]
    }
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].options.context" in error
    assert "malformed" in error


def test_inline_context_without_chunks_key(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["options"]["context"] = {"mode": "retrieved"}
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].options.context" in error
    assert "must be a declared context name" in error


def test_context_must_be_name_or_chunks(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["options"]["context"] = 5
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "must be a declared context name" in error


def test_inline_context_is_accepted(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["options"]["context"] = {
        "chunks": [{"chunk_id": "x", "text": "y"}]
    }
    path = write_spec(workdir / "ok.yaml", spec)
    assert main(["validate", str(path)]) == 0


def test_metric_with_undeclared_judge(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["metrics"][0]["options"] = {"judge": "judge-a"}
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "metrics[0].options.judge" in error
    assert "undeclared judge" in error


def test_judge_declaration_is_accepted(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["judges"] = [
        {
            "name": "judge-a",
            "adapter": "test:judge",
            "credential": "stub",
            "model": "stub-1",
            "options": {},
        }
    ]
    spec["metrics"][0]["options"] = {"judge": "judge-a"}
    path = write_spec(workdir / "ok.yaml", spec)
    assert main(["validate", str(path)]) == 0


def test_unknown_judge_adapter(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["judges"] = [{"name": "judge-a", "adapter": "nope:judge", "options": {}}]
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "judges[0].adapter" in error


def test_judge_credential_unknown(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["judges"] = [
        {
            "name": "judge-a",
            "adapter": "test:judge",
            "credential": "missing-key",
            "options": {},
        }
    ]
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "judges[0].credential" in error


def test_judge_model_needs_price(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["judges"] = [
        {
            "name": "judge-a",
            "adapter": "test:judge",
            "model": "judge-model",
            "options": {},
        }
    ]
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "prices.judge-model" in error


def test_unknown_metric_adapter(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["metrics"][0]["adapter"] = "nope:metric"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "metrics[0].adapter" in error


def test_metric_key_mismatch(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["metrics"][0]["adapter"] = "test:wrong_key_metric"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "metrics[0].key" in error
    assert "different key" in error


def test_gate_names_undeclared_metric(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["gates"] = [{"metric": "recall", "min": 0.9}]
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "gates[0].metric" in error


def test_gate_needs_a_bound(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["gates"] = [{"metric": "exact_match"}]
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "gates[0]" in error
    assert "neither min nor max" in error


def test_duplicate_gate(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["gates"] = [
        {"metric": "exact_match", "min": 0.9},
        {"metric": "exact_match", "max": 1.0},
    ]
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "gates[1].metric" in error
    assert "duplicate gate" in error


def test_duplicate_candidate_name(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"].append(copy.deepcopy(spec["candidates"][0]))
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates" in error
    assert "duplicate candidate" in error


def test_duplicate_metric_key(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["metrics"].append(copy.deepcopy(spec["metrics"][0]))
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "duplicate metric" in error


def test_duplicate_judge_name(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["judges"] = [
        {"name": "judge-a", "adapter": "test:judge", "options": {}},
        {"name": "judge-a", "adapter": "test:judge", "options": {}},
    ]
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "duplicate judge" in error


def test_duplicate_credential_name(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["credentials"].append({"name": "stub", "provider": "none"})
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "duplicate credential" in error


def test_unknown_source_adapter(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["source"]["adapter"] = "nope:source"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "source.adapter" in error


def test_source_factory_must_load(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["source"]["adapter"] = "test:failing_source"
    spec["source"]["options"] = {}
    path = write_spec(workdir / "bad-source.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "source" in captured.err
    assert "failed to load cases" in captured.err


def test_source_returning_no_cases(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["source"]["adapter"] = "test:empty_source"
    path = write_spec(workdir / "empty.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "source" in captured.err
    assert "no cases" in captured.err


def test_duplicate_case_ids(evalh_home, workdir, capsys):
    spec = base_spec()
    cases = spec["source"]["options"]["cases"]
    cases.append(copy.deepcopy(cases[0]))
    path = write_spec(workdir / "dup.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "duplicate case_id" in captured.err


def test_candidate_must_expose_capabilities(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["adapter"] = "test:no_capabilities"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "did not expose Capabilities" in error


def test_candidate_factory_explodes(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["candidates"][0]["adapter"] = "test:boom_candidate"
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "candidates[0].adapter" in error
    assert "factory raised" in error


def test_unknown_sink_adapter(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["sink"] = {"adapter": "nope:sink", "options": {}}
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "sink.adapter" in error


def test_spec_version_must_be_one(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["spec_version"] = 2
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "spec_version" in error


def test_yaml_syntax_error(evalh_home, workdir, capsys):
    error = _failure(evalh_home, workdir, capsys, "spec_version: [unclosed")
    assert "spec" in error
    assert "YAML" in error


def test_yaml_must_be_a_mapping(evalh_home, workdir, capsys):
    error = _failure(evalh_home, workdir, capsys, "- one\n- two\n")
    assert "must be a YAML mapping" in error


def test_unknown_field_is_rejected(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["typo_field"] = True
    error = _failure(evalh_home, workdir, capsys, spec)
    assert "typo_field" in error


def test_run_validates_before_executing(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["prices"] = {}
    path = write_spec(workdir / "bad-run.yaml", spec)
    code = main(["run", str(path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "prices.stub-1" in captured.err


def test_missing_spec_file(evalh_home, workdir, capsys):
    code = main(["validate", str(workdir / "nope.yaml")])
    captured = capsys.readouterr()
    assert code == 2
    assert "could not be read" in captured.err


def test_valid_custom_sink_declaration(evalh_home, workdir, capsys):
    spec = base_spec()
    spec["sink"] = {"adapter": JSON_SINK, "options": {"artifacts_dir": "ignored"}}
    path = write_spec(workdir / "sink.yaml", spec)
    assert main(["validate", str(path)]) == 0