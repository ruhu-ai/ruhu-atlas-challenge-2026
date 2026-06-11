from __future__ import annotations

from typing import Any, Callable, Iterable, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ruhu.capture.audit import InMemoryAuditWriter
from ruhu.capture.types import CaptureAuditRow


_CITED_OUTCOMES = frozenset({"accepted", "stored_audit_only"})


class Citation(BaseModel):
    """A grounded variable extraction: value + the utterance it came from."""

    fact_name: str
    value: Any | None = None
    raw_value: Any | None = None
    confidence: float | None = None
    source: str
    turn_id: str
    step_id: str | None = None
    transcript_span: tuple[int, int] | None = None
    source_utterance: str | None = None
    source_ref: str | None = None
    evidence: str | None = None
    replaced_previous: bool = False


class ConversationCitationsResponse(BaseModel):
    conversation_id: str
    citations: list[Citation] = Field(default_factory=list)


class CitationReader(Protocol):
    """Returns capture audit rows for a conversation."""

    def citations_for(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[CaptureAuditRow]: ...


class InMemoryCitationReader:
    """Reader backed by the in-memory audit writer used in tests and dev."""

    def __init__(self, writer: InMemoryAuditWriter) -> None:
        self._writer = writer

    def citations_for(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[CaptureAuditRow]:
        return [
            row
            for row in self._writer.rows
            if row.conversation_id == conversation_id
            and (organization_id is None or row.organization_id == organization_id)
        ]


class SqlCitationReader:
    """Reader backed by the SQL capture_audit table."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def citations_for(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[CaptureAuditRow]:
        from ruhu.capture.sqlalchemy_models import CaptureAuditRecord

        with self._session_factory() as session:
            statement = (
                select(CaptureAuditRecord)
                .where(CaptureAuditRecord.conversation_id == conversation_id)
                .order_by(CaptureAuditRecord.created_at.asc())
            )
            if organization_id is not None:
                statement = statement.where(CaptureAuditRecord.organization_id == organization_id)
            records = session.execute(statement).scalars().all()
        return [_record_to_row(record) for record in records]


def _record_to_row(record: Any) -> CaptureAuditRow:
    return CaptureAuditRow(
        conversation_id=record.conversation_id,
        turn_id=record.turn_id,
        step_id=record.step_id,
        fact_name=record.fact_name,
        source=record.source,
        outcome=record.outcome,
        reason=record.reason,
        raw_value=record.raw_value,
        normalized_value=record.normalized_value,
        confidence=float(record.confidence) if record.confidence is not None else None,
        evidence=record.evidence,
        source_ref=record.source_ref,
        storage_scope=record.storage_scope,
        retention_policy=record.retention_policy,
        sensitivity=record.sensitivity,
        audit_raw_policy=record.audit_raw_policy,
        replaced_previous=bool(record.replaced_previous),
        organization_id=record.organization_id,
        transcript_span=None,
    )


def build_citations(
    *,
    rows: Iterable[CaptureAuditRow],
    turn_text_lookup: Callable[[str], str | None] | None = None,
) -> list[Citation]:
    """Convert capture audit rows into citations with grounded source utterances.

    `turn_text_lookup` resolves a `turn_id` to the user's text for that turn,
    so the citation can carry the source utterance substring.
    """

    by_fact: dict[str, Citation] = {}
    for row in rows:
        if row.outcome not in _CITED_OUTCOMES:
            continue
        turn_text = turn_text_lookup(row.turn_id) if turn_text_lookup else None
        span = row.transcript_span
        if span is None and turn_text and isinstance(row.evidence, str) and row.evidence:
            span = _locate_evidence(turn_text, row.evidence)
        source_utterance = _extract_utterance(turn_text, span, row.evidence)
        citation = Citation(
            fact_name=row.fact_name,
            value=row.normalized_value,
            raw_value=row.raw_value,
            confidence=row.confidence,
            source=row.source,
            turn_id=row.turn_id,
            step_id=row.step_id,
            transcript_span=span,
            source_utterance=source_utterance,
            source_ref=row.source_ref,
            evidence=row.evidence,
            replaced_previous=row.replaced_previous,
        )
        # Latest write wins per fact (preserves conflict-resolution outcome).
        by_fact[row.fact_name] = citation
    return list(by_fact.values())


def _locate_evidence(text: str, evidence: str) -> tuple[int, int] | None:
    needle = evidence.strip()
    if not needle:
        return None
    start = text.find(needle)
    if start == -1:
        lowered_start = text.lower().find(needle.lower())
        if lowered_start == -1:
            return None
        start = lowered_start
    return (start, start + len(needle))


def _extract_utterance(
    text: str | None,
    span: tuple[int, int] | None,
    evidence: str | None,
) -> str | None:
    if text and span:
        start, end = span
        if 0 <= start <= end <= len(text):
            return text[start:end]
    if isinstance(evidence, str) and evidence:
        return evidence
    return None
