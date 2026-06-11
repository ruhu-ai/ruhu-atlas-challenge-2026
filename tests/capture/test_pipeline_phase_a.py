from ruhu.agent_document import AgentDocument, Scenario, Step, compile_agent_document
from ruhu.kernel import ConversationKernel
from ruhu.capture import FactCandidate, FactPipeline
from ruhu.capture.audit import InMemoryAuditWriter
from ruhu.capture.confirmation import resolve_pending_confirmations
from ruhu.capture.deterministic import DeterministicFactExtractor
from ruhu.capture.llm_extractor import FieldExtractorLLM, LLMFactExtractor
from ruhu.capture.safety import SafetyGuard
from ruhu.capture.storage import TOOL_CONTEXT_METADATA_KEY, TURN_CAPTURE_METADATA_KEY, StorageRouter
from ruhu.capture.validators import build_default_validator_registry
from ruhu.schemas import FactDef, FactUpdate, RuntimeTurn
from datetime import datetime, timezone


def _pipeline(audit: InMemoryAuditWriter | None = None, llm: FieldExtractorLLM | None = None) -> FactPipeline:
    return FactPipeline(
        deterministic=DeterministicFactExtractor(),
        llm=LLMFactExtractor(llm),
        validators=build_default_validator_registry(),
        guard=SafetyGuard(),
        audit_writer=audit or InMemoryAuditWriter(),
    )


class _TranscriptAwareExtractor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def extract(self, *, text: str, fields: list[str], hints: dict[str, str]) -> dict[str, str | None]:
        self.calls.append((text, list(fields)))
        values: dict[str, str | None] = {field: None for field in fields}
        lowered = text.lower()
        if "appointment_date" in fields and "tuesday" in lowered:
            values["appointment_date"] = "next Tuesday"
        if "appointment_reason" in fields and "onboarding" in lowered:
            values["appointment_reason"] = "onboarding"
        return values


def _loan_document() -> tuple[object, Step]:
    step = Step(
        id="loan_collect_details",
        name="Loan Details",
        fact_requirements=[
            {"name": "loan_purpose"},
            {"name": "loan_amount"},
            {"name": "preferred_tenor"},
            {"name": "repayment_source"},
            {"name": "documents_ready"},
        ],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(name="loan_purpose", type="string"),
            FactDef(name="loan_amount", type="money", validator_config={"currency_default": "NGN"}),
            FactDef(name="preferred_tenor", type="duration", capture_aliases=["tenor", "term"]),
            FactDef(name="repayment_source", type="string"),
            FactDef(name="documents_ready", type="boolean"),
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    return compile_agent_document(doc), step


def test_pipeline_captures_multiple_facts_from_one_step() -> None:
    compiled, step = _loan_document()
    result = _pipeline().extract(
        text="I want to buy a car, ₦2m, 2 years, from salary, yes I have my documents ready",
        turn_id="turn-1",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    values = {update.name: update.value for update in result.updates}
    assert values["loan_purpose"] == "buy a car"
    assert values["loan_amount"] == {"amount": 2_000_000, "currency": "NGN"}
    assert values["preferred_tenor"] == {"months": 24}
    assert values["repayment_source"] == "salary"
    assert values["documents_ready"] is True
    assert set(result.new_fact_metadata) == set(values)


def test_voice_style_fragments_can_fill_multiple_facts_with_transcript_context(monkeypatch) -> None:
    monkeypatch.delenv("RUHU_LLM_FACT_EXTRACTION", raising=False)
    step = Step(
        id="appointment",
        name="Appointment",
        fact_requirements=[
            {"name": "appointment_date"},
            {"name": "appointment_reason"},
            {"name": "appointment_location"},
        ],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(name="appointment_date", type="datetime", llm_confidence_default=0.9),
            FactDef(name="appointment_reason", type="string", llm_confidence_default=0.9),
            FactDef(name="appointment_location", type="string", llm_confidence_default=0.9),
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    compiled = compile_agent_document(doc)
    backend = _TranscriptAwareExtractor()
    pipeline = _pipeline(llm=backend)

    first = pipeline.extract(
        text="next Tuesday",
        turn_id="turn-1",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
    )
    existing = {update.name: update.value for update in first.updates}
    second = pipeline.extract(
        text="for onboarding",
        turn_id="turn-2",
        step=step,
        agent_document=compiled,
        existing_facts=existing,
        existing_fact_metadata=first.new_fact_metadata,
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
        transcript_context="next Tuesday",
    )

    values = {**existing, **{update.name: update.value for update in second.updates}}
    assert values == {
        "appointment_date": "next Tuesday",
        "appointment_reason": "onboarding",
    }
    assert "Recent conversation:" in backend.calls[-1][0]


def test_llm_fact_extraction_env_gate_disables_user_message_llm(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_LLM_FACT_EXTRACTION", "off")
    step = Step(
        id="appointment",
        name="Appointment",
        fact_requirements=[{"name": "appointment_date"}, {"name": "appointment_reason"}],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(name="appointment_date", type="datetime", llm_confidence_default=0.9),
            FactDef(name="appointment_reason", type="string", llm_confidence_default=0.9),
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline(llm=_TranscriptAwareExtractor()).extract(
        text="next Tuesday",
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates == []


def test_pipeline_redacts_otp_like_values_before_storage() -> None:
    audit = InMemoryAuditWriter()
    step = Step(
        id="verify",
        name="Verify",
        description="Ask for OTP code",
        fact_requirements=[{"name": "verification_code"}],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="verification_code", type="string")],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline(audit).extract(
        text="4231",
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates == []
    assert audit.rows[0].outcome == "rejected_safety"
    assert audit.rows[0].raw_value is None
    assert audit.rows[0].normalized_value is None


def test_pipeline_applies_agent_safety_deny_patterns() -> None:
    audit = InMemoryAuditWriter()
    step = Step(id="collect", name="Collect", fact_requirements=[{"name": "note"}])
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="note", type="string")],
        metadata={"safety_config": {"deny_patterns": ["forbidden-token"]}},
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline(audit).process_candidates(
        candidates=[FactCandidate("note", "contains forbidden-token", "deterministic", "span", 0.9)],
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates == []
    assert audit.rows[0].outcome == "rejected_safety"
    assert audit.rows[0].reason == "tenant_deny_pattern"


def test_kernel_writes_pipeline_updates_and_metadata() -> None:
    compiled, _step = _loan_document()
    document = compiled.document
    kernel = ConversationKernel()
    kernel.start_conversation("conversation-1", agent_document=document, agent_id="agent-1")

    kernel.process_turn(
        "conversation-1",
        RuntimeTurn(
            turn_id="turn-1",
            dedupe_key="turn-1",
            channel="web_chat",
            modality="text",
            event_type="user_message",
            text="car purchase, 500000, 6 months, salary, yes",
            received_at=datetime.now(timezone.utc),
        ),
        agent_document=document,
    )

    conversation = kernel.conversation_store.load("conversation-1")
    assert conversation is not None
    assert conversation.facts["loan_amount"] == {"amount": 500000, "currency": "NGN"}
    assert conversation.facts["preferred_tenor"] == {"months": 6}
    assert conversation.metadata["__ruhu_fact_metadata__"]["loan_amount"]["source"] == "deterministic"


def test_pipeline_arbitration_can_require_confirmation_on_disagreement() -> None:
    step = Step(
        id="contact",
        name="Contact",
        fact_requirements=[{"name": "email"}],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(
                name="email",
                type="email",
                arbitration_rules=[{"kind": "require_confirmation_on_disagreement"}],
            )
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline().process_candidates(
        candidates=[
            FactCandidate("email", "user@example.com", "deterministic", "span", 0.95),
            FactCandidate("email", "other@example.com", "classifier", "slot", 0.96),
        ],
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates == []
    assert len(result.needs_confirmation) == 1
    assert result.needs_confirmation[0].name == "email"
    assert result.needs_confirmation[0].reason == "conflict_requires_confirmation"


def test_pipeline_rejects_source_not_allowed_by_fact_def() -> None:
    step = Step(id="contact", name="Contact", fact_requirements=[{"name": "email"}])
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(
                name="email",
                type="email",
                allowed_sources={"deterministic"},
            )
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline().process_candidates(
        candidates=[FactCandidate("email", "llm@example.com", "llm_proposed", "span", 0.95)],
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates == []
    assert result.rejected[0][1].reason == "source_not_allowed"


def test_pipeline_arbitration_prefers_authoritative_tool_source_ref() -> None:
    step = Step(id="contact", name="Contact", fact_requirements=[{"name": "email"}])
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(
                name="email",
                type="email",
                arbitration_rules=[
                    {"kind": "prefer_authoritative_tool", "config": {"authoritative_tools": ["crm_lookup"]}},
                    {"kind": "prefer_highest_confidence"},
                ],
            )
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline().process_candidates(
        candidates=[
            FactCandidate("email", "classifier@example.com", "classifier", "slot", 0.99),
            FactCandidate("email", "tool@example.com", "tool", "output", 0.95, source_ref="crm_lookup"),
        ],
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates[0].value == "tool@example.com"
    assert result.updates[0].source == "tool"


def test_pipeline_arbitration_supports_prefer_latest_rule() -> None:
    step = Step(id="contact", name="Contact", fact_requirements=[{"name": "email"}])
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(
                name="email",
                type="email",
                arbitration_rules=[{"kind": "prefer_latest"}],
            )
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline().process_candidates(
        candidates=[
            FactCandidate("email", "first@example.com", "deterministic", "span", 0.99),
            FactCandidate("email", "second@example.com", "deterministic", "span", 0.90),
        ],
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates[0].value == "second@example.com"


def test_default_arbitration_prefers_classifier_over_llm_when_both_are_exact() -> None:
    step = Step(id="contact", name="Contact", fact_requirements=[{"name": "email"}])
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="email", type="email")],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline().process_candidates(
        candidates=[
            FactCandidate("email", "model@example.com", "llm_proposed", "span", 0.99),
            FactCandidate("email", "classifier@example.com", "classifier", "slot", 0.90),
        ],
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates[0].value == "classifier@example.com"


def test_conflict_policy_prefers_existing_deterministic_fact() -> None:
    step = Step(id="contact", name="Contact", fact_requirements=[{"name": "email"}])
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="email", type="email", conflict_policy="prefer_deterministic")],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline().process_candidates(
        candidates=[FactCandidate("email", "new@example.com", "llm_proposed", "span", 0.95)],
        turn_id="turn-2",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={"email": "old@example.com"},
        existing_fact_metadata={"email": {"source": "deterministic", "confidence": 1.0}},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates == []
    assert result.rejected[0][1].decision == "rejected_conflict"
    assert result.rejected[0][1].reason == "existing_deterministic_preferred"


def test_conflict_policy_prefers_latest_only_when_confidence_is_not_lower() -> None:
    step = Step(id="contact", name="Contact", fact_requirements=[{"name": "email"}])
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="email", type="email", conflict_policy="prefer_latest_high_confidence")],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    compiled = compile_agent_document(doc)
    pipeline = _pipeline()

    lower = pipeline.process_candidates(
        candidates=[FactCandidate("email", "lower@example.com", "classifier", "slot", 0.85)],
        turn_id="turn-2",
        step=step,
        agent_document=compiled,
        existing_facts={"email": "old@example.com"},
        existing_fact_metadata={"email": {"source": "classifier", "confidence": 0.9}},
        conversation_id="conversation-1",
        organization_id="org-1",
    )
    higher = pipeline.process_candidates(
        candidates=[FactCandidate("email", "higher@example.com", "classifier", "slot", 0.95)],
        turn_id="turn-3",
        step=step,
        agent_document=compiled,
        existing_facts={"email": "old@example.com"},
        existing_fact_metadata={"email": {"source": "classifier", "confidence": 0.9}},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert lower.updates == []
    assert lower.rejected[0][1].reason == "existing_confidence_higher"
    assert higher.updates[0].value == "higher@example.com"
    assert higher.updates[0].replaced_previous is True


def test_secret_audit_only_capture_never_materializes_raw_or_normalized_value() -> None:
    audit = InMemoryAuditWriter()
    step = Step(
        id="internal",
        name="Internal",
        fact_requirements=[{"name": "secret_note"}],
    )
    doc = AgentDocument(
        start_scenario_id="main",
        fact_schema=[
            FactDef(
                name="secret_note",
                type="string",
                storage_policy={"scope": "audit_only", "retention": "audit_90d", "sensitivity": "secret"},
            )
        ],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    result = _pipeline(audit).process_candidates(
        candidates=[FactCandidate("secret_note", "private value", "deterministic", "span", 0.9)],
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(doc),
        existing_facts={},
        existing_fact_metadata={},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates == []
    assert result.storage_writes == {}
    assert audit.rows[0].outcome == "stored_audit_only_redacted"
    assert audit.rows[0].raw_value is None
    assert audit.rows[0].normalized_value is None
    assert audit.rows[0].audit_raw_policy == "redact"


def test_narration_filter_hides_non_narration_facts() -> None:
    facts = {"public_answer": "visible", "internal_note": "hidden"}
    metadata = {
        "internal_note": {
            "storage_policy": {
                "expose_to_narration": False,
            }
        }
    }

    visible = ConversationKernel._filter_narration_facts(facts, metadata)

    assert visible == {"public_answer": "visible"}


def test_pending_confirmation_yes_becomes_user_confirmed_update() -> None:
    pending_resolution = resolve_pending_confirmations(
        text="yes",
        turn_id="turn-2",
        pending_items=[
            {
                "pending_id": "pending-1",
                "name": "loan_amount",
                "proposed_value": {"amount": 2_000_000, "currency": "NGN"},
                "raw_value": "₦2m",
                "source": "llm_proposed",
                "confidence": 0.7,
                "reason": "below_threshold",
                "turn_id": "turn-1",
            }
        ],
    )
    assert pending_resolution.pending_items[0]["status"] == "confirmed"

    compiled, step = _loan_document()
    audit = InMemoryAuditWriter()
    result = _pipeline(audit).process_candidates(
        candidates=pending_resolution.candidates,
        turn_id="turn-2",
        step=step,
        agent_document=compiled,
        existing_facts={},
        existing_fact_metadata={},
        conversation_id="conversation-1",
        organization_id="org-1",
    )

    assert result.updates[0].source == "user_confirmed"
    assert result.updates[0].value == {"amount": 2_000_000, "currency": "NGN"}
    assert audit.rows[0].source_ref == "pending-1"


def test_pending_confirmation_reject_and_expire_on_read() -> None:
    rejected = resolve_pending_confirmations(
        text="no",
        turn_id="turn-2",
        pending_items=[
            {
                "pending_id": "pending-2",
                "name": "email",
                "proposed_value": "ops@example.com",
                "raw_value": "ops@example.com",
                "source": "llm_proposed",
                "confidence": 0.7,
                "reason": "below_threshold",
                "turn_id": "turn-1",
            }
        ],
    )

    assert rejected.resolved is True
    assert rejected.candidates == []
    assert rejected.pending_items[0]["status"] == "rejected"

    expired = resolve_pending_confirmations(
        text="anything else",
        turn_id="turn-3",
        pending_items=[
            {
                "pending_id": "pending-3",
                "name": "email",
                "proposed_value": "ops@example.com",
                "raw_value": "ops@example.com",
                "source": "llm_proposed",
                "confidence": 0.7,
                "reason": "below_threshold",
                "turn_id": "turn-1",
                "expires_at": "2000-01-01T00:00:00+00:00",
            }
        ],
    )

    assert expired.resolved is True
    assert expired.candidates == []
    assert expired.pending_items[0]["status"] == "expired"


def test_kernel_emits_fact_needs_confirmation_event() -> None:
    step = Step(id="collect", name="Collect", fact_requirements=[{"name": "nickname"}])
    document = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="nickname", type="string", confidence_threshold=0.99)],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    kernel = ConversationKernel()
    kernel.start_conversation("conversation-confirm", agent_document=document, agent_id="agent-1")

    result = kernel.process_turn(
        "conversation-confirm",
        RuntimeTurn(
            turn_id="turn-1",
            dedupe_key="turn-1",
            channel="web_chat",
            modality="text",
            event_type="user_message",
            text="Ada",
            received_at=datetime.now(timezone.utc),
        ),
        agent_document=document,
    )

    assert any(
        event.family == "fact_needs_confirmation" and event.name == "nickname"
        for event in result.semantic_events
    )


def test_storage_router_routes_turn_tool_context_and_audit_only_scopes() -> None:
    router = StorageRouter()
    conversation_metadata: dict[str, object] = {}
    turn_metadata: dict[str, object] = {}

    result = router.apply(
        storage_writes={
            "turn": {
                "scratch": FactUpdate(name="scratch", value="one", source="deterministic"),
            },
            "tool_context": {
                "tool_arg": FactUpdate(name="tool_arg", value={"id": "T-1"}, source="tool"),
            },
            "audit_only": {
                "telemetry": FactUpdate(name="telemetry", value="seen", source="system"),
            },
        },
        conversation_metadata=conversation_metadata,
        turn_metadata=turn_metadata,
    )

    assert result.routed_counts == {"turn": 1, "tool_context": 1, "audit_only": 1}
    assert turn_metadata[TURN_CAPTURE_METADATA_KEY]["scratch"]["value"] == "one"
    assert conversation_metadata[TOOL_CONTEXT_METADATA_KEY]["tool_arg"] == {"id": "T-1"}
    assert "telemetry" not in conversation_metadata


def test_strict_audit_mode_raises_on_audit_failure() -> None:
    class FailingAuditWriter:
        def write(self, rows):
            raise RuntimeError("audit unavailable")

    pipeline = FactPipeline(
        deterministic=DeterministicFactExtractor(),
        llm=LLMFactExtractor(None),
        validators=build_default_validator_registry(),
        guard=SafetyGuard(),
        audit_writer=FailingAuditWriter(),
        strict_audit=True,
    )
    step = Step(id="collect", name="Collect", fact_requirements=[{"name": "nickname"}])
    document = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="nickname", type="string")],
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )

    try:
        pipeline.process_candidates(
            candidates=[FactCandidate("nickname", "Ada", "deterministic", "span", 0.9)],
            turn_id="turn-1",
            step=step,
            agent_document=compile_agent_document(document),
            existing_facts={},
            existing_fact_metadata={},
            conversation_id="conversation-1",
            organization_id="org-1",
        )
    except RuntimeError as exc:
        assert "audit unavailable" in str(exc)
    else:
        raise AssertionError("strict audit mode should raise audit writer failures")
