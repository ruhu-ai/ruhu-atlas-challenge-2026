"""WI-6.5 — LoRA registry resolver and lifecycle helpers.

Used by the runtime dispatcher to pick which LoRA (if any) to pass to
the classifier backend per turn, and by the training pipeline (when it
calls back into the runtime API) to register candidates and promote
winners.

Resolution order per
``docs/pre-fill-intent-classifier-design/05-training-pipeline.md`` and
WI-6.5:

1. (organization, agent_id, step_id, status="production") — per-step LoRA
2. (organization, agent_id, step_id IS NULL, status="production") — agent-wide
3. ``None`` — base model (no LoRA)

Lifecycle:

- ``register_candidate`` writes a row with ``status="candidate"`` —
  the eval harness picks it up next.
- ``promote_to_production`` flips the candidate to ``production`` and
  demotes the prior production for the same scope to ``shadow`` (kept
  loaded for instant rollback per spec).
- ``retire`` flips a row to ``retired`` (terminal).

The "at most one production per (organization, agent, step)" invariant
is enforced by ``promote_to_production`` rather than a partial unique
index — SQLite parity for tests.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db_models import ClassifierLoraRecord

LoraStatus = Literal["candidate", "production", "shadow", "retired"]

VALID_STATUSES: frozenset[str] = frozenset(
    {"candidate", "production", "shadow", "retired"}
)


@dataclass(slots=True, frozen=True)
class RegistryEntry:
    """Lightweight projection of a registry row for callers."""

    lora_id: str
    organization_id: str | None
    agent_id: str
    step_id: str | None
    lora_name: str
    model_uri: str
    version: str
    status: LoraStatus
    published_at: datetime | None


# ── resolver ───────────────────────────────────────────────────────────────


def resolve_lora(
    session: Session,
    *,
    agent_id: str,
    step_id: str | None,
    organization_id: str | None = None,
) -> str | None:
    """Return the ``lora_name`` to pass to the classifier backend.

    Per-step takes precedence over per-agent. When ``step_id`` is None
    the per-step lookup is skipped. Returns ``None`` when no production
    LoRA exists for the scope (caller falls back to the base model).
    """
    if step_id is not None:
        row = _select_production(
            session,
            organization_id=organization_id,
            agent_id=agent_id,
            step_id=step_id,
        )
        if row is not None:
            return row.lora_name

    row = _select_production(
        session,
        organization_id=organization_id,
        agent_id=agent_id,
        step_id=None,
    )
    if row is not None:
        return row.lora_name
    return None


def _select_production(
    session: Session,
    *,
    organization_id: str | None,
    agent_id: str,
    step_id: str | None,
) -> ClassifierLoraRecord | None:
    query = select(ClassifierLoraRecord).where(
        ClassifierLoraRecord.agent_id == agent_id,
        ClassifierLoraRecord.status == "production",
    )
    if organization_id is None:
        query = query.where(ClassifierLoraRecord.organization_id.is_(None))
    else:
        query = query.where(ClassifierLoraRecord.organization_id == organization_id)
    if step_id is None:
        query = query.where(ClassifierLoraRecord.step_id.is_(None))
    else:
        query = query.where(ClassifierLoraRecord.step_id == step_id)
    query = query.order_by(ClassifierLoraRecord.published_at.desc())
    return session.execute(query).scalars().first()


# ── lifecycle ──────────────────────────────────────────────────────────────


def register_candidate(
    session: Session,
    *,
    agent_id: str,
    lora_name: str,
    model_uri: str,
    version: str,
    step_id: str | None = None,
    organization_id: str | None = None,
    eval_score: dict | None = None,
    now: datetime | None = None,
) -> ClassifierLoraRecord:
    """Insert a new ``status="candidate"`` row. The eval harness scores it next."""
    timestamp = now or datetime.now(timezone.utc)
    record = ClassifierLoraRecord(
        lora_id=str(uuid.uuid4()),
        organization_id=organization_id,
        agent_id=agent_id,
        step_id=step_id,
        lora_name=lora_name,
        model_uri=model_uri,
        version=version,
        status="candidate",
        eval_score_json=eval_score,
        created_at=timestamp,
        updated_at=timestamp,
        published_at=None,
    )
    session.add(record)
    session.flush()
    return record


def promote_to_production(
    session: Session,
    *,
    lora_id: str,
    now: datetime | None = None,
) -> ClassifierLoraRecord:
    """Flip a candidate to production; demote prior production for same scope to shadow.

    Spec §Promotion gate: "On pass: status = production. The previous
    production LoRA is demoted to status = shadow (kept loaded for
    instant rollback)."
    """
    record = session.get(ClassifierLoraRecord, lora_id)
    if record is None:
        raise ValueError(f"unknown lora_id: {lora_id!r}")
    if record.status == "retired":
        raise ValueError(f"cannot promote retired lora: {lora_id!r}")

    timestamp = now or datetime.now(timezone.utc)

    prior = _select_production(
        session,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        step_id=record.step_id,
    )
    if prior is not None and prior.lora_id != record.lora_id:
        prior.status = "shadow"
        prior.updated_at = timestamp

    record.status = "production"
    record.updated_at = timestamp
    if record.published_at is None:
        record.published_at = timestamp
    session.flush()
    return record


def retire(
    session: Session,
    *,
    lora_id: str,
    now: datetime | None = None,
) -> ClassifierLoraRecord:
    """Mark a row as ``retired``. Terminal — caller must not promote afterwards."""
    record = session.get(ClassifierLoraRecord, lora_id)
    if record is None:
        raise ValueError(f"unknown lora_id: {lora_id!r}")
    record.status = "retired"
    record.updated_at = now or datetime.now(timezone.utc)
    session.flush()
    return record


# ── lightweight projection (for API responses) ─────────────────────────────


def to_entry(record: ClassifierLoraRecord) -> RegistryEntry:
    return RegistryEntry(
        lora_id=record.lora_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        step_id=record.step_id,
        lora_name=record.lora_name,
        model_uri=record.model_uri,
        version=record.version,
        status=record.status,  # type: ignore[arg-type]
        published_at=record.published_at,
    )


__all__ = [
    "RegistryEntry",
    "VALID_STATUSES",
    "promote_to_production",
    "register_candidate",
    "resolve_lora",
    "retire",
    "to_entry",
]
