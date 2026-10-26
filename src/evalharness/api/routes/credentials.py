"""Credentials console: the page, its no-JS forms and the JSON API.

The page never receives a secret back: add returns a redirect naming only the
credential, test returns a redirect whose message is read from the redacted
last_error column, and every JSON row is metadata plus a fingerprint prefix.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

from evalharness.api.credential_views import (
    credential_rows,
    credential_stats,
    error_data,
    provider_options,
    read_input,
    row_data,
    row_for,
    unknown_credential,
)
from evalharness.api.deps import get_repository, get_settings, render
from evalharness.core.config import Settings
from evalharness.core.vault import MasterKeyError, Vault, open_vault
from evalharness.credential_service import (
    add_credential,
    delete_credential,
    set_enabled,
    verify_credential,
)
from evalharness.errors import SpecError
from evalharness.storage.repository import Repository

router = APIRouter()

CREDENTIAL_FORM_FIX = (
    "send {name, provider, secret} as JSON or use the form at /credentials"
)
"""What an unreadable add request says to do instead."""


@router.get("/credentials", response_class=HTMLResponse, name="credential_list")
def credential_list(
    request: Request,
    added: str = "",
    tested: str = "",
    ok: str = "",
    changed: str = "",
    state: str = "",
    deleted: str = "",
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Render every stored credential, masked, with its state and actions."""
    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _vault_page(request, exc)
    return _render_page(
        request,
        repository,
        vault,
        settings,
        added=added,
        tested=tested,
        test_ok=ok == "1",
        changed=changed,
        changed_state=state,
        deleted=deleted,
    )


@router.post("/credentials", response_class=HTMLResponse, name="credential_add")
async def credential_add(
    request: Request,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Add a credential from the form and redirect, never echoing the secret."""
    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _vault_page(request, exc)
    try:
        values = read_input(dict(await request.form()))
    except SpecError as exc:
        # The secret is never part of a validation value, so the page can
        # render the error and the existing rows without a leak.
        return _render_page(
            request,
            repository,
            vault,
            settings,
            error=exc,
            status_code=400,
        )
    existing = repository.get_credential(values.name)
    if existing is not None:
        return _render_page(
            request,
            repository,
            vault,
            settings,
            error=_duplicate(values.name),
            status_code=409,
        )
    record = add_credential(
        repository, vault, values.name, values.provider, values.secret
    )
    return RedirectResponse(
        url=f"/credentials?added={quote(record.name)}", status_code=303
    )


@router.post(
    "/credentials/{name}/test",
    response_class=HTMLResponse,
    name="credential_test",
)
async def credential_test(
    name: str,
    request: Request,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Test one credential from the form and redirect with only its outcome."""
    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _vault_page(request, exc)
    if repository.get_credential(name) is None:
        return _not_found(request, name)
    try:
        _, result = await verify_credential(
            repository, vault, name, settings.verify_url or ""
        )
    except SpecError as exc:
        return _render_page(
            request,
            repository,
            vault,
            settings,
            error=exc,
            status_code=400,
        )
    return RedirectResponse(
        url=f"/credentials?tested={quote(name)}&ok={1 if result.ok else 0}",
        status_code=303,
    )


@router.post(
    "/credentials/{name}/disable",
    response_class=HTMLResponse,
    name="credential_disable",
)
def credential_disable(
    name: str,
    request: Request,
    repository: Repository = Depends(get_repository),
) -> Response:
    """Disable one credential and redirect to the masked list."""
    if repository.get_credential(name) is None:
        return _not_found(request, name)
    set_enabled(repository, name, enabled=False)
    return RedirectResponse(
        url=f"/credentials?changed={quote(name)}&state=disabled", status_code=303
    )


@router.post(
    "/credentials/{name}/enable",
    response_class=HTMLResponse,
    name="credential_enable",
)
def credential_enable(
    name: str,
    request: Request,
    repository: Repository = Depends(get_repository),
) -> Response:
    """Enable one credential and redirect to the masked list."""
    if repository.get_credential(name) is None:
        return _not_found(request, name)
    set_enabled(repository, name, enabled=True)
    return RedirectResponse(
        url=f"/credentials?changed={quote(name)}&state=enabled", status_code=303
    )


@router.post(
    "/credentials/{name}/delete",
    response_class=HTMLResponse,
    name="credential_delete",
)
def credential_delete(
    name: str,
    request: Request,
    repository: Repository = Depends(get_repository),
) -> Response:
    """Delete one credential and redirect to the masked list."""
    if repository.get_credential(name) is None:
        return _not_found(request, name)
    delete_credential(repository, name)
    return RedirectResponse(
        url=f"/credentials?deleted={quote(name)}", status_code=303
    )


@router.get("/api/credentials", name="api_credentials")
def api_credential_list(
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Return every stored credential as metadata and a fingerprint prefix."""
    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _json_error(exc, 503)
    return JSONResponse(
        content=[row_data(row) for row in credential_rows(repository, vault)]
    )


@router.post(
    "/api/credentials",
    status_code=201,
    name="api_credentials_add",
)
async def api_credential_add(
    request: Request,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Store one credential from a JSON body and return metadata only."""
    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _json_error(exc, 503)
    try:
        values = read_input(await _json_object(request))
    except SpecError as exc:
        return _json_error(exc, 400)
    if repository.get_credential(values.name) is not None:
        return _json_error(_duplicate(values.name), 409)
    record = add_credential(
        repository, vault, values.name, values.provider, values.secret
    )
    return JSONResponse(
        status_code=201,
        content=row_data(row_for(record, repository, vault)),
    )


@router.post(
    "/api/credentials/{name}/test",
    name="api_credential_test",
)
async def api_credential_test(
    name: str,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Make one provider request and return the redacted verdict and message."""
    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _json_error(exc, 503)
    if repository.get_credential(name) is None:
        return _json_error(unknown_credential(name), 404)
    try:
        record, result = await verify_credential(
            repository, vault, name, settings.verify_url or ""
        )
    except SpecError as exc:
        return _json_error(exc, 400)
    return JSONResponse(
        content={
            "verified": result.ok,
            "message": result.message,
            "credential": row_data(row_for(record, repository, vault)),
        }
    )


@router.post(
    "/api/credentials/{name}/disable",
    name="api_credential_disable",
)
def api_credential_disable(
    name: str,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Disable one credential so the editor no longer offers it."""
    return _set_enabled(name, repository, settings, enabled=False)


@router.post(
    "/api/credentials/{name}/enable",
    name="api_credential_enable",
)
def api_credential_enable(
    name: str,
    repository: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Enable one credential again after a fix."""
    return _set_enabled(name, repository, settings, enabled=True)


@router.delete(
    "/api/credentials/{name}",
    name="api_credential_delete",
)
def api_credential_delete(
    name: str,
    repository: Repository = Depends(get_repository),
) -> Response:
    """Delete one credential and its ciphertext."""
    if repository.get_credential(name) is None:
        return _json_error(unknown_credential(name), 404)
    delete_credential(repository, name)
    return JSONResponse(content={"deleted": name})


def _set_enabled(
    name: str,
    repository: Repository,
    settings: Settings,
    *,
    enabled: bool,
) -> Response:
    """Enable or disable one credential and return its masked JSON row."""
    try:
        vault = open_vault(settings)
    except MasterKeyError as exc:
        return _json_error(exc, 503)
    if repository.get_credential(name) is None:
        return _json_error(unknown_credential(name), 404)
    record = set_enabled(repository, name, enabled=enabled)
    return JSONResponse(content=row_data(row_for(record, repository, vault)))


async def _json_object(request: Request) -> dict[str, Any]:
    """Read a JSON object, never echoing the body when it does not parse."""
    try:
        payload = await request.json()
    except ValueError as exc:
        raise SpecError(
            "body",
            "<body>",
            "the request body is not valid JSON",
            CREDENTIAL_FORM_FIX,
        ) from exc
    if not isinstance(payload, dict):
        raise SpecError(
            "body",
            "<body>",
            "the request body must be a JSON object",
            CREDENTIAL_FORM_FIX,
        )
    return payload


def _render_page(
    request: Request,
    repository: Repository,
    vault: Vault,
    settings: Settings,
    *,
    added: str = "",
    tested: str = "",
    test_ok: bool = False,
    changed: str = "",
    changed_state: str = "",
    deleted: str = "",
    error: SpecError | None = None,
    status_code: int = 200,
) -> Response:
    """Render the credentials page from one vault and the stored metadata."""
    rows = credential_rows(repository, vault)
    return render(
        request,
        "credentials.html",
        {
            "rows": rows,
            "stats": credential_stats(rows),
            "providers": provider_options(),
            "verify_url": settings.verify_url or "",
            "added": added,
            "tested": tested,
            "test_ok": test_ok,
            "changed": changed,
            "changed_state": changed_state,
            "deleted": deleted,
            "error": error,
        },
        status_code=status_code,
    )


def _vault_page(request: Request, error: MasterKeyError) -> Response:
    """Render the vault error page; a generated key is never displayed."""
    return render(
        request,
        "credential_error.html",
        {"error": error},
        status_code=503,
    )


def _json_error(error: SpecError, status_code: int) -> JSONResponse:
    """Return one field error as JSON, with a redacted value."""
    return JSONResponse(
        status_code=status_code,
        content={"errors": [error_data(error)]},
    )


def _duplicate(name: str) -> SpecError:
    """Return the actionable error for an existing credential name."""
    return SpecError(
        f"credentials.{name}",
        name,
        "a credential with this name already exists",
        "delete it first or choose another name",
    )


def _not_found(request: Request, name: str) -> Response:
    """Render the console's 404 page for an unknown credential."""
    return render(
        request,
        "not_found.html",
        {"message": f"credential {name} does not exist"},
        status_code=404,
    )