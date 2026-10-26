"""The stateless four step comparison wizard.

The draft is YAML text in a hidden form field. No session, cookie or server-side
draft exists; a step merges the fields it owns into the draft and hands the
whole draft to the next render. Only the last step writes a file.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from evalharness.api.comparisons_views import (
    REMOVABLE_KINDS,
    WizardError,
    build_page,
    collect_errors,
    default_draft,
    draft_text,
    merge_step,
    parse_action,
    parse_draft,
    remove_row,
)
from evalharness.api.deps import get_repository, get_settings, render
from evalharness.api.routes.pages import _not_found
from evalharness.api.spec_store import SpecStore, spec_stem
from evalharness.api.spec_views import check_document, editor_credentials
from evalharness.core.config import Settings
from evalharness.storage.repository import Repository

router = APIRouter()
"""Every route the comparison wizard mounts."""


@router.get("/comparisons", response_class=HTMLResponse, name="comparison_index")
def comparison_index(saved: str = "") -> RedirectResponse:
    """Send the comparison list to the spec list, keeping the saved name."""
    suffix = f"?{urlencode({'saved': saved})}" if saved else ""
    return RedirectResponse(f"/specs{suffix}", status_code=303)


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
