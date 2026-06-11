from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from ruhu.capture.types import CaptureAuditRow

logger = logging.getLogger(__name__)


class AuditWriter(Protocol):
    def write(self, rows: list[CaptureAuditRow]) -> None: ...


class InMemoryAuditWriter:
    def __init__(self) -> None:
        self.rows: list[CaptureAuditRow] = []

    def write(self, rows: list[CaptureAuditRow]) -> None:
        self.rows.extend(rows)


class SqlAuditWriter:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def write(self, rows: list[CaptureAuditRow]) -> None:
        if not rows:
            return
        from ruhu.capture.sqlalchemy_models import CaptureAuditRecord

        with self._session_factory.begin() as session:
            for row in rows:
                raw_value = _stringify_raw(row.raw_value)
                raw_policy = _effective_raw_policy(row.audit_raw_policy)
                raw_value_hash = None
                raw_value_plaintext = None
                if raw_value is not None and raw_policy == "hash":
                    raw_value_hash = _hash_raw(raw_value)
                elif raw_value is not None and raw_policy == "plaintext":
                    raw_value_plaintext = raw_value
                session.add(
                    CaptureAuditRecord(
                        id=str(uuid4()),
                        conversation_id=row.conversation_id,
                        turn_id=row.turn_id,
                        step_id=row.step_id,
                        fact_name=row.fact_name,
                        storage_scope=row.storage_scope,
                        retention_policy=row.retention_policy,
                        sensitivity=row.sensitivity,
                        audit_raw_policy=row.audit_raw_policy,
                        raw_value_hash=raw_value_hash,
                        raw_value=raw_value_plaintext,
                        normalized_value=_jsonable(row.normalized_value),
                        source=row.source,
                        confidence=row.confidence,
                        evidence=row.evidence,
                        source_ref=row.source_ref,
                        outcome=row.outcome,
                        reason=row.reason,
                        replaced_previous=row.replaced_previous,
                        organization_id=row.organization_id,
                        created_at=datetime.now(timezone.utc),
                    )
                )


def _stringify_raw(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _hash_raw(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _effective_raw_policy(policy: str) -> str:
    if policy == "redact":
        return "redact"
    if policy == "plaintext_if_enabled" and os.getenv("RUHU_CAPTURE_AUDIT_PLAINTEXT_RAW", "").lower() in {"1", "true", "yes", "on"}:
        return "plaintext"
    return "hash"


def _jsonable(value: object | None):
    if value is None:
        return None
    try:
        json.dumps(value)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))
