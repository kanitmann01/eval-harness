"""JSON read surface, SSE progress and artifact download for runs."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from evalharness.api.artifacts import artifact_file
from evalharness.api.deps import get_repository, get_settings
from evalharness.api.views import cell_data, run_detail_data, run_summary_data
from evalharness.core.config import Settings
from evalharness.core.events import Event
from evalharness.read_model import load_run_read
from evalharness.storage.repository import Repository

router = APIRouter()

TERMINAL_EVENT = "run_finished"
"""The event after which a run has no more progress to stream."""

SSE_POLL_SECONDS = 0.2
"""How often the stream re-reads the events table."""


@router.get("/api/runs", name="api_runs")
def list_runs(
    candidate: str = "",
    metric: str = "",
    repository: Repository = Depends(get_repository),
) -> list[dict[str, Any]]:
    """Return the run list, optionally narrowed by candidate and metric."""
    summaries = repository.list_run_summaries(
        candidate=candidate or None, metric=metric or None
    )
    return [run_summary_data(summary) for summary in summaries]


@router.get("/api/runs/{run_id}", name="api_run")
def get_run(
    run_id: str,
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    """Return one run with its aggregates, gates and cells."""
    read = load_run_read(repository, run_id)
    if read is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} does not exist")
    return run_detail_data(read)


@router.get("/api/runs/{run_id}/cells", name="api_run_cells")
def run_cells(
    run_id: str,
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    """Return every cell of a run with its checks."""
    read = load_run_read(repository, run_id)
    if read is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} does not exist")
    return {
        "run_id": run_id,
        "cells": [
            cell_data(cell, read.checks_for(cell.cell_id)) for cell in read.cells
        ],
    }


@router.get(
    "/runs/{run_id}/cells/{cell_id}/artifact",
    name="cell_artifact",
)
def cell_artifact(
    run_id: str,
    cell_id: str,
    file: str = "cell.json",
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve an allowlisted artifact file stored for one cell."""
    directory = repository.artifact_path(run_id, cell_id)
    path = artifact_file(directory, file, settings.artifacts_dir)
    if path is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(path)


@router.get("/runs/{run_id}/events", name="run_events")
@router.get("/api/runs/{run_id}/events", name="api_run_events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = 0,
    repository: Repository = Depends(get_repository),
) -> StreamingResponse:
    """Stream a run's stored events as SSE until the terminal event."""
    if repository.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} does not exist")
    header = request.headers.get("last-event-id", "")
    last = int(header) if header.isdigit() else after
    return StreamingResponse(
        _event_stream(repository, run_id, last),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(
    repository: Repository,
    run_id: str,
    after: int,
) -> AsyncIterator[str]:
    """Yield stored events, polling the table until run_finished arrives."""
    last = after
    while True:
        for event in repository.list_events_after(run_id, last):
            last = event.seq
            yield sse_frame(event)
            if event.kind == TERMINAL_EVENT:
                return
        await asyncio.sleep(SSE_POLL_SECONDS)


def sse_frame(event: Event) -> str:
    """Format one stored event as an SSE frame with id, event and JSON data."""
    data = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {data}\n\n"
