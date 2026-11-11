"""The stateless four step comparison wizard and the structured edit form.

The wizard keeps its draft as YAML text in a hidden form field. No session,
cookie or server-side draft exists; a step merges the fields it owns into the
draft and hands the whole draft to the next render. Only the last step writes a
file. The edit form reads one saved file, embeds its sha256 digest, and writes
only while the file on disk still matches that digest.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from evalharness.api.comparison_draft import (
    REMOVABLE_KINDS,
    default_draft,
    draft_text,
    merge_step,
    parse_action,
    parse_draft,
    remove_row,
)
from evalharness.api.comparison_rows import (
    WizardError,
    WizardPage,
    build_page,
    collect_edit_errors,
    collect_errors,
)
from evalharness.api.deps import get_repository, get_settings, render
from evalharness.api.routes.pages import _not_found
from evalharness.api.spec_store import SpecFile, SpecStore, spec_stem
from evalharness.api.spec_views import check_document, editor_credentials
from evalharness.core.config import Settings
from evalharness.demos.catalog import bundled_text, find_demo
from evalharness.errors import SpecError
from evalharness.storage.repository import Repository

router = APIRouter()
"""Every comparison route the console mounts."""


@router.get("/comparisons", response_class=HTMLResponse, name="comparison_index")
def comparison_index(saved: str = "") -> RedirectResponse:
    """Send the comparison list to the spec list, keeping the saved name."""
    suffix = f"?{urlencode({'saved': saved})}" if saved else ""
    return RedirectResponse(f"/specs{suffix}", status_code=303)


@router.get(
    "/comparisons/{name}/edit",
    response_class=HTMLResponse,
    name="comparison_edit",
)
def comparison_edit(
    request: Request,
    name: str,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Render every section of one saved spec on a single page."""
    stem = spec_stem(name)
    store = SpecStore(settings.specs_dir)
    try:
        path = store.path_for(stem)
    except SpecError:
        return _not_found(request, f"spec {stem} does not exist")
    if not path.is_file():
        return _not_found(request, f"spec {stem} does not exist")
    text = store.read(stem)
    document, _ = parse_draft(text)
    if document is None:
        return _not_found(request, f"spec {stem} is not readable as YAML")
    check = check_document(text, repository)
    page = build_page(1, document, text, check, editor_credentials(repository))
    return render(
        request,
        "comparisons_edit.html",
        _edit_context(page, stem, store.file(stem), _digest(text), conflict=False),
    )


@router.post(
    "/comparisons/{name}/edit",
    response_class=HTMLResponse,
    name="comparison_edit_submit",
)
async def comparison_edit_submit(
    request: Request,
    name: str,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Validate one posted page, write the spec only when it is safe, redirect."""
    stem = spec_stem(name)
    store = SpecStore(settings.specs_dir)
    try:
        path = store.path_for(stem)
    except SpecError:
        return _not_found(request, f"spec {stem} does not exist")
    if not path.is_file():
        return _not_found(request, f"spec {stem} does not exist")
    current = store.read(stem)
    document, _ = parse_draft(current)
    if document is None:
        return _not_found(request, f"spec {stem} is not readable as YAML")
    fields = await _form_fields(request)
    credentials = editor_credentials(repository)
    current_digest = _digest(current)
    if fields.get("digest", "") != current_digest:
        posted = dict(fields)
        posted["name"] = str(document.get("name", ""))
        merged = _merge_all(document, posted)
        merged_draft = draft_text(merged)
        check = check_document(merged_draft, repository)
        page = build_page(1, merged, merged_draft, check, credentials)
        return render(
            request,
            "comparisons_edit.html",
            _edit_context(
                page,
                stem,
                store.file(stem),
                fields.get("digest", ""),
                conflict=True,
            ),
            status_code=409,
        )
    posted = dict(fields)
    posted["name"] = str(document.get("name", ""))
    verb, kind, target_index = parse_action(posted.get("action", "save"))
    merged = _merge_all(document, posted)
    merged_draft = draft_text(merged)
    if verb == "add":
        check = check_document(merged_draft, repository)
        page = build_page(
            1,
            merged,
            merged_draft,
            check,
            credentials,
            extra_rows=(kind,),
        )
        return render(
            request,
            "comparisons_edit.html",
            _edit_context(page, stem, store.file(stem), current_digest, conflict=False),
        )
    if verb == "remove":
        if kind in REMOVABLE_KINDS:
            merged = remove_row(merged, kind, target_index)
            merged_draft = draft_text(merged)
        check = check_document(merged_draft, repository)
        page = build_page(1, merged, merged_draft, check, credentials)
        return render(
            request,
            "comparisons_edit.html",
            _edit_context(page, stem, store.file(stem), current_digest, conflict=False),
        )
    check = check_document(merged_draft, repository)
    errors, general = collect_edit_errors(check, merged, credentials, posted)
    if errors or general or not check.valid:
        page = build_page(
            1,
            merged,
            merged_draft,
            check,
            credentials,
            errors=errors,
            general_errors=general,
        )
        return render(
            request,
            "comparisons_edit.html",
            _edit_context(page, stem, store.file(stem), current_digest, conflict=False),
            status_code=400,
        )
    store.write(stem, merged_draft)
    if verb == "save-run":
        return RedirectResponse(f"/launch?spec={quote(stem)}", status_code=303)
    return RedirectResponse(f"/specs?saved={quote(stem)}", status_code=303)


@router.get("/comparisons/new", name="comparison_new")
def comparison_new() -> RedirectResponse:
    """Send the wizard entry point to the first step."""
    return RedirectResponse("/comparisons/new/1", status_code=303)


@router.get(
    "/comparisons/new/{step}",
    response_class=HTMLResponse,
    name="comparison_step",
)
def comparison_step(
    request: Request,
    step: str,
    repository: Repository = Depends(get_repository),
) -> Response:
    """Render one step fresh; a later step without a draft goes back to step 1."""
    index = _step_index(step)
    if index is None:
        return _not_found(request, f"step {step} is not a comparison wizard step")
    if index != 1:
        return RedirectResponse("/comparisons/new/1", status_code=303)
    credentials = editor_credentials(repository)
    document = default_draft()
    draft = draft_text(document)
    check = check_document(draft, repository)
    page = build_page(1, document, draft, check, credentials)
    return render(request, "comparisons_new.html", {"page": page})


@router.post(
    "/comparisons/new/{step}",
    response_class=HTMLResponse,
    name="comparison_submit",
)
async def comparison_submit(
    request: Request,
    step: str,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Merge this step's fields, validate the draft and write only at step 4."""
    index = _step_index(step)
    if index is None:
        return _not_found(request, f"step {step} is not a comparison wizard step")
    fields = await _form_fields(request)
    credentials = editor_credentials(repository)
    raw_draft = fields.get("draft", "")
    document, draft_error = parse_draft(raw_draft)
    if document is None and not raw_draft.strip():
        if index != 1:
            return RedirectResponse("/comparisons/new/1", status_code=303)
        document = default_draft()
    if document is None:
        document = default_draft()
    owner_text = fields.get("form_step", "")
    owner = (
        int(owner_text)
        if owner_text.isdigit() and 1 <= int(owner_text) <= 4
        else index
    )
    verb, kind, target_index = parse_action(fields.get("action", "continue"))
    if verb == "template":
        # A preset replaces the whole draft, so the operator starts from a
        # working example instead of a blank slate; step 1 re-renders with it.
        merged = _template_document(fields.get("template", "")) or default_draft()
        merged_draft = draft_text(merged)
        check = check_document(merged_draft, repository)
        page = build_page(1, merged, merged_draft, check, credentials)
        return render(request, "comparisons_new.html", {"page": page})
    merged = merge_step(document, owner, fields)
    merged_draft = draft_text(merged)
    if draft_error:
        check = check_document(merged_draft, repository)
        page = build_page(
            index,
            merged,
            merged_draft,
            check,
            credentials,
            general_errors=(_draft_error(draft_error),),
        )
        return render(request, "comparisons_new.html", {"page": page}, status_code=400)
    if verb == "add":
        check = check_document(merged_draft, repository)
        page = build_page(
            index,
            merged,
            merged_draft,
            check,
            credentials,
            extra_rows=(kind,),
        )
        return render(request, "comparisons_new.html", {"page": page})
    if verb == "remove":
        if kind in REMOVABLE_KINDS:
            merged = remove_row(merged, kind, target_index)
            merged_draft = draft_text(merged)
        check = check_document(merged_draft, repository)
        page = build_page(index, merged, merged_draft, check, credentials)
        return render(request, "comparisons_new.html", {"page": page})
    if verb in ("back", "goto"):
        target = (
            target_index
            if verb == "goto" and 1 <= target_index <= 4
            else index
        )
        check = check_document(merged_draft, repository)
        page = build_page(target, merged, merged_draft, check, credentials)
        return render(request, "comparisons_new.html", {"page": page})
    check = check_document(merged_draft, repository)
    errors, general = collect_errors(check, merged, owner, credentials, fields)
    if errors or general or not check.valid:
        page = build_page(
            index,
            merged,
            merged_draft,
            check,
            credentials,
            errors=errors,
            general_errors=general,
        )
        return render(request, "comparisons_new.html", {"page": page}, status_code=400)
    if index < 4:
        page = build_page(index + 1, merged, merged_draft, check, credentials)
        return render(request, "comparisons_new.html", {"page": page})
    stem = spec_stem(str(merged.get("name", "")))
    SpecStore(settings.specs_dir).write(stem, merged_draft)
    return RedirectResponse(
        f"/comparisons?{urlencode({'saved': stem})}",
        status_code=303,
    )


def _step_index(step: str) -> int | None:
    """Return the step number for a URL segment, or None when it is invalid."""
    number = int(step) if step.isdigit() else 0
    return number if 1 <= number <= 4 else None


def _template_document(template: str) -> dict[str, Any] | None:
    """Return a bundled demo spec as a draft document, or None when unknown."""
    scenario = find_demo(template.strip())
    if scenario is None:
        return None
    document, _error = parse_draft(bundled_text(scenario))
    return document


async def _form_fields(request: Request) -> dict[str, str]:
    """Parse a urlencoded form body without python-multipart."""
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def _draft_error(message: str) -> WizardError:
    """Return the general error shown when the hidden draft cannot be read."""
    return WizardError(
        "",
        "Draft",
        "",
        message,
        "start again from step 1 and retype the values",
    )


def _digest(text: str) -> str:
    """Return the sha256 hex digest of the exact text read from disk."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _merge_all(
    document: Mapping[str, Any],
    fields: Mapping[str, str],
) -> dict[str, Any]:
    """Apply every section's fields to one document."""
    merged = merge_step(document, 1, fields)
    for step in (2, 3, 4):
        merged = merge_step(merged, step, fields)
    return merged


def _edit_context(
    page: WizardPage,
    stem: str,
    file: SpecFile,
    digest: str,
    *,
    conflict: bool,
) -> dict[str, Any]:
    """Build the template context shared by the edit page's render paths."""
    return {
        "page": page,
        "stem": stem,
        "file": file,
        "digest": digest,
        "conflict": conflict,
    }
