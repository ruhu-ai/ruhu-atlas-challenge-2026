from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ruhu.capture.types import FactCandidate
from ruhu.schemas import PendingFactUpdate

PENDING_FACTS_METADATA_KEY = "__ruhu_pending_facts__"

_AFFIRMATIVE = {
    "yes",
    "y",
    "yeah",
    "yep",
    "correct",
    "confirm",
    "confirmed",
    "that's correct",
    "that is correct",
    "ok",
    "okay",
}
_NEGATIVE = {
    "no",
    "n",
    "nope",
    "incorrect",
    "wrong",
    "reject",
    "cancel",
    "that's wrong",
    "that is wrong",
}


@dataclass(slots=True)
class PendingConfirmationResolution:
    candidates: list[FactCandidate]
    pending_items: list[dict[str, Any]]
    resolved: bool = False


def resolve_pending_confirmations(
    *,
    text: str,
    pending_items: list[Any],
    turn_id: str,
    now: datetime | None = None,
) -> PendingConfirmationResolution:
    now = now or datetime.now(timezone.utc)
    normalized = _normalize_text(text)
    candidates: list[FactCandidate] = []
    next_pending: list[dict[str, Any]] = []
    resolved = False

    parsed_items = [_parse_pending(item) for item in pending_items]
    for pending in parsed_items:
        if pending is None:
            continue
        if pending.status != "pending":
            next_pending.append(pending.model_dump())
            continue
        if _is_expired(pending, now):
            expired = pending.model_copy(update={"status": "expired"})
            next_pending.append(expired.model_dump())
            resolved = True
            continue
        if normalized in _AFFIRMATIVE:
            confirmed = pending.model_copy(update={"status": "confirmed"})
            next_pending.append(confirmed.model_dump())
            candidates.append(
                FactCandidate(
                    fact_name=pending.name,
                    raw_value=pending.raw_value if pending.raw_value is not None else pending.proposed_value,
                    source="user_confirmed",
                    evidence=f"pending:{pending.pending_id}",
                    confidence=1.0,
                    source_ref=pending.pending_id,
                )
            )
            resolved = True
            continue
        if normalized in _NEGATIVE:
            rejected = pending.model_copy(update={"status": "rejected"})
            next_pending.append(rejected.model_dump())
            resolved = True
            continue
        next_pending.append(pending.model_dump())
    return PendingConfirmationResolution(
        candidates=candidates,
        pending_items=next_pending,
        resolved=resolved,
    )


def _parse_pending(item: Any) -> PendingFactUpdate | None:
    try:
        return PendingFactUpdate.model_validate(item)
    except Exception:
        return None


def _is_expired(pending: PendingFactUpdate, now: datetime) -> bool:
    if not pending.expires_at:
        return False
    try:
        expires_at = datetime.fromisoformat(pending.expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().strip(".!?,").split())
