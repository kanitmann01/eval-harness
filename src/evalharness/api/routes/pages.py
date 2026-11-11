"""Server-rendered console pages: run list, run detail and cell detail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from evalharness.api.deps import get_repository, render
from evalharness.api.graph_views import run_graph
from evalharness.api.views import (
    cell_error,
    citations_for,
    context_summary,
    heatmap,
    progress_summary,
    run_list_stats,
    run_thumbnails,
    run_verdict,
)
from evalharness.read_model import load_run_read
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get("/", response_class=HTMLResponse, name="run_list")
def run_list(
    request: Request,
    candidate: str = "",
    metric: str = "",
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """Render the run list, optionally narrowed by candidate and metric."""
    runs = repository.list_run_summaries(
        candidate=candidate or None, metric=metric or None
    )
    return render(
        request,
        "runs.html",
        {
            "runs": runs,
            "stats": run_list_stats(runs),
            "thumbnails": run_thumbnails(runs),
            "filters": repository.list_run_filters(),
            "selected_candidate": candidate,
            "selected_metric": metric,
            "filtered": bool(candidate or metric),
        },
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse, name="run_detail")
def run_detail(
    request: Request,
    run_id: str,
    cell: str = "",
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """Render one run's heatmap and, when selected, one cell's evidence."""
    return _detail_page(request, repository, run_id, cell)


@router.get(
    "/runs/{run_id}/cells/{cell_id}",
    response_class=HTMLResponse,
    name="cell_detail",
)
def cell_detail(
    request: Request,
    run_id: str,
    cell_id: str,
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """Render the run detail page with one cell selected."""
    return _detail_page(request, repository, run_id, cell_id)


def _detail_page(
    request: Request,
    repository: Repository,
    run_id: str,
    selected_cell_id: str,
) -> HTMLResponse:
    """Render a run with the optional selected cell, or a 404 page."""
    read = load_run_read(repository, run_id, with_events=True)
    if read is None:
        return _not_found(request, f"run {run_id} does not exist")
    selected = None
    if selected_cell_id:
        selected = next(
            (cell for cell in read.cells if cell.cell_id == selected_cell_id),
            None,
        )
        if selected is None:
            return _not_found(
                request,
                f"cell {selected_cell_id} is not part of run {run_id}",
            )
    columns, rows = heatmap(
        read.cells, read.checks_by_cell, run_id, selected_cell_id
    )
    selected_checks = read.checks_for(selected.cell_id) if selected else []
    graph = run_graph(read.run, read.cells, read.gates)
    return render(
        request,
        "run_detail.html",
        {
            "run": read.run,
            "duration_s": read.duration_s,
            "verdict": run_verdict(
                read.run, read.cells, read.checks, read.gates
            ),
            "progress": progress_summary(read.run, read.cells),
            "columns": columns,
            "rows": rows,
            "graph": graph,
            "contexts": context_summary(read.cells),
            "aggregates": read.aggregates,
            "gates": read.gates,
            "events": read.events,
            "selected_cell": selected,
            "selected_checks": selected_checks,
            "selected_citations": citations_for(selected) if selected else [],
            "selected_error": cell_error(selected) if selected else None,
        },
    )


def _not_found(request: Request, message: str) -> HTMLResponse:
    """Render the console's 404 page."""
    return render(request, "not_found.html", {"message": message}, status_code=404)