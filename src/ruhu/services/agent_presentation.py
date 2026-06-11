"""Agent presentation helpers extracted from ``create_app()`` (RP-3.1 step 3).

Seeded with the template-provenance helpers that enrich the publish-review
surface (optional-tool classification and missing-tool remediation hints per
Template-Required-Tools-Onboarding-Spec §5.5.1). Step 10 grew it with the
rest of the agent presentation layer: version summaries, validation reports,
settings / evaluation-policy resolution, and publish-review assembly — the
helpers the ``routes.agents`` builders consume.

### Why factories, not plain functions

Same reasoning as ``services.org_scope``: most helpers close over
application-construction state (``agent_registry``, ``template_store``,
``runtime_session_factory``) that only exists inside ``create_app()``. The
factories return closures and ``create_app()`` REBINDS the old local names to
the factory outputs, so downstream references inside ``create_app()`` are
textually untouched. Helpers with no construction state
(``agent_version_summary``, ``validation_report``, the defaults) are plain
functions.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Callable

from fastapi import HTTPException

from ..agent_document import AgentValidationReport, validate_agent_document
from ..agent_review import (
    AgentPublishReadiness,
    PublishReviewItem,
    PublishReviewRemediation,
    apply_publish_qualification,
    build_publish_readiness,
)
from ..api_models import AgentSettings, AgentSettingsPatchRequest, AgentSummary, AgentVersionSummary
from ..simulation_eval import EvaluationPolicyConfig

if TYPE_CHECKING:
    from ..registry import AgentRegistration, AgentVersionSnapshot, SQLAlchemyAgentRegistry

__all__ = [
    "agent_version_summary",
    "default_agent_settings",
    "default_evaluation_policy",
    "deep_merge_mapping",
    "make_agent_evaluation_policy",
    "make_agent_settings",
    "make_agent_summary",
    "make_build_agent_publish_review",
    "make_resolved_agent_settings",
    "make_resolve_optional_tool_refs",
    "make_resolve_missing_tool_remediation",
    "make_validate_classifier_strategy",
    "make_version_summary_by_id",
    "validation_report",
]

logger = logging.getLogger(__name__)


def agent_version_summary(snapshot: "AgentVersionSnapshot") -> AgentVersionSummary:
    document = snapshot.agent_document
    if document is None:
        raise HTTPException(status_code=500, detail="agent version is missing agent document")
    return AgentVersionSummary(
        version_id=snapshot.version_id,
        agent_id=snapshot.agent_id,
        status=snapshot.status,
        version_number=snapshot.version_number,
        schema_version=document.version,
        based_on_version_id=snapshot.based_on_version_id,
        published_at=snapshot.published_at,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
        is_current_draft=snapshot.is_current_draft,
        is_current_published=snapshot.is_current_published,
    )


def validation_report(snapshot: "AgentVersionSnapshot") -> AgentValidationReport:
    if snapshot.agent_document is None:
        raise HTTPException(status_code=500, detail="agent document unavailable for validation")
    return validate_agent_document(snapshot.agent_document)


def default_agent_settings() -> AgentSettings:
    return AgentSettings()


def default_evaluation_policy() -> EvaluationPolicyConfig:
    return EvaluationPolicyConfig()


def deep_merge_mapping(
    base: dict[str, object],
    updates: Mapping[str, object],
) -> dict[str, object]:
    result = dict(base)
    for key, value in updates.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            result[key] = deep_merge_mapping(current, dict(value))
        else:
            result[key] = value
    return result


def make_agent_summary(
    *,
    agent_registry: "SQLAlchemyAgentRegistry",
) -> Callable[..., AgentSummary | None]:
    """Build the list-surface AgentSummary resolver."""

    def _agent_summary(registration: "AgentRegistration", *, organization_id: str | None = None) -> AgentSummary | None:
        """Return an AgentSummary for *registration*, or None if its version snapshot
        cannot be resolved (e.g. the version row is missing or RLS-filtered out).
        Callers must filter out None entries.
        """
        active_snapshot: AgentVersionSnapshot | None = None
        try:
            if registration.current_draft_version_id is not None:
                active_snapshot = agent_registry.get_version_snapshot(
                    registration.current_draft_version_id,
                    organization_id=organization_id,
                )
            elif registration.current_published_version_id is not None:
                active_snapshot = agent_registry.get_version_snapshot(
                    registration.current_published_version_id,
                    organization_id=organization_id,
                )
        except (KeyError, Exception):
            logger.warning(
                "agent version snapshot not found — skipping agent in summary",
                extra={"agent_id": registration.agent_id},
            )
            return None
        if active_snapshot is None:
            return None
        if active_snapshot.agent_document is None:
            logger.warning(
                "agent document missing from version snapshot — skipping agent in summary",
                extra={"agent_id": registration.agent_id, "version_id": active_snapshot.version_id},
            )
            return None
        settings_payload = registration.settings.get("agent_settings")
        if isinstance(settings_payload, dict):
            agent_settings = AgentSettings.model_validate(settings_payload)
        else:
            agent_settings = default_agent_settings()
        # Determine whether the draft differs from published.
        has_unpublished = False
        if (
            registration.current_draft_version_id is not None
            and registration.current_published_version_id is not None
        ):
            try:
                published_snapshot = agent_registry.get_version_snapshot(
                    registration.current_published_version_id,
                    organization_id=organization_id,
                )
                if published_snapshot.agent_document is not None:
                    has_unpublished = (
                        active_snapshot.agent_document.model_dump(mode="json")
                        != published_snapshot.agent_document.model_dump(mode="json")
                    )
            except (KeyError, Exception):
                has_unpublished = False
        return AgentSummary(
            id=registration.agent_id,
            name=registration.name,
            version=active_snapshot.agent_document.version,
            step_count=len(active_snapshot.agent_document.steps),
            description=agent_settings.description,
            agent_type=agent_settings.agent_type,
            llm_provider=agent_settings.llm_config.provider,
            llm_model=agent_settings.llm_config.model,
            knowledge_base_count=len(agent_settings.knowledge_base_ids),
            has_draft_version=registration.current_draft_version_id is not None,
            has_published_version=registration.current_published_version_id is not None,
            has_unpublished_changes=has_unpublished,
            updated_at=registration.updated_at,
            current_draft_version_id=registration.current_draft_version_id,
            current_published_version_id=registration.current_published_version_id,
            is_widget_enabled=registration.is_widget_enabled,
            widget_mode=registration.widget_mode,
            widget_config=dict(registration.widget_config or {}),
        )

    return _agent_summary


def make_version_summary_by_id(
    *,
    agent_registry: "SQLAlchemyAgentRegistry",
) -> Callable[..., "AgentVersionSnapshot"]:
    """Build the version-snapshot-or-404 resolver."""

    def _version_summary_by_id(
        agent_id: str,
        version_id: str,
        *,
        organization_id: str | None = None,
    ) -> "AgentVersionSnapshot":
        try:
            return agent_registry.get_version_snapshot(version_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _version_summary_by_id


def make_agent_evaluation_policy(
    *,
    agent_registry: "SQLAlchemyAgentRegistry",
) -> Callable[..., EvaluationPolicyConfig]:
    """Build the per-agent evaluation-policy resolver."""

    def _agent_evaluation_policy(
        agent_id: str,
        *,
        organization_id: str | None,
    ) -> EvaluationPolicyConfig:
        registration = agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
        payload = registration.settings.get("evaluation_policy")
        if isinstance(payload, dict):
            return EvaluationPolicyConfig.model_validate(payload)
        return default_evaluation_policy()

    return _agent_evaluation_policy


def make_agent_settings(
    *,
    agent_registry: "SQLAlchemyAgentRegistry",
) -> Callable[..., AgentSettings]:
    """Build the per-agent settings resolver."""

    def _agent_settings(
        agent_id: str,
        *,
        organization_id: str | None,
    ) -> AgentSettings:
        registration = agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
        payload = registration.settings.get("agent_settings")
        if isinstance(payload, dict):
            return AgentSettings.model_validate(payload)
        return default_agent_settings()

    return _agent_settings


def make_resolved_agent_settings(
    *,
    agent_settings: Callable[..., AgentSettings],
) -> Callable[..., AgentSettings]:
    """Build the PATCH-merge resolver for agent settings."""

    def _resolved_agent_settings(
        agent_id: str,
        payload: AgentSettingsPatchRequest,
        *,
        organization_id: str | None,
    ) -> AgentSettings:
        current = agent_settings(agent_id, organization_id=organization_id)
        updates = payload.model_dump(mode="python", exclude_none=True)
        if not updates:
            return current
        merged = deep_merge_mapping(current.model_dump(mode="python"), updates)
        return AgentSettings.model_validate(merged)

    return _resolved_agent_settings


def make_validate_classifier_strategy(
    *,
    runtime_session_factory,
) -> Callable[..., None]:
    """Build the prefill-strategy gate."""

    def _validate_classifier_strategy(
        agent_id: str,
        next_settings: AgentSettings,
        *,
        organization_id: str | None,
    ) -> None:
        """Reject ``strategy = "prefill"`` when no production LoRA exists.

        UI greying alone is not authoritative — operators or scripts that
        bypass the UI must hit the same gate. The check uses the same
        ``resolve_lora`` the runtime calls, which already filters to
        ``status = "production"``. So "production-status + passed eval"
        is implied by a non-None resolution (eval is enforced by
        ``promote_to_production``).
        """
        from ..classifier.registry import resolve_lora as _resolve_lora_for_validation

        try:
            strategy = next_settings.llm_config.classifier.strategy
        except AttributeError:
            return
        if strategy != "prefill":
            return
        with runtime_session_factory.begin() as session:
            lora_name = _resolve_lora_for_validation(
                session,
                agent_id=agent_id,
                step_id=None,
                organization_id=organization_id,
            )
        if lora_name is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "classifier strategy 'prefill' requires a production-status "
                    f"LoRA for agent '{agent_id}'. Train and promote a LoRA "
                    "first, or set strategy to 'main_llm' / 'off'."
                ),
            )

    return _validate_classifier_strategy


def make_build_agent_publish_review(
    *,
    agent_registry: "SQLAlchemyAgentRegistry",
    version_summary_by_id: Callable[..., "AgentVersionSnapshot"],
    available_tool_refs: Callable[..., list[str]],
    resolve_optional_tool_refs: Callable[..., set[str]],
    resolve_missing_tool_remediation: Callable[..., PublishReviewRemediation],
    simulation_fixture_store,
    evaluation_service,
    agent_evaluation_policy: Callable[..., EvaluationPolicyConfig],
    readiness_store=None,
) -> Callable[..., AgentPublishReadiness]:
    """Build the publish-review assembler.

    ``available_tool_refs`` stays an api.py closure (it reads
    ``kernel.tool_runtime``) and threads in as a kwarg. ``readiness_store`` is
    optional; when present, the latest Atlas readiness verdict and any held
    fix-run apply lock are surfaced as advisory warnings (AR-4.6).
    """

    def _build_agent_publish_review(
        agent_id: str,
        *,
        organization_id: str | None,
    ) -> AgentPublishReadiness:
        try:
            draft_version_id = agent_registry.resolve_version_id(agent_id, target="draft", organization_id=organization_id)
        except KeyError:
            draft_version_id = None
        try:
            published_version_id = agent_registry.resolve_version_id(agent_id, target="published", organization_id=organization_id)
        except KeyError:
            published_version_id = None
        # Use draft if available, otherwise fall back to published for the review snapshot.
        review_version_id = draft_version_id or published_version_id
        if review_version_id is None:
            raise HTTPException(status_code=404, detail=f"agent {agent_id} has no versions")
        draft_snapshot = version_summary_by_id(agent_id, review_version_id, organization_id=organization_id)
        published_snapshot = (
            version_summary_by_id(agent_id, published_version_id, organization_id=organization_id)
            if published_version_id is not None
            else None
        )
        readiness = build_publish_readiness(
            draft_snapshot=draft_snapshot,
            validation=validation_report(draft_snapshot),
            published_snapshot=published_snapshot,
            available_tool_refs=available_tool_refs(organization_id=organization_id),
        )
        # Demote every tool.missing_runtime_spec from blocker to
        # warning.  The strict publish gate is removed — publish
        # always succeeds regardless of whether referenced tools are
        # configured.  Rationale (per design discussion in this
        # session, see also docs/atlas/README.md):
        #
        #   - Old Ruhu had no publish gate at all
        #     (services/agent_deployment_service.py:68-181 validated
        #     LLM/voice config but not tool refs).  This was a
        #     deliberate product choice — match it.
        #   - Atlas (docs/atlas/) is explicitly designed to "validate
        #     readiness before publish" with a richer copilot-driven
        #     model — the rigid gate here was placeholder.
        #   - Axis 2 of the publish-gate gradient (commit 42a386d)
        #     converts mid-conversation missing-tool calls into
        #     structured tool_outcome:_error events that route via
        #     existing transition rules and surface the LLM-rendered
        #     fallback message — no more hard crashes on the call
        #     path.
        #
        # The Setup checklist (post-clone), the Required/Optional
        # badges (gallery + checklist), and the warnings here remain
        # visible — customers SEE what's not set up, they're just no
        # longer prevented from publishing.  Required vs optional
        # metadata still differentiates UX framing (the checklist
        # gates "Continue to canvas" on required satisfaction) but is
        # advisory at publish time.
        if readiness.blockers:
            enriched_blockers: list[PublishReviewItem] = []
            demoted_warnings: list[PublishReviewItem] = []
            optional_refs = resolve_optional_tool_refs(
                agent_id=agent_id,
                organization_id=organization_id,
            )
            for blocker in readiness.blockers:
                if blocker.code == "tool.missing_runtime_spec":
                    missing_ref = blocker.message.rsplit(":", 1)[-1].strip()
                    remediation = resolve_missing_tool_remediation(
                        tool_ref=missing_ref,
                        agent_id=agent_id,
                        organization_id=organization_id,
                    )
                    is_optional = missing_ref in optional_refs
                    demoted_warnings.append(
                        PublishReviewItem(
                            severity="warning",
                            code=(
                                "tool.missing_optional_setup"
                                if is_optional
                                else "tool.missing_required_setup"
                            ),
                            message=(
                                f"{'Optional' if is_optional else 'Required'} tool "
                                f"not configured: {missing_ref}. Conversations that "
                                f"invoke it will degrade gracefully (the agent will "
                                f"say it cannot complete that action). Configure it "
                                f"to enable the affected branches."
                            ),
                            remediation=remediation,
                        )
                    )
                else:
                    enriched_blockers.append(blocker)
            new_warnings = [*readiness.warnings, *demoted_warnings]
            readiness = readiness.model_copy(update={
                "blockers": enriched_blockers,
                "warnings": new_warnings,
                "can_publish": not enriched_blockers,
            })
        # AR-4.6: surface the latest Atlas readiness verdict and any held fix-run
        # apply lock as advisory warnings. Readiness does not own publishing
        # (per docs/atlas), but a do_not_publish verdict or an in-flight fix run
        # must be visible at the publish gate rather than silently ignored.
        # F17: the readiness store requires a concrete org scope; without one
        # (auth-disabled dev) the advisory lookups are skipped, not unscoped.
        if readiness_store is not None and organization_id is not None:
            readiness_warnings: list[PublishReviewItem] = []
            try:
                latest_report = readiness_store.latest_report_for_agent(
                    agent_id, organization_id=organization_id
                )
            except Exception:  # readiness is advisory — never break publish review
                latest_report = None
            if latest_report is not None and latest_report.publish_recommendation == "do_not_publish":
                readiness_warnings.append(
                    PublishReviewItem(
                        severity="warning",
                        code="atlas.readiness_not_ready",
                        message=(
                            "The latest Atlas readiness run recommends do_not_publish. "
                            "Review the readiness report before publishing."
                        ),
                    )
                )
            try:
                fix_in_progress = bool(draft_version_id) and readiness_store.has_active_apply_lock(
                    agent_id, draft_version_id, organization_id=organization_id
                )
            except Exception:
                fix_in_progress = False
            if fix_in_progress:
                readiness_warnings.append(
                    PublishReviewItem(
                        severity="warning",
                        code="atlas.readiness_fix_in_progress",
                        message=(
                            "An Atlas readiness fix run holds an apply lock on this draft. "
                            "Publishing now may race with readiness-proposed changes."
                        ),
                    )
                )
            if readiness_warnings:
                readiness = readiness.model_copy(
                    update={"warnings": [*readiness.warnings, *readiness_warnings]}
                )
        fixtures = simulation_fixture_store.list_for_agent(
            agent_id,
            organization_id=organization_id,
            is_active=True,
        )
        policy = agent_evaluation_policy(agent_id, organization_id=organization_id)
        qualification = evaluation_service.build_publish_qualification_summary(
            draft_snapshot,
            fixtures,
            organization_id=organization_id,
            minimum_pass_rate_ratio=policy.minimum_pass_rate_ratio,
            allow_warning_failures=policy.allow_warning_failures,
            max_qualified_run_age_hours=policy.max_qualified_run_age_hours,
        )
        return apply_publish_qualification(readiness, qualification)

    return _build_agent_publish_review


def make_resolve_optional_tool_refs(
    *,
    agent_registry: "SQLAlchemyAgentRegistry",
    template_store: object | None,
) -> Callable[..., set[str]]:
    """Build the optional-tool-refs resolver for publish review."""

    def _resolve_optional_tool_refs(
        *,
        agent_id: str,
        organization_id: str | None,
    ) -> set[str]:
        """Return the set of tool refs the source template marks as
        optional (required=False). Empty set when there is no template
        provenance; in that case every missing tool is treated as
        required.
        """
        try:
            registration = agent_registry.get_agent_registration(
                agent_id, organization_id=organization_id,
            )
            settings = registration.settings or {}
            agent_settings = settings.get("agent_settings") or {}
            source_template_id = agent_settings.get("source_template_id")
            if not source_template_id or template_store is None:
                return set()
            detail = template_store.get_template_detail(
                str(source_template_id), organization_id=organization_id,
            )
            if detail is None:
                return set()
            return {
                entry.tool_ref
                for entry in detail.required_tools
                if not entry.required
            }
        except Exception:  # noqa: BLE001 — best-effort UX hint; never block publish-review
            logger.debug(
                "optional-tool resolution failed for agent_id=%s; "
                "treating all missing tools as required",
                agent_id,
            )
            return set()

    return _resolve_optional_tool_refs


def make_resolve_missing_tool_remediation(
    *,
    agent_registry: "SQLAlchemyAgentRegistry",
    template_store: object | None,
    resolve_setup_url: Callable[..., str],
) -> Callable[..., PublishReviewRemediation]:
    """Build the missing-tool remediation resolver for publish review.

    ``resolve_setup_url`` is api.py's ``_resolve_setup_url`` — it stays
    there (with the template DTOs) until blueprint step 10 migrates the
    full presentation layer into this module.
    """

    def _resolve_missing_tool_remediation(
        *,
        tool_ref: str,
        agent_id: str,
        organization_id: str | None,
    ) -> PublishReviewRemediation:
        """Build a remediation hint for a publish-review
        ``tool.missing_runtime_spec`` blocker.

        Per Template-Required-Tools-Onboarding-Spec §5.5.1: prefer the
        rich metadata from the cloned template's ``required_tools``
        (looked up via ``agent_settings.source_template_id`` provenance),
        and degrade gracefully to a generic Integrations link when
        provenance is missing or stale.  Never raises — remediation is
        best-effort UX, not a correctness invariant.
        """
        try:
            registration = agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
            settings = registration.settings or {}
            agent_settings = settings.get("agent_settings") or {}
            source_template_id = agent_settings.get("source_template_id")
            if source_template_id and template_store is not None:
                detail = template_store.get_template_detail(
                    str(source_template_id), organization_id=organization_id,
                )
                if detail is not None:
                    for entry in detail.required_tools:
                        if entry.tool_ref == tool_ref:
                            return PublishReviewRemediation(
                                kind="configure_tool",
                                tool_ref=tool_ref,
                                url=resolve_setup_url(
                                    agent_id=agent_id,
                                    template_setup_url_path=entry.setup_url_path,
                                ),
                                label=f"Set up {entry.display_name}",
                                documentation_url=entry.documentation_url,
                            )
        except Exception:  # noqa: BLE001 — never block publish-review on remediation lookup
            logger.debug(
                "remediation lookup failed for tool_ref=%s agent_id=%s; falling back to generic",
                tool_ref, agent_id,
            )
        # Generic fallback: still names the missing ref so the user
        # knows what to look up.
        return PublishReviewRemediation(
            kind="configure_tool",
            tool_ref=tool_ref,
            url=f"/settings/integrations?tool_ref={tool_ref}",
            label=f"Set up {tool_ref}",
            documentation_url=None,
        )

    return _resolve_missing_tool_remediation
