"""Deterministic view model for the spec graph on the console run page.

The graph is the signature element of the console: five columns read a run
from left to right, every wire carries the number of cells that flow through
it, and every box and wire states its health as a tone. This module is pure.
It takes plain spec data, stored cells and gate results, and returns geometry
and labels. Nothing here reads the clock, the disk or the network, so the same
input always renders the same picture.

Tone rules:

- candidate and case: ok when every cell is ok, warn when some are ok and
  some are not, bad when none are ok, pending when there are no cells yet.
- metric: bad when a gate for that metric failed, ok when a gate passed,
  pending when its gates have no result yet, neutral when it has no gate.
- gate: ok or bad from GateResult.passed, pending while the spec declares it
  but no run has recorded a result.
- verdict: bad when any gate failed, pending when the run is still going or
  has no gates, ok otherwise.

An edge inherits the tone of the box it leaves, except feed edges, which are
neutral. The tone maps to one of pending, active, pass, fail or error, and
the SVG macro repeats fail and error as a dash pattern, so colour is never
the only signal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalharness.core.models import CellRecord, GateResult, RunRecord, Spec
from evalharness.core.spec_projection import (
    ProjectedGate,
    SpecProjection,
    projection_for,
)

_STATE_BY_TONE = {
    "neutral": "pending",
    "pending": "pending",
    "info": "active",
    "ok": "pass",
    "warn": "error",
    "bad": "fail",
}
_DASH_BY_STATE = {"fail": "2 3", "error": "6 4"}
_VERDICT_WORD = {"pending": "pending", "bad": "failed", "ok": "passed"}
_RUNNING_STATUSES = frozenset({"pending", "running"})
_VERDICT_KEY = "verdict"
_OK = "ok"
_COLUMN_WIDTH_RATIO = 0.16
_COLUMN_GAP_RATIO = 0.04
_MARGIN_RATIO = 0.02
_NODE_HEIGHT = 36.0

_LABEL_CHAR_UNITS = 6.5
"""Width of one 11px semibold label character, for column truncation."""
_NODE_GAP = 8.0
_NODE_TOP = 34.0
_NODE_BOTTOM = 12.0
_MIN_STROKE = 1.0
_MAX_STROKE = 6.0
_STROKE_PER_CELL = 0.25
_THUMB_WIDTH = 96.0
_THUMB_HEIGHT = 32.0
_RAIL_WIDTH = 320.0
_RAIL_NODE_HEIGHT = 18.0
_RAIL_NODE_GAP = 5.0
_RAIL_NODE_TOP = 20.0
_RAIL_NODE_BOTTOM = 8.0
_VARIANT_FULL = "full"
_VARIANT_RAIL = "compact"
_VARIANT_THUMB = "thumb"
_RAIL_LABELS = {"feeds": "Prompts", "gates": "Gates"}


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One box in the spec graph.

    The key is unique inside a graph, the column is the column key, and the
    detail is the mono count line under the label.
    """

    key: str
    column: str
    label: str
    text: str
    detail: str
    tone: str
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        """Return the horizontal centre of the box."""
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        """Return the vertical centre of the box."""
        return self.y + self.h / 2.0


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One orthogonal wire between two boxes.

    The weight is the number of cells the wire carries. The tone is the tone
    of the box it leaves, and the state is the tone expressed as one of
    pending, active, pass, fail or error.
    """

    source_key: str
    target_key: str
    cells: int
    tone: str
    state: str
    path: str

    @property
    def stroke_width(self) -> float:
        """Return the line width in pixels, heavier for busier wires."""
        return min(_MIN_STROKE + _STROKE_PER_CELL * self.cells, _MAX_STROKE)

    @property
    def dasharray(self) -> str:
        """Return a dash pattern that repeats fail and error without colour."""
        return _DASH_BY_STATE.get(self.state, "none")


@dataclass(frozen=True, slots=True)
class GraphColumn:
    """One named column of boxes."""

    key: str
    label: str
    x: float
    w: float


@dataclass(frozen=True, slots=True)
class SpecGraph:
    """A complete, deterministic description of one rendered graph."""

    width: float
    height: float
    columns: tuple[GraphColumn, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    aria: str
    variant: str = "full"


@dataclass(frozen=True, slots=True)
class _GateFace:
    """One gate as the picture needs it: label, measured metric and health."""

    label: str
    metric: str
    tone: str
    candidate: str | None


def spec_graph(
    spec: Spec | SpecProjection | Mapping[str, Any],
    *,
    cells: Sequence[CellRecord] = (),
    gates: Sequence[GateResult] = (),
    width: float = 960.0,
    short_labels: bool = False,
) -> SpecGraph:
    """Return the spec graph for one spec, optionally with run data.

    Parameters:
        spec: A validated core spec, its typed projection, or a raw snapshot
            mapping (typically RunRecord.spec_snapshot). All three become one
            projection before any layout happens.
        cells: Stored cells of the run, used for box tones and wire weights.
        gates: Gate results of the run. When omitted, declared gates show as
            pending.
        width: The total width of the SVG in pixels.
        short_labels: Use the abbreviated column headings. A narrow graph keeps
            its box labels but cannot fit the long headings.

    The model is pure and tolerant: missing keys and empty collections only
    remove boxes, they never raise.
    """
    return _build_graph(
        projection_for(spec),
        cells=cells,
        gates=gates,
        width=width,
        running=False,
        short=short_labels,
    )


def run_graph(
    run: RunRecord,
    cells: Sequence[CellRecord] = (),
    gates: Sequence[GateResult] = (),
    *,
    width: float = 960.0,
    short_labels: bool = False,
) -> SpecGraph:
    """Return the spec graph for a stored run, with live run state applied.

    Parameters:
        run: The stored run; run.spec_snapshot supplies the spec.
        cells: Stored cells of the run.
        gates: Stored gate results of the run.
        width: The total width of the SVG in pixels.
        short_labels: Use the abbreviated column headings.

    A run that is still pending or running shows its verdict as pending even
    when gate results are already present.
    """
    return _build_graph(
        projection_for(run.spec_snapshot),
        cells=cells,
        gates=gates,
        width=width,
        running=run.status in _RUNNING_STATUSES,
        short=short_labels,
    )


def thumbnail(
    graph: SpecGraph,
    *,
    width: float = _THUMB_WIDTH,
    height: float = _THUMB_HEIGHT,
) -> SpecGraph:
    """Return a miniature copy of a graph for a decorative table cell.

    Parameters:
        graph: The full graph to shrink.
        width: The width of the miniature in pixels.
        height: The height of the miniature in pixels.

    Boxes, wires and tones survive the scale. The labels stay in the model but
    the template omits them, because no label is legible at 96 by 32 pixels.
    """
    scale_x = width / graph.width if graph.width else 1.0
    scale_y = height / graph.height if graph.height else 1.0
    columns = tuple(
        GraphColumn(
            key=column.key,
            label=column.label,
            x=column.x * scale_x,
            w=column.w * scale_x,
        )
        for column in graph.columns
    )
    nodes = tuple(
        GraphNode(
            key=node.key,
            column=node.column,
            label=node.label,
            text=node.text,
            detail=node.detail,
            tone=node.tone,
            x=node.x * scale_x,
            y=node.y * scale_y,
            w=node.w * scale_x,
            h=node.h * scale_y,
        )
        for node in graph.nodes
    )
    return SpecGraph(
        width=float(width),
        height=float(height),
        columns=columns,
        nodes=nodes,
        edges=_rebuild_edges(graph.edges, nodes),
        aria=graph.aria,
        variant=_VARIANT_THUMB,
    )


def compact_graph(graph: SpecGraph) -> SpecGraph:
    """Return the same graph re-laid out for a narrow panel.

    Parameters:
        graph: The full graph to compress.

    The columns and tones are the ones the full graph uses, the geometry is not:
    a rail or a side-by-side pair is a few hundred pixels wide, so node labels
    would be grey mush. Only the short column headings, the box order and the
    wire weights remain.
    """
    columns = _columns(graph.width, short=True)
    by_column = {column.key: column for column in columns}
    depth: dict[str, int] = {}
    nodes: list[GraphNode] = []
    for node in graph.nodes:
        index = depth.get(node.column, 0)
        depth[node.column] = index + 1
        nodes.append(
            GraphNode(
                key=node.key,
                column=node.column,
                label=node.label,
                text=_display_text(node.label, by_column[node.column].w),
                detail=node.detail,
                tone=node.tone,
                x=by_column[node.column].x,
                y=_RAIL_NODE_TOP + index * (_RAIL_NODE_HEIGHT + _RAIL_NODE_GAP),
                w=by_column[node.column].w,
                h=_RAIL_NODE_HEIGHT,
            )
        )
    rows = max(depth.values(), default=1)
    height = (
        _RAIL_NODE_TOP
        + (rows - 1) * (_RAIL_NODE_HEIGHT + _RAIL_NODE_GAP)
        + _RAIL_NODE_HEIGHT
        + _RAIL_NODE_BOTTOM
    )
    return SpecGraph(
        width=graph.width,
        height=height,
        columns=columns,
        nodes=tuple(nodes),
        edges=_rebuild_edges(graph.edges, tuple(nodes)),
        aria=graph.aria,
        variant=_VARIANT_RAIL,
    )


def rail_graph(spec: Spec | SpecProjection | Mapping[str, Any]) -> SpecGraph:
    """Return the compact graph drawn inside the wizard rail.

    Parameters:
        spec: The draft spec being edited, as a document, core spec or typed
            projection.
    """
    return compact_graph(spec_graph(spec, width=_RAIL_WIDTH))


def _rebuild_edges(
    edges: Sequence[GraphEdge],
    nodes: Sequence[GraphNode],
) -> tuple[GraphEdge, ...]:
    """Return the same wires routed between boxes at their new positions."""
    by_key = {node.key: node for node in nodes}
    rebuilt: list[GraphEdge] = []
    for edge in edges:
        rebuilt.append(
            GraphEdge(
                source_key=edge.source_key,
                target_key=edge.target_key,
                cells=edge.cells,
                tone=edge.tone,
                state=edge.state,
                path=_edge_path(by_key[edge.source_key], by_key[edge.target_key]),
            )
        )
    return tuple(rebuilt)


def _build_graph(
    spec: SpecProjection,
    *,
    cells: Sequence[CellRecord],
    gates: Sequence[GateResult],
    width: float,
    running: bool,
    short: bool = False,
) -> SpecGraph:
    """Assemble the columns, boxes, wires and aria sentence for one graph."""
    case_ids = [case.id for case in spec.cases]
    candidates = [
        (
            candidate.name,
            f"candidate:{candidate.name}",
            candidate.prompt,
            candidate.context,
        )
        for candidate in spec.candidates
    ]
    metric_names = list(spec.metrics)
    prompt_names = [prompt.name for prompt in spec.prompts]
    context_names = [context.name for context in spec.contexts]
    faces = _gate_faces(gates, spec.gates)
    replicas = spec.replicas

    columns = _columns(width, short=short)

    case_nodes = [
        _node(
            key=f"case:{case_id}",
            column="cases",
            label=case_id,
            detail=_plural(len(_cells_for_case(cells, case_id)), "cell"),
            tone=_cell_tone(_cells_for_case(cells, case_id)),
            column_view=columns[0],
            index=index,
        )
        for index, case_id in enumerate(case_ids)
    ]

    feed_nodes: list[GraphNode] = []
    for index, prompt in enumerate(spec.prompts):
        detail = f"{len(prompt.text) if prompt.text is not None else 0} chars"
        feed_nodes.append(
            _node(
                key=f"prompt:{prompt.name}",
                column="feeds",
                label=prompt.name,
                detail=detail,
                tone="neutral",
                column_view=columns[1],
                index=index,
            )
        )
    for index, context in enumerate(spec.contexts):
        feed_nodes.append(
            _node(
                key=f"context:{context.name}",
                column="feeds",
                label=context.name,
                detail=_plural(context.chunks, "chunk"),
                tone="neutral",
                column_view=columns[1],
                index=len(prompt_names) + index,
            )
        )

    candidate_nodes: list[GraphNode] = []
    for index, (name, key, _prompt, _context) in enumerate(candidates):
        own = _cells_for_candidate(cells, name)
        candidate_nodes.append(
            _node(
                key=key,
                column="candidates",
                label=name,
                detail=_plural(len(own), "cell"),
                tone=_cell_tone(own),
                column_view=columns[2],
                index=index,
            )
        )

    metric_nodes: list[GraphNode] = []
    for index, key in enumerate(metric_names):
        measured = [face for face in faces if face.metric == key]
        metric_nodes.append(
            _node(
                key=f"metric:{key}",
                column="metrics",
                label=key,
                detail=_plural(len(measured), "gate"),
                tone=_metric_tone(measured),
                column_view=columns[3],
                index=index,
            )
        )

    gate_nodes = [
        _node(
            key=f"gate:{index}",
            column="gates",
            label=face.label,
            detail=_plural(len(_cells_behind(cells, face)), "cell"),
            tone=face.tone,
            column_view=columns[4],
            index=index,
        )
        for index, face in enumerate(faces)
    ]
    verdict = _node(
        key=_VERDICT_KEY,
        column="gates",
        label="Verdict",
        detail=f"{_failed(faces)}/{len(faces)} gates",
        tone=_verdict_tone(faces, running),
        column_view=columns[4],
        index=len(faces),
    )

    nodes = (
        *case_nodes,
        *feed_nodes,
        *candidate_nodes,
        *metric_nodes,
        *gate_nodes,
        verdict,
    )
    by_key = {node.key: node for node in nodes}
    edges: list[GraphEdge] = []

    for case_id in case_ids:
        case_cells = _cells_for_case(cells, case_id)
        case_key = f"case:{case_id}"
        for name, candidate_key, _prompt, _context in candidates:
            count = sum(1 for cell in case_cells if cell.candidate == name)
            _add_edge(
                edges,
                by_key[case_key],
                by_key[candidate_key],
                count,
                by_key[case_key].tone,
            )

    for name, candidate_key, prompt, context in candidates:
        count = len(_cells_for_candidate(cells, name))
        for prefix, known, value in (
            ("prompt", prompt_names, prompt),
            ("context", context_names, context),
        ):
            if value is not None and value in known:
                _add_edge(
                    edges,
                    by_key[f"{prefix}:{value}"],
                    by_key[candidate_key],
                    count,
                    "neutral",
                )

    for name, candidate_key, _prompt, _context in candidates:
        count = len(_cells_for_candidate(cells, name))
        tone = by_key[candidate_key].tone
        for metric in metric_names:
            _add_edge(
                edges,
                by_key[candidate_key],
                by_key[f"metric:{metric}"],
                count,
                tone,
            )

    for index, face in enumerate(faces):
        gate_key = f"gate:{index}"
        metric_key = f"metric:{face.metric}"
        count = len(_cells_behind(cells, face))
        if metric_key in by_key:
            _add_edge(
                edges,
                by_key[metric_key],
                by_key[gate_key],
                count,
                by_key[metric_key].tone,
            )
        _add_edge(
            edges,
            by_key[gate_key],
            by_key[_VERDICT_KEY],
            count,
            face.tone,
        )

    rows = max(
        len(case_nodes),
        len(feed_nodes),
        len(candidate_nodes),
        len(metric_nodes),
        len(gate_nodes) + 1,
    )
    return SpecGraph(
        width=float(width),
        height=_height(rows),
        columns=columns,
        nodes=nodes,
        edges=tuple(edges),
        aria=_aria(
            n_candidates=len(candidates),
            n_cases=len(case_ids),
            planned=len(candidates) * len(case_ids) * replicas,
            faces=faces,
            running=running,
        ),
    )


def _node(
    *,
    key: str,
    column: str,
    label: str,
    detail: str,
    tone: str,
    column_view: GraphColumn,
    index: int,
) -> GraphNode:
    """Return a box placed at its index inside one column."""
    return GraphNode(
        key=key,
        column=column,
        label=label,
        text=_display_text(label, column_view.w),
        detail=detail,
        tone=tone,
        x=column_view.x,
        y=_stack_boxes(index),
        w=column_view.w,
        h=_NODE_HEIGHT,
    )


def _display_text(label: str, width: float) -> str:
    """Truncate a node label to its column box; the title keeps the full name.

    A long model or candidate name drawn at full length crosses the gutter
    into the next column's boxes and wires, which the design system forbids;
    the macro's <title> still carries the whole name.
    """
    max_chars = max(4, int((width - 16) / _LABEL_CHAR_UNITS))
    if len(label) <= max_chars:
        return label
    return label[: max_chars - 1] + "…"


def _columns(width: float, *, short: bool = False) -> tuple[GraphColumn, ...]:
    """Return the five fixed columns, left to right.

    Parameters:
        width: The width of the drawing in pixels.
        short: Use the abbreviated headings the narrow rail has room for.
    """
    column_w = width * _COLUMN_WIDTH_RATIO
    stride = width * (_COLUMN_WIDTH_RATIO + _COLUMN_GAP_RATIO)
    margin = width * _MARGIN_RATIO
    definitions = (
        ("cases", "Cases"),
        ("feeds", "Prompts and contexts"),
        ("candidates", "Candidates"),
        ("metrics", "Metrics"),
        ("gates", "Gates and verdict"),
    )
    columns: list[GraphColumn] = []
    for index, (key, label) in enumerate(definitions):
        if short:
            label = _RAIL_LABELS.get(key, label)
        columns.append(
            GraphColumn(key=key, label=label, x=margin + stride * index, w=column_w)
        )
    return tuple(columns)


def _stack_boxes(index: int) -> float:
    """Return the top edge of the box at one column slot."""
    return _NODE_TOP + index * (_NODE_HEIGHT + _NODE_GAP)


def _height(rows: int) -> float:
    """Return the SVG height for the tallest column."""
    return _stack_boxes(max(rows, 1) - 1) + _NODE_HEIGHT + _NODE_BOTTOM


def _edge_path(source: GraphNode, target: GraphNode) -> str:
    """Return one orthogonal path from the right edge of source to target."""
    return (
        f"M {_number(source.x + source.w)} {_number(source.cy)} "
        f"H {_number(target.x)} V {_number(target.cy)}"
    )


def _number(value: float) -> str:
    """Return a compact, stable decimal string for SVG output."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _add_edge(
    edges: list[GraphEdge],
    source: GraphNode,
    target: GraphNode,
    cells: int,
    tone: str,
) -> None:
    """Append one wire from the right edge of source to the left of target."""
    edges.append(
        GraphEdge(
            source_key=source.key,
            target_key=target.key,
            cells=cells,
            tone=tone,
            state=_STATE_BY_TONE[tone],
            path=_edge_path(source, target),
        )
    )


def _gate_faces(
    gates: Sequence[GateResult],
    declared: Sequence[ProjectedGate],
) -> tuple[_GateFace, ...]:
    """Return gate faces from results, or from declarations when empty."""
    if gates:
        return tuple(
            _GateFace(
                label=gate.name or gate.metric or "gate",
                metric=gate.metric,
                tone="ok" if gate.passed else "bad",
                candidate=gate.candidate,
            )
            for gate in gates
        )
    return tuple(
        _GateFace(
            label=gate.label,
            metric=gate.metric,
            tone="pending",
            candidate=gate.candidate,
        )
        for gate in declared
    )


def _cells_for_case(cells: Sequence[CellRecord], case_id: str) -> list[CellRecord]:
    """Return every stored cell of one case."""
    return [cell for cell in cells if cell.case_id == case_id]


def _cells_for_candidate(
    cells: Sequence[CellRecord],
    candidate: str,
) -> list[CellRecord]:
    """Return every stored cell of one candidate."""
    return [cell for cell in cells if cell.candidate == candidate]


def _cells_behind(
    cells: Sequence[CellRecord],
    face: _GateFace,
) -> list[CellRecord]:
    """Return the cells a gate measures: its candidate, or every cell."""
    if face.candidate:
        return [cell for cell in cells if cell.candidate == face.candidate]
    return list(cells)


def _cell_tone(cells: Sequence[CellRecord]) -> str:
    """Return the tone from the ok count of a box's cells."""
    total = len(cells)
    if not total:
        return "pending"
    ok = sum(1 for cell in cells if cell.status == _OK)
    if ok == total:
        return "ok"
    if ok == 0:
        return "bad"
    return "warn"


def _metric_tone(faces: Sequence[_GateFace]) -> str:
    """Return the tone from the gates that measure this metric."""
    if not faces:
        return "neutral"
    if any(face.tone == "bad" for face in faces):
        return "bad"
    if any(face.tone == "ok" for face in faces):
        return "ok"
    return "pending"


def _verdict_tone(faces: Sequence[_GateFace], running: bool) -> str:
    """Return the verdict tone, pending while the run is still going."""
    if running or not faces:
        return "pending"
    if _failed(faces):
        return "bad"
    if any(face.tone == "pending" for face in faces):
        return "pending"
    return "ok"


def _failed(faces: Sequence[_GateFace]) -> int:
    """Return how many gates failed."""
    return sum(1 for face in faces if face.tone == "bad")


def _aria(
    *,
    n_candidates: int,
    n_cases: int,
    planned: int,
    faces: Sequence[_GateFace],
    running: bool,
) -> str:
    """Return the one-sentence description read by assistive technology."""
    produces = "produces" if planned == 1 else "produce"
    verdict = _VERDICT_WORD[_verdict_tone(faces, running)]
    return (
        f"{_plural(n_candidates, 'candidate')} over {_plural(n_cases, 'case')} "
        f"{produces} {_plural(planned, 'cell')}; {_plural(len(faces), 'gate')}, "
        f"{_failed(faces)} failed; verdict {verdict}."
    )


def _plural(count: int, word: str) -> str:
    """Return the count with a singular or plural noun."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
