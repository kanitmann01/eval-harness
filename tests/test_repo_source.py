"""Behaviour tests for the repository source adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalharness.adapters.sources import repo as repo_module
from evalharness.adapters.sources.repo import build, conformance_setup
from evalharness.core.context import RunContext
from evalharness.core.events import EventEmitter
from evalharness.core.models import RepoSpec, SourceSpec
from evalharness.core.repo_snapshot import snapshot_repo
from evalharness.errors import SpecError


def _snapshot(tmp_path: Path) -> tuple[Path, object]:
    root = tmp_path / "repo"
    (root / "cases").mkdir(parents=True)
    (root / "fixtures").mkdir()
    (root / "cases" / "questions.jsonl").write_text(
        '{"case_id": "one", "prompt": "q1", '
        '"metadata": {"fixtures": ["notes.txt", "raw.txt"]}}\n'
        '\n'
        '{"case_id": "two", "prompt": "q2"}\n',
        encoding="utf-8",
    )
    (root / "fixtures" / "notes.txt").write_bytes(b"notes\n")
    (root / "fixtures" / "raw.txt").write_bytes(b"\xff\xfe")
    snapshot = snapshot_repo(
        "tiny",
        RepoSpec(root="repo"),
        base_dir=tmp_path,
        home=tmp_path / "home",
    )
    return root, snapshot


def _fresh(tmp_path: Path) -> object:
    return snapshot_repo(
        "tiny",
        RepoSpec(root="repo"),
        base_dir=tmp_path,
        home=tmp_path / "home",
    )


def _context(snapshot: object | None, name: str = "tiny") -> RunContext:
    return RunContext(
        run_id="run",
        seed=0,
        credential=None,
        events=EventEmitter("run"),
        repos={} if snapshot is None else {name: snapshot},
    )


def _options(**overrides: object) -> dict[str, object]:
    options: dict[str, object] = {
        "repo": "tiny",
        "cases": "cases/questions.jsonl",
        "fixtures": "fixtures",
    }
    options.update(overrides)
    return options


def test_load_resolves_cases_and_fixtures_from_the_snapshot(tmp_path: Path) -> None:
    _, snapshot = _snapshot(tmp_path)
    source = build("source", _options(inline_fixtures=True))
    spec = SourceSpec(adapter="evalharness.adapters.sources.repo:build")

    cases = source.load(spec, _context(snapshot))

    assert [case.case_id for case in cases] == ["one", "two"]
    fixtures = cases[0].metadata["fixtures"]
    assert [item["path"] for item in fixtures] == [
        "fixtures/notes.txt",
        "fixtures/raw.txt",
    ]
    assert fixtures[0]["text"] == "notes\n"
    assert fixtures[0]["bytes"] == 6
    assert len(fixtures[0]["sha256"]) == 64
    assert "text" not in fixtures[1]


def test_load_without_a_snapshot_is_actionable() -> None:
    source = build("source", _options())
    with pytest.raises(SpecError) as error:
        source.load(SourceSpec(adapter="repo"), _context(None))
    assert error.value.field == "source.options.repo"
    assert "snapshotted" in error.value.reason


def test_case_limit_duplicate_and_invalid_case_paths(tmp_path: Path) -> None:
    root, snapshot = _snapshot(tmp_path)
    limited = build("source", _options(case_limit=1))
    with pytest.raises(SpecError) as error:
        limited.load(SourceSpec(adapter="repo"), _context(snapshot))
    assert error.value.field == "source.options.case_limit"

    cases = root / "cases" / "questions.jsonl"
    cases.write_text(
        '{"case_id": "one", "prompt": "q"}\n'
        '{"case_id": "one", "prompt": "q"}\n',
        encoding="utf-8",
    )
    duplicate = build("source", _options())
    with pytest.raises(SpecError) as error:
        duplicate.load(SourceSpec(adapter="repo"), _context(_fresh(tmp_path)))
    assert "duplicate" in error.value.reason

    for bad in ["/abs.jsonl", "../x.jsonl", "~x.jsonl", "C:/x.jsonl"]:
        source = build("source", _options(cases=bad))
        with pytest.raises(SpecError) as error:
            source.load(SourceSpec(adapter="repo"), _context(snapshot))
        assert error.value.field == "source.options.cases"


def test_missing_and_unreadable_case_files_are_actionable(tmp_path: Path) -> None:
    _, snapshot = _snapshot(tmp_path)
    source = build("source", _options(cases="cases/missing.jsonl"))
    with pytest.raises(SpecError) as error:
        source.load(SourceSpec(adapter="repo"), _context(snapshot))
    assert "does not exist" in error.value.reason

    bad = tmp_path / "repo" / "cases" / "bad.jsonl"
    bad.write_bytes(b"\xff\xfe")
    source = build("source", _options(cases="cases/bad.jsonl"))
    with pytest.raises(SpecError) as error:
        source.load(SourceSpec(adapter="repo"), _context(_fresh(tmp_path)))
    assert "UTF-8" in error.value.reason


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("empty.jsonl", "", "contains no cases"),
        ("broken.jsonl", "{", "not valid JSON"),
        ("scalar.jsonl", "1\n", "not valid"),
        ("broken.yaml", "cases: [", "did not parse"),
        ("mapping.yaml", "other: []\n", "must be a list"),
        ("empty.yaml", "", "must be a list"),
        ("list.yaml", "- case_id: one\n  prompt: q\n", "ok"),
        ("map.yaml", "cases:\n  - case_id: one\n    prompt: q\n", "ok"),
        ("bad.txt", "x", "must end in"),
    ],
)
def test_yaml_and_jsonl_parsing_is_actionable(
    tmp_path: Path, name: str, text: str, message: str
) -> None:
    root, snapshot = _snapshot(tmp_path)
    (root / "cases" / name).write_text(text, encoding="utf-8")
    snapshot = _fresh(tmp_path)
    source = build("source", _options(cases=f"cases/{name}"))
    spec = SourceSpec(adapter="repo")
    if message == "ok":
        assert len(source.load(spec, _context(snapshot))) == 1
    else:
        with pytest.raises(SpecError) as error:
            source.load(spec, _context(snapshot))
        assert message in error.value.reason


def test_fixture_metadata_rejects_bad_paths_and_missing_files(tmp_path: Path) -> None:
    root, snapshot = _snapshot(tmp_path)
    questions = root / "cases" / "questions.jsonl"

    for metadata, field in [
        ({"fixtures": "notes.txt"}, "source.options.cases"),
        ({"fixtures": [1]}, "source.options.cases"),
        ({"fixtures": ["../secret.txt"]}, "source.options.fixtures"),
        ({"fixtures": ["/abs.txt"]}, "source.options.fixtures"),
        ({"fixtures": ["~x.txt"]}, "source.options.fixtures"),
        ({"fixtures": ["C:/x.txt"]}, "source.options.fixtures"),
        ({"fixtures": ["missing.txt"]}, "source.options.fixtures"),
        ({"fixtures": ["bad\x00.txt"]}, "source.options.fixtures"),
    ]:
        questions.write_text(
            json.dumps(
                {"case_id": "one", "prompt": "q", "metadata": metadata}
            )
            + "\n",
            encoding="utf-8",
        )
        source = build("source", _options())
        with pytest.raises(SpecError) as error:
            source.load(SourceSpec(adapter="repo"), _context(_fresh(tmp_path)))
        assert error.value.field == field


def test_fixture_text_is_optional_and_bounded(tmp_path: Path) -> None:
    root, snapshot = _snapshot(tmp_path)
    questions = root / "cases" / "questions.jsonl"
    questions.write_text(
        '{"case_id": "one", "prompt": "q", "metadata": {"fixtures": ["notes.txt"]}}\n',
        encoding="utf-8",
    )
    snapshot = _fresh(tmp_path)
    plain = build("source", _options()).load(SourceSpec(adapter="repo"), _context(snapshot))
    assert "text" not in plain[0].metadata["fixtures"][0]

    tiny_cap = build("source", _options(inline_fixtures=True, inline_max_bytes=1))
    capped = tiny_cap.load(SourceSpec(adapter="repo"), _context(snapshot))
    assert "text" not in capped[0].metadata["fixtures"][0]

    (root / "fixtures" / "notes.txt").write_bytes(b"\xff")
    binary_text = build("source", _options(inline_fixtures=True))
    not_utf8 = binary_text.load(
        SourceSpec(adapter="repo"), _context(_fresh(tmp_path))
    )
    assert "text" not in not_utf8[0].metadata["fixtures"][0]


def test_fixture_base_is_optional(tmp_path: Path) -> None:
    root, snapshot = _snapshot(tmp_path)
    (root / "notes.txt").write_text("root notes\n", encoding="utf-8")
    (root / "cases" / "questions.jsonl").write_text(
        '{"case_id": "one", "prompt": "q", "metadata": {"fixtures": ["notes.txt"]}}\n',
        encoding="utf-8",
    )
    source = build("source", _options(fixtures=None))
    cases = source.load(SourceSpec(adapter="repo"), _context(_fresh(tmp_path)))
    assert cases[0].metadata["fixtures"][0]["path"] == "notes.txt"


@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({}, "source.options.repo"),
        ({"repo": ""}, "source.options.repo"),
        ({"repo": "tiny"}, "source.options.cases"),
        ({"repo": "tiny", "cases": ""}, "source.options.cases"),
        (_options(fixtures=""), "source.options.fixtures"),
        (_options(case_limit=0), "source.options.case_limit"),
        (_options(case_limit=True), "source.options.case_limit"),
        (_options(inline_fixtures=1), "source.options.inline_fixtures"),
        (_options(unknown=True), "source.options"),
    ],
)
def test_build_rejects_bad_options(
    options: dict[str, object], field: str
) -> None:
    with pytest.raises(SpecError) as error:
        build("source", options)
    assert error.value.field == field


def test_conformance_setup_builds_a_usable_snapshot(tmp_path: Path) -> None:
    options, repos = conformance_setup(tmp_path)

    assert options == {
        "repo": "conformance",
        "cases": "cases/questions.jsonl",
        "fixtures": "fixtures",
    }
    assert set(repos) == {"conformance"}
    source = build("source", options)
    cases = source.load(
        SourceSpec(adapter="repo"), _context(repos["conformance"], "conformance")
    )
    assert [case.case_id for case in cases] == [
        "conformance-case-1",
        "conformance-case-2",
    ]
    assert repo_module.CONFORMANCE_SETUP is repo_module.build.CONFORMANCE_SETUP

def test_fixture_read_errors_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _snapshot(tmp_path)
    (root / "cases" / "questions.jsonl").write_text(
        '{"case_id": "one", "prompt": "q", "metadata": {"fixtures": ["notes.txt"]}}\n',
        encoding="utf-8",
    )
    source = build("source", _options())
    snapshot = _fresh(tmp_path)

    def boom(self: Path) -> bytes:
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_bytes", boom)
    with pytest.raises(SpecError) as error:
        source.load(SourceSpec(adapter="repo"), _context(snapshot))
    assert error.value.field == "source.options.fixtures"
    assert "could not be read" in error.value.reason
