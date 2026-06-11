"""Transcript-span propagation through the capture pipeline → audit → citations."""

from __future__ import annotations

from ruhu.agent_document import AgentDocument, Scenario, Step, compile_agent_document
from ruhu.capture import FactPipeline
from ruhu.capture.audit import InMemoryAuditWriter
from ruhu.capture.deterministic import DeterministicFactExtractor
from ruhu.capture.llm_extractor import FieldExtractorLLM, LLMFactExtractor
from ruhu.capture.safety import SafetyGuard
from ruhu.capture.validators import build_default_validator_registry
from ruhu.citations import (
    InMemoryCitationReader,
    build_citations,
)
from ruhu.schemas import FactDef


def _pipeline(
    audit: InMemoryAuditWriter | None = None,
    llm: FieldExtractorLLM | None = None,
) -> FactPipeline:
    return FactPipeline(
        deterministic=DeterministicFactExtractor(),
        llm=LLMFactExtractor(llm),
        validators=build_default_validator_registry(),
        guard=SafetyGuard(),
        audit_writer=audit or InMemoryAuditWriter(),
    )


def _contact_document() -> tuple[object, Step]:
    step = Step(
        id="collect_contact",
        name="Contact",
        fact_requirements=[
            {"name": "email"},
            {"name": "phone_number"},
        ],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(name="email", type="email"),
            FactDef(name="phone_number", type="phone"),
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    return compile_agent_document(doc), step


def test_deterministic_extractor_records_email_span() -> None:
    compiled, step = _contact_document()
    audit = InMemoryAuditWriter()
    text = "Please reach me at jane@example.com about the application."
    _pipeline(audit).extract(
        text=text,
        turn_id="turn-1",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    rows = [row for row in audit.rows if row.fact_name == "email"]
    assert rows, "expected an email audit row"
    row = rows[0]
    assert row.transcript_span is not None
    start, end = row.transcript_span
    assert text[start:end] == "jane@example.com"


def test_deterministic_extractor_records_phone_span() -> None:
    compiled, step = _contact_document()
    audit = InMemoryAuditWriter()
    text = "Hi, my number is +234 802 555 0101 if you need to call back."
    _pipeline(audit).extract(
        text=text,
        turn_id="turn-2",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    rows = [row for row in audit.rows if row.fact_name == "phone_number"]
    assert rows, "expected a phone audit row"
    row = rows[0]
    assert row.transcript_span is not None
    start, end = row.transcript_span
    # The deterministic phone regex captures the +234... prefix substring.
    assert "+234" in text[start:end]


class _FixedLLM:
    """Deterministic LLM stub that returns a known value for one field."""

    def extract(self, *, text: str, fields: list[str], hints: dict[str, str]) -> dict[str, str | None]:
        result: dict[str, str | None] = {field: None for field in fields}
        if "appointment_reason" in fields and "onboarding" in text.lower():
            result["appointment_reason"] = "onboarding"
        return result


def test_llm_extractor_locates_span_in_source_text() -> None:
    # Two missing facts disables the deterministic single-fact generic fallback,
    # so the LLM extractor is the one to produce the appointment_reason candidate.
    step = Step(
        id="collect_reason",
        name="Reason",
        fact_requirements=[
            {"name": "appointment_reason"},
            {"name": "appointment_date"},
        ],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(
                name="appointment_reason",
                type="string",
                allowed_sources=["llm_proposed", "deterministic", "classifier", "user_confirmed", "system"],
            ),
            FactDef(
                name="appointment_date",
                type="datetime",
                allowed_sources=["llm_proposed", "deterministic", "classifier", "user_confirmed", "system"],
            ),
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    compiled = compile_agent_document(doc)
    audit = InMemoryAuditWriter()
    text = "I'd like an appointment for onboarding next week."
    _pipeline(audit, _FixedLLM()).extract(
        text=text,
        turn_id="turn-3",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    rows = [
        row for row in audit.rows
        if row.fact_name == "appointment_reason" and row.source == "llm_proposed"
    ]
    assert rows, "expected an llm-sourced appointment_reason audit row"
    row = rows[0]
    assert row.transcript_span is not None
    start, end = row.transcript_span
    assert text[start:end] == "onboarding"


def test_classifier_slot_span_passed_through() -> None:
    step = Step(
        id="collect_reason",
        name="Reason",
        fact_requirements=[{"name": "appointment_reason"}],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="appointment_reason", type="string")],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    compiled = compile_agent_document(doc)
    audit = InMemoryAuditWriter()
    text = "checking in for onboarding tomorrow"
    _pipeline(audit).extract(
        text=text,
        turn_id="turn-4",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots={
            "appointment_reason": {
                "raw_value": "onboarding",
                "confidence": 0.9,
                "transcript_span": (16, 26),
            },
        },
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    rows = [row for row in audit.rows if row.fact_name == "appointment_reason"]
    assert rows, "expected an appointment_reason audit row"
    row = rows[0]
    assert row.transcript_span == (16, 26)


def test_classifier_slot_span_derived_from_evidence_when_absent() -> None:
    step = Step(
        id="collect_reason",
        name="Reason",
        fact_requirements=[{"name": "appointment_reason"}],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="appointment_reason", type="string")],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    compiled = compile_agent_document(doc)
    audit = InMemoryAuditWriter()
    text = "checking in for onboarding tomorrow"
    _pipeline(audit).extract(
        text=text,
        turn_id="turn-5",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots={
            "appointment_reason": {
                "raw_value": "onboarding",
                "confidence": 0.9,
                "evidence": "onboarding",
            },
        },
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    rows = [row for row in audit.rows if row.fact_name == "appointment_reason"]
    assert rows[0].transcript_span == (16, 26)


def test_build_citations_returns_grounded_utterance() -> None:
    compiled, step = _contact_document()
    audit = InMemoryAuditWriter()
    text = "Please reach me at jane@example.com about the application."
    _pipeline(audit).extract(
        text=text,
        turn_id="turn-7",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-7",
        organization_id="org-7",
    )

    reader = InMemoryCitationReader(audit)
    citations = build_citations(
        rows=reader.citations_for("conversation-7", organization_id="org-7"),
        turn_text_lookup=lambda turn_id: text if turn_id == "turn-7" else None,
    )
    by_name = {citation.fact_name: citation for citation in citations}
    email_citation = by_name["email"]
    assert email_citation.source_utterance == "jane@example.com"
    assert email_citation.transcript_span is not None
    assert email_citation.confidence == 1.0
    assert email_citation.source == "deterministic"


def test_build_citations_skips_rejected_outcomes() -> None:
    """Rows for safety/validation rejections should never become citations."""

    compiled, step = _contact_document()
    audit = InMemoryAuditWriter()
    # Plain text with no email/phone — no candidates accepted, but audit rows
    # for unmatched candidates won't exist either. Inject a synthetic rejected
    # row to confirm filtering logic.
    from ruhu.capture.types import CaptureAuditRow

    audit.rows.append(
        CaptureAuditRow(
            conversation_id="conversation-8",
            turn_id="turn-8",
            step_id=step.id,
            fact_name="email",
            source="llm_proposed",
            outcome="rejected_safety",
            reason="tenant_deny_pattern",
            raw_value="bad@example.com",
            normalized_value=None,
            confidence=0.7,
            evidence="bad@example.com",
            source_ref=None,
            storage_scope="conversation",
            retention_policy="conversation",
            sensitivity="personal",
            audit_raw_policy="hash",
            replaced_previous=False,
            organization_id="org-8",
            transcript_span=None,
        )
    )

    reader = InMemoryCitationReader(audit)
    citations = build_citations(
        rows=reader.citations_for("conversation-8"),
        turn_text_lookup=lambda turn_id: "filler text",
    )
    assert citations == []
    # Reference compiled so the document is exercised.
    assert compiled is not None
