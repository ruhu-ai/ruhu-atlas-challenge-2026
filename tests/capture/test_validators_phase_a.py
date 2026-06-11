from ruhu.capture.comparison import fact_value_equals
from ruhu.capture.validators import build_default_validator_registry
from ruhu.schemas import FactDef


def test_money_validator_normalizes_million_naira() -> None:
    registry = build_default_validator_registry()
    result = registry.get("money").validate("₦2m", FactDef(name="loan_amount", type="money"))

    assert result.status == "passed"
    assert result.normalized_value == {"amount": 2_000_000, "currency": "NGN"}


def test_duration_validator_normalizes_years_to_months() -> None:
    registry = build_default_validator_registry()
    result = registry.get("duration").validate("2 years", FactDef(name="preferred_tenor", type="duration"))

    assert result.status == "passed"
    assert result.normalized_value == {"months": 24}


def test_common_validators_normalize_expected_shapes() -> None:
    registry = build_default_validator_registry()

    assert registry.get("email").validate("USER@Example.COM", FactDef(name="email", type="email")).normalized_value == "user@example.com"
    assert registry.get("phone").validate("0801 234 5678", FactDef(name="phone", type="phone")).normalized_value == "+2348012345678"
    assert registry.get("boolean").validate("yes, I have it", FactDef(name="ready", type="boolean")).normalized_value is True
    assert registry.get("enum").validate(
        "High",
        FactDef(name="priority", type="enum", validator_config={"allowed_values": ["low", "high"]}),
    ).normalized_value == "high"
    assert registry.get("datetime").validate(
        "2026-05-29T15:00:00+01:00",
        FactDef(name="appointment_time", type="datetime"),
    ).normalized_value == "2026-05-29T14:00:00+00:00"
    assert registry.get("name").validate("  Ada   Lovelace ", FactDef(name="customer_name", type="name")).normalized_value == "Ada Lovelace"
    assert registry.get("address").validate(
        "  12 Broad Street, Lagos ",
        FactDef(name="address", type="address"),
    ).normalized_value == "12 Broad Street, Lagos"
    assert registry.get("id").validate("TICKET-123", FactDef(name="ticket_id", type="id")).normalized_value == "TICKET-123"


def test_secret_fact_forces_redaction_policy_and_removes_llm_source() -> None:
    fact_def = FactDef(
        name="password_attempt",
        type="string",
        source_policy="model_allowed",
        storage_policy={"scope": "audit_only", "retention": "do_not_store", "sensitivity": "secret", "audit_raw_policy": "hash"},
    )

    assert fact_def.storage_policy.audit_raw_policy == "redact"
    assert "llm_proposed" not in fact_def.allowed_sources


def test_fact_value_equals_supports_normalized_structured_values() -> None:
    assert fact_value_equals({"amount": 2_000_000, "currency": "NGN"}, 2_000_000)
    assert fact_value_equals({"months": 24}, "24")
    assert fact_value_equals({"amount": 2_000_000, "currency": "NGN"}, "NGN", path="currency")
