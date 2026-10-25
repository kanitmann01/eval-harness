"""Judge declarations: configuration hashing and per-cell usage sessions."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

from evalharness.core.context import CredentialStore
from evalharness.core.models import JudgeSession, JudgeSpec, Price
from evalharness.core.protocols import Judge

__all__ = ["build_judge_sessions", "judge_config_hash"]


def judge_config_hash(judge: JudgeSpec) -> str:
    """Return the sha256 of one judge's declared configuration.

    The declaration is canonicalised as JSON with sorted mapping keys: the
    adapter path, the pricing model and the options mapping, which carries
    temperature and instructions. The judge's name and credential are left
    out because they select a declaration rather than change its behaviour,
    so two judges with the same configuration hash the same.
    """
    canonical = {
        "adapter": judge.adapter,
        "model": judge.model or "",
        "options": judge.options,
    }
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_judge_sessions(
    judge_specs: Iterable[JudgeSpec],
    judges: Mapping[str, Judge],
    prices: Mapping[str, Price],
    *,
    providers: Mapping[str, str] | None = None,
    credentials: CredentialStore | None = None,
) -> dict[str, JudgeSession]:
    """Build one fresh session per declared judge for a single cell.

    The adapter instance is shared; the session is not, so counters and cost
    stay attributed to the cell that triggered them even under concurrency.
    The session carries the judge's resolved credential handle, its pricing
    provider and its price, which `prices` maps by judge name because two
    declarations may use the same model at two different providers.

    A deterministic judge declares no credential, so `credential` stays None
    and the judge keeps working exactly as before.
    """
    providers_by_name = providers or {}
    sessions: dict[str, JudgeSession] = {}
    for spec in judge_specs:
        credential = (
            credentials.get(spec.credential)
            if credentials is not None and spec.credential is not None
            else None
        )
        sessions[spec.name] = JudgeSession(
            name=spec.name,
            judge=judges[spec.name],
            model=spec.model or "",
            provider=providers_by_name.get(spec.name, ""),
            config_hash=judge_config_hash(spec),
            price=prices.get(spec.name),
            credential=credential,
        )
    return sessions
