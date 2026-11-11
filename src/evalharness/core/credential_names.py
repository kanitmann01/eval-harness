"""The one rule for a credential name the harness does not know.

The spec validator, the CLI, the vault service, the storage mapper and the HTTP
API all reach the same failure: the caller named a credential that is not
declared in the spec or not stored in the vault. They raise this rule's error so
the field, the value and the fix read the same whichever surface asked, and so
no surface can quote a secret while doing it.
"""

from __future__ import annotations

from typing import Iterable

from evalharness.errors import SpecError

UNKNOWN_CREDENTIAL_FIX = (
    "add it with 'evalh credentials add', or list the stored credentials with "
    "'evalh credentials list'"
)
"""The one fix sentence every unknown-credential error carries."""


def unknown_credential(
    name: str,
    *,
    field: str | None = None,
    available: Iterable[str] | None = None,
) -> SpecError:
    """Return the actionable error for a credential name that is not known.

    Parameters:
        name: the credential the caller asked for, named by field and value and
            never accompanied by its secret.
        field: the spec field to blame; defaults to credentials.<name> for a
            request that names a stored credential directly.
        available: the names that would have been accepted, listed in the fix
            when given so the caller can retry without guessing.
    """
    fix = UNKNOWN_CREDENTIAL_FIX
    if available is not None:
        choices = ", ".join(sorted(available)) or "none declared"
        fix = f"use one of: {choices}, or {fix}"
    return SpecError(
        field or f"credentials.{name}",
        name,
        "unknown credential",
        fix,
    )
