"""Backend tests for the template required-tools onboarding system.

Covers:
  - validate_template_required_tools (consistency invariant, all four
    error codes, built-in classifier integration)
  - _collect_agent_document_tool_refs walks tool_policy + action_config
    callable_* refs
  - _auto_derive_required_tools placeholder generation
  - shipped templates pass the validator at seed time
  - PublishReviewItem.remediation remains optional

See docs/templates/Template-Required-Tools-Onboarding-Spec.md.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from ruhu.api import (
    TemplateRequiredTool,
    TemplateRequiredToolsValidationError,
    _auto_derive_required_tools,
    _collect_agent_document_tool_refs,
    _resolve_setup_url,
    validate_template_required_tools,
)
from ruhu.agent_review import PublishReviewItem, PublishReviewRemediation


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — construct minimal valid agent_document dicts for the validator
# ─────────────────────────────────────────────────────────────────────────────


def _step(
    step_id: str,
    *,
    tool_refs: list[str] | None = None,
    callable_system_refs: list[str] | None = None,
    callable_integrations: list[str] | None = None,
    action_code: str | None = None,
) -> dict:
    step: dict = {"id": step_id, "name": step_id, "transitions": []}
    if tool_refs:
        step["tool_policy"] = [{"ref": ref, "mode": "required"} for ref in tool_refs]
    if callable_system_refs is not None or callable_integrations is not None:
        step["action_config"] = {
            "code": action_code or "result = {}",
            "callable_system_refs": list(callable_system_refs or []),
            "callable_integrations": list(callable_integrations or []),
        }
    return step


def _document(steps: list[dict] | None = None) -> dict:
    """Wrap a list of step dicts in a minimal valid AgentDocument shape.

    The validator uses ``AgentDocument.model_validate``, so the document
    must be structurally complete: at least one scenario containing at
    least one step, with start_scenario_id + start_step_id referencing
    real ids.
    """
    actual_steps = steps if steps else [_step("s1")]
    start_step_id = actual_steps[0]["id"]
    return {
        "version": "3.0",
        "start_scenario_id": "main",
        "scenarios": [
            {
                "id": "main",
                "name": "Main",
                "start_step_id": start_step_id,
                "steps": actual_steps,
            }
        ],
    }


def _meta(tool_ref: str, *, required: bool = True) -> dict:
    return {
        "tool_ref": tool_ref,
        "display_name": tool_ref,
        "description": "x",
        "category": "test",
        "provider_hints": [],
        "setup_url_path": f"/setup/{tool_ref}",
        "required": required,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Validator — happy path + every named error code
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateTemplateRequiredTools:
    def test_happy_path_all_external_refs_have_metadata(self) -> None:
        validate_template_required_tools(
            agent_document_json=_document([_step("s1", tool_refs=["crm.submit_lead"])]),
            required_tools=[_meta("crm.submit_lead")],
            builtin_refs=set(),
        )  # no exception

    def test_built_in_refs_excluded_from_consistency(self) -> None:
        # Document references only a built-in; metadata is empty.
        # Consistency holds — built-ins don't need metadata.
        validate_template_required_tools(
            agent_document_json=_document([_step("s1", tool_refs=["knowledge.lookup"])]),
            required_tools=[],
            builtin_refs={"knowledge.lookup"},
        )

    def test_missing_metadata_for_external_ref_fails(self) -> None:
        with pytest.raises(TemplateRequiredToolsValidationError) as exc_info:
            validate_template_required_tools(
                agent_document_json=_document([_step("s1", tool_refs=["crm.submit_lead"])]),
                required_tools=[],
                builtin_refs=set(),
            )
        assert exc_info.value.codes == [
            "template.required_tools.missing_metadata:crm.submit_lead",
        ]

    def test_stale_metadata_for_unused_ref_fails(self) -> None:
        with pytest.raises(TemplateRequiredToolsValidationError) as exc_info:
            validate_template_required_tools(
                agent_document_json=_document([_step("s1")]),
                required_tools=[_meta("ghost.tool")],
                builtin_refs=set(),
            )
        assert exc_info.value.codes == [
            "template.required_tools.stale_metadata:ghost.tool",
        ]

    def test_builtin_in_metadata_fails(self) -> None:
        with pytest.raises(TemplateRequiredToolsValidationError) as exc_info:
            validate_template_required_tools(
                agent_document_json=_document([_step("s1", tool_refs=["knowledge.lookup"])]),
                required_tools=[_meta("knowledge.lookup")],
                builtin_refs={"knowledge.lookup"},
            )
        assert exc_info.value.codes == [
            "template.required_tools.builtin_in_metadata:knowledge.lookup",
        ]

    def test_multiple_violations_collected(self) -> None:
        with pytest.raises(TemplateRequiredToolsValidationError) as exc_info:
            validate_template_required_tools(
                agent_document_json=_document(
                    [
                        _step("s1", tool_refs=["crm.submit_lead"]),
                        _step("s2", tool_refs=["knowledge.lookup"]),
                    ]
                ),
                required_tools=[_meta("ghost.tool"), _meta("knowledge.lookup")],
                builtin_refs={"knowledge.lookup"},
            )
        codes = exc_info.value.codes
        assert "template.required_tools.missing_metadata:crm.submit_lead" in codes
        assert "template.required_tools.builtin_in_metadata:knowledge.lookup" in codes
        assert "template.required_tools.stale_metadata:ghost.tool" in codes

    def test_empty_metadata_passes_when_document_has_no_external_refs(self) -> None:
        validate_template_required_tools(
            agent_document_json=_document([_step("s1")]),
            required_tools=[],
            builtin_refs=set(),
        )

    def test_action_config_callable_refs_walked(self) -> None:
        # Reference comes from action_config.callable_system_refs (P5/P6
        # surface), not tool_policy.
        validate_template_required_tools(
            agent_document_json=_document(
                [_step("s1", callable_system_refs=["crm.submit_lead"])]
            ),
            required_tools=[_meta("crm.submit_lead")],
            builtin_refs=set(),
        )

    def test_action_config_callable_integration_alias_refs_walked(self) -> None:
        validate_template_required_tools(
            agent_document_json=_document(
                [
                    _step(
                        "s1",
                        callable_integrations=["crm-system"],
                        action_code="result = crm_system(action='submit_lead')",
                    )
                ]
            ),
            required_tools=[_meta("crm-system.submit_lead")],
            builtin_refs=set(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# _collect_agent_document_tool_refs
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectAgentDocumentToolRefs:
    def test_walks_tool_policy(self) -> None:
        refs = _collect_agent_document_tool_refs(
            _document([_step("s", tool_refs=["a.x", "b.y"])]),
        )
        assert refs == {"a.x", "b.y"}

    def test_walks_action_config_callable_system_refs(self) -> None:
        refs = _collect_agent_document_tool_refs(
            _document([_step("s", callable_system_refs=["a.x", "b.y"])]),
        )
        assert refs == {"a.x", "b.y"}

    def test_walks_action_config_callable_integration_action_refs(self) -> None:
        refs = _collect_agent_document_tool_refs(
            _document(
                [
                    _step(
                        "s",
                        callable_integrations=["crm-system", "calendar"],
                        action_code=(
                            "lead = crm_system(action='submit_lead')\n"
                            "event = calendar(action=\"create_event\")\n"
                        ),
                    )
                ]
            ),
        )
        assert refs == {"crm-system.submit_lead", "calendar.create_event"}

    def test_callable_integration_without_literal_action_falls_back_to_category(self) -> None:
        refs = _collect_agent_document_tool_refs(
            _document(
                [
                    _step(
                        "s",
                        callable_integrations=["crm"],
                        action_code="result = crm(action=vars.get('action_name'))",
                    )
                ]
            ),
        )
        assert refs == {"crm"}

    def test_dedupes_across_steps(self) -> None:
        refs = _collect_agent_document_tool_refs(
            _document(
                [
                    _step("s1", tool_refs=["a.x"]),
                    _step("s2", callable_system_refs=["a.x"]),
                ]
            ),
        )
        assert refs == {"a.x"}

    def test_handles_malformed_document_gracefully(self) -> None:
        # Invalid document shapes should be swallowed (the validator
        # uses AgentDocument.model_validate inside a try/except and
        # returns an empty set on failure).
        assert _collect_agent_document_tool_refs({"states": ["bogus"]}) == set()
        assert _collect_agent_document_tool_refs({"scenarios": ["not-a-dict"]}) == set()

    def test_empty_input_returns_empty_set(self) -> None:
        assert _collect_agent_document_tool_refs({}) == set()
        assert _collect_agent_document_tool_refs({"scenarios": None}) == set()


# ─────────────────────────────────────────────────────────────────────────────
# _auto_derive_required_tools
# ─────────────────────────────────────────────────────────────────────────────


class TestAutoDeriveRequiredTools:
    def test_derives_one_entry_per_external_ref(self) -> None:
        entries = _auto_derive_required_tools(
            agent_document_json=_document(
                [_step("s1", tool_refs=["crm.submit_lead", "knowledge.lookup"])]
            ),
            builtin_refs={"knowledge.lookup"},
        )
        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, TemplateRequiredTool)
        assert entry.tool_ref == "crm.submit_lead"
        assert entry.category == "crm"
        # Agent-relative path — resolved by consumers against
        # /agents/{agent_id}/ (see _resolve_setup_url helper).
        assert entry.setup_url_path == "canvas?view=library&tool_ref=crm.submit_lead"

    def test_category_falls_back_to_general_when_no_dot(self) -> None:
        entries = _auto_derive_required_tools(
            agent_document_json=_document([_step("s1", tool_refs=["bareref"])]),
            builtin_refs=set(),
        )
        assert entries[0].category == "general"

    def test_derived_metadata_passes_validator_round_trip(self) -> None:
        document_json = _document(
            [_step("s1", tool_refs=["crm.submit_lead", "ehr.verify"])]
        )
        derived = _auto_derive_required_tools(
            agent_document_json=document_json, builtin_refs=set()
        )
        # Round-trip: feed back into the validator — must accept.
        validate_template_required_tools(
            agent_document_json=document_json,
            required_tools=[t.model_dump() for t in derived],
            builtin_refs=set(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_setup_url — agent-relative vs absolute paths
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveSetupUrl:
    def test_relative_path_prepended_with_agent_root(self) -> None:
        url = _resolve_setup_url(
            agent_id="g123",
            template_setup_url_path="canvas?view=library&tool_ref=crm.submit_lead",
        )
        assert url == "/agents/g123/canvas?view=library&tool_ref=crm.submit_lead"

    def test_absolute_path_passed_through_unchanged(self) -> None:
        # Templates that point at org-wide pages (e.g. /tools, /settings)
        # use absolute paths and should not be modified.
        url = _resolve_setup_url(
            agent_id="g123",
            template_setup_url_path="/tools?tool_ref=custom.api",
        )
        assert url == "/tools?tool_ref=custom.api"


# ─────────────────────────────────────────────────────────────────────────────
# Shipped templates pass the validator
# ─────────────────────────────────────────────────────────────────────────────


_TEMPLATES_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "ruhu" / "templates" / "system"
)
# Mirror the production built-in registry; if a new built-in is added,
# update this set OR move the test to inject the runtime's actual set.
_PRODUCTION_BUILTIN_REFS = {"knowledge.lookup"}


@pytest.mark.parametrize(
    "template_path",
    sorted(_TEMPLATES_DIR.glob("*.json")),
    ids=lambda p: p.name,
)
def test_shipped_template_passes_required_tools_validator(template_path: pathlib.Path) -> None:
    """Spec §5.2 acceptance #1: every shipped template must pass the
    consistency validator at startup."""
    payload = json.loads(template_path.read_text())
    validate_template_required_tools(
        agent_document_json=dict(payload["agent_document"]),
        required_tools=list(payload.get("required_tools") or []),
        builtin_refs=_PRODUCTION_BUILTIN_REFS,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Axis 1 — per-tool required flag
# ─────────────────────────────────────────────────────────────────────────────


class TestRequiredFlag:
    """Pins the required/optional gradient introduced by Axis 1 of the
    publish-gate relaxation. Per-template UI gating + publish-review
    demotion both consume ``TemplateRequiredTool.required``."""

    def test_default_required_true_for_publish_safety(self) -> None:
        # Omitted requirement metadata defaults to required so missing
        # tool setup blocks publish unless a template marks it optional.
        t = TemplateRequiredTool(
            tool_ref="x",
            display_name="x",
            description="x",
            category="x",
            setup_url_path="x",
        )
        assert t.required is True

    def test_explicit_optional_overrides_default(self) -> None:
        t = TemplateRequiredTool(
            tool_ref="x",
            display_name="x",
            description="x",
            category="x",
            setup_url_path="x",
            required=False,
        )
        assert t.required is False

    def test_required_field_does_not_affect_consistency_validator(self) -> None:
        # Whether a tool is required or optional, the bidirectional
        # consistency invariant (document ↔ metadata) holds the same way.
        validate_template_required_tools(
            agent_document_json=_document([_step("s1", tool_refs=["a", "b"])]),
            required_tools=[_meta("a", required=True), _meta("b", required=False)],
            builtin_refs=set(),
        )  # no exception

    def test_sales_agent_marks_calendar_booking_required(self) -> None:
        # Sales agent's whole purpose is demo booking — calendar.create_event
        # must be marked required so publish stays gated on it.
        payload = json.loads((_TEMPLATES_DIR / "sales-agent.json").read_text())
        entries = payload.get("required_tools") or []
        calendar = next(e for e in entries if e["tool_ref"] == "calendar.create_event")
        assert calendar["required"] is True

    def test_ecommerce_branch_only_tools_marked_optional(self) -> None:
        # commerce.issue_refund / issue_store_credit are alternative
        # resolution paths — neither should block publish on its own.
        payload = json.loads((_TEMPLATES_DIR / "ecommerce-returns-refunds.json").read_text())
        entries = {e["tool_ref"]: e for e in (payload.get("required_tools") or [])}
        # Entry-point tools stay required
        assert entries["commerce.get_order"]["required"] is True
        assert entries["commerce.authorize_return"]["required"] is True
        # Branch-only tools demoted to optional
        assert entries["commerce.issue_refund"]["required"] is False
        assert entries["commerce.issue_store_credit"]["required"] is False
        assert entries["commerce.check_inventory"]["required"] is False
        assert entries["commerce.create_exchange_order"]["required"] is False

    def test_healthcare_only_verify_patient_is_required(self) -> None:
        payload = json.loads((_TEMPLATES_DIR / "healthcare-triage-scheduling.json").read_text())
        entries = {e["tool_ref"]: e for e in (payload.get("required_tools") or [])}
        required_refs = {ref for ref, e in entries.items() if e["required"]}
        # Only the entry-point identity verification gates publish;
        # refill / scheduling branches are optional.
        assert required_refs == {"ehr.verify_patient"}


# ─────────────────────────────────────────────────────────────────────────────
# PublishReviewItem.remediation
# ─────────────────────────────────────────────────────────────────────────────


class TestPublishReviewRemediation:
    def test_remediation_field_optional(self) -> None:
        # Existing payloads without remediation must still parse cleanly.
        item = PublishReviewItem(severity="error", code="x", message="y")
        assert item.remediation is None

    def test_remediation_serializes_when_present(self) -> None:
        item = PublishReviewItem(
            severity="error",
            code="tool.missing_runtime_spec",
            message="...",
            remediation=PublishReviewRemediation(
                kind="configure_tool",
                tool_ref="crm.submit_lead",
                url="/settings/integrations?tool_ref=crm.submit_lead",
                label="Set up CRM lead submission",
                documentation_url=None,
            ),
        )
        dumped = item.model_dump()
        assert dumped["remediation"]["kind"] == "configure_tool"
        assert dumped["remediation"]["tool_ref"] == "crm.submit_lead"
        assert dumped["remediation"]["label"] == "Set up CRM lead submission"
