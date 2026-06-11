"""Unit tests for the end-of-conversation analysis sweep."""

from __future__ import annotations

import pytest

from ruhu.agent_document import (
    AgentDocument,
    AnalysisVariableDef,
    Scenario,
    Step,
    compile_agent_document,
)
from ruhu.analysis_sweep import (
    TurnTranscript,
    run_analysis_sweep,
)
from ruhu.capture import FactPipeline
from ruhu.capture.audit import InMemoryAuditWriter
from ruhu.capture.deterministic import DeterministicFactExtractor
from ruhu.capture.llm_extractor import FieldExtractorLLM, LLMFactExtractor
from ruhu.capture.safety import SafetyGuard
from ruhu.capture.validators import build_default_validator_registry
from ruhu.citations import InMemoryCitationReader, build_citations


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


def _agent_document_with_schema(
    analysis_schema: list[AnalysisVariableDef],
) -> object:
    step = Step(id="start", name="Start")
    doc = AgentDocument(
        start_scenario_id="main",
        scenarios=[Scenario(id="main", name="Main", start_step_id="start", steps=[step])],
        analysis_schema=analysis_schema,
    )
    return compile_agent_document(doc)


def test_sweep_returns_zero_when_schema_is_empty() -> None:
    compiled = _agent_document_with_schema([])
    result = run_analysis_sweep(
        conversation_id="c-1",
        organization_id="org-1",
        agent_document=compiled,
        transcripts=[TurnTranscript("turn-1", "anything")],
        existing_facts={},
        existing_fact_metadata={},
        fact_pipeline=_pipeline(),
    )
    assert result.variables_total == 0
    assert result.variables_filled == []
    assert result.variables_unfilled == []


def test_sweep_skips_variables_already_present_in_facts() -> None:
    compiled = _agent_document_with_schema(
        [
            AnalysisVariableDef(name="email", type="string", description="email"),
            AnalysisVariableDef(name="topic", type="string", description="topic"),
        ]
    )
    audit = InMemoryAuditWriter()
    result = run_analysis_sweep(
        conversation_id="c-2",
        organization_id="org-2",
        agent_document=compiled,
        transcripts=[TurnTranscript("turn-1", "Just checking in")],
        existing_facts={"email": "x@example.com"},
        existing_fact_metadata={},
        fact_pipeline=_pipeline(audit),
    )
    assert result.variables_total == 2
    assert result.variables_skipped_existing == ["email"]
    # email was skipped — no audit row should reference it.
    assert not any(row.fact_name == "email" for row in audit.rows)


def test_sweep_extracts_email_from_transcript_via_deterministic() -> None:
    compiled = _agent_document_with_schema(
        [AnalysisVariableDef(name="email", type="string", description="customer email")]
    )
    audit = InMemoryAuditWriter()
    pipeline = _pipeline(audit)
    result = run_analysis_sweep(
        conversation_id="c-3",
        organization_id="org-3",
        agent_document=compiled,
        transcripts=[
            TurnTranscript("turn-a", "Hi I'd like to learn about pricing"),
            TurnTranscript("turn-b", "Reach me at jane@example.com please"),
        ],
        existing_facts={},
        existing_fact_metadata={},
        fact_pipeline=pipeline,
    )

    assert "email" in result.variables_filled
    # Audit row exists for the email capture and is citation-ready.
    email_rows = [row for row in audit.rows if row.fact_name == "email" and row.outcome == "accepted"]
    assert email_rows, "expected an accepted email audit row"
    row = email_rows[0]
    assert row.turn_id == "turn-b"
    assert row.transcript_span is not None


def test_sweep_audit_rows_flow_into_citations() -> None:
    compiled = _agent_document_with_schema(
        [AnalysisVariableDef(name="email", type="string", description="customer email")]
    )
    audit = InMemoryAuditWriter()
    pipeline = _pipeline(audit)
    transcripts = [
        TurnTranscript("turn-1", "Hi"),
        TurnTranscript("turn-2", "My email is jane@example.com"),
    ]
    run_analysis_sweep(
        conversation_id="c-4",
        organization_id="org-4",
        agent_document=compiled,
        transcripts=transcripts,
        existing_facts={},
        existing_fact_metadata={},
        fact_pipeline=pipeline,
    )

    reader = InMemoryCitationReader(audit)
    turn_text = {turn.turn_id: turn.text for turn in transcripts}
    citations = build_citations(
        rows=reader.citations_for("c-4", organization_id="org-4"),
        turn_text_lookup=turn_text.get,
    )
    by_name = {citation.fact_name: citation for citation in citations}
    assert "email" in by_name
    assert by_name["email"].source_utterance == "jane@example.com"
    assert by_name["email"].turn_id == "turn-2"


def test_sweep_skips_empty_text_turns() -> None:
    compiled = _agent_document_with_schema(
        [AnalysisVariableDef(name="email", type="string", description="email")]
    )
    audit = InMemoryAuditWriter()
    run_analysis_sweep(
        conversation_id="c-5",
        organization_id="org-5",
        agent_document=compiled,
        transcripts=[
            TurnTranscript("turn-a", ""),
            TurnTranscript("turn-b", "   "),
        ],
        existing_facts={},
        existing_fact_metadata={},
        fact_pipeline=_pipeline(audit),
    )
    assert audit.rows == []


def test_analysis_variable_def_requires_categories_for_category_type() -> None:
    with pytest.raises(ValueError):
        AnalysisVariableDef(name="t", type="category", description="x")


def test_analysis_variable_def_rejects_categories_for_non_category_type() -> None:
    with pytest.raises(ValueError):
        AnalysisVariableDef(
            name="t", type="string", description="x", categories=["a", "b"]
        )
