"""Repository source adapter: cases and fixtures read from a labelled snapshot.

The adapter never touches a developer's working tree directly. Core snapshots
every declared repository before the first cell and hands the prepared trees
to this adapter through `RunContext.repos`. This module therefore reads only
snapshot-relative paths and records only snapshot-relative metadata.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from evalharness.core.context import RunContext
from evalharness.core.models import Case, RepoSpec, SourceSpec
from evalharness.core.repo_snapshot import RepoSnapshot, snapshot_repo
from evalharness.errors import SpecError

KNOWN_OPTIONS = frozenset(
    {
        "repo",
        "cases",
        "fixtures",
        "case_limit",
        "inline_fixtures",
        "inline_max_bytes",
    }
)
"""Every option this adapter understands; a typo is an error, not a default."""

DEFAULT_CASE_LIMIT = 500
DEFAULT_INLINE_MAX_BYTES = 65_536


class RepoSource:
    """Load cases from one prepared repository snapshot."""

    def __init__(
        self,
        *,
        repo: str,
        cases: str,
        fixtures: str | None,
        case_limit: int,
        inline_fixtures: bool,
        inline_max_bytes: int,
    ) -> None:
        self._repo = repo
        self._cases = cases
        self._fixtures = fixtures
        self._case_limit = case_limit
        self._inline_fixtures = inline_fixtures
        self._inline_max_bytes = inline_max_bytes

    def load(self, spec: SourceSpec, ctx: RunContext) -> list[Case]:
        """Read, validate and cap the cases named by the options."""
        del spec
        snapshot = ctx.repos.get(self._repo)
        if snapshot is None:
            raise SpecError(
                "source.options.repo",
                self._repo,
                "the repository was not snapshotted for this load",
                "run through 'evalh run' so the spec's repositories are prepared",
            )
        cases_path = _inside(snapshot, self._cases, "source.options.cases")
        cases = _read_cases(cases_path, self._cases)
        if len(cases) > self._case_limit:
            raise SpecError(
                "source.options.case_limit",
                self._case_limit,
                f"the cases file holds {len(cases)} cases, above the limit",
                "raise case_limit or split the cases file",
            )
        seen: set[str] = set()
        resolved: list[Case] = []
        for case in cases:
            if case.case_id in seen:
                raise SpecError(
                    "source.options.cases",
                    case.case_id,
                    "the cases file contains a duplicate case_id",
                    "give every case a unique case_id",
                )
            seen.add(case.case_id)
            resolved.append(self._with_fixtures(snapshot, case))
        return resolved

    def _with_fixtures(self, snapshot: RepoSnapshot, case: Case) -> Case:
        """Resolve case metadata fixtures to hashes, sizes and optional text."""
        raw = case.metadata.get("fixtures")
        if raw is None:
            return case
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise SpecError(
                "source.options.cases",
                case.case_id,
                "case metadata.fixtures must be a list of relative paths",
                "use paths such as ['context/notes.md']",
            )
        base = self._fixtures or ""
        resolved = [
            self._fixture(snapshot, base, path, case.case_id) for path in raw
        ]
        metadata = dict(case.metadata)
        metadata["fixtures"] = resolved
        return case.model_copy(update={"metadata": metadata})

    def _fixture(
        self,
        snapshot: RepoSnapshot,
        base: str,
        relative: str,
        case_id: str,
    ) -> dict[str, Any]:
        """Read one fixture from the snapshot and describe it without host paths."""
        joined = f"{base.rstrip('/')}/{relative}" if base else relative
        path = _inside(snapshot, joined, "source.options.fixtures")
        if not path.is_file():
            raise SpecError(
                "source.options.fixtures",
                relative,
                f"a fixture named by case {case_id!r} does not exist in the snapshot",
                "add the file to the repository or fix the metadata path",
            )
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise SpecError(
                "source.options.fixtures",
                relative,
                f"a fixture could not be read: {exc}",
                "fix the file permissions",
            ) from exc
        entry: dict[str, Any] = {
            "path": joined,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
        if (
            self._inline_fixtures
            and len(payload) <= self._inline_max_bytes
            and b"\x00" not in payload[:8192]
        ):
            try:
                entry["text"] = payload.decode("utf-8")
            except UnicodeDecodeError:
                pass
        return entry


def _read_cases(path: Path, display: str) -> list[Case]:
    """Parse a .jsonl or .yaml cases file into validated Case records."""
    if not path.is_file():
        raise SpecError(
            "source.options.cases",
            display,
            "the cases file does not exist in the snapshot",
            "add the file to the repository or fix source.options.cases",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(
            "source.options.cases",
            display,
            f"the cases file could not be read as UTF-8: {exc}",
            "save the cases file as UTF-8",
        ) from exc
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        raw = _jsonl(text, display)
    elif suffix in {".yaml", ".yml"}:
        raw = _yaml(text, display)
    else:
        raise SpecError(
            "source.options.cases",
            display,
            "the cases file must end in .jsonl, .yaml or .yml",
            "rename the file or point cases at a supported file",
        )
    if not raw:
        raise SpecError(
            "source.options.cases",
            display,
            "the cases file contains no cases",
            "add at least one case",
        )
    cases: list[Case] = []
    for index, item in enumerate(raw):
        try:
            cases.append(Case.model_validate(item))
        except ValidationError as exc:
            raise SpecError(
                "source.options.cases",
                f"{display}[{index}]",
                f"the case is not valid: {exc.errors()[0].get('msg', exc)}",
                "fix the case fields; case_id and prompt are required",
            ) from exc
    return cases


def _jsonl(text: str, display: str) -> list[Any]:
    """Parse one JSON object per non-empty line."""
    items: list[Any] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError as exc:
            raise SpecError(
                "source.options.cases",
                f"{display}:{number}",
                f"the line is not valid JSON: {exc}",
                "write one JSON object per line",
            ) from exc
        items.append(item)
    return items


def _yaml(text: str, display: str) -> list[Any]:
    """Parse a YAML list, or a mapping with a cases list."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(
            "source.options.cases",
            display,
            f"the YAML did not parse: {exc}",
            "fix the YAML syntax",
        ) from exc
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return data["cases"]
    raise SpecError(
        "source.options.cases",
        display,
        "the YAML must be a list of cases or a mapping with a cases list",
        "start the file with '- case_id: ...' or 'cases:'",
    )


def _inside(snapshot: RepoSnapshot, relative: str, field: str) -> Path:
    """Resolve a snapshot-relative path and refuse anything that escapes."""
    if not relative or "\x00" in relative:
        raise SpecError(
            field,
            relative,
            "a snapshot path must not be empty or contain a NUL byte",
            "use a path relative to the repository root",
        )
    normalized = relative.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (
        normalized.startswith("/")
        or normalized.startswith("~")
        or ".." in parts
        or PureWindowsPath(relative).is_absolute()
        or PureWindowsPath(relative).drive
    ):
        raise SpecError(
            field,
            relative,
            "a snapshot path must stay inside the repository",
            "use a path such as 'cases/questions.jsonl'",
        )
    target = (snapshot.tree / normalized).resolve()
    try:
        target.relative_to(snapshot.tree.resolve())
    except ValueError as exc:
        raise SpecError(
            field,
            relative,
            "a snapshot path must stay inside the repository",
            "use a path such as 'cases/questions.jsonl'",
        ) from exc
    return target


def _option_int(options: Mapping[str, Any], name: str, default: int) -> int:
    value = options.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SpecError(
            f"source.options.{name}",
            value,
            "the option must be a positive integer",
            f"set {name} to a positive integer",
        )
    return value


def _option_bool(options: Mapping[str, Any], name: str, default: bool) -> bool:
    value = options.get(name, default)
    if not isinstance(value, bool):
        raise SpecError(
            f"source.options.{name}",
            value,
            "the option must be true or false",
            f"set {name} to true or false",
        )
    return value


def build(identity: str, options: Mapping[str, Any]) -> RepoSource:
    """Build a repository source from the documented options."""
    del identity
    unknown = sorted(set(options) - KNOWN_OPTIONS)
    if unknown:
        raise SpecError(
            "source.options",
            unknown[0],
            "unknown source option",
            f"use one of: {', '.join(sorted(KNOWN_OPTIONS))}",
        )
    repo = options.get("repo")
    if not isinstance(repo, str) or not repo:
        raise SpecError(
            "source.options.repo",
            repo,
            "the repository source needs a repo name",
            "name a repository declared under repos:",
        )
    cases = options.get("cases")
    if not isinstance(cases, str) or not cases:
        raise SpecError(
            "source.options.cases",
            cases,
            "the repository source needs a cases file path",
            "set cases to a snapshot-relative .jsonl or .yaml file",
        )
    fixtures = options.get("fixtures")
    if fixtures is not None and (not isinstance(fixtures, str) or not fixtures):
        raise SpecError(
            "source.options.fixtures",
            fixtures,
            "fixtures must be a non-empty relative directory",
            "set fixtures to a snapshot-relative directory or omit it",
        )
    return RepoSource(
        repo=repo,
        cases=cases,
        fixtures=fixtures,
        case_limit=_option_int(options, "case_limit", DEFAULT_CASE_LIMIT),
        inline_fixtures=_option_bool(options, "inline_fixtures", False),
        inline_max_bytes=_option_int(
            options, "inline_max_bytes", DEFAULT_INLINE_MAX_BYTES
        ),
    )


def conformance_setup(
    scratch: Path,
) -> tuple[dict[str, object], dict[str, RepoSnapshot]]:
    """Create one tiny repository and the options that read it.

    The conformance runner calls this hook only for adapters that declare it,
    so the public suite can exercise a source that needs a prepared snapshot
    without every source growing a test-only option.
    """
    root = scratch / "repo"
    (root / "cases").mkdir(parents=True)
    (root / "fixtures").mkdir()
    (root / "cases" / "questions.jsonl").write_text(
        '{"case_id": "conformance-case-1", "prompt": "What is 6 times 7?", '
        '"expected": {"answer": "42"}, '
        '"metadata": {"fixtures": ["notes.txt"]}}\n'
        '{"case_id": "conformance-case-2", "prompt": "What is 7 times 6?", '
        '"expected": {"answer": "42"}}\n',
        encoding="utf-8",
    )
    (root / "fixtures" / "notes.txt").write_text(
        "conformance fixture\n", encoding="utf-8"
    )
    snapshot = snapshot_repo(
        "conformance",
        RepoSpec(root="repo"),
        base_dir=scratch,
        home=scratch / "home",
    )
    return (
        {
            "repo": "conformance",
            "cases": "cases/questions.jsonl",
            "fixtures": "fixtures",
        },
        {"conformance": snapshot},
    )


CONFORMANCE_SETUP = conformance_setup
"""The runner hook that prepares this adapter's fixture and snapshot."""

build.CONFORMANCE_SETUP = conformance_setup  # type: ignore[attr-defined]
