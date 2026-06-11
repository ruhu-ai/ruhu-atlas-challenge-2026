import json
from pathlib import Path

from ruhu.agent_document import AgentDocument, compile_agent_document
from ruhu.capture import build_default_fact_pipeline


def test_nafmfb_loan_collect_details_extracts_multiple_facts() -> None:
    template = json.loads(
        Path("src/ruhu/templates/system/nafmfb-financial-support-assistant-demo.json").read_text()
    )
    document = AgentDocument.model_validate(template["agent_document"])
    compiled = compile_agent_document(document)
    step = compiled.step_by_id("loan_collect_details")

    result = build_default_fact_pipeline().extract(
        text="purpose is school fees, amount is ₦500,000, tenor is 6 months, source is salary, docs: ready",
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
    assert values["loan_purpose"] == "school fees"
    assert values["loan_amount"] == {"amount": 500000, "currency": "NGN"}
    assert values["preferred_tenor"] == {"months": 6}
    assert values["repayment_source"] == "salary"
    assert values["documents_ready"] is True


def test_nafmfb_loan_collect_details_handles_voice_style_fragments() -> None:
    template = json.loads(
        Path("src/ruhu/templates/system/nafmfb-financial-support-assistant-demo.json").read_text()
    )
    document = AgentDocument.model_validate(template["agent_document"])
    compiled = compile_agent_document(document)
    step = compiled.step_by_id("loan_collect_details")
    pipeline = build_default_fact_pipeline()
    facts: dict[str, object] = {}
    metadata: dict[str, dict[str, object]] = {}

    for index, text in enumerate(
        [
            "Okay, the loan purpose is for a car and the requested amount is 2000 Naira. Preferred tenor 2 years.",
            "repayment source from salaries",
            "And yes, I have my documents ready.",
        ],
        start=1,
    ):
        result = pipeline.extract(
            text=text,
            turn_id=f"turn-{index}",
            step=step,
            agent_document=compiled,
            existing_facts=facts,
            existing_fact_metadata=metadata,
            classifier_entity_slots=None,
            conversation_id="conversation-1",
            organization_id="org-1",
        )
        facts.update({update.name: update.value for update in result.updates})
        metadata.update(result.new_fact_metadata)

    assert facts["loan_purpose"] == "a car"
    assert facts["loan_amount"] == {"amount": 2000, "currency": "NGN"}
    assert facts["preferred_tenor"] == {"months": 24}
    assert facts["repayment_source"] == "salaries"
    assert facts["documents_ready"] is True


def test_nafmfb_callback_details_handle_voice_fragments_and_corrections() -> None:
    template = json.loads(
        Path("src/ruhu/templates/system/nafmfb-financial-support-assistant-demo.json").read_text()
    )
    document = AgentDocument.model_validate(template["agent_document"])
    compiled = compile_agent_document(document)
    step = compiled.step_by_id("collect_callback_details")
    pipeline = build_default_fact_pipeline()
    facts: dict[str, object] = {}
    metadata: dict[str, dict[str, object]] = {}

    for index, text in enumerate(
        [
            "My name is Eda Lasa and my phone number is 0",
            "810746474",
            "Thank you. Okay, my name is Ijidailasa. My phone number is",
            "0810746474",
            "The branch is Abuja.",
            "And then, yes, I consent to a callback.",
        ],
        start=1,
    ):
        result = pipeline.extract(
            text=text,
            turn_id=f"turn-{index}",
            step=step,
            agent_document=compiled,
            existing_facts=facts,
            existing_fact_metadata=metadata,
            classifier_entity_slots=None,
            conversation_id="conversation-1",
            organization_id="org-1",
        )
        facts.update({update.name: update.value for update in result.updates})
        metadata.update(result.new_fact_metadata)

    assert facts["customer_name"] == "Ijidailasa"
    assert facts["phone_number"] == "+234810746474"
    assert facts["preferred_branch"] == "Abuja"
    assert facts["consent_to_callback"] is True
