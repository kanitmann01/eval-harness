"""Server-rendered spec editor: the spec list, the editor, save and duplicate.

The Check button is a normal form POST, so the editor works without JavaScript;
a small debounced script adds the live estimate and inline errors on top of the
same JSON validation route.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from evalharness.api.deps import get_repository, get_settings, render
from evalharness.api.spec_store import SpecStore, copy_name, spec_stem
from evalharness.api.spec_views import (
    SpecCheck,
    check_document,
    editor_credentials,
    list_rows,
)
from evalharness.core.config import Settings
from evalharness.errors import SpecError
from evalharness.storage.repository import Repository

router = APIRouter()


@router.get("/specs", response_class=HTMLResponse, name="spec_list")
def spec_list(
    request: Request,
    saved: str = "",
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Render every spec file with its cached or freshly checked outcome."""
    store = SpecStore(settings.specs_dir)
    rows = list_rows(store.list(), store, repository)
    return render(
        request,
        "specs.html",
        {"specs": rows, "saved": saved, "specs_dir": str(store.root)},
    )


@router.get("/specs/{name}/edit", response_class=HTMLResponse, name="spec_edit")
def spec_edit(
    request: Request,
    name: str,
    copied: str = "",
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Render the editor for one spec file, or a 404 page."""
    store = SpecStore(settings.specs_dir)
    stem = spec_stem(name)
    try:
        path = store.path_for(stem)
    except SpecError as exc:
        return _not_found(request, str(exc))
    if not path.is_file():
        return _not_found(request, f"spec {stem} does not exist")
    text = store.read(stem)
    check = check_document(text, repository)
    return render(
        request,
        "spec_editor.html",
        _editor_context(stem, text, check, copied, repository),
    )


@router.post("/specs/{name}/edit", response_class=HTMLResponse, name="spec_save")
async def spec_save(
    request: Request,
    name: str,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Check or save posted YAML; a save validates before it writes anything."""
    store = SpecStore(settings.specs_dir)
    stem = spec_stem(name)
    try:
        store.path_for(stem)
    except SpecError as exc:
        return _not_found(request, str(exc))
    form = await request.form()
    text = str(form.get("yaml", ""))
    action = str(form.get("action", "check"))
    check = check_document(text, repository)
    if action == "save" and check.valid:
        store.write(stem, text)
        return RedirectResponse(url=f"/specs?saved={stem}", status_code=303)
    return render(
        request,
        "spec_editor.html",
        _editor_context(stem, text, check, "", repository),
        status_code=400 if action == "save" else 200,
    )


@router.post(
    "/specs/{name}/duplicate",
    response_class=HTMLResponse,
    name="spec_duplicate",
)
def spec_duplicate(
    request: Request,
    name: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Copy a spec to the first free name and open the copy in the editor."""
    store = SpecStore(settings.specs_dir)
    stem = spec_stem(name)
    try:
        path = store.path_for(stem)
    except SpecError as exc:
        return _not_found(request, str(exc))
    if not path.is_file():
        return _not_found(request, f"spec {stem} does not exist")
    target = copy_name(store, stem)
    store.write(target, store.read(stem))
    return RedirectResponse(
        url=f"/specs/{target}/edit?copied={stem}",
        status_code=303,
    )


def _editor_context(
    name: str,
    text: str,
    check: SpecCheck,
    copied: str,
    repository: Repository,
) -> dict[str, Any]:
    """Build the template context for one editor render."""
    return {
        "name": name,
        "text": text,
        "check": check,
        "errors": check.errors,
        "warnings": check.warnings,
        "estimate": check.estimate,
        "copied": copied,
        "credentials": editor_credentials(repository),
    }


def _not_found(request: Request, message: str) -> HTMLResponse:
    """Render the console's 404 page."""
    return render(request, "not_found.html", {"message": message}, status_code=404)
