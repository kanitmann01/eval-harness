"""Tests for the spec graph view model and its server-rendered template."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader

from evalharness.api.graph_views import (
    SpecGraph,
    rail_graph,
    run_graph,
    spec_graph,
    thumbnail,
)
from evalharness.api.views import run_thumbnails
from evalharness.core.models import CellRecord, GateResult, RunRecord
from evalharness.storage.repository import RunSummary

TEMPLATES = Path(__file__).parents[1] / "src" / "evalharness" / "api" / "templates"


def _cell(
    candidate: str,
    case_id: str,
    status: str = "ok",
    replica: int = 1,
) -> CellRecord:
    """Build one stored cell for the graph edge and tone tests."""
    return CellRecord(
        cell_id=f"{candidate}:{case_id}:{replica}",
        run_id="run-1",
        candidate=candidate,
        case_id=case_id,
        replica=replica,
        status=status,
    )


def _gate(
    metric: str,
    passed: bool,
    *,
    name: str = "floor",
    candidate: str | None = None,
) -> GateResult:
    """Build one gate result with just the fields the graph reads."""
    return GateResult(
        name=name,
        metric=metric,
        passed=passed,
        candidate=candidate,
        n=1,
    )


def _tones_spec() -> dict[str, Any]:
    """Return a spec whose cells exercise every candidate and case tone."""
    return {
        "name": "tone-spec",
        "source": {
            "adapter": "inline",
            "options": {
                "cases": [
                    {"case_id": "ok-case"},
                    {"case_id": "mix-case"},
                    {"case_id": "bad-case"},
                    {"case_id": "empty-case"},
                ]
            },
        },
        "candidates": [
            {"name": "cand-ok", "options": {}},
            {"name": "cand-warn", "options": {}},
            {"name": "cand-bad", "options": {}},
            {"name": "cand-none", "options": {}},
        ],
        "metrics": [{"key": "exact_match"}],
        "replicas": 1,
    }


def _tones_cells() -> list[CellRecord]:
    """Return cells that make one node of every tone from _tones_spec."""
    return [
        _cell("cand-ok", "ok-case"),
        _cell("cand-ok", "mix-case"),
        _cell("cand-warn", "ok-case"),
        _cell("cand-warn", "mix-case", "error"),
        _cell("cand-bad", "mix-case", "error"),
        _cell("cand-bad", "bad-case", "error"),
    ]


def _full_spec() -> dict[str, Any]:
    """Return the 4 candidate, 12 case, 3 gate spec the edge test counts."""
    return {
        "name": "full-spec",
        "source": {
            "adapter": "inline",
            "options": {
                "cases": [{"case_id": f"case-{index:02d}"} for index in range(1, 13)]
            },
        },
        "prompts": {
            "concise": "Answer with digits only.",
            "explained": "Answer with a short clause.",
        },
        "contexts": {
            "none": [],
            "policy": [{"chunk_id": "policy-1", "text": "Answer as a bare number."}],
        },
        "candidates": [
            {"name": "c1", "options": {"prompt": "concise", "context": "none"}},
            {"name": "c2", "options": {"prompt": "explained", "context": "policy"}},
            {"name": "c3", "options": {"prompt": "concise", "context": "none"}},
            {"name": "c4", "options": {"prompt": "explained", "context": "policy"}},
        ],
        "metrics": [
            {"key": "exact_match"},
            {"key": "contains"},
            {"key": "cited"},
        ],
        "gates": [
            {"metric": "exact_match", "min": 0.9},
            {"metric": "contains", "min": 0.5, "candidate": "c1"},
            {"metric": "cited", "min": 0.5},
        ],
        "replicas": 1,
    }


def _full_cells() -> list[CellRecord]:
    """Return 48 cells: three candidates pass every case, the last errors."""
    cells: list[CellRecord] = []
    for candidate in ("c1", "c2", "c3", "c4"):
        status = "error" if candidate == "c4" else "ok"
        for index in range(1, 13):
            cells.append(_cell(candidate, f"case-{index:02d}", status))
    return cells


def _full_gates() -> list[GateResult]:
    """Return three gates: one fails, two pass, one of them candidate scoped."""
    return [
        _gate("exact_match", False, name="floor"),
        _gate("contains", True, name="contains-floor", candidate="c1"),
        _gate("cited", True, name="cited-floor"),
    ]


def _single_spec() -> dict[str, Any]:
    """Return the smallest spec that still declares a case, metric and gate."""
    return {
        "source": {"options": {"cases": [{"case_id": "case-1"}]}},
        "candidates": [{"name": "solo", "options": {}}],
        "metrics": [{"key": "exact_match"}],
        "gates": [{"metric": "exact_match", "min": 0.9}],
        "replicas": 1,
    }


def _partial_spec() -> dict[str, Any]:
    """Return a spec with no prompts list, no contexts mapping and odd values."""
    return {
        "source": {"options": {"cases": [{}, {"case_id": ""}]}},
        "prompts": {"": "x"},
        "contexts": ["not-a-mapping"],
        "candidates": [
            {},
            {"name": "solo", "options": {"prompt": "missing", "context": "missing"}},
        ],
        "metrics": [{"key": "ghost"}, {}],
        "gates": [
            {"metric": "ghost", "candidate": "solo"},
            {},
        ],
        "replicas": "two",
    }


def test_columns_are_fixed_and_ordered() -> None:
    """Five columns always appear, left to right, with proportional spacing."""
    graph = spec_graph({})
    assert graph.width == 960.0
    assert [column.key for column in graph.columns] == [
        "cases",
        "feeds",
        "candidates",
        "metrics",
        "gates",
    ]
    assert [column.label for column in graph.columns] == [
        "Cases",
        "Prompts and contexts",
        "Candidates",
        "Metrics",
        "Gates and verdict",
    ]
    xs = [column.x for column in graph.columns]
    assert xs == sorted(xs)
    assert graph.columns[0].x == pytest.approx(19.2)
    last = graph.columns[-1]
    assert last.x + last.w == pytest.approx(940.8)


def test_empty_spec_degrades_to_the_verdict_only() -> None:
    """An empty spec renders one pending verdict box and no edges."""
    graph = spec_graph({})
    assert [node.key for node in graph.nodes] == ["verdict"]
    assert graph.nodes[0].tone == "pending"
    assert graph.nodes[0].detail == "0/0 gates"
    assert graph.edges == ()
    assert graph.height == 82.0
    assert graph.aria == (
        "0 candidates over 0 cases produce 0 cells; 0 gates, 0 failed; "
        "verdict pending."
    )


def test_candidate_and_case_tones_follow_their_cells() -> None:
    """Every ok is ok, some ok is warn, none ok is bad, no cells is pending."""
    graph = spec_graph(_tones_spec(), cells=_tones_cells())
    nodes = {node.key: node for node in graph.nodes}
    assert nodes["candidate:cand-ok"].tone == "ok"
    assert nodes["candidate:cand-warn"].tone == "warn"
    assert nodes["candidate:cand-bad"].tone == "bad"
    assert nodes["candidate:cand-none"].tone == "pending"
    assert nodes["case:ok-case"].tone == "ok"
    assert nodes["case:mix-case"].tone == "warn"
    assert nodes["case:bad-case"].tone == "bad"
    assert nodes["case:empty-case"].tone == "pending"
    assert nodes["metric:exact_match"].tone == "neutral"
    assert nodes["verdict"].tone == "pending"


def test_metric_tone_follows_its_gates() -> None:
    """A failed gate makes the metric bad, a passed gate ok, no gate neutral."""
    spec = _single_spec()
    spec["metrics"] = [
        {"key": "bad-metric"},
        {"key": "good-metric"},
        {"key": "bare-metric"},
    ]
    spec["gates"] = [{"metric": "bad-metric"}, {"metric": "good-metric"}]
    cells = [_cell("solo", "case-1")]
    gates = [
        _gate("bad-metric", False, name=""),
        _gate("good-metric", True, name="good"),
    ]
    graph = spec_graph(spec, cells=cells, gates=gates)
    nodes = {node.key: node for node in graph.nodes}
    assert nodes["metric:bad-metric"].tone == "bad"
    assert nodes["metric:good-metric"].tone == "ok"
    assert nodes["metric:bare-metric"].tone == "neutral"
    assert nodes["metric:bad-metric"].detail == "1 gate"
    assert nodes["metric:bare-metric"].detail == "0 gates"
    assert nodes["gate:0"].label == "bad-metric"
    assert nodes["gate:0"].tone == "bad"
    assert nodes["gate:1"].tone == "ok"
    assert nodes["verdict"].tone == "bad"
    assert nodes["verdict"].detail == "1/2 gates"


def test_declared_gates_stay_pending_until_a_run_records_them() -> None:
    """A spec preview shows declared gates as pending, not as a passing run."""
    graph = spec_graph(_single_spec())
    nodes = {node.key: node for node in graph.nodes}
    assert nodes["gate:0"].label == "exact_match"
    assert nodes["gate:0"].tone == "pending"
    assert nodes["gate:0"].detail == "0 cells"
    assert nodes["metric:exact_match"].tone == "pending"
    assert nodes["verdict"].tone == "pending"
    assert "verdict pending" in graph.aria


def test_full_graph_edges_have_endpoints_weights_and_states() -> None:
    """The 4 candidate, 12 case, 3 gate spec yields the expected edges."""
    graph = spec_graph(_full_spec(), cells=_full_cells(), gates=_full_gates())
    nodes = {node.key: node for node in graph.nodes}
    edges = {(edge.source_key, edge.target_key): edge for edge in graph.edges}
    assert len(nodes) == 27
    assert len(graph.edges) == 74
    assert nodes["candidate:c1"].tone == "ok"
    assert nodes["candidate:c4"].tone == "bad"
    assert nodes["case:case-01"].tone == "warn"
    assert nodes["metric:exact_match"].tone == "bad"
    assert nodes["metric:contains"].tone == "ok"
    assert nodes["gate:0"].tone == "bad"
    assert nodes["gate:1"].tone == "ok"
    assert nodes["verdict"].tone == "bad"
    assert nodes["verdict"].detail == "1/3 gates"

    pair = edges[("case:case-01", "candidate:c1")]
    assert pair.cells == 1
    assert pair.tone == "warn"
    assert pair.state == "error"

    feed = edges[("prompt:concise", "candidate:c1")]
    assert feed.cells == 12
    assert feed.tone == "neutral"
    assert feed.state == "pending"

    metric = edges[("candidate:c1", "metric:exact_match")]
    assert metric.cells == 12
    assert metric.tone == "ok"
    assert metric.state == "pass"

    failed_gate = edges[("metric:exact_match", "gate:0")]
    assert failed_gate.cells == 48
    assert failed_gate.state == "fail"
    assert edges[("metric:contains", "gate:1")].cells == 12
    assert edges[("gate:0", "verdict")].cells == 48
    assert edges[("gate:1", "verdict")].state == "pass"


def test_edge_paths_are_orthogonal_and_touch_both_boxes() -> None:
    """Every path is 'M x y H x2 V y2' from the source edge to the target edge."""
    graph = spec_graph(_full_spec(), cells=_full_cells(), gates=_full_gates())
    nodes = {node.key: node for node in graph.nodes}
    assert graph.edges
    for edge in graph.edges:
        parts = edge.path.split()
        assert len(parts) == 7
        assert (parts[0], parts[3], parts[5]) == ("M", "H", "V")
        source = nodes[edge.source_key]
        target = nodes[edge.target_key]
        assert float(parts[1]) == pytest.approx(source.x + source.w)
        assert float(parts[2]) == pytest.approx(source.cy)
        assert float(parts[4]) == pytest.approx(target.x)
        assert float(parts[6]) == pytest.approx(target.cy)


def test_edge_weight_drives_stroke_and_state_drives_dashes() -> None:
    """Cell count becomes line width; fail and error also change the dash."""
    graph = spec_graph(_full_spec(), cells=_full_cells(), gates=_full_gates())
    edges = {(edge.source_key, edge.target_key): edge for edge in graph.edges}
    one = edges[("case:case-01", "candidate:c1")]
    twelve = edges[("candidate:c1", "metric:exact_match")]
    wide = edges[("gate:0", "verdict")]
    assert one.cells == 1
    assert one.stroke_width == 1.25
    assert twelve.cells == 12
    assert twelve.stroke_width == 4.0
    assert wide.cells == 48
    assert wide.stroke_width == 6.0
    assert (one.state, one.dasharray) == ("error", "6 4")
    assert (twelve.state, twelve.dasharray) == ("pass", "none")
    assert (wide.state, wide.dasharray) == ("fail", "2 3")


def test_graph_is_deterministic_for_the_same_input() -> None:
    """The same spec, cells and gates render exactly the same graph."""
    first = spec_graph(_full_spec(), cells=_full_cells(), gates=_full_gates())
    second = spec_graph(_full_spec(), cells=_full_cells(), gates=_full_gates())
    assert first == second
    assert first.nodes == second.nodes
    assert first.edges == second.edges
    assert first.aria == second.aria


def test_node_centres_are_derived_from_the_box() -> None:
    """Node centres are the box midpoint, in both directions."""
    node = spec_graph(_single_spec()).nodes[0]
    assert node.cx == pytest.approx(node.x + node.w / 2)
    assert node.cy == pytest.approx(node.y + node.h / 2)


def test_aria_text_states_pass_fail_and_running() -> None:
    """One sentence states the matrix, the gates and the verdict."""
    spec = _single_spec()
    cells = [_cell("solo", "case-1")]
    passed = [_gate("exact_match", True)]
    failed = [_gate("exact_match", False)]
    ok = spec_graph(spec, cells=cells, gates=passed)
    assert ok.aria == (
        "1 candidate over 1 case produces 1 cell; 1 gate, 0 failed; "
        "verdict passed."
    )
    bad = spec_graph(spec, cells=cells, gates=failed)
    assert bad.aria == (
        "1 candidate over 1 case produces 1 cell; 1 gate, 1 failed; "
        "verdict failed."
    )
    run = RunRecord(run_id="run-1", status="running", spec_snapshot=spec)
    live = run_graph(run, cells, passed)
    assert live.aria == (
        "1 candidate over 1 case produces 1 cell; 1 gate, 0 failed; "
        "verdict pending."
    )
    assert live.nodes[-1].tone == "pending"


def test_run_graph_reads_the_run_snapshot_and_status() -> None:
    """run_graph builds from run.spec_snapshot and derives the live state."""
    run = RunRecord(run_id="run-1", status="ok", spec_snapshot=_full_spec())
    graph = run_graph(run, _full_cells(), _full_gates())
    assert isinstance(graph, SpecGraph)
    assert [node.key for node in graph.nodes if node.column == "candidates"] == [
        "candidate:c1",
        "candidate:c2",
        "candidate:c3",
        "candidate:c4",
    ]
    assert graph.nodes[-1].tone == "bad"
    assert "verdict failed" in graph.aria
    running = RunRecord(run_id="run-2", status="running", spec_snapshot=_full_spec())
    assert run_graph(running, _full_cells(), _full_gates()).nodes[-1].tone == "pending"


def test_partial_spec_never_raises_and_drops_missing_nodes() -> None:
    """Missing prompts, contexts, metrics and gates degrade to fewer nodes."""
    graph = spec_graph(_partial_spec())
    nodes = {node.key: node for node in graph.nodes}
    assert len(nodes) == 9
    assert len(graph.edges) == 9
    assert nodes["case:case-1"].tone == "pending"
    assert nodes["candidate:candidate-1"].tone == "pending"
    assert nodes["metric:ghost"].tone == "pending"
    assert "metric:metric-1" not in nodes
    assert nodes["gate:0"].tone == "pending"
    assert nodes["gate:1"].tone == "pending"
    assert nodes["verdict"].tone == "pending"
    assert all(edge.source_key != "prompt:missing" for edge in graph.edges)
    assert graph.aria == (
        "2 candidates over 2 cases produce 4 cells; 2 gates, 0 failed; "
        "verdict pending."
    )
    strange = spec_graph({"metrics": "not-a-list", "candidates": None, "source": {}})
    assert [node.key for node in strange.nodes] == ["verdict"]


def test_graph_macro_renders_one_server_side_svg() -> None:
    """The macro emits one labelled svg with one group per node and path per edge."""
    graph = spec_graph(_full_spec(), cells=_full_cells(), gates=_full_gates())
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=True,
    )
    template = environment.from_string(
        '{% from "_graph.html" import render_graph %}{{ render_graph(graph) }}'
    )
    svg = template.render(graph=graph)
    assert svg.count("<svg") == 1
    assert 'role="img"' in svg
    assert f'aria-label="{graph.aria}"' in svg
    assert f'viewBox="0 0 {graph.width} {graph.height}"' in svg
    assert svg.count('class="sg-col"') == 5
    assert svg.count('class="sg-node ') == len(graph.nodes)
    assert svg.count('class="sg-edge ') == len(graph.edges)
    assert "sg-tone-bad" in svg
    assert "sg-state-fail" in svg
    assert "stroke-width: 6.0px" in svg
    assert "stroke-dasharray: 2 3" in svg


def _render(source: str, **context: Any) -> str:
    """Render one template fragment against the console templates."""
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=True,
    )
    return environment.from_string(source).render(**context)


def _summary(run_id: str, spec: dict[str, Any]) -> RunSummary:
    """Build the run-list row the thumbnail view model reads."""
    return RunSummary(
        run_id=run_id,
        spec_name=str(spec.get("name", "")),
        status="ok",
        cost_usd=0.0,
        pass_rate=1.0,
        duration_s=1.0,
        n_cells=1,
        n_ok=1,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=None,
        candidates=(),
        metrics=("exact_match",),
        failed_gates=(),
        spec_snapshot=spec,
    )


def test_thumbnail_keeps_shape_tones_and_aria() -> None:
    """The mini graph keeps every box, wire and tone inside its 96x32 box."""
    graph = spec_graph(_full_spec(), cells=_full_cells(), gates=_full_gates())
    mini = thumbnail(graph)
    assert mini.width == 96.0
    assert mini.height == 32.0
    assert mini.variant == "thumb"
    assert mini.aria == graph.aria
    assert [node.key for node in mini.nodes] == [node.key for node in graph.nodes]
    assert [node.tone for node in mini.nodes] == [node.tone for node in graph.nodes]
    assert len(mini.edges) == len(graph.edges)
    assert {node.key: node for node in mini.nodes}["verdict"].tone == "bad"
    assert all(0.0 <= node.x <= 96.0 - node.w for node in mini.nodes)
    assert all(0.0 <= node.y <= 32.0 - node.h for node in mini.nodes)


def test_thumbnail_edges_join_the_scaled_boxes() -> None:
    """Every scaled wire still starts at a source box and lands on its target."""
    graph = spec_graph(_tones_spec())
    mini = thumbnail(graph)
    nodes = {node.key: node for node in mini.nodes}
    for edge in mini.edges:
        source = nodes[edge.source_key]
        target = nodes[edge.target_key]
        tokens = edge.path.replace("M", " ").replace("H", " ").replace("V", " ").split()
        assert len(tokens) == 4
        start_x, start_y, elbow_x, end_y = (float(token) for token in tokens)
        assert start_x == pytest.approx(source.x + source.w, abs=0.01)
        assert start_y == pytest.approx(source.cy, abs=0.01)
        assert elbow_x == pytest.approx(target.x, abs=0.01)
        assert end_y == pytest.approx(target.cy, abs=0.01)


def test_row_thumbnail_is_aria_hidden_and_drops_labels() -> None:
    """A decorative table thumbnail loses its accessible name and text labels."""
    graph = thumbnail(spec_graph(_tones_spec()))
    svg = _render(
        '{% from "_graph.html" import render_graph %}'
        "{{ render_graph(graph, hidden=true) }}",
        graph=graph,
    )
    assert 'aria-hidden="true"' in svg
    assert 'focusable="false"' in svg
    assert 'role="img"' not in svg
    assert "aria-label" not in svg
    assert "sg-label" not in svg
    assert "sg-detail" not in svg
    assert svg.count('class="sg-node ') == len(graph.nodes)
    assert svg.count('class="sg-edge ') == len(graph.edges)


def test_rail_graph_is_compact_but_keeps_every_node() -> None:
    """The rail graph stacks the same nodes at 320 units without node text."""
    full = spec_graph(_full_spec())
    rail = rail_graph(_full_spec())
    assert rail.variant == "compact"
    assert rail.width == 320.0
    assert [column.key for column in rail.columns] == [
        "cases",
        "feeds",
        "candidates",
        "metrics",
        "gates",
    ]
    assert [column.label for column in rail.columns] == [
        "Cases",
        "Prompts",
        "Candidates",
        "Metrics",
        "Gates",
    ]
    assert [node.key for node in rail.nodes] == [node.key for node in full.nodes]
    assert [node.tone for node in rail.nodes] == [node.tone for node in full.nodes]
    assert all(node.h == 18.0 for node in rail.nodes)
    assert all(0.0 <= node.x <= 320.0 - node.w for node in rail.nodes)
    assert all(0.0 <= node.y <= rail.height - node.h for node in rail.nodes)
    svg = _render(
        '{% from "_graph.html" import render_graph %}{{ render_graph(graph) }}',
        graph=rail,
    )
    assert "Prompts and contexts" not in svg
    assert "sg-label" not in svg
    assert "<title>" in svg
    assert svg.count('class="sg-node ') == len(rail.nodes)


def test_full_graph_can_be_named_by_its_own_title_and_description() -> None:
    """Passing ids gives the svg a title and a long description for screen readers."""
    graph = spec_graph(_tones_spec())
    svg = _render(
        '{% from "_graph.html" import render_graph %}'
        '{{ render_graph(graph, title_id="graph-title", desc_id="graph-desc") }}',
        graph=graph,
    )
    assert 'aria-labelledby="graph-title graph-desc"' in svg
    assert '<title id="graph-title">Spec graph</title>' in svg
    assert f'<desc id="graph-desc">{graph.aria}</desc>' in svg
    assert 'aria-label="' not in svg


def test_run_thumbnails_builds_one_mini_graph_per_run() -> None:
    """The runs list gets one 96-wide decorative graph per row, keyed by run id."""
    spec = _tones_spec()
    runs = [_summary("run-a", spec), _summary("run-b", spec)]
    thumbnails = run_thumbnails(runs)
    assert sorted(thumbnails) == ["run-a", "run-b"]
    assert all(graph.width == 96.0 for graph in thumbnails.values())
    assert all(graph.height == 32.0 for graph in thumbnails.values())
    assert all(graph.variant == "thumb" for graph in thumbnails.values())
