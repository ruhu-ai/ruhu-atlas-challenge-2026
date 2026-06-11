from __future__ import annotations

from ruhu.rules_migration import convert_legacy_policy, extract_legacy_policies


def test_extract_legacy_policies_supports_common_export_shapes() -> None:
    assert extract_legacy_policies([{"id": "a"}]) == [{"id": "a"}]
    assert extract_legacy_policies({"items": [{"id": "b"}]}) == [{"id": "b"}]
    assert extract_legacy_policies({"policies": [{"id": "c"}]}) == [{"id": "c"}]


def test_convert_legacy_policy_from_condition_builder() -> None:
    converted = convert_legacy_policy(
        {
            "id": "policy-1",
            "name": "Card Block",
            "policy_type": "compliance",
            "rules": {
                "condition_builder": {
                    "logic": "AND",
                    "rules": [
                        {
                            "variable": "user_utterance",
                            "operator": "contains",
                            "value": "card",
                            "case_sensitive": False,
                        }
                    ],
                    "groups": [],
                }
            },
            "actions": {"action": "block", "reason": "PII not allowed"},
        }
    )
    assert converted is not None
    rule_id, body = converted
    assert rule_id == "legacy.compliance.policy_1"
    assert body.stage == "turn_ingress"
    assert body.effect.kind == "block"
    assert body.predicate.kind == "match"


def test_convert_legacy_policy_from_expression() -> None:
    converted = convert_legacy_policy(
        {
            "id": "policy-2",
            "name": "Tool Guard",
            "policy_type": "security",
            "conditions": {"expression": '"transfer_funds" in tool_name'},
            "actions": {"action": "warn", "reason": "Watch transfers"},
        }
    )
    assert converted is not None
    _, body = converted
    assert body.stage == "before_tool"
    assert body.effect.kind == "warn"
    assert body.predicate.kind == "match"


def test_convert_legacy_policy_skips_unsupported_expression() -> None:
    converted = convert_legacy_policy(
        {
            "id": "policy-3",
            "conditions": {"expression": "contains_pii(user_utterance)"},
            "actions": {"action": "block", "reason": "No PII"},
        }
    )
    assert converted is None
