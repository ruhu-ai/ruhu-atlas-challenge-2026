from ruhu.agent_document import AgentDocument, Scenario, Step, compile_agent_document
from ruhu.capture import FactPipeline
from ruhu.capture.audit import InMemoryAuditWriter
from ruhu.capture.deterministic import DeterministicFactExtractor
from ruhu.capture.llm_extractor import LLMFactExtractor
from ruhu.capture.safety import SafetyGuard
from ruhu.capture.validators import build_default_validator_registry
from ruhu.schemas import FactDef


def _pipeline() -> FactPipeline:
    return FactPipeline(
        deterministic=DeterministicFactExtractor(),
        llm=LLMFactExtractor(None),
        validators=build_default_validator_registry(),
        guard=SafetyGuard(),
        audit_writer=InMemoryAuditWriter(),
    )


def _extract_values(*, step: Step, fact_schema: list[FactDef], text: str) -> dict[str, object]:
    document = AgentDocument(
        start_scenario_id="main",
        fact_schema=fact_schema,
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )
    result = _pipeline().extract(
        text=text,
        turn_id="turn-1",
        step=step,
        agent_document=compile_agent_document(document),
        existing_facts={},
        existing_fact_metadata={},
        classifier_entity_slots=None,
        conversation_id="conversation-1",
        organization_id="org-1",
    )
    return {update.name: update.value for update in result.updates}


def test_support_ticket_capture_e2e() -> None:
    step = Step(
        id="support_ticket",
        name="Support Ticket",
        fact_requirements=[
            {"name": "issue_summary"},
            {"name": "email"},
            {"name": "account_id"},
        ],
    )

    values = _extract_values(
        step=step,
        fact_schema=[
            FactDef(name="issue_summary", type="string", capture_aliases=["issue", "problem"]),
            FactDef(name="email", type="email"),
            FactDef(name="account_id", type="id", capture_aliases=["account"]),
        ],
        text="issue: cannot log in; email: USER@Example.COM; account: ACCT1234",
    )

    assert values == {
        "issue_summary": "cannot log in",
        "email": "user@example.com",
        "account_id": "ACCT1234",
    }


def test_appointment_capture_e2e() -> None:
    step = Step(
        id="appointment",
        name="Appointment",
        fact_requirements=[
            {"name": "appointment_date"},
            {"name": "appointment_reason"},
            {"name": "phone_number"},
        ],
    )

    values = _extract_values(
        step=step,
        fact_schema=[
            FactDef(name="appointment_date", type="datetime", capture_aliases=["date", "when"]),
            FactDef(name="appointment_reason", type="string", capture_aliases=["reason"]),
            FactDef(name="phone_number", type="phone", capture_aliases=["phone"]),
        ],
        text="date: next Tuesday; reason: onboarding; phone: 0803 123 4567",
    )

    assert values == {
        "appointment_date": "next Tuesday",
        "appointment_reason": "onboarding",
        "phone_number": "+2348031234567",
    }


def test_lead_qualification_capture_e2e() -> None:
    step = Step(
        id="lead_qualification",
        name="Lead Qualification",
        fact_requirements=[
            {"name": "company_name"},
            {"name": "budget"},
            {"name": "timeline"},
            {"name": "contact_consent"},
        ],
    )

    values = _extract_values(
        step=step,
        fact_schema=[
            FactDef(name="company_name", type="string", capture_aliases=["company"]),
            FactDef(name="budget", type="money", capture_aliases=["budget"], validator_config={"currency_default": "USD"}),
            FactDef(name="timeline", type="duration", capture_aliases=["timeline"]),
            FactDef(name="contact_consent", type="consent", capture_aliases=["consent"]),
        ],
        text="company: Atlas Retail; budget: $25k; timeline: 3 months; consent: I agree to be contacted",
    )

    assert values == {
        "company_name": "Atlas Retail",
        "budget": {"amount": 25000, "currency": "USD"},
        "timeline": {"months": 3},
        "contact_consent": True,
    }
