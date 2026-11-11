"""The demo library: metadata, bundled specs and first-use materialisation."""

from __future__ import annotations

from pathlib import Path

from evalharness.core.config import Settings
from evalharness.demos.catalog import (
    DEMOS,
    PIPELINE,
    bundled_text,
    find_demo,
    materialize,
    materialized_slugs,
    scenario_rows,
)


def test_pipeline_names_the_seven_stages_in_order() -> None:
    assert PIPELINE == (
        "Spec",
        "Cases",
        "Cells",
        "Checks",
        "Aggregates",
        "Gates",
        "Verdict",
    )


def test_every_demo_has_unique_identity_and_bundled_yaml(evalh_home: Path) -> None:
    assert len(DEMOS) == 4
    assert len({demo.slug for demo in DEMOS}) == len(DEMOS)
    for demo in DEMOS:
        assert find_demo(demo.slug) is demo
        assert demo.spec_name == f"demo-{demo.slug}"
        assert demo.edit_url == f"/specs/demo-{demo.slug}/edit"
        text = bundled_text(demo)
        assert f"name: {demo.spec_name}" in text
        assert "spec_version: 1" in text
    assert find_demo("no-such-demo") is None


def test_materialise_writes_the_bundled_copy_and_keeps_operator_edits(
    evalh_home: Path,
) -> None:
    settings = Settings.from_env()
    demo = DEMOS[0]
    first = materialize(settings, demo)
    assert first.is_file()
    assert first.name == "demo-prompt-choice.yaml"
    assert first.read_text(encoding="utf-8") == bundled_text(demo)

    first.write_text("name: edited\n", encoding="utf-8")
    assert materialize(settings, demo).read_text(encoding="utf-8") == "name: edited\n"

    first.unlink()
    assert materialize(settings, demo).read_text(encoding="utf-8") == bundled_text(demo)


def test_materialised_slugs_and_rows_follow_the_specs_directory(
    evalh_home: Path,
) -> None:
    settings = Settings.from_env()
    assert materialized_slugs(settings) == ()
    rows = scenario_rows(settings)
    assert len(rows) == len(DEMOS)
    assert all(row["materialized"] is False for row in rows)
    assert rows[0]["slug"] == "prompt-choice"
    assert rows[0]["spec_name"] == "demo-prompt-choice"
    assert rows[0]["edit_url"] == "/specs/demo-prompt-choice/edit"

    materialize(settings, DEMOS[1])
    assert materialized_slugs(settings) == ("context-grounding",)
    rows = scenario_rows(settings)
    assert rows[1]["materialized"] is True
    assert rows[0]["materialized"] is False
