from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


# Pending permission requests expire after this duration. Decisions submitted
# after expiration are rejected; expired pending requests are treated as
# "no longer pending" by callers that gate apply on pending count.
ATLAS_PERMISSION_REQUEST_TTL = timedelta(hours=24)

from .agent_document import (
    AgentDocument,
    Scenario,
    ScenarioRoute,
    Step,
    StepCompletion,
    StepHandoff,
    StepTransition,
    compile_agent_document,
    step_capability_flags,
    validate_agent_document,
)
from .atlas_models import (
    AtlasEvent,
    AtlasMessage,
    AtlasPermissionRequest,
    AtlasReviewDecisionRecord,
    AtlasSession,
)
from .atlas_docs_parser import AtlasDocsPageParser
from .atlas_generator import AtlasGeneratorContext, AtlasGeneratorOutput, AtlasProposalGenerator
from .atlas_protocol import (
    AtlasAPIDiscoveryResult,
    AtlasAttachmentIngestionResult,
    AtlasBlocker,
    AtlasDependency,
    BlockingQuestion,
    IntegrationBindingDelta,
    AtlasRolloutSummaryResponse,
    AtlasGeneratorInfo,
    AtlasNextAction,
    AtlasPermissionRequestModel,
    AtlasProvisioningManifestItem,
    AtlasProposedChanges,
    AtlasReferences,
    AtlasReviewDecision,
    AtlasReviewState,
    AtlasSelectedContext,
    AtlasToolCall,
    AtlasTurnRequest,
    AtlasTurnResponse,
    AtlasValidationCheck,
    AtlasValidationResult,
    AgentMetadataDelta,
    ScenarioDelta,
    ScenarioRouteDelta,
    StepDelta,
)
from .atlas_provisioning import (
    build_provisioning_manifest,
    discovery_result_for_request,
    discovery_result_with_payload,
    is_safe_provisioning_base_url,
)
from .atlas_store import (
    AtlasStore,
    new_atlas_event_id,
    new_atlas_message_id,
    new_atlas_permission_request_id,
    new_atlas_review_decision_id,
)
from .atlas_rollout import build_atlas_rollout_summary
from .observability.metrics import (
    atlas_apply_deltas_total,
    atlas_generator_delta_candidates_total,
    atlas_generator_delta_filtered_total,
    atlas_review_decisions_total,
    safe_observe,
)
from .schemas import Condition, FactRequirement, GuardDef, ResponsePolicy, ToolBinding
from .tools.ingestion import OpenAPIToolIngestionService
from .tools.management import ToolAgentAssignmentStore
from .tools.provider_templates import PROVIDER_TEMPLATES, setup_provider
from .tools.oauth_providers import OAUTH_PROVIDERS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def atlas_delta_payload_hash(delta: Any) -> str:
    """Content address for a proposed delta, stable across status flips.

    Review approvals are pinned to this hash so an approval only authorizes
    the exact content that was reviewed; `status` is excluded because it is
    the field the review lifecycle mutates.
    """
    payload = delta.model_dump(mode="json", exclude={"status"})
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# The eight delta-family attributes on AtlasProposedChanges, in field order.
# Single source of truth: every place that iterates or rebuilds the proposal
# set derives from this tuple, so adding a new family (channel_policy, rule,
# knowledge are already here) is a one-line change instead of ~8 hand-edits.
_DELTA_FAMILY_ATTRS: tuple[str, ...] = (
    "agent_metadata_deltas",
    "scenario_deltas",
    "step_deltas",
    "scenario_route_deltas",
    "channel_policy_deltas",
    "rule_deltas",
    "knowledge_deltas",
    "integration_binding_deltas",
)


def _delta_id() -> str:
    return f"atlas_delta_{uuid4().hex}"


def _attachment_mode(kind: str) -> str:
    if kind == "agent_document_json":
        return "agent_document"
    if kind == "json_brief":
        return "json_brief"
    if kind in {"workflow_description", "document", "transcript"}:
        return "text_extracted"
    return "attachment_bundle"


def _attachment_interpretation(kind: str) -> str:
    if kind == "agent_document_json":
        return "review_as_authored_document"
    if kind in {"json_brief", "workflow_description"}:
        return "review_as_partial_brief"
    return "review_as_reference_only"


def _extract_quoted(text: str) -> str | None:
    quoted = re.search(r'"([^"]+)"', text)
    if quoted:
        return quoted.group(1).strip()
    single = re.search(r"'([^']+)'", text)
    if single:
        return single.group(1).strip()
    return None


def _slug_id(value: str, *, prefix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if slug:
        return f"{prefix}_{slug}"
    return f"{prefix}_{uuid4().hex[:8]}"


def _outcome_condition(event: str, description: str | None) -> dict[str, Any]:
    """Build an ``OutcomeCondition`` dict for a transition or scenario route.

    The condition validator requires ``description`` to be at least 8
    characters (so the LLM has something to evaluate), so we synthesise
    a fallback when the author didn't supply one.
    """
    text = (description or "").strip()
    if len(text) < 8:
        text = f"User triggers the {event.replace('_', ' ')} workflow outcome."
    return {"kind": "outcome", "event": event, "description": text}


def _extract_named_target(
    normalized: str,
    pattern: str,
) -> tuple[str | None, str | None]:
    match = re.search(pattern, normalized, re.IGNORECASE)
    if not match:
        return None, None
    values = [item.strip() for item in match.groups() if item and item.strip()]
    first = values[0] if values else None
    second = values[1] if len(values) > 1 else None
    return first, second


def _slug_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _has_provisioning_intent(message: str | None) -> bool:
    if not message:
        return False
    lower = message.lower()
    # Keep diagnostic provisioning turns read-only unless the user asks Atlas to
    # actually change setup state.
    if re.search(r"\b(review|show|inspect|summari[sz]e|explain)\b", lower) and re.search(
        r"\b(blockers?|issues?|gaps?|status|dependencies)\b",
        lower,
    ):
        return False
    if re.search(r"\bsetup blockers?\b", lower):
        return False
    return any(
        re.search(pattern, lower)
        for pattern in [
            r"\bset up\b",
            r"\bsetup\b",
            r"\bprovision\b",
            r"\bconnect\b",
            r"\bconfigure\b",
            r"\bimport\b",
            r"\bingest\b",
            r"\bre-?auth(?:orize)?\b",
            r"\breauthorize\b",
            r"\brepair\b",
            r"\bfix\b",
            r"\bcreate\b.*\bintegration\b",
        ]
    )


def _auth_type_for_missing_fields(missing_fields: list[str]) -> str:
    if "oauth_authorization" in missing_fields:
        return "oauth2"
    if "api_key" in missing_fields:
        return "api_key"
    if "bearer_token" in missing_fields:
        return "bearer"
    if "basic_auth" in missing_fields:
        return "basic"
    return "none"


def _connection_scope_values(connection: Any) -> list[str]:
    metadata = getattr(connection, "metadata_json", {}) or {}
    metadata_scopes = metadata.get("scopes")
    if isinstance(metadata_scopes, list):
        return [str(item).strip() for item in metadata_scopes if str(item).strip()]
    oauth_token = getattr(connection, "oauth_token_json", {}) or {}
    scope_value = oauth_token.get("scope")
    if isinstance(scope_value, str) and scope_value.strip():
        return [item.strip() for item in scope_value.split() if item.strip()]
    return []


def _provider_default_scopes(provider_slug: str | None) -> list[str]:
    if not provider_slug:
        return []
    config = OAUTH_PROVIDERS.get(provider_slug)
    if config is None:
        return []
    return list(config.default_scopes)


_ATLAS_DELTA_FAMILY_BY_CHANGE_TYPE = {
    "rename_step": "low_risk_updates",
    "rename_scenario": "low_risk_updates",
    "update_step_say": "low_risk_updates",
    "set_step_handoff": "low_risk_updates",
    "set_step_completion": "low_risk_updates",
    "update_response_policy": "low_risk_updates",
    "add_fact_schema_entry": "additive_structure",
    "add_fact_requirement": "additive_structure",
    "add_tool_binding": "additive_structure",
    "add_guard": "additive_structure",
    "add_step_transition": "additive_structure",
    "create_scenario_route": "additive_structure",
    "create_step": "higher_risk_structure",
    "update_step_transition": "higher_risk_structure",
    "update_scenario_route": "higher_risk_structure",
    "update_fact_schema_entry": "higher_risk_structure",
    "reorder_fact_schema_entry": "higher_risk_structure",
    "reorder_step": "higher_risk_structure",
    "provision_provider_template": "provisioning_actions",
    "ingest_openapi_tools": "provisioning_actions",
    "prepare_custom_oauth_connection": "provisioning_actions",
    "bind_existing_connection": "provisioning_actions",
    "reauthorize_connection": "provisioning_actions",
    "repair_connection": "provisioning_actions",
    "delete_step_transition": "destructive",
    "delete_step": "destructive",
    "delete_scenario_route": "destructive",
    "delete_fact_schema_entry": "destructive",
}

_ALL_ATLAS_HEURISTIC_FAMILIES = set(_ATLAS_DELTA_FAMILY_BY_CHANGE_TYPE.values())
# Rollout-readiness constants + aggregation moved to atlas_rollout (AR-5.1d).


def _enabled_atlas_heuristic_families_from_env() -> set[str]:
    raw = (os.getenv("RUHU_ATLAS_HEURISTIC_ENABLED_FAMILIES") or "*").strip()
    if not raw or raw == "*":
        return set(_ALL_ATLAS_HEURISTIC_FAMILIES)
    enabled = {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }
    return {item for item in enabled if item in _ALL_ATLAS_HEURISTIC_FAMILIES}


def _atlas_message_tokens(message: str | None) -> tuple[str, set[str]]:
    normalized = (message or "").strip().lower()
    tokens = re.findall(r"[a-z0-9_'-]+", normalized)
    return normalized, set(tokens)


def _plan_atlas_tool_calls(
    *,
    message: str | None,
    scope: str,
    has_attachments: bool,
    has_api_discovery_requests: bool,
    has_review_decisions: bool,
    has_apply_request: bool,
    has_permission_decisions: bool,
) -> list[dict[str, Any]]:
    if has_review_decisions or has_apply_request or has_permission_decisions:
        return [{"name": "review_change_set", "reason": "User is reviewing or applying Atlas actions."}]

    _normalized, token_set = _atlas_message_tokens(message)
    has_user_work = len(token_set) > 2
    if not has_user_work and not has_attachments and not has_api_discovery_requests:
        return []

    tools: list[dict[str, Any]] = []
    if has_attachments:
        tools.append({"name": "ingest_attachments", "reason": "User attached files for Atlas to inspect."})
    if has_api_discovery_requests:
        tools.append({"name": "discover_api", "reason": "Atlas received explicit API discovery input."})
    if has_user_work:
        tools.append({"name": "inspect_agent", "reason": "User asked a question about the current agent."})
        tools.append({"name": "validate_publish", "reason": "Atlas validates the draft before summarizing."})
    # Provisioning proposals fire from two paths: explicit API-discovery
    # input (URL fetch / pasted schema) AND chat-only intent ("connect
    # HubSpot", "re-authorize the CRM connection", "repair the billing
    # connection"). Without the second branch, a user asking Atlas to
    # bind/reauth/repair an existing connection in plain English never
    # triggers the dependency-binding generator and the turn falls
    # through to a generic ``ready_to_provision`` with no proposed
    # changes — confusing and not what the user requested.
    if scope == "provisioning" and (
        has_api_discovery_requests or _has_provisioning_intent(message)
    ):
        tools.append(
            {
                "name": "propose_provisioning",
                "reason": "Atlas can propose setup from the user's request.",
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in tools:
        if tool["name"] in seen:
            continue
        seen.add(tool["name"])
        deduped.append(tool)
    return deduped


def _completed_tool_calls(tool_calls: list[dict[str, Any]]) -> list[AtlasToolCall]:
    return [
        AtlasToolCall(
            name=str(tool_call.get("name") or ""),
            reason=str(tool_call.get("reason") or "") or None,
            status="completed",
            summary=dict(tool_call.get("summary") or {}),
        )
        for tool_call in tool_calls
        if str(tool_call.get("name") or "")
    ]


def _validation_issue_label(compiled_document, check: AtlasValidationCheck) -> str:
    names: list[str] = []
    for reference_id in check.reference_ids:
        try:
            step = compiled_document.step_by_id(reference_id)
        except KeyError:
            step = None
        if step is not None:
            names.append(f"step '{step.name}'")
            continue
        try:
            scenario = compiled_document.scenario_by_id(reference_id)
        except KeyError:
            scenario = None
        if scenario is not None:
            names.append(f"scenario '{scenario.name}'")
    return names[0] if names else check.scope


class AtlasCoordinator:
    def __init__(
        self,
        *,
        agent_registry,
        atlas_store: AtlasStore,
        tool_runtime=None,
        connection_store=None,
        definition_store=None,
        binding_store=None,
        proposal_generator: AtlasProposalGenerator | None = None,
        docs_parser: AtlasDocsPageParser | None = None,
        conversation_store=None,
        trace_store=None,
    ) -> None:
        self._agent_registry = agent_registry
        self._atlas_store = atlas_store
        self._tool_runtime = tool_runtime
        self._connection_store = connection_store
        self._definition_store = definition_store
        self._binding_store = binding_store
        self._conversation_store = conversation_store
        self._trace_store = trace_store
        self._enabled_heuristic_families = _enabled_atlas_heuristic_families_from_env()
        self._proposal_generator = proposal_generator or AtlasProposalGenerator.from_env(
            fallback_generate=self._generate_proposals_heuristic
        )
        self._docs_parser = docs_parser or AtlasDocsPageParser.from_env()

    def get_session(self, session_id: str, *, organization_id: str | None = None) -> AtlasSession | None:
        return self._atlas_store.get_session(session_id, organization_id=organization_id)

    def resolve_document_and_compiled(self, session: AtlasSession):
        if session.agent_version_id:
            snapshot = self._agent_registry.get_version_snapshot(
                session.agent_version_id,
                organization_id=session.organization_id,
            )
            document = snapshot.agent_document or self._agent_registry.get_agent_document(
                snapshot.agent_id,
                target=snapshot.status,
                organization_id=session.organization_id,
            )
        else:
            document = self._agent_registry.get_agent_document(
                session.agent_id,
                organization_id=session.organization_id,
            )
        return document, compile_agent_document(document)

    def build_references(self, session: AtlasSession, compiled_document) -> AtlasReferences:
        return AtlasReferences(
            agent_ids=[session.agent_id],
            agent_version_ids=[session.agent_version_id] if session.agent_version_id else [],
            scenario_ids=sorted(compiled_document.scenario_ids),
            step_ids=sorted(compiled_document.step_ids),
            conversation_ids=[session.conversation_id] if session.conversation_id else [],
            trace_ids=[session.trace_id] if session.trace_id else [],
            tool_refs=sorted(
                {
                    tool_ref
                    for step in compiled_document.steps
                    for tool_ref in step.available_tool_refs
                }
            ),
        )

    def _append_tool_event(
        self,
        *,
        session: AtlasSession,
        organization_id: str,
        event_type: str,
        tool_name: str,
        reason: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"tool_name": tool_name}
        if reason:
            payload["reason"] = reason
        if summary:
            payload["summary"] = summary
        self._atlas_store.append_event(
            AtlasEvent(
                event_id=new_atlas_event_id(),
                session_id=session.session_id,
                organization_id=organization_id,
                sequence_number=0,
                type=event_type,  # type: ignore[arg-type]
                payload=payload,
                created_at=_utcnow(),
            )
        )

    def _append_tool_activity_events(
        self,
        *,
        session: AtlasSession,
        organization_id: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        for tool_call in tool_calls:
            tool_name = str(tool_call.get("name") or "")
            reason = str(tool_call.get("reason") or "")
            self._append_tool_event(
                session=session,
                organization_id=organization_id,
                event_type="tool_start",
                tool_name=tool_name,
                reason=reason,
            )
            self._append_tool_event(
                session=session,
                organization_id=organization_id,
                event_type="tool_done",
                tool_name=tool_name,
                reason=reason,
            )

    def _tool_dependency_context(self, session: AtlasSession, compiled_document) -> tuple[
        list[str],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        tool_refs = sorted(
            {
                tool_ref
                for step in compiled_document.steps
                for tool_ref in step.available_tool_refs
            }
        )
        if not tool_refs:
            return [], {}, {}, {}, {}
        # The four lookups below are best-effort enrichment for dependency
        # analysis. A failure here degrades the dependency snapshot but should
        # not block the turn — the empty-dict fallback keeps the rest of
        # run_turn working. We log each failure with structured context so
        # ops can spot a failing tool/connection store without grepping
        # through stack traces.
        specs_by_ref: dict[str, Any] = {}
        if self._tool_runtime is not None:
            try:
                specs_by_ref = {
                    spec.ref: spec
                    for spec in self._tool_runtime.list_for_agent(
                        agent_id=session.agent_id,
                        organization_id=session.organization_id,
                    )
                }
            except Exception:
                logger.warning(
                    "atlas.dependency_lookup_failed",
                    extra={
                        "lookup": "tool_runtime.list_for_agent",
                        "agent_id": session.agent_id,
                        "organization_id": session.organization_id,
                    },
                    exc_info=True,
                )
                specs_by_ref = {}
        definition_by_ref: dict[str, Any] = {}
        if self._definition_store is not None and session.organization_id is not None:
            try:
                definition_by_ref = {
                    item.tool_ref: item
                    for item in self._definition_store.list_for_org(session.organization_id)
                }
            except Exception:
                logger.warning(
                    "atlas.dependency_lookup_failed",
                    extra={
                        "lookup": "definition_store.list_for_org",
                        "agent_id": session.agent_id,
                        "organization_id": session.organization_id,
                    },
                    exc_info=True,
                )
                definition_by_ref = {}
        bindings_by_tool_definition_id: dict[str, Any] = {}
        if self._binding_store is not None and session.organization_id is not None:
            try:
                bindings_by_tool_definition_id = {
                    item.tool_definition_id: item
                    for item in self._binding_store.list_for_agent(session.organization_id, session.agent_id)
                }
            except Exception:
                logger.warning(
                    "atlas.dependency_lookup_failed",
                    extra={
                        "lookup": "binding_store.list_for_agent",
                        "agent_id": session.agent_id,
                        "organization_id": session.organization_id,
                    },
                    exc_info=True,
                )
                bindings_by_tool_definition_id = {}
        connections_by_id: dict[str, Any] = {}
        if self._connection_store is not None and session.organization_id is not None:
            try:
                connections_by_id = {
                    item.connection_id: item
                    for item in self._connection_store.list_for_org(session.organization_id)
                }
            except Exception:
                logger.warning(
                    "atlas.dependency_lookup_failed",
                    extra={
                        "lookup": "connection_store.list_for_org",
                        "agent_id": session.agent_id,
                        "organization_id": session.organization_id,
                    },
                    exc_info=True,
                )
                connections_by_id = {}
        return (
            tool_refs,
            specs_by_ref,
            definition_by_ref,
            bindings_by_tool_definition_id,
            connections_by_id,
        )

    def build_dependencies(self, session: AtlasSession, compiled_document) -> list[AtlasDependency]:
        (
            tool_refs,
            specs_by_ref,
            definition_by_ref,
            bindings_by_tool_definition_id,
            connections_by_id,
        ) = self._tool_dependency_context(session, compiled_document)
        return self._build_dependencies_from_context(
            tool_refs=tool_refs,
            specs_by_ref=specs_by_ref,
            definition_by_ref=definition_by_ref,
            bindings_by_tool_definition_id=bindings_by_tool_definition_id,
            connections_by_id=connections_by_id,
        )

    def _build_dependencies_from_context(
        self,
        *,
        tool_refs: list[str],
        specs_by_ref: dict[str, Any],
        definition_by_ref: dict[str, Any],
        bindings_by_tool_definition_id: dict[str, Any],
        connections_by_id: dict[str, Any],
    ) -> list[AtlasDependency]:
        dependencies: list[AtlasDependency] = []
        for tool_ref in tool_refs:
            spec = specs_by_ref.get(tool_ref)
            definition = definition_by_ref.get(tool_ref)
            display_name = (
                getattr(spec, "display_name", None)
                or getattr(definition, "display_name", None)
                or tool_ref
            )
            connection = None
            if definition is not None:
                binding = bindings_by_tool_definition_id.get(definition.tool_definition_id)
                connection_id = binding.connection_id if binding is not None else definition.connection_id
                if connection_id:
                    connection = connections_by_id.get(connection_id)
            if spec is not None and definition is None:
                dependencies.append(
                    AtlasDependency(
                        key=f"tool:{tool_ref}",
                        kind="tool",
                        display_name=display_name,
                        status="available",
                        blocking=False,
                        reference_ids=[tool_ref],
                    )
                )
                continue
            if definition is None:
                dependencies.append(
                    AtlasDependency(
                        key=f"tool:{tool_ref}",
                        kind="tool",
                        display_name=display_name,
                        status="missing",
                        blocking=True,
                        reason="No matching tool definition or builtin runtime tool was found.",
                        suggested_action=f"Create or import a tool definition for '{tool_ref}'.",
                        reference_ids=[tool_ref],
                    )
                )
                continue
            if definition.connection_id is None and bindings_by_tool_definition_id.get(definition.tool_definition_id) is None:
                dependencies.append(
                    AtlasDependency(
                        key=f"tool:{tool_ref}",
                        kind="tool",
                        display_name=display_name,
                        status="configured",
                        blocking=False,
                        reference_ids=[tool_ref, definition.tool_definition_id],
                    )
                )
                continue
            if connection is None:
                dependencies.append(
                    AtlasDependency(
                        key=f"tool:{tool_ref}",
                        kind="tool",
                        display_name=display_name,
                        status="missing",
                        blocking=True,
                        reason="The tool definition exists but no active connection is bound to it.",
                        suggested_action=f"Bind '{tool_ref}' to an API connection before provisioning.",
                        reference_ids=[tool_ref, definition.tool_definition_id],
                    )
                )
                continue
            connection_status = getattr(connection, "status", "")
            if connection_status == "active":
                status = "connected"
                blocking = False
                reason = None
                suggested_action = None
            elif connection_status == "needs_auth":
                status = "requires_auth"
                blocking = True
                reason = "The underlying API connection still needs authentication."
                suggested_action = f"Reconnect or authorize the API connection for '{tool_ref}'."
            else:
                status = "invalid"
                blocking = True
                reason = f"The underlying API connection is not ready (status: {connection_status or 'unknown'})."
                suggested_action = f"Repair the API connection for '{tool_ref}' before provisioning."
            dependencies.append(
                AtlasDependency(
                    key=f"tool:{tool_ref}",
                    kind="tool",
                    display_name=display_name,
                    status=status,  # type: ignore[arg-type]
                    blocking=blocking,
                    reason=reason,
                    suggested_action=suggested_action,
                    reference_ids=[
                        item
                        for item in [tool_ref, definition.tool_definition_id, getattr(connection, "connection_id", None)]
                        if item
                    ],
                )
            )
        return dependencies

    def build_validation(self, document) -> AtlasValidationResult:
        report = validate_agent_document(document)
        checks = [
            AtlasValidationCheck(
                code=issue.code,
                scope="step" if issue.step_id else "scenario" if issue.scenario_id else "agent",
                status="failed" if issue.severity == "error" else "warning",
                message=issue.message,
                reference_ids=[
                    item
                    for item in [issue.scenario_id, issue.step_id, issue.transition_id, issue.route_id, issue.tool_ref]
                    if item
                ],
            )
            for issue in report.issues
        ]
        return AtlasValidationResult(
            status="failed" if not report.valid else "passed",
            blocking=report.error_count > 0,
            errors=[issue.message for issue in report.issues if issue.severity == "error"],
            warnings=[issue.message for issue in report.issues if issue.severity == "warning"],
            checks=checks,
        )

    def build_blockers(self, validation: AtlasValidationResult) -> list[AtlasBlocker]:
        return [
            AtlasBlocker(
                code=check.code,
                message=check.message,
                blocking=check.status == "failed",
                reference_ids=check.reference_ids,
            )
            for check in validation.checks
            if check.status in {"failed", "warning"}
        ]

    def _sanitize_delta_dependencies(
        self,
        proposed_changes: AtlasProposedChanges,
    ) -> tuple[AtlasProposedChanges, list[AtlasBlocker]]:
        """Drop deltas with unknown or cyclic dependencies, as blockers.

        Generated output is untrusted: a model can hallucinate a
        depends_on_delta_ids entry or emit a cycle. Those must become
        reviewable blockers, never an exception that aborts the turn.
        """
        all_deltas = self._delta_map(proposed_changes)
        blockers: list[AtlasBlocker] = []
        removed: set[str] = set()

        while True:
            # Cascade removal: a delta whose dependency is unknown (or was
            # itself removed) cannot be ordered or applied.
            changed = True
            while changed:
                changed = False
                for delta_id, delta in all_deltas.items():
                    if delta_id in removed:
                        continue
                    bad = [
                        dependency_id
                        for dependency_id in (getattr(delta, "depends_on_delta_ids", []) or [])
                        if dependency_id not in all_deltas or dependency_id in removed
                    ]
                    if bad:
                        removed.add(delta_id)
                        blockers.append(
                            AtlasBlocker(
                                code="atlas.unknown_delta_dependency",
                                message=(
                                    f"{getattr(delta, 'change_type', type(delta).__name__)}: depends on "
                                    f"unknown or rejected delta(s) {', '.join(bad)}"
                                ),
                                blocking=True,
                                reference_ids=[delta_id],
                            )
                        )
                        changed = True

            # Cycle removal: attempt a topological order over the survivors;
            # each detected cycle removes the flagged delta, then dependents of
            # the removal are re-cascaded on the next pass.
            remaining = [delta_id for delta_id in all_deltas if delta_id not in removed]
            remaining_map = {delta_id: all_deltas[delta_id] for delta_id in remaining}
            try:
                self._ordered_delta_ids_for_apply(remaining, remaining_map)
                break
            except ValueError as exc:
                match = re.search(r"cycle detected at '([^']+)'", str(exc))
                if match is None:
                    raise
                cycle_id = match.group(1)
                removed.add(cycle_id)
                blockers.append(
                    AtlasBlocker(
                        code="atlas.delta_dependency_cycle",
                        message=(
                            f"{getattr(all_deltas[cycle_id], 'change_type', '')}: part of a dependency "
                            "cycle and cannot be applied"
                        ),
                        blocking=True,
                        reference_ids=[cycle_id],
                    )
                )

        if not removed:
            return proposed_changes, []
        for delta_id in removed:
            safe_observe(
                "atlas_generator_delta_filtered_total",
                atlas_generator_delta_filtered_total.labels(
                    family=self._delta_family_for_change_type(
                        getattr(all_deltas[delta_id], "change_type", "other")
                    ),
                    reason="invalid_dependency",
                ).inc,
            )
        kept = [delta_id for delta_id in all_deltas if delta_id not in removed]
        return self._selected_proposed_changes(proposed_changes, kept), blockers

    def validate_proposed_changes(
        self,
        *,
        document: AgentDocument,
        proposed_changes: AtlasProposedChanges,
    ) -> tuple[AtlasProposedChanges, list[AtlasBlocker]]:
        proposed_changes, dependency_blockers = self._sanitize_delta_dependencies(proposed_changes)
        all_deltas = self._delta_map(proposed_changes)
        valid_ids: set[str] = set()
        blockers: list[AtlasBlocker] = list(dependency_blockers)
        for delta_id in self._ordered_delta_ids_for_apply(self._all_delta_ids(proposed_changes), all_deltas):
            delta = all_deltas[delta_id]
            candidate = self._selected_proposed_changes(proposed_changes, [*valid_ids, delta_id])
            try:
                candidate_document = document.model_copy(deep=True)
                for ordered_id in self._ordered_delta_ids_for_apply(self._all_delta_ids(candidate), self._delta_map(candidate)):
                    candidate_document = self._apply_delta(candidate_document, self._delta_map(candidate)[ordered_id])
                validation = self.build_validation(candidate_document)
                if validation.blocking:
                    raise ValueError("; ".join(validation.errors) or "delta would leave the draft invalid")
                compile_agent_document(candidate_document)
                valid_ids.add(delta_id)
            except Exception as exc:
                safe_observe(
                    "atlas_generator_delta_filtered_total",
                    atlas_generator_delta_filtered_total.labels(
                        family=self._delta_family_for_change_type(getattr(delta, "change_type", "other")),
                        reason="semantic_validation",
                    ).inc,
                )
                blockers.append(
                    AtlasBlocker(
                        code="atlas.invalid_proposed_change",
                        message=f"{getattr(delta, 'change_type', type(delta).__name__)}: {exc}",
                        blocking=True,
                        reference_ids=[delta_id],
                    )
                )
        return self._selected_proposed_changes(proposed_changes, list(valid_ids)), blockers

    def build_review_state(
        self,
        session: AtlasSession,
        *,
        proposed_changes: AtlasProposedChanges | None = None,
    ) -> AtlasReviewState:
        effective_changes = proposed_changes or self._atlas_store.load_proposed_changes(
            session.session_id,
            organization_id=session.organization_id,
        )
        latest_apply = self._atlas_store.latest_apply_request(
            session.session_id,
            organization_id=session.organization_id,
        )
        approved = self._delta_ids_with_status(effective_changes, "approved")
        rejected = self._delta_ids_with_status(effective_changes, "rejected")
        applied = set(self._delta_ids_with_status(effective_changes, "applied"))
        current_ids = self._all_delta_ids(effective_changes)
        pending = [
            item
            for item in current_ids
            if item not in set(approved) and item not in set(rejected) and item not in applied
        ]
        return AtlasReviewState(
            approved_delta_ids=approved,
            rejected_delta_ids=rejected,
            pending_delta_ids=pending,
            latest_apply_request_id=latest_apply.apply_request_id if latest_apply is not None else None,
        )

    def permission_models(self, session: AtlasSession) -> list[AtlasPermissionRequestModel]:
        pending = self._atlas_store.list_permission_requests(
            session.session_id,
            organization_id=session.organization_id,
            status="pending",
        )
        return [
            AtlasPermissionRequestModel(
                request_id=item.request_id,
                kind=item.kind,
                status=item.status,
                reason=item.reason,
                risk_summary=item.risk_summary,
                scope_ref=item.scope_ref,
                delta_ids=item.delta_ids,
                requested_actions=item.requested_actions,
                created_at=item.created_at,
                expires_at=item.expires_at,
            )
            for item in pending
        ]

    def next_action_for(
        self,
        *,
        session: AtlasSession,
        validation: AtlasValidationResult,
        provisioning_manifest: list[AtlasProvisioningManifestItem],
        pending_permissions: list[AtlasPermissionRequestModel],
        attachment_results: list[AtlasAttachmentIngestionResult],
        dependencies: list[AtlasDependency],
        proposed_changes: AtlasProposedChanges,
        questions: list[BlockingQuestion] | None = None,
    ) -> AtlasNextAction:
        if pending_permissions:
            return "blocked"
        if questions or any(item.blocking_questions for item in attachment_results):
            return "ask_questions"
        # AR-3.1: only deltas that still need user action keep the session in
        # review. Applied/rejected deltas are retained in the store for audit
        # but are terminal — once every delta is applied or rejected the
        # session must progress past `ready_to_review_changes`.
        if self._actionable_delta_ids(proposed_changes):
            return "ready_to_review_changes"
        if session.scope == "provisioning" and (dependencies or provisioning_manifest):
            return "ready_to_provision"
        if session.scope == "validation" or validation.blocking:
            return "ready_to_validate"
        return "complete"

    def assistant_summary(
        self,
        *,
        session: AtlasSession,
        tool_calls: list[dict[str, Any]],
        request_message: str | None,
        compiled_document,
        validation: AtlasValidationResult,
        attachment_results: list[AtlasAttachmentIngestionResult],
        pending_permissions: list[AtlasPermissionRequestModel],
        proposed_changes: AtlasProposedChanges,
        assistant_rationale: str | None = None,
    ) -> str:
        # Prefer the LLM-authored rationale when the generator returned one.
        # The rationale is a conversational reply written for the chat surface;
        # the templated branches below remain as fallbacks for the
        # no-generator (fallback mode) path and for early-exit turns where the
        # generator was never invoked.
        if assistant_rationale and assistant_rationale.strip():
            return assistant_rationale.strip()
        delta_count = len(self._all_delta_ids(proposed_changes))
        if (
            not tool_calls
            and delta_count == 0
            and not validation.blocking
            and not validation.warnings
            and not attachment_results
            and not pending_permissions
        ):
            return "Hi, I'm here. Tell me what you'd like to change, review, or connect in this agent."

        if validation.checks and delta_count == 0:
            failed_checks = [check for check in validation.checks if check.status == "failed"]
            warning_checks = [check for check in validation.checks if check.status == "warning"]
            parts = [
                f"I reviewed the agent and found {len(failed_checks)} blocker(s) and {len(warning_checks)} warning(s)."
            ]
            for check in [*failed_checks, *warning_checks][:6]:
                severity = "Blocker" if check.status == "failed" else "Warning"
                label = _validation_issue_label(compiled_document, check)
                parts.append(f"{severity}: {label} - {check.message}")
            remaining = len(failed_checks) + len(warning_checks) - 6
            if remaining > 0:
                parts.append(f"{remaining} more issue(s) are available in the validation details.")
            return " ".join(parts)

        if "validate_publish" in {str(item.get("name") or "") for item in tool_calls} and delta_count == 0:
            scenario, step = self._safe_summary_focus(compiled_document, session)
            return (
                "I reviewed the agent and found no validation blockers or warnings. "
                f"Current focus is scenario '{scenario.name}' and step '{step.name}'."
            )

        scenario, step = self._safe_summary_focus(compiled_document, session)
        flags = step_capability_flags(step)
        capabilities: list[str] = []
        if flags["collects_missing_details"]:
            capabilities.append("asks for required details")
        if flags["uses_tooling"]:
            capabilities.append("can use tools or side effects")
        if flags["hands_off"]:
            capabilities.append("can hand off")
        if flags["completes"]:
            capabilities.append("can complete the interaction")
        capability_text = ", ".join(capabilities) if capabilities else "responds in place"
        parts = [
            f"Atlas reviewed the agent document and is focused on scenario '{scenario.name}' and step '{step.name}'.",
            f"This step {capability_text}.",
        ]
        if delta_count:
            parts.append(f"Atlas proposed {delta_count} authored change(s) for review.")
        if validation.blocking:
            parts.append(f"Validation currently has {len(validation.errors)} blocking issue(s).")
        elif validation.warnings:
            parts.append(f"Validation has {len(validation.warnings)} warning(s).")
        if attachment_results:
            parts.append(f"Ingested {len(attachment_results)} attachment(s) for review.")
        if pending_permissions:
            parts.append("Atlas is waiting on permission before it can continue.")
        return " ".join(parts)

    def _safe_summary_focus(self, compiled_document, session: AtlasSession):
        """Resolve the (scenario, step) focus for the summary, tolerating a
        stale selection.

        `assistant_summary` runs on the read path (`GET /sessions/{id}/state`)
        with the raw stored `session.scenario_id`/`step_id`; if the draft was
        edited so those IDs no longer exist, `scenario_by_id`/`step_by_id`
        raise `KeyError` and 500 the endpoint (AR-3.2). Fall back to the
        document's start scenario/step instead.
        """
        scenario_id = session.scenario_id or compiled_document.start_scenario_id
        step_id = session.step_id or compiled_document.start_step_id
        try:
            scenario = compiled_document.scenario_by_id(scenario_id)
        except KeyError:
            scenario = compiled_document.scenario_by_id(compiled_document.start_scenario_id)
        try:
            step = compiled_document.step_by_id(step_id)
        except KeyError:
            step = compiled_document.step_by_id(compiled_document.start_step_id)
        return scenario, step

    def build_attachment_results(self, attachments: list[Any]) -> list[AtlasAttachmentIngestionResult]:
        # AR-2.4: Atlas does not yet run real ingestion (extraction/chunking),
        # so the numeric/quality telemetry on the request is a CLIENT claim,
        # not a server measurement. Echo it for display but label it
        # client-asserted in provenance, and never let client-supplied
        # blocking_questions drive server control flow (next_action) — those
        # are reserved for questions the server actually computed.
        return [
            AtlasAttachmentIngestionResult(
                attachment_id=item.attachment_id,
                display_name=item.display_name,
                kind=item.kind,
                mode=_attachment_mode(item.kind),  # type: ignore[arg-type]
                extracted_characters=int(item.metadata.get("extracted_characters", 0) or 0),
                chunk_count=int(item.metadata.get("chunk_count", 0) or 0),
                used_chunk_count=int(item.metadata.get("used_chunk_count", 0) or 0),
                quality_flags=list(item.metadata.get("quality_flags", [])),
                provenance={
                    "telemetry_source": "client_asserted",
                    **({"source_url": item.source_url} if item.source_url else {}),
                    **{k: v for k, v in item.metadata.items() if k in {"filename", "mime_type", "source"}},
                },
                truncated=bool(item.metadata.get("truncated", False)),
                suggested_interpretation=_attachment_interpretation(item.kind),  # type: ignore[arg-type]
                blocking_questions=[],
            )
            for item in attachments
        ]

    def build_api_discovery_results(self, requests: list[Any]) -> list[AtlasAPIDiscoveryResult]:
        return [
            discovery_result_for_request(item, docs_parser=self._docs_parser)
            for item in requests
        ]

    def _discover_api_results_with_payloads(
        self, requests: list[Any]
    ) -> tuple[list[AtlasAPIDiscoveryResult], dict[str, dict[str, Any] | None]]:
        """Run discovery once per request, capturing each parsed spec payload.

        The payload map (keyed by request_id) lets provisioning reuse the
        exact spec the human-reviewed result was derived from, so the
        reviewed candidate list and the ingested spec can never diverge
        (no second fetch — AR-2.1 TOCTOU fix).
        """
        results: list[AtlasAPIDiscoveryResult] = []
        payloads_by_request_id: dict[str, dict[str, Any] | None] = {}
        for item in requests:
            result, payload = discovery_result_with_payload(item, docs_parser=self._docs_parser)
            results.append(result)
            payloads_by_request_id[result.request_id] = payload
        return results, payloads_by_request_id

    def _provider_slug_for_definition(self, definition: Any) -> str | None:
        metadata = getattr(definition, "metadata_json", None)
        if isinstance(metadata, dict):
            template_slug = str(metadata.get("template_slug") or "").strip()
            if template_slug:
                return template_slug
        return None

    def _generate_dependency_provisioning_proposals(
        self,
        *,
        session: AtlasSession,
        message: str | None,
        dependencies: list[AtlasDependency],
        definition_by_ref: dict[str, Any],
        bindings_by_tool_definition_id: dict[str, Any],
        connections_by_id: dict[str, Any],
    ) -> AtlasProposedChanges:
        if session.scope != "provisioning" or not _has_provisioning_intent(message):
            return AtlasProposedChanges()
        proposed = AtlasProposedChanges()
        created_actions: set[tuple[str, str]] = set()
        active_connections_by_provider: dict[str, list[Any]] = {}
        for connection in connections_by_id.values():
            provider = str(getattr(connection, "provider", "") or "").strip()
            if not provider:
                continue
            active_connections_by_provider.setdefault(provider, []).append(connection)

        for dependency in dependencies:
            if dependency.kind != "tool":
                continue
            tool_ref = dependency.reference_ids[0] if dependency.reference_ids else None
            if not tool_ref:
                continue
            definition = definition_by_ref.get(tool_ref)
            provider_slug = self._provider_slug_for_definition(definition) if definition is not None else None
            binding = (
                bindings_by_tool_definition_id.get(definition.tool_definition_id)
                if definition is not None
                else None
            )
            connection_id = (
                binding.connection_id
                if binding is not None
                else getattr(definition, "connection_id", None)
            )
            connection = connections_by_id.get(connection_id) if connection_id else None

            if dependency.status == "configured" and definition is not None and provider_slug:
                candidate_connections = active_connections_by_provider.get(provider_slug, [])
                candidate = next(
                    (item for item in candidate_connections if getattr(item, "status", None) == "active"),
                    None,
                )
                if candidate is None:
                    continue
                action_key = ("bind_existing_connection", tool_ref)
                if action_key in created_actions:
                    continue
                created_actions.add(action_key)
                proposed.integration_binding_deltas.append(
                    IntegrationBindingDelta(
                        target_id=session.agent_id,
                        delta_id=f"delta_bind_existing_{uuid4().hex[:8]}",
                        operation="update",
                        change_type="bind_existing_connection",
                        payload={
                            "tool_ref": tool_ref,
                            "tool_definition_id": definition.tool_definition_id,
                            "provider_slug": provider_slug,
                            "connection_id": candidate.connection_id,
                            "connection_display_name": getattr(candidate, "display_name", None),
                            "connection_status": getattr(candidate, "status", None),
                            "connection_auth_type": getattr(candidate, "auth_type", None),
                            "connection_base_url": getattr(candidate, "base_url", None),
                            "setup_url": f"/settings/integrations?connection_id={candidate.connection_id}",
                            "available_scopes": _connection_scope_values(candidate),
                            "required_scopes": _provider_default_scopes(provider_slug),
                        },
                        summary=f"Bind the existing {provider_slug} connection to '{tool_ref}' for this agent.",
                    )
                )
            elif dependency.status == "requires_auth" and connection is not None:
                action_key = ("reauthorize_connection", connection.connection_id)
                if action_key in created_actions:
                    continue
                created_actions.add(action_key)
                proposed.integration_binding_deltas.append(
                    IntegrationBindingDelta(
                        target_id=session.agent_id,
                        delta_id=f"delta_reauth_{uuid4().hex[:8]}",
                        operation="update",
                        change_type="reauthorize_connection",
                        payload={
                            "connection_id": connection.connection_id,
                            "provider_slug": getattr(connection, "provider", None),
                            "tool_ref": tool_ref,
                            "connection_display_name": getattr(connection, "display_name", None),
                            "connection_status": getattr(connection, "status", None),
                            "connection_auth_type": getattr(connection, "auth_type", None),
                            "connection_base_url": getattr(connection, "base_url", None),
                            "setup_url": f"/settings/integrations?connection_id={connection.connection_id}",
                            "available_scopes": _connection_scope_values(connection),
                            "required_scopes": _provider_default_scopes(getattr(connection, "provider", None)),
                        },
                        summary=f"Prepare the existing connection for re-authorization before Atlas continues provisioning '{tool_ref}'.",
                    )
                )
            elif dependency.status == "invalid" and connection is not None:
                action_key = ("repair_connection", connection.connection_id)
                if action_key in created_actions:
                    continue
                created_actions.add(action_key)
                proposed.integration_binding_deltas.append(
                    IntegrationBindingDelta(
                        target_id=session.agent_id,
                        delta_id=f"delta_repair_{uuid4().hex[:8]}",
                        operation="update",
                        change_type="repair_connection",
                        payload={
                            "connection_id": connection.connection_id,
                            "provider_slug": getattr(connection, "provider", None),
                            "tool_ref": tool_ref,
                            "connection_display_name": getattr(connection, "display_name", None),
                            "connection_status": getattr(connection, "status", None),
                            "connection_auth_type": getattr(connection, "auth_type", None),
                            "connection_base_url": getattr(connection, "base_url", None),
                            "setup_url": f"/settings/integrations?connection_id={connection.connection_id}",
                            "available_scopes": _connection_scope_values(connection),
                            "required_scopes": _provider_default_scopes(getattr(connection, "provider", None)),
                        },
                        summary=f"Record a reviewed repair action for the existing connection used by '{tool_ref}'.",
                    )
                )
        return proposed

    def _merge_proposed_changes(
        self,
        left: AtlasProposedChanges,
        right: AtlasProposedChanges,
    ) -> AtlasProposedChanges:
        return AtlasProposedChanges(
            **{
                attr: [*getattr(left, attr), *getattr(right, attr)]
                for attr in _DELTA_FAMILY_ATTRS
            }
        )

    def _generate_provisioning_proposals(
        self,
        *,
        session: AtlasSession,
        message: str | None,
        api_discovery_requests: list[Any],
        api_discovery_results: list[AtlasAPIDiscoveryResult],
        spec_payloads_by_request_id: dict[str, dict[str, Any] | None] | None = None,
    ) -> AtlasProposedChanges:
        if session.scope != "provisioning" or not _has_provisioning_intent(message):
            return AtlasProposedChanges()
        proposed = AtlasProposedChanges()
        created_provider_actions: set[str] = set()
        for request, result in zip(api_discovery_requests, api_discovery_results):
            if result.status != "discovered":
                continue
            if result.spec_type not in {"openapi", "swagger"}:
                continue
            if not result.candidate_tool_refs:
                continue
            # AR-2.2: the base_url is attacker-influenceable (spec servers[]
            # / LLM extraction). Refuse to emit a provisioning delta whose
            # connection base targets an internal address.
            if not is_safe_provisioning_base_url(result.base_url):
                logger.warning(
                    "atlas skipped provisioning proposal with unsafe base_url",
                    extra={"request_id": result.request_id},
                )
                continue
            first_candidate = result.provisioning_candidates[0] if result.provisioning_candidates else None
            provider_slug = first_candidate.provider_slug if first_candidate is not None else None
            if provider_slug in PROVIDER_TEMPLATES and provider_slug not in {"custom_api", "custom_oauth"}:
                if provider_slug in created_provider_actions:
                    continue
                created_provider_actions.add(provider_slug)
                template = PROVIDER_TEMPLATES[provider_slug]
                proposed.integration_binding_deltas.append(
                    IntegrationBindingDelta(
                        target_id=session.agent_id,
                        delta_id=f"delta_provision_{provider_slug}_{uuid4().hex[:8]}",
                        operation="create",
                        change_type="provision_provider_template",
                        payload={
                            "provider_slug": provider_slug,
                            "display_name": result.provider_name or template.display_name,
                            "base_url": result.base_url,
                            "tool_refs": list(result.candidate_tool_refs[:25]),
                            "source_request_id": request.request_id,
                            "setup_url": first_candidate.setup_url if first_candidate is not None else None,
                            "documentation_url": first_candidate.documentation_url if first_candidate is not None else None,
                            "required_scopes": list(template.oauth_config.default_scopes) if template.oauth_config is not None else [],
                            "missing_fields": list(result.missing_auth_fields),
                        },
                        summary=f"Set up the {template.display_name} integration and assign its starter tools to the agent.",
                    )
                )
                continue
            # Reuse the spec captured at discovery time — never re-fetch
            # (AR-2.1): a second fetch could return different bytes than the
            # human reviewed.
            spec = (spec_payloads_by_request_id or {}).get(result.request_id)
            if spec is None:
                continue
            auth_type = _auth_type_for_missing_fields(result.missing_auth_fields)
            if auth_type == "oauth2":
                proposed.integration_binding_deltas.append(
                    IntegrationBindingDelta(
                        target_id=session.agent_id,
                        delta_id=f"delta_custom_oauth_{uuid4().hex[:8]}",
                        operation="create",
                        change_type="prepare_custom_oauth_connection",
                        payload={
                            "display_name": result.provider_name or "Custom OAuth API",
                            "base_url": result.base_url,
                            "source_request_id": request.request_id,
                            "setup_url": first_candidate.setup_url if first_candidate is not None else None,
                            "documentation_url": request.source_value if request.source_value.startswith(("http://", "https://")) else None,
                            "required_scopes": _provider_default_scopes(provider_slug),
                            "missing_fields": list(result.missing_auth_fields),
                        },
                        summary=f"Prepare a reviewed custom OAuth connection scaffold for {result.provider_name or 'the discovered API'}.",
                    )
                )
                continue
            provider = provider_slug or "openapi"
            display_name = result.provider_name or "Imported API"
            tool_ref_prefix = _slug_token(provider_slug or display_name)
            proposed.integration_binding_deltas.append(
                IntegrationBindingDelta(
                    target_id=session.agent_id,
                    delta_id=f"delta_ingest_openapi_{uuid4().hex[:8]}",
                    operation="create",
                    change_type="ingest_openapi_tools",
                    payload={
                        "provider": provider,
                        "display_name": display_name,
                        "auth_type": auth_type,
                        "base_url": result.base_url,
                        "tool_ref_prefix": tool_ref_prefix or None,
                        "spec": spec,
                        "source_request_id": request.request_id,
                        "setup_url": first_candidate.setup_url if first_candidate is not None else None,
                        "documentation_url": first_candidate.documentation_url if first_candidate is not None else None,
                        "missing_fields": list(result.missing_auth_fields),
                    },
                    summary=f"Create a reviewed API connection for {display_name} and import its discovered operations as agent tools.",
                )
            )
        return proposed

    def rollout_summary(self) -> AtlasRolloutSummaryResponse:
        # AR-5.1d: the aggregation lives in atlas_rollout; the coordinator
        # only supplies which heuristic families are enabled.
        return build_atlas_rollout_summary(
            enabled_heuristic_families=self._enabled_heuristic_families
        )

    def _delta_family_for_change_type(self, change_type: str) -> str:
        return _ATLAS_DELTA_FAMILY_BY_CHANGE_TYPE.get(change_type, "other")

    def _observe_generated_delta_families(
        self,
        proposed_changes: AtlasProposedChanges,
        *,
        mode: str,
    ) -> None:
        for attr in _DELTA_FAMILY_ATTRS:
            for item in getattr(proposed_changes, attr):
                family = self._delta_family_for_change_type(getattr(item, "change_type", "other"))
                safe_observe(
                    "atlas_generator_delta_candidates_total",
                    atlas_generator_delta_candidates_total.labels(mode=mode, family=family).inc,
                )

    def _observe_review_decisions(
        self,
        *,
        proposed_changes: AtlasProposedChanges,
        decisions: list[AtlasReviewDecision],
    ) -> None:
        all_deltas = self._delta_map(proposed_changes)
        for item in decisions:
            delta = all_deltas.get(item.delta_id)
            if delta is None:
                continue
            family = self._delta_family_for_change_type(getattr(delta, "change_type", "other"))
            safe_observe(
                "atlas_review_decisions_total",
                atlas_review_decisions_total.labels(family=family, decision=item.decision).inc,
            )

    def observe_apply_outcome(
        self,
        *,
        session: AtlasSession,
        delta_ids: list[str],
        organization_id: str,
        outcome: str,
    ) -> None:
        proposed_changes = self._atlas_store.load_proposed_changes(
            session.session_id,
            organization_id=organization_id,
        )
        all_deltas = self._delta_map(proposed_changes)
        for delta_id in delta_ids:
            delta = all_deltas.get(delta_id)
            if delta is None:
                continue
            family = self._delta_family_for_change_type(getattr(delta, "change_type", "other"))
            safe_observe(
                "atlas_apply_deltas_total",
                atlas_apply_deltas_total.labels(family=family, outcome=outcome).inc,
            )

    def _filtered_heuristic_proposed_changes(
        self,
        proposed_changes: AtlasProposedChanges,
    ) -> AtlasProposedChanges:
        if self._enabled_heuristic_families == _ALL_ATLAS_HEURISTIC_FAMILIES:
            return proposed_changes

        filtered = AtlasProposedChanges()
        for attr in _DELTA_FAMILY_ATTRS:
            kept = []
            for item in getattr(proposed_changes, attr):
                family = self._delta_family_for_change_type(getattr(item, "change_type", "other"))
                if family in self._enabled_heuristic_families:
                    kept.append(item)
                    continue
                safe_observe(
                    "atlas_generator_delta_filtered_total",
                    atlas_generator_delta_filtered_total.labels(
                        family=family,
                        reason="heuristic_family_disabled",
                    ).inc,
                )
            setattr(filtered, attr, kept)
        return filtered

    def resolve_selection(
        self,
        session: AtlasSession,
        compiled_document,
        selected_context: AtlasSelectedContext | None,
    ) -> tuple[str, str]:
        requested_step_id = selected_context.step_id if selected_context else None
        requested_scenario_id = selected_context.scenario_id if selected_context else None

        step_id = requested_step_id or session.step_id or compiled_document.start_step_id
        if step_id not in compiled_document.step_ids:
            step_id = compiled_document.start_step_id

        derived_scenario_id = compiled_document.scenario_for_step_id(step_id).id
        scenario_id = requested_scenario_id or session.scenario_id or derived_scenario_id
        if scenario_id not in compiled_document.scenario_ids:
            scenario_id = derived_scenario_id
        if compiled_document.scenario_for_step_id(step_id).id != scenario_id:
            scenario_id = derived_scenario_id
        return scenario_id, step_id

    def _build_generator_context(
        self,
        *,
        session: AtlasSession,
        compiled_document,
        message: str | None,
        selected_scenario_id: str,
        selected_step_id: str,
    ) -> AtlasGeneratorContext:
        scenario = compiled_document.scenario_by_id(selected_scenario_id)
        step = compiled_document.step_by_id(selected_step_id)
        document = compiled_document.document
        authored_step = next(
            (
                item
                for authored_scenario in document.scenarios
                for item in authored_scenario.steps
                if item.id == selected_step_id
            ),
            None,
        )
        selected_step_detail: dict[str, Any] = {}
        if authored_step is not None:
            selected_step_detail = authored_step.model_dump(
                mode="json",
                include={
                    "id",
                    "name",
                    "say",
                    "guards",
                    "fact_requirements",
                    "tool_policy",
                    "transitions",
                    "completion",
                    "handoff",
                },
            )
        validation = self.build_validation(document)
        stored_changes = self._atlas_store.load_proposed_changes(
            session.session_id,
            organization_id=session.organization_id,
        )
        prior_delta_summaries = [
            f"[{getattr(delta, 'status', 'proposed')}] {getattr(delta, 'summary', '')}".strip()
            for delta in self._delta_map(stored_changes).values()
            if getattr(delta, "summary", "")
        ][:20]
        recent_message_rows, _total, _has_more = self._atlas_store.list_messages(
            session.session_id,
            organization_id=session.organization_id,
            limit=8,
        )
        recent_messages = [{"role": item.role, "content": item.content} for item in recent_message_rows]
        return AtlasGeneratorContext(
            agent_id=session.agent_id,
            scope=session.scope,
            user_message=message,
            selected_scenario_id=selected_scenario_id,
            selected_scenario_name=scenario.name,
            selected_step_id=selected_step_id,
            selected_step_name=step.name,
            scenario_ids=sorted(compiled_document.scenario_ids),
            step_ids=sorted(compiled_document.step_ids),
            fact_names=sorted(item.name for item in document.fact_schema),
            tool_refs=sorted(
                {
                    tool_ref
                    for compiled_step in compiled_document.steps
                    for tool_ref in compiled_step.available_tool_refs
                }
            ),
            selected_step_detail=selected_step_detail,
            validation_errors=list(validation.errors),
            validation_warnings=list(validation.warnings),
            prior_delta_summaries=prior_delta_summaries,
            recent_messages=recent_messages,
        )

    def _generate_proposals_heuristic(
        self,
        context: AtlasGeneratorContext,
        compiled_document: Any,
    ) -> AtlasProposedChanges:
        if compiled_document is None:
            raise ValueError("heuristic proposal generation requires the compiled document for this turn")
        session = type("SessionLike", (), {"agent_id": context.agent_id})()
        message = context.user_message
        selected_scenario_id = context.selected_scenario_id or compiled_document.start_scenario_id
        selected_step_id = context.selected_step_id or compiled_document.start_step_id
        if not message:
            return AtlasProposedChanges()

        normalized = message.strip()
        lower = normalized.lower()
        proposed = AtlasProposedChanges()
        selected_step = compiled_document.step_by_id(selected_step_id)
        selected_scenario = compiled_document.scenario_by_id(selected_scenario_id)
        existing_fact_names = {item.name for item in compiled_document.document.fact_schema}
        created_fact_delta_ids: dict[str, str] = {}

        rename_step_match = re.search(r"\brename (?:this )?step to\b", lower)
        if rename_step_match:
            new_name = _extract_quoted(normalized) or normalized[rename_step_match.end() :].strip(" .")
            if new_name:
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        step_id=selected_step_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="rename_step",
                        payload={"name": new_name},
                        summary=f"Rename step '{selected_step_id}' to '{new_name}'.",
                    )
                )

        rename_scenario_match = re.search(r"\brename (?:this )?scenario to\b", lower)
        if rename_scenario_match:
            new_name = _extract_quoted(normalized) or normalized[rename_scenario_match.end() :].strip(" .")
            if new_name:
                proposed.scenario_deltas.append(
                    ScenarioDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="rename_scenario",
                        payload={"name": new_name},
                        summary=f"Rename scenario '{selected_scenario_id}' to '{new_name}'.",
                    )
                )

        say_match = re.search(r"\b(?:change|update|make) (?:this |the )?(?:step )?say(?: to)?\b", lower)
        if say_match:
            say_slice = normalized[say_match.start() :]
            say_text = _extract_quoted(say_slice) or normalized[say_match.end() :].strip(" .")
            if say_text:
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        step_id=selected_step_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="update_step_say",
                        payload={"say": say_text},
                        summary=f"Update what step '{selected_step_id}' says.",
                    )
                )

        handoff_match = re.search(r"\bhandoff to ([a-zA-Z0-9_:\\-]+)", normalized, re.IGNORECASE)
        if handoff_match:
            target = handoff_match.group(1).strip()
            proposed.step_deltas.append(
                StepDelta(
                    agent_id=session.agent_id,
                    scenario_id=selected_scenario_id,
                    step_id=selected_step_id,
                    delta_id=_delta_id(),
                    operation="update",
                    change_type="set_step_handoff",
                    payload={"handoff": {"target_type": "agent", "target": target}},
                    summary=f"Configure step '{selected_step_id}' to hand off to '{target}'.",
                )
            )

        completion_match = re.search(r"\bmark (?:this )?step complete\b", lower)
        if completion_match:
            disposition_match = re.search(r"\bdisposition ([a-zA-Z0-9_\\-]+)", normalized, re.IGNORECASE)
            disposition = disposition_match.group(1).strip() if disposition_match else "resolved"
            proposed.step_deltas.append(
                StepDelta(
                    agent_id=session.agent_id,
                    scenario_id=selected_scenario_id,
                    step_id=selected_step_id,
                    delta_id=_delta_id(),
                    operation="update",
                    change_type="set_step_completion",
                    payload={"completion": {"disposition": disposition}},
                    summary=f"Mark step '{selected_step_id}' as complete with disposition '{disposition}'.",
                )
            )

        add_step_match = re.search(r"\badd (?:a )?step called\b", lower)
        if add_step_match:
            new_name = _extract_quoted(normalized) or normalized[add_step_match.end() :].strip(" .")
            if new_name:
                new_step_id = re.sub(r"[^a-z0-9]+", "_", new_name.lower()).strip("_") or f"step_{uuid4().hex[:8]}"
                if new_step_id in compiled_document.step_ids:
                    new_step_id = f"{new_step_id}_{uuid4().hex[:4]}"
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        step_id=None,
                        delta_id=_delta_id(),
                        operation="create",
                        change_type="create_step",
                        payload={
                            "step": {
                                "id": new_step_id,
                                "name": new_name,
                                "say": None,
                                "transitions": [],
                                "completion": {
                                    "disposition": "pending_design",
                                    "summary": "Atlas created this step as a placeholder to be wired into the flow.",
                                },
                            }
                        },
                        summary=f"Create a new step '{new_name}' in scenario '{selected_scenario_id}'.",
                    )
                )

        voice_style_match = re.search(r"\bset (?:the )?voice style to (concise|balanced|detailed)\b", lower)
        if voice_style_match:
            voice_style = voice_style_match.group(1)
            proposed.step_deltas.append(
                StepDelta(
                    agent_id=session.agent_id,
                    scenario_id=selected_scenario_id,
                    step_id=selected_step_id,
                    delta_id=_delta_id(),
                    operation="update",
                    change_type="update_response_policy",
                    payload={"response_policy": {"voice_style": voice_style}},
                    summary=f"Set the response voice style for step '{selected_step_id}' to '{voice_style}'.",
                )
            )

        require_fact_match = re.search(
            r'\b(?:require|collect) fact\s+"([^"]+)"|\b(?:require|collect) fact\s+([a-zA-Z0-9_:-]+)',
            normalized,
            re.IGNORECASE,
        )
        if require_fact_match:
            fact_name = (require_fact_match.group(1) or require_fact_match.group(2) or "").strip()
            if fact_name:
                depends_on_delta_ids = []
                if fact_name not in existing_fact_names and fact_name in created_fact_delta_ids:
                    depends_on_delta_ids.append(created_fact_delta_ids[fact_name])
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        step_id=selected_step_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="add_fact_requirement",
                        depends_on_delta_ids=depends_on_delta_ids,
                        payload={"fact_requirement": {"name": fact_name}},
                        summary=f"Require fact '{fact_name}' in step '{selected_step_id}'.",
                    )
                )

        add_fact_match = re.search(
            r'\badd fact\s+"([^"]+)"(?: of type ([a-zA-Z0-9_:-]+))?|\badd fact\s+([a-zA-Z0-9_:-]+)(?: of type ([a-zA-Z0-9_:-]+))?',
            normalized,
            re.IGNORECASE,
        )
        if add_fact_match:
            fact_name = (add_fact_match.group(1) or add_fact_match.group(3) or "").strip()
            fact_type = (add_fact_match.group(2) or add_fact_match.group(4) or "string").strip()
            if fact_name:
                delta_id = _delta_id()
                proposed.agent_metadata_deltas.append(
                    AgentMetadataDelta(
                        agent_id=session.agent_id,
                        delta_id=delta_id,
                        operation="create",
                        change_type="add_fact_schema_entry",
                        payload={"fact": {"name": fact_name, "type": fact_type}},
                        summary=f"Add fact '{fact_name}' to the agent fact schema.",
                    )
                )
                created_fact_delta_ids[fact_name] = delta_id

        delete_fact_target, _unused = _extract_named_target(
            normalized,
            r'\bdelete fact\s+"([^"]+)"|\bdelete fact\s+([a-zA-Z0-9_:-]+)',
        )
        if delete_fact_target:
            proposed.agent_metadata_deltas.append(
                AgentMetadataDelta(
                    agent_id=session.agent_id,
                    delta_id=_delta_id(),
                    operation="delete",
                    change_type="delete_fact_schema_entry",
                    payload={"fact_name": delete_fact_target},
                    summary=f"Delete fact '{delete_fact_target}' from the agent fact schema.",
                )
            )

        update_fact_match = re.search(
            r'\bchange fact\s+"([^"]+)"\s+type to\s+([a-zA-Z0-9_:-]+)|\bchange fact\s+([a-zA-Z0-9_:-]+)\s+type to\s+([a-zA-Z0-9_:-]+)',
            normalized,
            re.IGNORECASE,
        )
        if update_fact_match:
            fact_name = (update_fact_match.group(1) or update_fact_match.group(3) or "").strip()
            fact_type = (update_fact_match.group(2) or update_fact_match.group(4) or "").strip()
            if fact_name and fact_type:
                proposed.agent_metadata_deltas.append(
                    AgentMetadataDelta(
                        agent_id=session.agent_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="update_fact_schema_entry",
                        payload={"fact_name": fact_name, "fact": {"type": fact_type}},
                        summary=f"Change fact '{fact_name}' to type '{fact_type}'.",
                    )
                )

        reorder_fact_match = re.search(
            r'\bmove fact\s+"([^"]+)"\s+before\s+"([^"]+)"|\bmove fact\s+([a-zA-Z0-9_:-]+)\s+before\s+([a-zA-Z0-9_:-]+)',
            normalized,
            re.IGNORECASE,
        )
        if reorder_fact_match:
            fact_name = (reorder_fact_match.group(1) or reorder_fact_match.group(3) or "").strip()
            before_fact_name = (reorder_fact_match.group(2) or reorder_fact_match.group(4) or "").strip()
            if fact_name and before_fact_name:
                proposed.agent_metadata_deltas.append(
                    AgentMetadataDelta(
                        agent_id=session.agent_id,
                        delta_id=_delta_id(),
                        operation="reorder",
                        change_type="reorder_fact_schema_entry",
                        payload={"fact_name": fact_name, "before_fact_name": before_fact_name},
                        summary=f"Move fact '{fact_name}' before '{before_fact_name}'.",
                    )
                )

        add_tool_match = re.search(
            r'\b(?:add|use) tool\s+"([^"]+)"|\b(?:add|use) tool\s+([a-zA-Z0-9_.:-]+)',
            normalized,
            re.IGNORECASE,
        )
        if add_tool_match:
            tool_ref = (add_tool_match.group(1) or add_tool_match.group(2) or "").strip()
            if tool_ref:
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        step_id=selected_step_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="add_tool_binding",
                        payload={"tool_binding": {"ref": tool_ref, "mode": "optional", "invocation_strategy": "never"}},
                        summary=f"Add tool '{tool_ref}' to step '{selected_step_id}'.",
                    )
                )

        add_guard_match = re.search(
            r'\badd guard (channel_allowed|fact_required)\s+"([^"]+)"|\badd guard (channel_allowed|fact_required)\s+([a-zA-Z0-9_.:-]+)',
            normalized,
            re.IGNORECASE,
        )
        if add_guard_match:
            guard_kind = (add_guard_match.group(1) or add_guard_match.group(3) or "").strip()
            guard_value = (add_guard_match.group(2) or add_guard_match.group(4) or "").strip()
            if guard_kind and guard_value:
                depends_on_delta_ids = []
                if guard_kind == "fact_required" and guard_value not in existing_fact_names and guard_value in created_fact_delta_ids:
                    depends_on_delta_ids.append(created_fact_delta_ids[guard_value])
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        step_id=selected_step_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="add_guard",
                        depends_on_delta_ids=depends_on_delta_ids,
                        payload={"guard": {"kind": guard_kind, "value": guard_value}},
                        summary=f"Add guard '{guard_kind}:{guard_value}' to step '{selected_step_id}'.",
                    )
                )

        transition_match = re.search(
            r'\badd (?:a )?transition to\s+"([^"]+)"\s+on the outcome event\s+"([^"]+)"'
            r'(?:\s+with description\s+"([^"]+)")?',
            normalized,
            re.IGNORECASE,
        )
        if transition_match:
            target_step_id = transition_match.group(1).strip()
            event_value = transition_match.group(2).strip()
            description = (transition_match.group(3) or "").strip() or None
            if target_step_id and event_value:
                transition_id = _slug_id(f"{selected_step_id}_{target_step_id}_{event_value}", prefix="t")
                existing_ids = {item.id for item in selected_step.transitions}
                if transition_id in existing_ids:
                    transition_id = f"{transition_id}_{uuid4().hex[:4]}"
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        step_id=selected_step_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="add_step_transition",
                        payload={
                            "transition": {
                                "id": transition_id,
                                "when": _outcome_condition(event_value, description),
                                "to_step_id": target_step_id,
                                "label": event_value,
                                "priority": 50,
                            }
                        },
                        summary=f"Add a transition from step '{selected_step_id}' to '{target_step_id}'.",
                    )
                )

        delete_transition_target, _unused = _extract_named_target(
            normalized,
            r'\bdelete transition\s+"([^"]+)"|\bdelete transition\s+([a-zA-Z0-9_:-]+)',
        )
        if delete_transition_target and any(item.id == delete_transition_target for item in selected_step.transitions):
            proposed.step_deltas.append(
                StepDelta(
                    agent_id=session.agent_id,
                    scenario_id=selected_scenario_id,
                    step_id=selected_step_id,
                    delta_id=_delta_id(),
                    operation="update",
                    change_type="delete_step_transition",
                    payload={"transition_id": delete_transition_target},
                    summary=f"Delete transition '{delete_transition_target}' from step '{selected_step_id}'.",
                )
            )

        update_transition_match = re.search(
            r'\bupdate transition\s+"([^"]+)"\s+to\s+"([^"]+)"\s+on the outcome event\s+"([^"]+)"'
            r'(?:\s+with description\s+"([^"]+)")?',
            normalized,
            re.IGNORECASE,
        )
        if update_transition_match:
            transition_id = update_transition_match.group(1).strip()
            target_step_id = update_transition_match.group(2).strip()
            event_value = update_transition_match.group(3).strip()
            description = (update_transition_match.group(4) or "").strip() or None
            if transition_id and target_step_id and event_value:
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=selected_scenario_id,
                        step_id=selected_step_id,
                        delta_id=_delta_id(),
                        operation="update",
                        change_type="update_step_transition",
                        payload={
                            "transition_id": transition_id,
                            "transition": {
                                "id": transition_id,
                                "when": _outcome_condition(event_value, description),
                                "to_step_id": target_step_id,
                                "label": event_value,
                                "priority": 50,
                            },
                        },
                        summary=f"Update transition '{transition_id}' in step '{selected_step_id}'.",
                    )
                )

        delete_step_target, _unused = _extract_named_target(
            normalized,
            r'\bdelete step\s+"([^"]+)"|\bdelete step\s+([a-zA-Z0-9_:-]+)',
        )
        if delete_step_target and delete_step_target in compiled_document.step_ids:
            target_scenario_id = compiled_document.scenario_for_step_id(delete_step_target).id
            proposed.step_deltas.append(
                StepDelta(
                    agent_id=session.agent_id,
                    scenario_id=target_scenario_id,
                    step_id=delete_step_target,
                    delta_id=_delta_id(),
                    operation="delete",
                    change_type="delete_step",
                    payload={},
                    summary=f"Delete step '{delete_step_target}'.",
                )
            )

        move_step_match = re.search(
            r'\bmove step\s+"([^"]+)"\s+before\s+"([^"]+)"',
            normalized,
            re.IGNORECASE,
        )
        if move_step_match:
            moving_step_id = move_step_match.group(1).strip()
            before_step_id = move_step_match.group(2).strip()
            if (
                moving_step_id
                and before_step_id
                and moving_step_id in compiled_document.step_ids
                and before_step_id in compiled_document.step_ids
                and compiled_document.scenario_for_step_id(moving_step_id).id == compiled_document.scenario_for_step_id(before_step_id).id
            ):
                target_scenario_id = compiled_document.scenario_for_step_id(moving_step_id).id
                proposed.step_deltas.append(
                    StepDelta(
                        agent_id=session.agent_id,
                        scenario_id=target_scenario_id,
                        step_id=moving_step_id,
                        delta_id=_delta_id(),
                        operation="reorder",
                        change_type="reorder_step",
                        payload={"before_step_id": before_step_id},
                        summary=f"Move step '{moving_step_id}' before '{before_step_id}'.",
                    )
                )

        scenario_route_match = re.search(
            r'\badd (?:a )?scenario route to\s+"([^"]+)"\s+on the outcome event\s+"([^"]+)"'
            r'(?:\s+with description\s+"([^"]+)")?',
            normalized,
            re.IGNORECASE,
        )
        if scenario_route_match:
            target_scenario_id = scenario_route_match.group(1).strip()
            event_value = scenario_route_match.group(2).strip()
            description = (scenario_route_match.group(3) or "").strip() or None
            if target_scenario_id and event_value:
                route_id = _slug_id(f"{selected_scenario_id}_{target_scenario_id}_{event_value}", prefix="route")
                existing_route_ids = {item.id for item in compiled_document.document.scenario_routes}
                if route_id in existing_route_ids:
                    route_id = f"{route_id}_{uuid4().hex[:4]}"
                proposed.scenario_route_deltas.append(
                    ScenarioRouteDelta(
                        agent_id=session.agent_id,
                        route_id=None,
                        delta_id=_delta_id(),
                        operation="create",
                        change_type="create_scenario_route",
                        payload={
                            "route": {
                                "id": route_id,
                                "from_scenario_id": selected_scenario_id,
                                "when": _outcome_condition(event_value, description),
                                "to_scenario_id": target_scenario_id,
                                "label": event_value,
                                "priority": 50,
                            }
                        },
                        summary=f"Add a scenario route from '{selected_scenario_id}' to '{target_scenario_id}'.",
                    )
                )

        update_route_match = re.search(
            r'\bupdate scenario route\s+"([^"]+)"\s+to\s+"([^"]+)"\s+on the outcome event\s+"([^"]+)"'
            r'(?:\s+with description\s+"([^"]+)")?',
            normalized,
            re.IGNORECASE,
        )
        if update_route_match:
            route_id = update_route_match.group(1).strip()
            target_scenario_id = update_route_match.group(2).strip()
            event_value = update_route_match.group(3).strip()
            description = (update_route_match.group(4) or "").strip() or None
            if route_id and target_scenario_id and event_value:
                existing_route = next(
                    (item for item in compiled_document.document.scenario_routes if item.id == route_id),
                    None,
                )
                if existing_route is not None:
                    proposed.scenario_route_deltas.append(
                        ScenarioRouteDelta(
                            agent_id=session.agent_id,
                            route_id=route_id,
                            delta_id=_delta_id(),
                            operation="update",
                            change_type="update_scenario_route",
                            payload={
                                "route": {
                                    "id": route_id,
                                    "from_scenario_id": existing_route.from_scenario_id,
                                    "when": _outcome_condition(event_value, description),
                                    "to_scenario_id": target_scenario_id,
                                    "label": event_value,
                                    "priority": 50,
                                }
                            },
                            summary=f"Update scenario route '{route_id}'.",
                        )
                    )

        delete_route_target, _unused = _extract_named_target(
            normalized,
            r'\bdelete scenario route\s+"([^"]+)"|\bdelete scenario route\s+([a-zA-Z0-9_:-]+)',
        )
        if delete_route_target and any(item.id == delete_route_target for item in compiled_document.document.scenario_routes):
            proposed.scenario_route_deltas.append(
                ScenarioRouteDelta(
                    agent_id=session.agent_id,
                    route_id=delete_route_target,
                    delta_id=_delta_id(),
                    operation="delete",
                    change_type="delete_scenario_route",
                    payload={},
                    summary=f"Delete scenario route '{delete_route_target}'.",
                )
            )

        fact_create_dependencies = {
            str(item.payload.get("fact", {}).get("name") or "").strip(): item.delta_id
            for item in proposed.agent_metadata_deltas
            if item.change_type == "add_fact_schema_entry"
        }
        for delta in proposed.step_deltas:
            if delta.change_type == "add_fact_requirement":
                fact_name = str(delta.payload.get("fact_requirement", {}).get("name") or "").strip()
                dependency_id = fact_create_dependencies.get(fact_name)
                if dependency_id and fact_name not in existing_fact_names:
                    delta.depends_on_delta_ids = sorted({*delta.depends_on_delta_ids, dependency_id})
            if delta.change_type == "add_guard":
                guard = delta.payload.get("guard") or {}
                guard_kind = str(guard.get("kind") or "").strip()
                guard_value = str(guard.get("value") or "").strip()
                dependency_id = fact_create_dependencies.get(guard_value)
                if guard_kind == "fact_required" and dependency_id and guard_value not in existing_fact_names:
                    delta.depends_on_delta_ids = sorted({*delta.depends_on_delta_ids, dependency_id})

        return proposed

    def _replace_proposed_changes_preserving_reviewed(
        self,
        session_id: str,
        new_changes: AtlasProposedChanges,
        *,
        organization_id: str,
    ) -> AtlasProposedChanges:
        """Replace the session's proposal set without dropping reviewed work.

        A new proposal supersedes earlier *pending* deltas, but deltas the user
        already approved (and the applied audit trail) must survive — otherwise
        a follow-up turn silently wipes an approved-but-unapplied review queue.
        """
        stored = self._atlas_store.load_proposed_changes(
            session_id,
            organization_id=organization_id,
        )
        new_ids = set(self._all_delta_ids(new_changes))
        preserved_ids = [
            delta_id
            for delta_id, delta in self._delta_map(stored).items()
            if getattr(delta, "status", None) in {"approved", "applied"} and delta_id not in new_ids
        ]
        merged = (
            self._merge_proposed_changes(
                self._selected_proposed_changes(stored, preserved_ids),
                new_changes,
            )
            if preserved_ids
            else new_changes
        )
        return self._atlas_store.replace_proposed_changes(
            session_id,
            merged,
            organization_id=organization_id,
        )

    def _rationale_for_accepted_deltas(
        self,
        *,
        assistant_rationale: str | None,
        accepted_changes: AtlasProposedChanges,
        validation_blockers: list[AtlasBlocker],
        deltas_were_filtered: bool,
    ) -> str | None:
        """Keep the chat reply honest about what actually entered review.

        The model writes its rationale before validation runs, so when
        validation filters deltas the rationale describes changes that no
        longer exist. In that case the reply is recomposed from the accepted
        delta set (Generator-Spec §8) instead of shown verbatim.
        """
        if not deltas_were_filtered:
            return assistant_rationale
        accepted_summaries = [
            summary
            for summary in (
                getattr(delta, "summary", "") for delta in self._delta_map(accepted_changes).values()
            )
            if summary
        ]
        parts: list[str] = []
        if accepted_summaries:
            shown = accepted_summaries[:6]
            parts.append(
                f"I prepared {len(accepted_summaries)} change(s) for review: " + " ".join(shown)
            )
            if len(accepted_summaries) > len(shown):
                parts.append(f"({len(accepted_summaries) - len(shown)} more in the review panel.)")
        else:
            parts.append("None of the changes I drafted passed validation against the current draft, so nothing was queued for review.")
        dropped = [item for item in validation_blockers if item.blocking]
        if dropped:
            parts.append(
                f"{len(dropped)} proposed change(s) failed validation and were not included — see the blockers for details."
            )
        return " ".join(parts)

    def run_turn(
        self,
        *,
        session: AtlasSession,
        payload: AtlasTurnRequest,
        organization_id: str,
        user_id: str | None,
    ) -> AtlasTurnResponse:
        document, compiled_document = self.resolve_document_and_compiled(session)
        now = _utcnow()
        self._atlas_store.append_event(
            AtlasEvent(
                event_id=new_atlas_event_id(),
                session_id=session.session_id,
                organization_id=organization_id,
                sequence_number=0,
                type="start",
                payload={"scope": session.scope},
                created_at=now,
            )
        )

        if payload.message:
            self._atlas_store.append_message(
                AtlasMessage(
                    message_id=new_atlas_message_id(),
                    session_id=session.session_id,
                    organization_id=organization_id,
                    sequence_number=0,
                    role="user",
                    content=payload.message,
                    metadata={"question_answers": payload.question_answers},
                    created_at=now,
                )
            )

        if payload.review_decisions:
            stored_changes_for_review = self._atlas_store.load_proposed_changes(
                session.session_id,
                organization_id=organization_id,
            )
            reviewed_deltas = self._delta_map(stored_changes_for_review)
            self._atlas_store.save_review_decisions(
                [
                    AtlasReviewDecisionRecord(
                        review_decision_id=new_atlas_review_decision_id(),
                        session_id=session.session_id,
                        organization_id=organization_id,
                        delta_id=item.delta_id,
                        decision=item.decision,
                        # Hash of the content actually on review; a decision
                        # naming an unknown delta_id gets no hash and can
                        # never satisfy the apply gate.
                        delta_payload_hash=(
                            self._delta_payload_hash(reviewed_deltas[item.delta_id])
                            if item.delta_id in reviewed_deltas
                            else None
                        ),
                        note=item.note,
                        decided_by_user_id=user_id,
                        created_at=now,
                    )
                    for item in payload.review_decisions
                ]
            )
            self._observe_review_decisions(
                proposed_changes=stored_changes_for_review,
                decisions=payload.review_decisions,
            )
            self._atlas_store.update_proposed_delta_statuses(
                session.session_id,
                {
                    item.delta_id: "approved" if item.decision == "approved" else "rejected"
                    for item in payload.review_decisions
                },
                organization_id=organization_id,
            )

        attachment_results = self.build_attachment_results(payload.attachments)
        api_discovery_results, api_discovery_spec_payloads = self._discover_api_results_with_payloads(
            payload.api_discovery_requests
        )

        if payload.apply_request and payload.apply_request.delta_ids:
            self._atlas_store.create_permission_request(
                AtlasPermissionRequest(
                    request_id=new_atlas_permission_request_id(),
                    session_id=session.session_id,
                    organization_id=organization_id,
                    kind="apply_deltas",
                    status="pending",
                    reason="Atlas requires permission before applying authored changes.",
                    risk_summary="Applying changes can modify the draft agent document.",
                    scope_ref={"agent_id": session.agent_id, "session_id": session.session_id},
                    delta_ids=payload.apply_request.delta_ids,
                    requested_actions=["apply reviewed atlas deltas to the draft agent document"],
                    created_at=now,
                    expires_at=now + ATLAS_PERMISSION_REQUEST_TTL,
                )
            )

        if payload.permission_decisions:
            # Explicit confirmation, not a second human: the protocol requires
            # the user to decide permission requests and confirm apply as
            # separate explicit actions; the session creator may approve their
            # own requests (single-author orgs would otherwise be unable to
            # apply at all).
            self._atlas_store.apply_permission_decisions(
                session.session_id,
                [item.model_dump(mode="json") for item in payload.permission_decisions],
                organization_id=organization_id,
                decided_by_user_id=user_id,
            )

        selected_scenario_id, selected_step_id = self.resolve_selection(
            session,
            compiled_document,
            payload.selected_context,
        )
        selected_session = session.model_copy(
            update={
                "scenario_id": selected_scenario_id,
                "step_id": selected_step_id,
                "updated_at": _utcnow(),
            }
        )

        if selected_session.scope == "operations":
            return self._run_operations_turn(
                session=selected_session,
                payload=payload,
                organization_id=organization_id,
                compiled_document=compiled_document,
            )

        tool_calls = _plan_atlas_tool_calls(
            message=payload.message,
            scope=selected_session.scope,
            has_attachments=bool(payload.attachments),
            has_api_discovery_requests=bool(payload.api_discovery_requests),
            has_review_decisions=bool(payload.review_decisions),
            has_apply_request=bool(payload.apply_request and payload.apply_request.delta_ids),
            has_permission_decisions=bool(payload.permission_decisions),
        )

        if not tool_calls:
            proposed_changes = self._atlas_store.load_proposed_changes(
                selected_session.session_id,
                organization_id=organization_id,
            )
            validation = AtlasValidationResult()
            generator_info = AtlasGeneratorInfo(mode="fallback", model=None)
            pending_permissions: list[AtlasPermissionRequestModel] = []
            next_action: AtlasNextAction = "complete"
            self._atlas_store.update_session(
                selected_session,
                organization_id=organization_id,
                expected_updated_at=session.updated_at,
            )
            self._atlas_store.append_event(
                AtlasEvent(
                    event_id=new_atlas_event_id(),
                    session_id=selected_session.session_id,
                    organization_id=organization_id,
                    sequence_number=0,
                    type="progress",
                    payload={
                        "scenario_id": selected_scenario_id,
                        "step_id": selected_step_id,
                        "next_action": next_action,
                        "atlas_tools": [],
                        "proposed_delta_count": len(self._all_delta_ids(proposed_changes)),
                        "generator_mode": generator_info.mode,
                        "generator_model": generator_info.model,
                    },
                    created_at=_utcnow(),
                )
            )
            review_state = self.build_review_state(selected_session, proposed_changes=proposed_changes)
            message = self.assistant_summary(
                session=selected_session,
                tool_calls=tool_calls,
                request_message=payload.message,
                compiled_document=compiled_document,
                validation=validation,
                attachment_results=[],
                pending_permissions=pending_permissions,
                proposed_changes=proposed_changes,
            )
            self._atlas_store.append_message(
                AtlasMessage(
                    message_id=new_atlas_message_id(),
                    session_id=selected_session.session_id,
                    organization_id=organization_id,
                    sequence_number=0,
                    role="assistant",
                    content=message,
                    metadata={
                        "next_action": next_action,
                        "atlas_tools": [],
                        "selected_scenario_id": selected_scenario_id,
                        "selected_step_id": selected_step_id,
                        "proposed_delta_ids": self._all_delta_ids(proposed_changes),
                        "generator_mode": generator_info.mode,
                        "generator_model": generator_info.model,
                    },
                    created_at=_utcnow(),
                )
            )
            self._atlas_store.append_event(
                AtlasEvent(
                    event_id=new_atlas_event_id(),
                    session_id=selected_session.session_id,
                    organization_id=organization_id,
                    sequence_number=0,
                    type="complete",
                    payload={
                        "next_action": next_action,
                        "atlas_tools": [],
                        "generator_mode": generator_info.mode,
                        "generator_model": generator_info.model,
                    },
                    created_at=_utcnow(),
                )
            )
            return AtlasTurnResponse(
                session_id=selected_session.session_id,
                message=message,
                next_action=next_action,
                generator=generator_info,
                tool_calls=[],
                dependencies=[],
                blockers=[],
                proposed_changes=proposed_changes,
                validation=validation,
                provisioning_manifest=[],
                api_discovery_results=[],
                attachment_ingestion_results=[],
                references=self.build_references(selected_session, compiled_document),
                review_state=review_state,
                pending_permission_requests=pending_permissions,
            )

        tool_names = {str(item.get("name") or "") for item in tool_calls}
        should_propose_workflow = (
            selected_session.scope == "agent_authoring"
            and bool((payload.message or "").strip())
            and "inspect_agent" in tool_names
            and "review_change_set" not in tool_names
        )
        should_propose_provisioning = bool({"discover_api", "propose_provisioning"} & tool_names)

        generator_context = self._build_generator_context(
            session=selected_session,
            compiled_document=compiled_document,
            message=payload.message,
            selected_scenario_id=selected_scenario_id,
            selected_step_id=selected_step_id,
        )
        if should_propose_workflow:
            generator_output = self._proposal_generator.generate(
                generator_context,
                compiled_document=compiled_document,
            )
        else:
            generator_output = AtlasGeneratorOutput(
                proposed_changes=AtlasProposedChanges(),
                generation_mode="fallback",
                generation_model=None,
            )
        generated_proposed_changes = self._normalized_generated_changes(
            generator_output.proposed_changes,
            agent_id=selected_session.agent_id,
        )
        proposal_blockers: list[AtlasBlocker] = list(generator_output.generator_blockers)
        blocking_questions = list(generator_output.blocking_questions)
        generator_info = AtlasGeneratorInfo(
            mode=generator_output.generation_mode,
            model=generator_output.generation_model,
        )
        if generator_info.mode == "fallback":
            generated_proposed_changes = self._filtered_heuristic_proposed_changes(generated_proposed_changes)
        if should_propose_provisioning:
            provisioning_proposed_changes = self._filtered_heuristic_proposed_changes(
                self._generate_provisioning_proposals(
                    session=selected_session,
                    message=payload.message,
                    api_discovery_requests=payload.api_discovery_requests,
                    api_discovery_results=api_discovery_results,
                    spec_payloads_by_request_id=api_discovery_spec_payloads,
                )
            )
        else:
            provisioning_proposed_changes = AtlasProposedChanges()
        self._observe_generated_delta_families(
            generated_proposed_changes,
            mode=generator_info.mode,
        )
        self._observe_generated_delta_families(
            provisioning_proposed_changes,
            mode="fallback",
        )
        generated_proposed_changes = self._merge_proposed_changes(
            generated_proposed_changes,
            provisioning_proposed_changes,
        )
        validation_blockers: list[AtlasBlocker] = []
        if self._all_delta_ids(generated_proposed_changes):
            generated_proposed_changes, validation_blockers = self.validate_proposed_changes(
                document=document,
                proposed_changes=generated_proposed_changes,
            )
            if validation_blockers and generator_info.mode == "anthropic":
                # One semantic repair attempt (Generator-Spec §6.1): feed the
                # validation blockers back to the model and accept the repaired
                # proposal when it survives validation at least as well.
                repair_context = generator_context.model_copy(
                    update={
                        "repair_feedback": "; ".join(item.message for item in validation_blockers),
                    }
                )
                repaired_output = self._proposal_generator.generate(
                    repair_context,
                    compiled_document=compiled_document,
                )
                if repaired_output.generation_mode == "anthropic":
                    normalized_repaired_changes = self._normalized_generated_changes(
                        repaired_output.proposed_changes,
                        agent_id=selected_session.agent_id,
                    )
                    self._observe_generated_delta_families(
                        normalized_repaired_changes,
                        mode="anthropic",
                    )
                    repaired_changes = self._merge_proposed_changes(
                        normalized_repaired_changes,
                        provisioning_proposed_changes,
                    )
                    repaired_changes, repaired_blockers = self.validate_proposed_changes(
                        document=document,
                        proposed_changes=repaired_changes,
                    )
                    if len(self._all_delta_ids(repaired_changes)) >= len(
                        self._all_delta_ids(generated_proposed_changes)
                    ):
                        generated_proposed_changes = repaired_changes
                        validation_blockers = repaired_blockers
                        generator_output = repaired_output
                        proposal_blockers = list(repaired_output.generator_blockers)
                        blocking_questions = list(repaired_output.blocking_questions)
            proposal_blockers.extend(validation_blockers)
        deltas_were_filtered = bool(validation_blockers)
        if self._all_delta_ids(generated_proposed_changes):
            tool_calls.append(
                {
                    "name": "propose_workflow_change",
                    "reason": "Atlas produced authored workflow changes for review.",
                }
            )
            tool_names.add("propose_workflow_change")
        if self._all_delta_ids(generated_proposed_changes):
            proposed_changes = self._replace_proposed_changes_preserving_reviewed(
                selected_session.session_id,
                generated_proposed_changes,
                organization_id=organization_id,
            )
        else:
            proposed_changes = self._atlas_store.load_proposed_changes(
                selected_session.session_id,
                organization_id=organization_id,
            )
        validation = (
            self.build_validation(document)
            if "validate_publish" in tool_names or self._all_delta_ids(generated_proposed_changes)
            else AtlasValidationResult()
        )
        (
            _tool_refs,
            _specs_by_ref,
            definition_by_ref,
            bindings_by_tool_definition_id,
            connections_by_id,
        ) = self._tool_dependency_context(selected_session, compiled_document)
        dependencies = self._build_dependencies_from_context(
            tool_refs=_tool_refs,
            specs_by_ref=_specs_by_ref,
            definition_by_ref=definition_by_ref,
            bindings_by_tool_definition_id=bindings_by_tool_definition_id,
            connections_by_id=connections_by_id,
        )
        if should_propose_provisioning:
            dependency_proposed_changes = self._filtered_heuristic_proposed_changes(
                self._generate_dependency_provisioning_proposals(
                    session=selected_session,
                    message=payload.message,
                    dependencies=dependencies,
                    definition_by_ref=definition_by_ref,
                    bindings_by_tool_definition_id=bindings_by_tool_definition_id,
                    connections_by_id=connections_by_id,
                )
            )
        else:
            dependency_proposed_changes = AtlasProposedChanges()
        self._observe_generated_delta_families(
            dependency_proposed_changes,
            mode="fallback",
        )
        if self._all_delta_ids(dependency_proposed_changes):
            merged_proposed_changes = self._merge_proposed_changes(
                proposed_changes,
                dependency_proposed_changes,
            )
            merged_proposed_changes, dependency_validation_blockers = self.validate_proposed_changes(
                document=document,
                proposed_changes=merged_proposed_changes,
            )
            proposal_blockers.extend(dependency_validation_blockers)
            deltas_were_filtered = deltas_were_filtered or bool(dependency_validation_blockers)
            proposed_changes = self._replace_proposed_changes_preserving_reviewed(
                selected_session.session_id,
                merged_proposed_changes,
                organization_id=organization_id,
            )
            tool_names.add("propose_provisioning")
        # Manifest is descriptive, not transformative — it answers
        # "what does this agent need set up?" — so populate it for any
        # provisioning-scope turn that has dependencies, even diagnostic
        # ones ("what's missing?", "show setup blockers"). Outside of
        # provisioning scope it stays empty because the manifest doesn't
        # apply to other workflows (validation, agent_authoring).
        provisioning_manifest = (
            [
                item.model_copy(update={"agent_id": selected_session.agent_id})
                for item in build_provisioning_manifest(
                    dependencies,
                    definition_by_ref=definition_by_ref,
                    bindings_by_tool_definition_id=bindings_by_tool_definition_id,
                    connections_by_id=connections_by_id,
                )
            ]
            if selected_session.scope == "provisioning"
            else []
        )
        pending_permissions = self.permission_models(selected_session)
        next_action = self.next_action_for(
            session=selected_session,
            validation=validation,
            provisioning_manifest=provisioning_manifest,
            pending_permissions=pending_permissions,
            attachment_results=attachment_results,
            dependencies=dependencies,
            proposed_changes=proposed_changes,
            questions=blocking_questions,
        )
        selected_session = selected_session.model_copy(
            update={"status": "blocked" if next_action == "blocked" else "active"}
        )
        self._atlas_store.update_session_status(
            selected_session.session_id,
            selected_session.status,
            organization_id=organization_id,
            updated_at=_utcnow(),
        )
        self._append_tool_activity_events(
            session=selected_session,
            organization_id=organization_id,
            tool_calls=tool_calls,
        )
        completed_tool_calls = _completed_tool_calls(tool_calls)

        self._atlas_store.append_event(
            AtlasEvent(
                event_id=new_atlas_event_id(),
                session_id=selected_session.session_id,
                organization_id=organization_id,
                sequence_number=0,
                type="progress",
                payload={
                    "scenario_id": selected_scenario_id,
                    "step_id": selected_step_id,
                    "next_action": next_action,
                    "atlas_tools": tool_calls,
                    "proposed_delta_count": len(self._all_delta_ids(proposed_changes)),
                    "generator_mode": generator_info.mode,
                    "generator_model": generator_info.model,
                },
                created_at=_utcnow(),
            )
        )

        review_state = self.build_review_state(selected_session, proposed_changes=proposed_changes)
        message = self.assistant_summary(
            session=selected_session,
            tool_calls=tool_calls,
            request_message=payload.message,
            compiled_document=compiled_document,
            validation=validation,
            attachment_results=attachment_results,
            pending_permissions=pending_permissions,
            proposed_changes=proposed_changes,
            assistant_rationale=self._rationale_for_accepted_deltas(
                assistant_rationale=generator_output.assistant_rationale,
                accepted_changes=generated_proposed_changes,
                validation_blockers=[
                    item
                    for item in proposal_blockers
                    if item.code in {"atlas.invalid_proposed_change", "atlas.unknown_delta_dependency", "atlas.delta_dependency_cycle"}
                ],
                deltas_were_filtered=deltas_were_filtered,
            ),
        )
        self._atlas_store.append_message(
            AtlasMessage(
                message_id=new_atlas_message_id(),
                session_id=selected_session.session_id,
                organization_id=organization_id,
                sequence_number=0,
                role="assistant",
                content=message,
                metadata={
                    "next_action": next_action,
                    "atlas_tools": tool_calls,
                    "selected_scenario_id": selected_scenario_id,
                    "selected_step_id": selected_step_id,
                    "proposed_delta_ids": self._all_delta_ids(proposed_changes),
                    "generator_mode": generator_info.mode,
                    "generator_model": generator_info.model,
                },
                created_at=_utcnow(),
            )
        )
        self._atlas_store.append_event(
            AtlasEvent(
                event_id=new_atlas_event_id(),
                session_id=selected_session.session_id,
                organization_id=organization_id,
                sequence_number=0,
                type="complete",
                payload={
                    "next_action": next_action,
                    "atlas_tools": tool_calls,
                    "generator_mode": generator_info.mode,
                    "generator_model": generator_info.model,
                },
                created_at=_utcnow(),
            )
        )

        return AtlasTurnResponse(
            session_id=selected_session.session_id,
            message=message,
            next_action=next_action,
            generator=generator_info,
            tool_calls=completed_tool_calls,
            questions=blocking_questions,
            dependencies=dependencies,
            blockers=[*self.build_blockers(validation), *proposal_blockers],
            proposed_changes=proposed_changes,
            validation=validation,
            provisioning_manifest=provisioning_manifest,
            api_discovery_results=api_discovery_results,
            attachment_ingestion_results=attachment_results,
            references=self.build_references(selected_session, compiled_document),
            review_state=review_state,
            pending_permission_requests=pending_permissions,
        )

    def _run_operations_turn(
        self,
        *,
        session: AtlasSession,
        payload: AtlasTurnRequest,
        organization_id: str,
        compiled_document,
    ) -> AtlasTurnResponse:
        """Operations/debug mode: explain what a conversation actually did.

        Reads the recorded turn traces for the linked conversation and
        produces a deterministic routing/failure summary. Proposes no deltas.
        """
        selected = payload.selected_context
        conversation_id = (selected.conversation_id if selected else None) or session.conversation_id
        trace_id = (selected.trace_id if selected else None) or session.trace_id

        questions: list[BlockingQuestion] = []
        blockers: list[AtlasBlocker] = []
        message: str

        if self._trace_store is None:
            blockers.append(
                AtlasBlocker(
                    code="atlas.operations_unavailable",
                    message="Operations mode requires the trace store, which is not configured for this deployment.",
                    blocking=True,
                )
            )
            message = "I can't inspect conversations right now: the trace store is not configured."
        elif not conversation_id:
            questions.append(
                BlockingQuestion(
                    question_id="operations_conversation_id",
                    question="Which conversation should I inspect?",
                    help_text="Provide a conversation id (or open this session from a conversation) so I can read its traces.",
                    required=True,
                )
            )
            message = "Tell me which conversation to inspect and I'll walk through what the agent did and where it went wrong."
        else:
            traces = self._trace_store.by_conversation(conversation_id, organization_id=organization_id)
            # AR-2.3: org scoping alone is insufficient — an operations session
            # may only read traces for its OWN agent. A client-supplied
            # conversation_id must not expose another agent's routing/tool
            # history within the same org.
            traces = [item for item in traces if getattr(item, "agent_id", None) == session.agent_id]
            if trace_id:
                traces = [item for item in traces if item.trace_id == trace_id] or traces
            if not traces:
                blockers.append(
                    AtlasBlocker(
                        code="atlas.operations_no_traces",
                        message=f"No traces were found for conversation '{conversation_id}'.",
                        blocking=False,
                        reference_ids=[conversation_id],
                    )
                )
                message = (
                    f"I couldn't find any recorded traces for conversation '{conversation_id}'. "
                    "Either the conversation has no turns yet or it belongs to a different agent."
                )
            else:
                message = self._summarize_operations_traces(
                    traces=traces,
                    compiled_document=compiled_document,
                )

        now = _utcnow()
        next_action: AtlasNextAction = "ask_questions" if questions else "complete"
        self._atlas_store.append_event(
            AtlasEvent(
                event_id=new_atlas_event_id(),
                session_id=session.session_id,
                organization_id=organization_id,
                sequence_number=0,
                type="progress",
                payload={
                    "mode": "operations",
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                    "next_action": next_action,
                },
                created_at=now,
            )
        )
        self._atlas_store.append_message(
            AtlasMessage(
                message_id=new_atlas_message_id(),
                session_id=session.session_id,
                organization_id=organization_id,
                sequence_number=0,
                role="assistant",
                content=message,
                metadata={
                    "next_action": next_action,
                    "mode": "operations",
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                },
                created_at=now,
            )
        )
        self._atlas_store.append_event(
            AtlasEvent(
                event_id=new_atlas_event_id(),
                session_id=session.session_id,
                organization_id=organization_id,
                sequence_number=0,
                type="complete",
                payload={"next_action": next_action, "mode": "operations"},
                created_at=now,
            )
        )
        references = self.build_references(session, compiled_document)
        if conversation_id and conversation_id not in references.conversation_ids:
            references.conversation_ids.append(conversation_id)
        if trace_id and trace_id not in references.trace_ids:
            references.trace_ids.append(trace_id)
        return AtlasTurnResponse(
            session_id=session.session_id,
            message=message,
            next_action=next_action,
            generator=AtlasGeneratorInfo(mode="fallback", model=None),
            questions=questions,
            blockers=blockers,
            proposed_changes=AtlasProposedChanges(),
            references=references,
            review_state=self.build_review_state(session),
        )

    def _summarize_operations_traces(self, *, traces, compiled_document) -> str:
        def _step_name(step_id: str) -> str:
            try:
                return f"'{compiled_document.step_by_id(step_id).name}'"
            except KeyError:
                return f"'{step_id}'"

        parts: list[str] = [f"I read {len(traces)} recorded turn(s) for this conversation."]
        path: list[str] = []
        for trace in traces:
            if not path:
                path.append(_step_name(trace.step_before))
            if trace.step_after != trace.step_before or not path:
                path.append(_step_name(trace.step_after))
        if path:
            parts.append("Step path: " + " -> ".join(path) + ".")
        errored = [trace for trace in traces if getattr(trace, "error_kind", "none") != "none"]
        if errored:
            details = "; ".join(
                f"turn {index + 1} ({_step_name(trace.step_before)}): {trace.error_kind}"
                for index, trace in enumerate(traces)
                if getattr(trace, "error_kind", "none") != "none"
            )
            parts.append(f"{len(errored)} turn(s) recorded errors: {details}.")
        else:
            parts.append("No turn-level errors were recorded.")
        tool_calls = [call for trace in traces for call in getattr(trace, "tool_calls", [])]
        if tool_calls:
            tool_refs = sorted(
                {
                    str(getattr(call, "tool_ref", None) or getattr(call, "name", None) or "unknown")
                    for call in tool_calls
                }
            )
            parts.append(f"Tools invoked: {', '.join(tool_refs)}.")
        last = traces[-1]
        last_action = getattr(getattr(last, "chosen_action", None), "type", None)
        if last_action:
            parts.append(f"The conversation ended at step {_step_name(last.step_after)} after action '{last_action}'.")
        return " ".join(parts)

    def apply_requested_deltas(
        self,
        *,
        session: AtlasSession,
        delta_ids: list[str],
        organization_id: str,
    ) -> AgentDocument:
        with self._atlas_store.apply_lock(session.session_id, organization_id=organization_id):
            return self._apply_requested_deltas_locked(
                session=session,
                delta_ids=delta_ids,
                organization_id=organization_id,
            )

    def _apply_requested_deltas_locked(
        self,
        *,
        session: AtlasSession,
        delta_ids: list[str],
        organization_id: str,
    ) -> AgentDocument:
        document, _compiled_document = self.resolve_document_and_compiled(session)
        stored_changes = self._atlas_store.load_proposed_changes(
            session.session_id,
            organization_id=organization_id,
        )
        all_deltas = self._delta_map(stored_changes)
        if not delta_ids:
            raise ValueError("no atlas deltas were requested for apply")
        missing = [delta_id for delta_id in delta_ids if delta_id not in all_deltas]
        if missing:
            raise ValueError(f"unknown atlas delta ids: {', '.join(missing)}")

        # AR-3.6: idempotent retry. If every requested delta is already applied
        # (e.g. a client retried after a successful-but-timed-out apply), return
        # the current draft unchanged instead of failing the not-approved gate
        # with a spurious 'failed' result.
        if all(getattr(all_deltas[delta_id], "status", None) == "applied" for delta_id in delta_ids):
            return document

        decisions = self._atlas_store.list_review_decisions(
            session.session_id,
            organization_id=organization_id,
        )
        # Approval is content-addressed: an approval only authorizes the exact
        # payload that was reviewed. A delta re-proposed under an approved
        # delta_id with different content (or a legacy decision without a
        # hash) does not inherit the approval and must be re-reviewed.
        approved_hashes: dict[str, set[str]] = {}
        for item in decisions:
            if item.decision == "approved" and item.delta_payload_hash:
                approved_hashes.setdefault(item.delta_id, set()).add(item.delta_payload_hash)
        unapproved = [
            delta_id
            for delta_id in delta_ids
            if self._delta_payload_hash(all_deltas[delta_id]) not in approved_hashes.get(delta_id, set())
        ]
        if unapproved:
            raise ValueError(
                "atlas deltas are not approved for their current content "
                f"(re-review required): {', '.join(unapproved)}"
            )
        non_applyable = [
            delta_id
            for delta_id in delta_ids
            if getattr(all_deltas[delta_id], "status", None) != "approved"
        ]
        if non_applyable:
            statuses = ", ".join(
                f"{delta_id}={getattr(all_deltas[delta_id], 'status', None) or 'unknown'}"
                for delta_id in non_applyable
            )
            raise ValueError(f"atlas deltas are not ready to apply: {statuses}")

        updated_document = document.model_copy(deep=True)
        integration_binding_deltas: list[IntegrationBindingDelta] = []
        for delta_id in self._ordered_delta_ids_for_apply(delta_ids, all_deltas):
            delta = all_deltas[delta_id]
            if isinstance(delta, IntegrationBindingDelta):
                integration_binding_deltas.append(delta)
                continue
            updated_document = self._apply_delta(updated_document, delta)

        validation = self.build_validation(updated_document)
        if validation.blocking:
            raise ValueError("; ".join(validation.errors) or "applied atlas deltas left the draft invalid")
        compile_agent_document(updated_document)

        # Apply discipline (Agent-Doc plan §8.1): everything checkable is
        # checked before any state mutates, the document write happens first
        # because it is the only step we can deterministically revert, and a
        # binding failure mid-sequence reverts the document so the draft and
        # the integration state never diverge silently.
        for delta in integration_binding_deltas:
            self._preflight_integration_binding_delta(
                delta=delta,
                organization_id=organization_id,
            )

        self._agent_registry.update_draft_agent_document(
            session.agent_id,
            updated_document,
            organization_id=organization_id,
        )
        executed_binding_delta_ids: list[str] = []
        try:
            for delta in integration_binding_deltas:
                self._apply_integration_binding_delta(
                    session=session,
                    delta=delta,
                    organization_id=organization_id,
                )
                executed_binding_delta_ids.append(delta.delta_id)
        except Exception as exc:
            revert_succeeded = True
            try:
                self._agent_registry.update_draft_agent_document(
                    session.agent_id,
                    document,
                    organization_id=organization_id,
                )
            except Exception:
                revert_succeeded = False
                logger.exception(
                    "atlas apply rollback failed; draft document may include applied deltas",
                    extra={"session_id": session.session_id, "agent_id": session.agent_id},
                )
            if executed_binding_delta_ids:
                # External provisioning effects cannot be deterministically
                # compensated; record exactly which binding deltas executed so
                # the failure report is honest about partial state.
                self._atlas_store.update_proposed_delta_statuses(
                    session.session_id,
                    {delta_id: "applied" for delta_id in executed_binding_delta_ids},
                    organization_id=organization_id,
                )
            # AR-3.3: the message must reflect whether the revert actually
            # succeeded — claiming "reverted" when the revert write itself
            # failed leaves the draft in an unknown state and misleads the user.
            revert_note = (
                "the draft document change was reverted"
                if revert_succeeded
                else "the draft document change could NOT be reverted and may contain "
                "applied deltas — manual review of the draft is required"
            )
            raise ValueError(
                "atlas apply failed while executing integration binding deltas "
                f"(executed: {', '.join(executed_binding_delta_ids) or 'none'}; "
                f"{revert_note}): {exc}"
            ) from exc
        self._atlas_store.update_proposed_delta_statuses(
            session.session_id,
            {delta_id: "applied" for delta_id in delta_ids},
            organization_id=organization_id,
        )
        return updated_document

    def _preflight_integration_binding_delta(
        self,
        *,
        delta: IntegrationBindingDelta,
        organization_id: str,
    ) -> None:
        """Run every non-mutating check `_apply_integration_binding_delta` would hit.

        Catching bad references and missing stores here means the common
        failure modes abort the apply before anything — document or external
        provisioning state — has mutated.
        """
        if self._definition_store is None:
            raise ValueError("atlas provisioning apply requires a tool definition store")
        # AR-2.2 backstop: refuse to apply any binding whose connection base
        # URL targets an internal address, regardless of how the delta reached
        # this point (heuristic proposal, model output, replayed payload).
        delta_base_url = str(delta.payload.get("base_url") or "").strip() or None
        if not is_safe_provisioning_base_url(delta_base_url):
            raise ValueError("atlas provisioning base_url targets a non-public address")
        if delta.change_type == "provision_provider_template":
            provider_slug = str(delta.payload.get("provider_slug") or "").strip()
            if PROVIDER_TEMPLATES.get(provider_slug) is None:
                raise ValueError(f"unknown provider template: {provider_slug}")
            return
        if delta.change_type == "ingest_openapi_tools":
            if self._connection_store is None:
                raise ValueError("atlas provisioning apply requires an API connection store")
            spec = delta.payload.get("spec")
            if not isinstance(spec, dict) or not spec:
                raise ValueError("ingest_openapi_tools delta is missing payload.spec")
            return
        if delta.change_type == "prepare_custom_oauth_connection":
            if self._connection_store is None:
                raise ValueError("atlas provisioning apply requires an API connection store")
            return
        if delta.change_type == "bind_existing_connection":
            if self._binding_store is None:
                raise ValueError("atlas provisioning apply requires an agent tool binding store")
            if self._connection_store is None:
                raise ValueError("atlas provisioning apply requires an API connection store")
            tool_definition_id = str(delta.payload.get("tool_definition_id") or "").strip()
            connection_id = str(delta.payload.get("connection_id") or "").strip()
            if not tool_definition_id or not connection_id:
                raise ValueError("bind_existing_connection delta is missing tool_definition_id or connection_id")
            definition = self._definition_store.get(tool_definition_id)
            if definition is None or getattr(definition, "organization_id", None) != organization_id:
                raise ValueError(f"unknown tool definition for atlas provisioning action: {tool_definition_id}")
            existing_connection = self._connection_store.get(connection_id)
            if existing_connection is None or existing_connection.organization_id != organization_id:
                raise ValueError(f"unknown connection for atlas provisioning action: {connection_id}")
            return
        if delta.change_type in {"reauthorize_connection", "repair_connection"}:
            if self._connection_store is None:
                raise ValueError("atlas provisioning apply requires an API connection store")
            connection_id = str(delta.payload.get("connection_id") or "").strip()
            if not connection_id:
                raise ValueError(f"{delta.change_type} delta is missing connection_id")
            existing = self._connection_store.get(connection_id)
            if existing is None or existing.organization_id != organization_id:
                raise ValueError(f"unknown connection for atlas provisioning action: {connection_id}")
            return
        raise ValueError(f"unsupported integration binding delta change_type: {delta.change_type}")

    def _ordered_delta_ids_for_apply(self, delta_ids: list[str], all_deltas: dict[str, Any]) -> list[str]:
        selected = set(delta_ids)
        ordered: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(delta_id: str) -> None:
            if delta_id in visited:
                return
            if delta_id in visiting:
                raise ValueError(f"atlas delta dependency cycle detected at '{delta_id}'")
            visiting.add(delta_id)
            delta = all_deltas[delta_id]
            for dependency_id in getattr(delta, "depends_on_delta_ids", []) or []:
                if dependency_id not in all_deltas:
                    raise ValueError(f"atlas delta '{delta_id}' depends on unknown delta '{dependency_id}'")
                if dependency_id in selected:
                    visit(dependency_id)
            visiting.remove(delta_id)
            visited.add(delta_id)
            ordered.append(delta_id)

        for delta_id in delta_ids:
            visit(delta_id)
        return ordered

    def _all_delta_ids(self, proposed_changes: AtlasProposedChanges | None) -> list[str]:
        if proposed_changes is None:
            return []
        return [
            item.delta_id
            for attr in _DELTA_FAMILY_ATTRS
            for item in getattr(proposed_changes, attr)
        ]

    def _delta_map(self, proposed_changes: AtlasProposedChanges) -> dict[str, Any]:
        return {
            item.delta_id: item
            for attr in _DELTA_FAMILY_ATTRS
            for item in getattr(proposed_changes, attr)
        }

    def _delta_ids_with_status(self, proposed_changes: AtlasProposedChanges, status: str) -> list[str]:
        return [
            item.delta_id
            for item in self._delta_map(proposed_changes).values()
            if getattr(item, "status", None) == status
        ]

    def _actionable_delta_ids(self, proposed_changes: AtlasProposedChanges | None) -> list[str]:
        """Delta IDs that still need user action (not yet applied or rejected).

        Includes `proposed` (awaiting review) and `approved` (awaiting apply);
        excludes the terminal `applied`/`rejected` deltas kept for audit.
        """
        if proposed_changes is None:
            return []
        return [
            item.delta_id
            for item in self._delta_map(proposed_changes).values()
            if getattr(item, "status", None) not in {"applied", "rejected"}
        ]

    def _normalized_generated_changes(
        self,
        proposed_changes: AtlasProposedChanges,
        *,
        agent_id: str,
    ) -> AtlasProposedChanges:
        """Sanitize generator output before it enters the review surface.

        The model output is untrusted: only the server may move a delta
        through the review lifecycle, so every generated delta enters as
        `proposed` and scoped to the session's agent — a delta that
        self-reports `approved`/`applied` would otherwise render as
        human-reviewed, survive proposal replacement, and pass the
        status half of the apply gate.
        """
        def _normalize(item: Any) -> Any:
            update: dict[str, Any] = {"status": "proposed"}
            if "agent_id" in type(item).model_fields:
                update["agent_id"] = agent_id
            return item.model_copy(update=update)

        return AtlasProposedChanges(
            **{
                attr: [_normalize(item) for item in getattr(proposed_changes, attr)]
                for attr in _DELTA_FAMILY_ATTRS
            }
        )

    def _delta_payload_hash(self, delta: Any) -> str:
        """Content address for a proposed delta, stable across status flips."""
        return atlas_delta_payload_hash(delta)

    def _selected_proposed_changes(
        self,
        proposed_changes: AtlasProposedChanges,
        delta_ids: list[str],
    ) -> AtlasProposedChanges:
        selected = set(delta_ids)
        return AtlasProposedChanges(
            **{
                attr: [item for item in getattr(proposed_changes, attr) if item.delta_id in selected]
                for attr in _DELTA_FAMILY_ATTRS
            }
        )

    def _apply_delta(self, document: AgentDocument, delta: Any) -> AgentDocument:
        if isinstance(delta, ScenarioDelta):
            return self._apply_scenario_delta(document, delta)
        if isinstance(delta, StepDelta):
            return self._apply_step_delta(document, delta)
        if isinstance(delta, ScenarioRouteDelta):
            return self._apply_scenario_route_delta(document, delta)
        if isinstance(delta, AgentMetadataDelta):
            return self._apply_agent_metadata_delta(document, delta)
        if isinstance(delta, IntegrationBindingDelta):
            return document
        raise ValueError(f"unsupported atlas delta family for apply: {type(delta).__name__}")

    def _apply_integration_binding_delta(
        self,
        *,
        session: AtlasSession,
        delta: IntegrationBindingDelta,
        organization_id: str,
    ) -> None:
        if self._definition_store is None:
            raise ValueError("atlas provisioning apply requires a tool definition store")
        session_factory = self._definition_store.session_factory
        if delta.change_type == "provision_provider_template":
            provider_slug = str(delta.payload.get("provider_slug") or "").strip()
            template = PROVIDER_TEMPLATES.get(provider_slug)
            if template is None:
                raise ValueError(f"unknown provider template: {provider_slug}")
            connection, tools = setup_provider(
                template,
                session_factory=session_factory,
                organization_id=organization_id,
                display_name=str(delta.payload.get("display_name") or template.display_name).strip() or template.display_name,
                base_url=str(delta.payload.get("base_url") or "").strip() or None,
                template_config=dict(delta.payload.get("template_config") or {}),
            )
            requested_tool_refs = {str(item).strip() for item in delta.payload.get("tool_refs") or [] if str(item).strip()}
            assignment_store = ToolAgentAssignmentStore(session_factory)
            for tool in tools:
                if requested_tool_refs and tool.tool_ref not in requested_tool_refs:
                    continue
                try:
                    assignment_store.assign(
                        organization_id=organization_id,
                        agent_id=session.agent_id,
                        tool_definition_id=tool.tool_definition_id,
                    )
                except Exception:
                    # Per-tool assignment failure (commonly: already assigned)
                    # should not abort the whole template ingest. Log and move
                    # on so ops can audit how often this fires.
                    logger.warning(
                        "atlas.tool_assignment_failed",
                        extra={
                            "operation": "ingest_template_tools",
                            "agent_id": session.agent_id,
                            "organization_id": organization_id,
                            "tool_definition_id": tool.tool_definition_id,
                            "tool_ref": tool.tool_ref,
                            "delta_change_type": delta.change_type,
                        },
                        exc_info=True,
                    )
                    continue
            return
        if delta.change_type == "ingest_openapi_tools":
            if self._connection_store is None:
                raise ValueError("atlas provisioning apply requires an API connection store")
            spec = delta.payload.get("spec")
            if not isinstance(spec, dict) or not spec:
                raise ValueError("ingest_openapi_tools delta is missing payload.spec")
            assignment_store = ToolAgentAssignmentStore(session_factory)
            service = OpenAPIToolIngestionService(
                connection_store=self._connection_store,
                definition_store=self._definition_store,
                assignment_store=assignment_store,
            )
            service.ingest(
                organization_id=organization_id,
                spec=spec,
                display_name=str(delta.payload.get("display_name") or "Imported OpenAPI").strip() or "Imported OpenAPI",
                provider=str(delta.payload.get("provider") or "openapi").strip() or "openapi",
                auth_type=str(delta.payload.get("auth_type") or "none").strip() or "none",
                base_url=str(delta.payload.get("base_url") or "").strip() or None,
                tool_ref_prefix=str(delta.payload.get("tool_ref_prefix") or "").strip() or None,
                agent_id=session.agent_id,
            )
            return
        if delta.change_type == "prepare_custom_oauth_connection":
            if self._connection_store is None:
                raise ValueError("atlas provisioning apply requires an API connection store")
            display_name = str(delta.payload.get("display_name") or "Custom OAuth API").strip() or "Custom OAuth API"
            base_url = str(delta.payload.get("base_url") or "").strip() or None
            documentation_url = str(delta.payload.get("documentation_url") or "").strip() or None
            connection = self._connection_store.create(
                organization_id=organization_id,
                display_name=display_name,
                provider="custom_oauth",
                auth_type="oauth2",
                base_url=base_url,
                metadata={
                    "atlas_source_request_id": delta.payload.get("source_request_id"),
                    "atlas_scaffold": "custom_oauth",
                    **({"documentation_url": documentation_url} if documentation_url else {}),
                },
            )
            self._connection_store.update(
                connection.connection_id,
                status="needs_auth",
            )
            return
        if delta.change_type == "bind_existing_connection":
            if self._binding_store is None:
                raise ValueError("atlas provisioning apply requires an agent tool binding store")
            if self._connection_store is None:
                raise ValueError("atlas provisioning apply requires an API connection store")
            if self._definition_store is None:
                raise ValueError("atlas provisioning apply requires a tool definition store")
            tool_definition_id = str(delta.payload.get("tool_definition_id") or "").strip()
            connection_id = str(delta.payload.get("connection_id") or "").strip()
            if not tool_definition_id or not connection_id:
                raise ValueError("bind_existing_connection delta is missing tool_definition_id or connection_id")
            definition = self._definition_store.get(tool_definition_id)
            if definition is None or getattr(definition, "organization_id", None) != organization_id:
                raise ValueError(f"unknown tool definition for atlas provisioning action: {tool_definition_id}")
            existing_connection = self._connection_store.get(connection_id)
            if existing_connection is None or existing_connection.organization_id != organization_id:
                raise ValueError(f"unknown connection for atlas provisioning action: {connection_id}")
            assignment_store = ToolAgentAssignmentStore(session_factory)
            try:
                assignment_store.assign(
                    organization_id=organization_id,
                    agent_id=session.agent_id,
                    tool_definition_id=tool_definition_id,
                )
            except Exception:
                # Pre-existing assignment is the most common failure mode and
                # is benign because the binding write below is the actual
                # state transition we care about. Log so we can spot DB
                # errors here that look the same to bare except.
                logger.warning(
                    "atlas.tool_assignment_failed",
                    extra={
                        "operation": "bind_existing_connection",
                        "agent_id": session.agent_id,
                        "organization_id": organization_id,
                        "tool_definition_id": tool_definition_id,
                        "connection_id": connection_id,
                        "delta_change_type": delta.change_type,
                    },
                    exc_info=True,
                )
            self._binding_store.create_or_update(
                organization_id=organization_id,
                agent_id=session.agent_id,
                tool_definition_id=tool_definition_id,
                connection_id=connection_id,
                enabled=True,
            )
            return
        if delta.change_type in {"reauthorize_connection", "repair_connection"}:
            if self._connection_store is None:
                raise ValueError("atlas provisioning apply requires an API connection store")
            connection_id = str(delta.payload.get("connection_id") or "").strip()
            if not connection_id:
                raise ValueError(f"{delta.change_type} delta is missing connection_id")
            existing = self._connection_store.get(connection_id)
            if existing is None or existing.organization_id != organization_id:
                raise ValueError(f"unknown connection for atlas provisioning action: {connection_id}")
            metadata = {
                "atlas_action_requested_at": _utcnow().isoformat(),
                "atlas_action_requested_by": "atlas",
                "atlas_action_requested_for_agent_id": session.agent_id,
                "atlas_action_kind": delta.change_type,
                "atlas_action_tool_ref": delta.payload.get("tool_ref"),
            }
            next_status = existing.status
            if delta.change_type == "reauthorize_connection":
                next_status = "needs_auth"
            elif delta.change_type == "repair_connection" and existing.auth_type == "oauth2":
                next_status = "needs_auth"
            self._connection_store.update(
                connection_id,
                status=next_status,
                metadata=metadata,
            )
            return
        raise ValueError(f"unsupported integration binding delta change_type: {delta.change_type}")

    def _apply_agent_metadata_delta(self, document: AgentDocument, delta: AgentMetadataDelta) -> AgentDocument:
        updated = document.model_copy(deep=True)
        if delta.change_type == "add_fact_schema_entry":
            from .schemas import FactDef

            fact_payload = dict(delta.payload.get("fact") or {})
            if not fact_payload:
                raise ValueError("add_fact_schema_entry delta is missing payload.fact")
            fact_def = FactDef.model_validate(fact_payload)
            # AR-3.3: don't report a no-op as a successful apply. Adding a fact
            # that already exists is a conflict, not a silent success.
            if any(item.name == fact_def.name for item in updated.fact_schema):
                raise ValueError(f"fact schema entry already exists: {fact_def.name}")
            updated.fact_schema = [*updated.fact_schema, fact_def]
            return updated
        if delta.change_type == "delete_fact_schema_entry":
            fact_name = str(delta.payload.get("fact_name") or "").strip()
            if not fact_name:
                raise ValueError("delete_fact_schema_entry delta is missing payload.fact_name")
            # AR-3.3: deleting an absent fact must not report success — Atlas
            # would claim "I deleted fact X" when X never existed.
            if not any(item.name == fact_name for item in updated.fact_schema):
                raise ValueError(f"unknown fact schema entry to delete: {fact_name}")
            updated.fact_schema = [item for item in updated.fact_schema if item.name != fact_name]
            return updated
        if delta.change_type == "update_fact_schema_entry":
            from .schemas import FactDef

            fact_name = str(delta.payload.get("fact_name") or "").strip()
            fact_payload = dict(delta.payload.get("fact") or {})
            if not fact_name or not fact_payload:
                raise ValueError("update_fact_schema_entry delta is missing fact identity or payload")
            # AR-3.3: updating an absent fact is a no-op masquerading as success.
            if not any(item.name == fact_name for item in updated.fact_schema):
                raise ValueError(f"unknown fact schema entry to update: {fact_name}")
            updated.fact_schema = [
                FactDef.model_validate({**item.model_dump(mode="json"), **fact_payload}) if item.name == fact_name else item
                for item in updated.fact_schema
            ]
            return updated
        if delta.change_type == "reorder_fact_schema_entry":
            fact_name = str(delta.payload.get("fact_name") or "").strip()
            before_fact_name = str(delta.payload.get("before_fact_name") or "").strip()
            if not fact_name or not before_fact_name:
                raise ValueError("reorder_fact_schema_entry delta is missing fact ordering payload")
            facts = list(updated.fact_schema)
            moving = next((item for item in facts if item.name == fact_name), None)
            if moving is None:
                raise ValueError(f"unknown fact for reorder: {fact_name}")
            facts = [item for item in facts if item.name != fact_name]
            insert_at = next((index for index, item in enumerate(facts) if item.name == before_fact_name), len(facts))
            facts.insert(insert_at, moving)
            updated.fact_schema = facts
            return updated
        if delta.operation == "update" and isinstance(delta.payload, dict):
            updated.metadata = {**dict(updated.metadata), **dict(delta.payload)}
            return updated
        raise ValueError(f"unsupported agent metadata delta change_type: {delta.change_type}")

    def _apply_scenario_delta(self, document: AgentDocument, delta: ScenarioDelta) -> AgentDocument:
        if not delta.scenario_id:
            raise ValueError("scenario delta is missing scenario_id")
        updated = document.model_copy(deep=True)
        scenarios = list(updated.scenarios)
        for index, scenario in enumerate(scenarios):
            if scenario.id != delta.scenario_id:
                continue
            if delta.operation == "update" and delta.change_type == "rename_scenario":
                scenarios[index] = scenario.model_copy(update={"name": str(delta.payload.get("name") or scenario.name)})
                updated.scenarios = scenarios
                return updated
            raise ValueError(f"unsupported scenario delta change_type: {delta.change_type}")
        raise ValueError(f"unknown scenario for delta apply: {delta.scenario_id}")

    def _apply_step_delta(self, document: AgentDocument, delta: StepDelta) -> AgentDocument:
        updated = document.model_copy(deep=True)
        scenarios = list(updated.scenarios)
        for scenario_index, scenario in enumerate(scenarios):
            if scenario.id != delta.scenario_id:
                continue
            steps = list(scenario.steps)
            if delta.operation == "delete":
                if not delta.step_id:
                    raise ValueError("delete step delta is missing step_id")
                if scenario.start_step_id == delta.step_id:
                    raise ValueError("cannot delete a scenario start step")
                referenced = [
                    step.id
                    for step in scenario.steps
                    for transition in step.transitions
                    if transition.to_step_id == delta.step_id
                ]
                if referenced:
                    raise ValueError(f"cannot delete step '{delta.step_id}' while transitions still point to it")
                steps = [step for step in steps if step.id != delta.step_id]
                scenarios[scenario_index] = scenario.model_copy(update={"steps": steps})
                updated.scenarios = scenarios
                return updated
            if delta.operation == "reorder":
                if not delta.step_id:
                    raise ValueError("reorder step delta is missing step_id")
                moving = next((item for item in steps if item.id == delta.step_id), None)
                if moving is None:
                    raise ValueError(f"unknown step for delta apply: {delta.step_id}")
                before_step_id = str(delta.payload.get("before_step_id") or "").strip()
                if not before_step_id:
                    raise ValueError("reorder_step delta is missing payload.before_step_id")
                steps = [step for step in steps if step.id != delta.step_id]
                insert_at = next((index for index, item in enumerate(steps) if item.id == before_step_id), len(steps))
                steps.insert(insert_at, moving)
                scenarios[scenario_index] = scenario.model_copy(update={"steps": steps})
                updated.scenarios = scenarios
                return updated
            if delta.operation == "create":
                payload_step = dict(delta.payload.get("step") or {})
                if not payload_step:
                    raise ValueError("create_step delta is missing payload.step")
                steps.append(Step.model_validate(payload_step))
                scenarios[scenario_index] = scenario.model_copy(update={"steps": steps})
                updated.scenarios = scenarios
                return updated
            if not delta.step_id:
                raise ValueError("step delta is missing step_id")
            for step_index, step in enumerate(steps):
                if step.id != delta.step_id:
                    continue
                steps[step_index] = self._updated_step_for_delta(step, delta)
                scenarios[scenario_index] = scenario.model_copy(update={"steps": steps})
                updated.scenarios = scenarios
                return updated
            raise ValueError(f"unknown step for delta apply: {delta.step_id}")
        raise ValueError(f"unknown scenario for step delta apply: {delta.scenario_id}")

    def _apply_scenario_route_delta(self, document: AgentDocument, delta: ScenarioRouteDelta) -> AgentDocument:
        updated = document.model_copy(deep=True)
        routes = list(updated.scenario_routes)
        if delta.operation == "create" and delta.change_type == "create_scenario_route":
            route_payload = dict(delta.payload.get("route") or {})
            if not route_payload:
                raise ValueError("create_scenario_route delta is missing payload.route")
            routes.append(ScenarioRoute.model_validate(route_payload))
            updated.scenario_routes = routes
            return updated
        if delta.operation == "update" and delta.change_type == "update_scenario_route":
            route_payload = dict(delta.payload.get("route") or {})
            if not delta.route_id or not route_payload:
                raise ValueError("update_scenario_route delta is missing route identity or payload")
            updated.scenario_routes = [
                ScenarioRoute.model_validate(route_payload) if item.id == delta.route_id else item
                for item in routes
            ]
            return updated
        if delta.operation == "delete" and delta.change_type == "delete_scenario_route":
            if not delta.route_id:
                raise ValueError("delete_scenario_route delta is missing route_id")
            updated.scenario_routes = [item for item in routes if item.id != delta.route_id]
            return updated
        raise ValueError(f"unsupported scenario route delta change_type: {delta.change_type}")

    def _updated_step_for_delta(self, step: Step, delta: StepDelta) -> Step:
        payload = dict(delta.payload or {})
        if delta.operation != "update":
            raise ValueError(f"unsupported step delta operation: {delta.operation}")
        if delta.change_type == "rename_step":
            return step.model_copy(update={"name": str(payload.get("name") or step.name)})
        if delta.change_type == "update_step_say":
            return step.model_copy(update={"say": payload.get("say")})
        if delta.change_type == "set_step_handoff":
            handoff_payload = payload.get("handoff")
            if not isinstance(handoff_payload, dict):
                raise ValueError("set_step_handoff delta is missing payload.handoff")
            return step.model_copy(
                update={
                    "handoff": StepHandoff.model_validate(handoff_payload),
                    "completion": None,
                }
            )
        if delta.change_type == "set_step_completion":
            completion_payload = payload.get("completion")
            if not isinstance(completion_payload, dict):
                raise ValueError("set_step_completion delta is missing payload.completion")
            return step.model_copy(
                update={
                    "completion": StepCompletion.model_validate(completion_payload),
                    "handoff": None,
                }
            )
        if delta.change_type == "update_response_policy":
            policy_payload = payload.get("response_policy")
            if not isinstance(policy_payload, dict):
                raise ValueError("update_response_policy delta is missing payload.response_policy")
            return step.model_copy(
                update={
                    "response_policy": ResponsePolicy.model_validate(
                        {**step.response_policy.model_dump(mode="json"), **policy_payload}
                    )
                }
            )
        if delta.change_type == "add_fact_requirement":
            fact_payload = payload.get("fact_requirement")
            if not isinstance(fact_payload, dict):
                raise ValueError("add_fact_requirement delta is missing payload.fact_requirement")
            requirement = FactRequirement.model_validate(fact_payload)
            if any(item.name == requirement.name for item in step.fact_requirements):
                return step
            return step.model_copy(update={"fact_requirements": [*step.fact_requirements, requirement]})
        if delta.change_type == "add_tool_binding":
            binding_payload = payload.get("tool_binding")
            if not isinstance(binding_payload, dict):
                raise ValueError("add_tool_binding delta is missing payload.tool_binding")
            binding = ToolBinding.model_validate(binding_payload)
            if any(item.ref == binding.ref for item in step.tool_policy):
                return step
            return step.model_copy(update={"tool_policy": [*step.tool_policy, binding]})
        if delta.change_type == "add_guard":
            guard_payload = payload.get("guard")
            if not isinstance(guard_payload, dict):
                raise ValueError("add_guard delta is missing payload.guard")
            guard = GuardDef.model_validate(guard_payload)
            if any(item.kind == guard.kind and item.value == guard.value for item in step.guards):
                return step
            return step.model_copy(update={"guards": [*step.guards, guard]})
        if delta.change_type == "add_step_transition":
            transition_payload = payload.get("transition")
            if not isinstance(transition_payload, dict):
                raise ValueError("add_step_transition delta is missing payload.transition")
            transition = StepTransition.model_validate(transition_payload)
            if any(item.id == transition.id for item in step.transitions):
                return step
            return step.model_copy(update={"transitions": [*step.transitions, transition]})
        if delta.change_type == "update_step_transition":
            transition_id = str(payload.get("transition_id") or "").strip()
            transition_payload = payload.get("transition")
            if not transition_id or not isinstance(transition_payload, dict):
                raise ValueError("update_step_transition delta is missing transition identity or payload")
            updated_transition = StepTransition.model_validate(transition_payload)
            return step.model_copy(
                update={
                    "transitions": [
                        updated_transition if item.id == transition_id else item
                        for item in step.transitions
                    ]
                }
            )
        if delta.change_type == "delete_step_transition":
            transition_id = str(payload.get("transition_id") or "").strip()
            if not transition_id:
                raise ValueError("delete_step_transition delta is missing payload.transition_id")
            return step.model_copy(update={"transitions": [item for item in step.transitions if item.id != transition_id]})
        raise ValueError(f"unsupported step delta change_type: {delta.change_type}")
