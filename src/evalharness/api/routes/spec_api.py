"""JSON API for the spec editor: list, read, validate, write and duplicate.

Validation calls the same loader as `evalh validate`, so a rejected field reads
the same here as it does on the command line. A write validates first and then
replaces the file atomically; a document that fails validation is never written.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from evalharness.api.deps import get_repository, get_settings
from evalharness.api.spec_store import SpecStore, copy_name
from evalharness.api.spec_views import (
    check_data,
    check_document,
    error_body,
    file_data,
    row_data,
    row_for,
)
from evalharness.core.config import Settings
from evalharness.errors import SpecError
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get("/api/specs", name="api_specs")
def list_specs(
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Return every spec file with its metadata and current validation state."""
    store = SpecStore(settings.specs_dir)
    return [row_data(row_for(file, store, repository)) for file in store.list()]


@router.post("/api/specs/validate", name="api_specs_validate")
async def validate_spec(
    request: Request,
    repository: Repository = Depends(get_repository),
) -> JSONResponse:
    """Validate posted YAML text and return its cells, cost and field errors."""
    try:
        text = await yaml_body(request)
    except SpecError as exc:
        return JSONResponse(status_code=400, content=error_body(exc))
    check = check_document(text, repository)
    return JSONResponse(
        status_code=200 if check.valid else 400,
        content=check_data(check),
    )


@router.post("/api/specs/{name}/duplicate", status_code=201, name="api_spec_duplicate")
def duplicate_spec(
    name: str,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Copy an existing spec to the first free `<name>-copy` name."""
    store = SpecStore(settings.specs_dir)
    try:
        path = store.path_for(name)
    except SpecError as exc:
        return JSONResponse(status_code=400, content=error_body(exc))
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"spec {name} does not exist")
    target = copy_name(store, name)
    written = store.write(target, store.read(name))
    return JSONResponse(
        status_code=201,
        content={
            "name": written.stem,
            "path": str(written),
            "duplicate_of": name,
        },
    )


@router.get("/api/specs/{name:path}", name="api_spec")
def get_spec(
    name: str,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Return one spec file's raw YAML plus its parsed summary and estimate."""
    store = SpecStore(settings.specs_dir)
    try:
        path = store.path_for(name)
    except SpecError as exc:
        return JSONResponse(status_code=400, content=error_body(exc))
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"spec {name} does not exist")
    text = store.read(name)
    return JSONResponse(
        content=file_data(
            store.file(name),
            text,
            check_document(text, repository),
        )
    )


@router.put("/api/specs/{name:path}", name="api_spec_write")
async def put_spec(
    name: str,
    request: Request,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Validate posted YAML text and write it; an invalid spec is never written."""
    store = SpecStore(settings.specs_dir)
    try:
        path = store.path_for(name)
    except SpecError as exc:
        return JSONResponse(status_code=400, content=error_body(exc))
    try:
        text = await yaml_body(request)
    except SpecError as exc:
        return JSONResponse(status_code=400, content=error_body(exc))
    check = check_document(text, repository)
    if not check.valid:
        return JSONResponse(status_code=400, content=check_data(check))
    created = not path.is_file()
    written = store.write(name, text)
    payload = check_data(check)
    payload.update(
        {"name": written.stem, "path": str(written), "created": created}
    )
    return JSONResponse(content=payload)


async def yaml_body(request: Request) -> str:
    """Read YAML text from a JSON envelope or from the raw request body."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type.endswith("json"):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise SpecError(
                "yaml",
                "<body>",
                f"the body is not valid JSON: {exc}",
                'send the YAML text as a JSON string under "yaml"',
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("yaml"), str):
            raise SpecError(
                "yaml",
                "<body>",
                "the body must be a JSON object with a yaml string",
                'send {"yaml": "<spec text>"} or a raw text body',
            )
        return str(payload["yaml"])
    return (await request.body()).decode("utf-8", errors="replace")
