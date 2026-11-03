"""External behaviour of the repository graph context selector."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from evalharness.adapters.contexts.repo_graph import build
from evalharness.core.context import RunContext
from evalharness.core.events import EventEmitter
from evalharness.core.models import Case, RepoSpec
from evalharness.core.repo_snapshot import (
    RepoSnapshot,
    SnapshotFile,
    snapshot_repo,
)
from evalharness.errors import SpecError


def _write(root: Path, files: dict[str, str]) -> None:
    """Write a mapping of relative paths to UTF-8 text under a root."""
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _snapshot(
    base: Path, files: dict[str, str], *, name: str = "repo"
) -> RepoSnapshot:
    """Snapshot a fresh local tree the way a run prepares repositories."""
    root = base / name
    root.mkdir(parents=True, exist_ok=True)
    _write(root, files)
    return snapshot_repo(
        name,
        RepoSpec(root=name),
        base_dir=base,
        home=base / "home",
    )


def _context(snapshot: RepoSnapshot) -> RunContext:
    """Build the ambient run context a selector reads snapshots from."""
    return RunContext(
        run_id="run",
        seed=0,
        credential=None,
        events=EventEmitter("run"),
        repos={snapshot.name: snapshot},
    )


def _case(prompt: str) -> Case:
    """Build one case with a stable identifier."""
    return Case(case_id="case-1", prompt=prompt)


def _select(
    snapshot: RepoSnapshot,
    prompt: str,
    options: dict[str, object] | None = None,
) -> tuple[list, Case]:
    """Run the selector through its public build and select seam."""
    merged: dict[str, object] = {"repo": snapshot.name}
    merged.update(options or {})
    selector = build("ctx", merged)
    case = _case(prompt)
    return selector.select(case, _context(snapshot)), case


def test_lexical_selection_is_deterministic_and_bounded(workdir: Path) -> None:
    snapshot = _snapshot(
        workdir,
        {
            "src/alpha.py": "alpha beta gamma\nalpha beta\n",
            "src/helper.py": "nothing to see here\n",
            "docs/readme.md": "alpha notes\n",
            "tests/test_alpha.py": "alpha alpha alpha\n",
        },
    )
    first, case = _select(snapshot, "alpha beta")
    before = case.model_dump()

    assert first
    assert first[0].chunk_id == "repo:repo/src/alpha.py#L1-L2"
    assert first[0].start_line == 1
    assert first[0].end_line == 2
    assert first[0].score is not None and first[0].score > 0
    assert all("tests/" not in (chunk.path or "") for chunk in first)
    assert len({chunk.chunk_id for chunk in first}) == len(first)

    second, _ = _select(snapshot, "alpha beta")
    assert second == first
    assert case.model_dump() == before


def test_include_tests_controls_test_material(workdir: Path) -> None:
    snapshot = _snapshot(
        workdir,
        {
            "tests/one.py": "alpha\n",
            "pkg/test_two.py": "alpha\n",
            "pkg/three_test.py": "alpha\n",
            "pkg/four.py": "alpha\n",
        },
    )

    excluded, _ = _select(
        snapshot, "alpha", {"include_tests": False, "seed_chunks": 10}
    )
    included, _ = _select(
        snapshot, "alpha", {"include_tests": True, "seed_chunks": 10}
    )

    assert {chunk.path for chunk in excluded} == {"pkg/four.py"}
    assert {chunk.path for chunk in included} == {
        "tests/one.py",
        "pkg/test_two.py",
        "pkg/three_test.py",
        "pkg/four.py",
    }


def test_pagerank_boosts_files_that_others_import(workdir: Path) -> None:
    snapshot = _snapshot(
        workdir,
        {
            "pkg/__init__.py": "",
            "pkg/needle.py": "needle\n",
            "pkg/left.py": "from pkg.needle import value\n",
            "pkg/right.py": "from pkg.needle import value\n",
            "pkg/far.py": "unrelated\n",
        },
    )
    lexical, _ = _select(snapshot, "needle", {"rank": "lexical"})
    pagerank, _ = _select(snapshot, "needle", {"rank": "pagerank"})

    lexical_needle = next(
        chunk for chunk in lexical if chunk.path == "pkg/needle.py"
    )
    ranked_needle = next(
        chunk for chunk in pagerank if chunk.path == "pkg/needle.py"
    )
    assert ranked_needle.score > lexical_needle.score
    assert pagerank[0].path == "pkg/needle.py"


def test_import_forms_and_relative_targets_are_read(workdir: Path) -> None:
    snapshot = _snapshot(
        workdir,
        {
            "web/app.js": "const util = require('./lib/util');\n",
            "web/lib/util.js": "export const x = 1;\n",
            "web/index.js": "import data from './lib';\n",
            "web/lib/index.js": "export default {};\n",
            "native/main.c": '#include "native/helper.h"\n',
            "native/helper.h": "int helper(void);\n",
            "scripts/run.ts": 'import { thing } from "./lib/thing";\n',
            "scripts/lib/thing.ts": "export const thing = 1;\n",
            "templates/page.html": '{% include "partials/header.html" %}\n',
            "templates/partials/header.html": "<header></header>\n",
            "pkg/multi.py": "import os, pkg.mod\n",
            "pkg/mod.py": "value = 1\n",
            "pkg/empty.py": 'import "   "\n',
        },
    )
    first, _ = _select(
        snapshot,
        "",
        {"rank": "pagerank", "seed_chunks": 10, "max_chunks": 20},
    )
    second, _ = _select(
        snapshot,
        "",
        {"rank": "pagerank", "seed_chunks": 10, "max_chunks": 20},
    )

    assert first
    assert first == second
    assert len({chunk.chunk_id for chunk in first}) == len(first)
    paths = {chunk.path for chunk in first}
    assert "web/app.js" in paths
    assert "native/main.c" in paths
    selected = {chunk.chunk_id for chunk in first}
    assert all(set(chunk.graph_nodes) <= selected for chunk in first)


def test_expansion_adds_siblings_and_import_neighbours(workdir: Path) -> None:
    snapshot = _snapshot(
        workdir,
        {
            "pkg/a.py": "alpha\n",
            "pkg/b.py": "from pkg.a import value\n",
            "pkg/c.py": "from pkg.a import value\n",
            "other/d.py": "nothing\n",
        },
    )
    chunks, _ = _select(
        snapshot,
        "alpha",
        {"seed_chunks": 1, "expand": 2, "max_chunks": 10},
    )

    paths = {chunk.path for chunk in chunks}
    assert "pkg/a.py" in paths
    assert {"pkg/b.py", "pkg/c.py"} & paths
    selected = {chunk.chunk_id for chunk in chunks}
    assert all(set(chunk.graph_nodes) <= selected for chunk in chunks)


def test_expand_zero_keeps_only_seeds(workdir: Path) -> None:
    snapshot = _snapshot(
        workdir,
        {"pkg/a.py": "alpha\n", "pkg/b.py": "beta\n", "pkg/c.py": "gamma\n"},
    )
    chunks, _ = _select(
        snapshot,
        "alpha",
        {"seed_chunks": 2, "expand": 0, "max_chunks": 10},
    )
    assert len(chunks) == 2
    assert chunks[0].path == "pkg/a.py"


def test_max_chunks_stops_expansion(workdir: Path) -> None:
    snapshot = _snapshot(
        workdir,
        {
            "pkg/a.py": "alpha\n",
            "pkg/b.py": "from pkg.a import value\n",
            "pkg/c.py": "from pkg.a import value\n",
        },
    )
    chunks, _ = _select(
        snapshot,
        "alpha",
        {"seed_chunks": 2, "expand": 10, "max_chunks": 2},
    )
    assert len(chunks) == 2


def test_no_expansion_candidates_ends_early(workdir: Path) -> None:
    snapshot = _snapshot(workdir, {"only.py": "alpha\n"})
    chunks, _ = _select(
        snapshot,
        "alpha",
        {"seed_chunks": 1, "expand": 3, "max_chunks": 8},
    )
    assert [chunk.path for chunk in chunks] == ["only.py"]


def test_max_bytes_skips_an_oversized_chunk_without_truncating(
    workdir: Path,
) -> None:
    snapshot = _snapshot(
        workdir,
        {"big.txt": "match " + "x" * 40 + "\nmatch\n"},
    )
    chunks, _ = _select(
        snapshot,
        "match",
        {"chunk_lines": 1, "seed_chunks": 2, "expand": 0, "max_chunks": 2,
         "max_bytes": 5},
    )

    assert [chunk.text for chunk in chunks] == ["match"]
    assert chunks[0].start_line == 2
    assert sum(len(chunk.text.encode("utf-8")) for chunk in chunks) <= 5


def test_empty_repository_returns_no_chunks(workdir: Path) -> None:
    snapshot = _snapshot(workdir, {})
    lexical, _ = _select(snapshot, "alpha")
    pagerank, _ = _select(snapshot, "alpha", {"rank": "pagerank"})

    assert lexical == []
    assert pagerank == []


def test_chunk_metadata_comes_from_the_snapshot(workdir: Path) -> None:
    snapshot = _snapshot(workdir, {"src/mod.py": "alpha\nbeta\ngamma\n"})
    chunks, _ = _select(
        snapshot, "alpha", {"chunk_lines": 2, "seed_chunks": 3}
    )

    first = chunks[0]
    recorded = next(
        entry for entry in snapshot.files if entry.path == "src/mod.py"
    )
    assert first.chunk_id == "repo:repo/src/mod.py#L1-L2"
    assert first.path == "src/mod.py"
    assert first.start_line == 1
    assert first.end_line == 2
    assert first.blob_sha256 == recorded.sha256
    assert first.text == "alpha\nbeta"
    assert chunks[1].chunk_id == "repo:repo/src/mod.py#L3-L3"


def test_remote_url_snapshot_is_read_from_its_local_tree(
    workdir: Path,
) -> None:
    snapshot = _snapshot(workdir, {"src/mod.py": "alpha\n"})
    remote = replace(snapshot, url="https://example.invalid/repo.git", root="")
    chunks, _ = _select(remote, "alpha")

    assert [chunk.path for chunk in chunks] == ["src/mod.py"]


def test_invalid_utf8_is_replaced_not_fatal(workdir: Path) -> None:
    snapshot = _snapshot(workdir, {"src/odd.txt": "placeholder\n"})
    (snapshot.tree / "src" / "odd.txt").write_bytes(b"alpha \xff\n")
    chunks, _ = _select(snapshot, "alpha")

    assert len(chunks) == 1
    assert "\ufffd" in chunks[0].text


def test_unsafe_unreadable_and_binary_entries_are_skipped(
    workdir: Path,
) -> None:
    snapshot = _snapshot(workdir, {"inside.py": "alpha needle\n"})
    (snapshot.tree / "directory").mkdir()
    (snapshot.tree / "binary.dat").write_bytes(b"\x00alpha needle")
    (workdir / "outside.py").write_text("alpha needle secret", encoding="utf-8")

    hostile = [
        "",
        "bad\x00name.py",
        "/etc/passwd",
        "~/secret.py",
        "../outside.py",
        "nested/../../outside.py",
        "C:relative.py",
        "C:/Windows/win.ini",
    ]
    extras = [
        SnapshotFile("directory", "", 0, 0o755),
        SnapshotFile("binary.dat", "", 0, 0o644),
        *[SnapshotFile(path, "", 0, 0o644) for path in hostile],
    ]
    dangerous = replace(snapshot, files=snapshot.files + tuple(extras))
    chunks, _ = _select(
        dangerous,
        "alpha",
        {"include_tests": True, "seed_chunks": 10, "max_chunks": 20},
    )

    assert [chunk.path for chunk in chunks] == ["inside.py"]
    assert all("secret" not in chunk.text for chunk in chunks)


@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({"repo": "r", "surprise": 1}, "contexts.ctx.options.surprise"),
        ({}, "contexts.ctx.options.repo"),
        ({"repo": ""}, "contexts.ctx.options.repo"),
        ({"repo": 5}, "contexts.ctx.options.repo"),
        ({"repo": "r", "rank": "weird"}, "contexts.ctx.options.rank"),
        ({"repo": "r", "rank": 3}, "contexts.ctx.options.rank"),
        (
            {"repo": "r", "include_tests": "yes"},
            "contexts.ctx.options.include_tests",
        ),
        ({"repo": "r", "seed_chunks": 0}, "contexts.ctx.options.seed_chunks"),
        ({"repo": "r", "seed_chunks": 11}, "contexts.ctx.options.seed_chunks"),
        ({"repo": "r", "seed_chunks": "3"}, "contexts.ctx.options.seed_chunks"),
        ({"repo": "r", "seed_chunks": True}, "contexts.ctx.options.seed_chunks"),
        ({"repo": "r", "expand": -1}, "contexts.ctx.options.expand"),
        ({"repo": "r", "expand": 11}, "contexts.ctx.options.expand"),
        ({"repo": "r", "chunk_lines": 0}, "contexts.ctx.options.chunk_lines"),
        ({"repo": "r", "chunk_lines": 401}, "contexts.ctx.options.chunk_lines"),
        ({"repo": "r", "max_chunks": 0}, "contexts.ctx.options.max_chunks"),
        ({"repo": "r", "max_chunks": 51}, "contexts.ctx.options.max_chunks"),
        ({"repo": "r", "max_bytes": 0}, "contexts.ctx.options.max_bytes"),
        (
            {"repo": "r", "max_bytes": 2_000_001},
            "contexts.ctx.options.max_bytes",
        ),
    ],
)
def test_build_rejects_unknown_or_invalid_options(
    options: dict[str, object], field: str
) -> None:
    with pytest.raises(SpecError) as caught:
        build("ctx", options)

    assert caught.value.field == field
    assert caught.value.reason
    assert caught.value.fix
    assert field in str(caught.value)


def test_build_accepts_lower_and_upper_bounds(workdir: Path) -> None:
    snapshot = _snapshot(workdir, {"src/mod.py": "alpha\n"})
    lower = build(
        "lower",
        {
            "repo": "repo",
            "seed_chunks": 1,
            "expand": 0,
            "chunk_lines": 1,
            "max_chunks": 1,
            "max_bytes": 1,
            "include_tests": False,
        },
    )
    upper = build(
        "upper",
        {
            "repo": "repo",
            "rank": "pagerank",
            "seed_chunks": 10,
            "expand": 10,
            "chunk_lines": 400,
            "max_chunks": 50,
            "max_bytes": 2_000_000,
            "include_tests": True,
        },
    )

    assert lower.name == "lower"
    assert upper.name == "upper"
    assert lower.select(_case("alpha"), _context(snapshot)) == []
    assert upper.select(_case("alpha"), _context(snapshot))


def test_missing_repository_is_an_actionable_spec_error(workdir: Path) -> None:
    snapshot = _snapshot(workdir, {"src/mod.py": "alpha\n"})
    selector = build("ctx", {"repo": "elsewhere"})
    context = RunContext(
        run_id="run",
        seed=0,
        credential=None,
        events=EventEmitter("run"),
        repos={snapshot.name: snapshot},
    )

    with pytest.raises(SpecError) as caught:
        selector.select(_case("alpha"), context)

    assert caught.value.field == "contexts.ctx.options.repo"
    assert caught.value.value == "elsewhere"
    assert "not snapshotted" in caught.value.reason
    assert caught.value.fix

def _directory_link(link: Path, target: Path) -> bool:
    """Create a directory link, using a junction on Windows; report success."""
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    return True


def test_resolved_escape_is_refused(workdir: Path) -> None:
    snapshot = _snapshot(workdir, {"inside.py": "alpha needle\\n"})
    outside = workdir / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("alpha needle secret", encoding="utf-8")
    if not _directory_link(snapshot.tree / "link", outside):
        pytest.skip("this host cannot create directory links")

    dangerous = replace(
        snapshot,
        files=snapshot.files
        + (SnapshotFile("link/secret.py", "", 0, 0o644),),
    )
    chunks, _ = _select(
        dangerous,
        "alpha",
        {"include_tests": True, "seed_chunks": 10, "max_chunks": 20},
    )

    assert [chunk.path for chunk in chunks] == ["inside.py"]
    assert all("secret" not in chunk.text for chunk in chunks)
