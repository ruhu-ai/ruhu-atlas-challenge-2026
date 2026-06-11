from ruhu.capture.deterministic import DeterministicFactExtractor
from ruhu.schemas import FactDef, FactRequirement


def _extract(text: str, fact_names: list[str], existing_facts: dict[str, object] | None = None):
    return DeterministicFactExtractor().extract(
        text=text,
        fact_requirements=[FactRequirement(name=name) for name in fact_names],
        fact_defs=[FactDef(name=name, type="string") for name in fact_names],
        existing_facts=existing_facts or {},
    )


def test_extracts_delimited_loan_details_in_fact_order() -> None:
    candidates = _extract(
        "I want to buy a car, $2000, 2 years, from salary, yes I have my documents ready",
        [
            "loan_purpose",
            "loan_amount",
            "preferred_tenor",
            "repayment_source",
            "documents_ready",
        ],
    )

    assert [(candidate.fact_name, candidate.raw_value) for candidate in candidates] == [
        ("loan_purpose", "buy a car"),
        ("loan_amount", "$2000"),
        ("preferred_tenor", "2 years"),
        ("repayment_source", "salary"),
        ("documents_ready", "yes I have my documents ready"),
    ]


def test_delimited_capture_respects_already_known_facts() -> None:
    candidates = _extract(
        "school fees, 500000, 6 months, no",
        [
            "customer_name",
            "deposit_goal",
            "deposit_amount",
            "deposit_timeframe",
            "liquidity_need",
        ],
        {"customer_name": "Ada"},
    )

    assert [(candidate.fact_name, candidate.raw_value) for candidate in candidates] == [
        ("deposit_goal", "school fees"),
        ("deposit_amount", "500000"),
        ("deposit_timeframe", "6 months"),
        ("liquidity_need", "no"),
    ]


def test_extracts_partial_delimited_answers_when_values_validate() -> None:
    candidates = _extract(
        "car purchase, 2000",
        [
            "loan_purpose",
            "loan_amount",
            "preferred_tenor",
            "repayment_source",
            "documents_ready",
        ],
    )

    assert [(candidate.fact_name, candidate.raw_value) for candidate in candidates] == [
        ("loan_purpose", "car purchase"),
        ("loan_amount", "2000"),
    ]


def test_rejects_ordered_capture_when_a_value_fails_field_validation() -> None:
    candidates = _extract(
        "car purchase, salary",
        [
            "loan_purpose",
            "loan_amount",
            "preferred_tenor",
            "repayment_source",
            "documents_ready",
        ],
    )

    assert candidates == []


def test_extracts_labelled_capture_without_position_dependency() -> None:
    candidates = _extract(
        "tenor: 2 years; amount: NGN 500,000; purpose: school fees; source: salary; docs: yes",
        [
            "loan_purpose",
            "loan_amount",
            "preferred_tenor",
            "repayment_source",
            "documents_ready",
        ],
    )

    assert {candidate.fact_name: candidate.raw_value for candidate in candidates} == {
        "loan_purpose": "school fees",
        "loan_amount": "NGN 500,000",
        "preferred_tenor": "2 years",
        "repayment_source": "salary",
        "documents_ready": "yes",
    }


def test_phone_number_fact_uses_phone_extractor() -> None:
    candidates = _extract("My phone number is 0803 123 4567", ["phone_number"])

    assert [(candidate.fact_name, candidate.raw_value) for candidate in candidates] == [
        ("phone_number", "08031234567")
    ]
