"""Turn-processing service extracted from ``create_app()`` (RP-3.1 step 11).

The route-decomposition pivot: every kernel call site (widget messages/SSE,
``/conversations/{id}/turns``, simulation replay, synthetic channels, the
Meta WhatsApp webhook, LiveKit transcripts/messages, and the channel-ingress
helpers) flows through ``ConversationTurnService``. ``create_app()``
constructs one instance and REBINDS the old closure names
(``_process_turn_with_intent_tags`` etc.) to the service's bound methods, so
all call sites are textually untouched — routes land on the service
explicitly in steps 12–16.

Dependencies that remain application-construction state stay in api.py and
are threaded as callables (blueprint closure-capture hazard):

- ``agent_settings_resolver`` — the SAME ``_agent_settings`` resolver the
  agent_presentation factories produce (step 10), not a duplicate.
- ``preclassification_profile_resolver`` — api.py's
  ``_effective_preclassification_profile`` (it owns the per-step fallback
  intent catalog and the intent-tags profile service).
- ``company_name_lookup`` — H6: ``_widget_config`` itself stays in api.py;
  the lookup defaults to a lambda over it, and
  :meth:`ConversationTurnService._with_response_generation_metadata`
  preserves the swallow-to-None try/except semantics around the call
  exactly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from fastapi import HTTPException

from ..analytics_tagging.adapters import IntentTagsClassificationRequest
from ..analytics_tagging.runtime_integration import (
    CLASSIFIER_ADAPTER_NAME_METADATA_KEY,
    CLASSIFIER_LANGUAGE_CONFIDENCE_METADATA_KEY,
    CLASSIFIER_LANGUAGE_METADATA_KEY,
    CLASSIFIER_METADATA_METADATA_KEY,
    CLASSIFIER_MODEL_VERSION_METADATA_KEY,
    CLASSIFIER_RESPONSE_LANGUAGE_METADATA_KEY,
    CLASSIFIER_SEMANTIC_EVENTS_METADATA_KEY,
    CLASSIFIER_SIGNALS_METADATA_KEY,
    CLASSIFIER_SLOTS_METADATA_KEY,
)
from ..schemas import ActionRecord, RuntimeTurn, RuntimeTurnResult
from .kernel_executor import run_in_kernel_executor

if TYPE_CHECKING:
    from ..agent_document import AgentDocument
    from ..api_models import AgentSettings
    from ..analytics_tagging.runtime_integration import IntentTagsRuntimeIntegrator
    from ..kernel import ConversationKernel
    from ..registry import SQLAlchemyAgentRegistry

__all__ = ["ConversationTurnService"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConversationTurnService:
    """Owns kernel turn processing plus its intent-tags enrichment envelope.

    Sync end-to-end (the kernel is sync); async routes hop onto the explicit
    kernel executor via :meth:`aprocess_turn`.
    """

    kernel: ConversationKernel
    agent_registry: SQLAlchemyAgentRegistry
    intent_tags_integrator: IntentTagsRuntimeIntegrator | None
    agent_settings_resolver: Callable[..., AgentSettings]
    company_name_lookup: Callable[[str], str | None]
    preclassification_profile_resolver: Callable[..., Any]

    def project_intent_tags_result(
        self,
        *,
        conversation_id: str,
        agent_document: AgentDocument | None = None,
        agent_name: str | None = None,
        result: RuntimeTurnResult,
        turn: RuntimeTurn | None = None,
    ) -> RuntimeTurnResult:
        if self.intent_tags_integrator is None:
            return result
        conversation = self.kernel.load_conversation(conversation_id)
        if conversation is None:
            return result
        if agent_document is None or agent_name is None:
            try:
                snapshot = self.agent_registry.get_version_snapshot(
                    conversation.agent_version_id,
                    organization_id=conversation.organization_id,
                )
            except KeyError:
                snapshot = None
            if agent_document is None:
                agent_document = None if snapshot is None else snapshot.agent_document
            if agent_name is None:
                agent_name = None if snapshot is None else snapshot.name
        try:
            self.intent_tags_integrator.handle_result(
                conversation=conversation,
                result=result,
                agent_document=agent_document,
                agent_name=agent_name,
                turn=turn,
            )
        except Exception:
            logger.exception(
                "intent-tags runtime projection failed",
                extra={"conversation_id": conversation_id, "trace_id": result.trace_id},
            )
        return result

    def process_turn(
        self,
        conversation_id: str,
        turn: RuntimeTurn,
        *,
        agent_document: AgentDocument,
        agent_id: str,
        agent_name: str,
        organization_id: str | None = None,
        on_first_sentence: object | None = None,
    ) -> RuntimeTurnResult:
        enriched_turn = self._with_response_generation_metadata(
            turn,
            agent_id=agent_id,
            organization_id=organization_id,
        )
        if self.intent_tags_integrator is not None:
            enriched_turn = self._with_classifier_semantic_metadata(
                conversation_id=conversation_id,
                agent_document=agent_document,
                agent_id=agent_id,
                agent_name=agent_name,
                turn=enriched_turn,
                organization_id=organization_id,
            )
        result = self.kernel.process_turn(
            conversation_id,
            enriched_turn,
            agent_document=agent_document,
            agent_id=agent_id,
            agent_name=agent_name,
            organization_id=organization_id,
            on_first_sentence=on_first_sentence,
        )
        return self.project_intent_tags_result(
            conversation_id=conversation_id,
            agent_document=agent_document,
            agent_name=agent_name,
            result=result,
            turn=enriched_turn,
        )

    async def aprocess_turn(
        self,
        app: Any,
        conversation_id: str,
        turn: RuntimeTurn,
        *,
        agent_document: AgentDocument,
        agent_id: str,
        agent_name: str,
        organization_id: str | None = None,
        on_first_sentence: object | None = None,
    ) -> RuntimeTurnResult:
        """Run :meth:`process_turn` on the app's explicit kernel executor.

        ``app`` is the FastAPI application (handlers pass ``request.app``) —
        Request objects must not leak into the service layer.
        """
        return await run_in_kernel_executor(
            app,
            self.process_turn,
            conversation_id,
            turn,
            agent_document=agent_document,
            agent_id=agent_id,
            agent_name=agent_name,
            organization_id=organization_id,
            on_first_sentence=on_first_sentence,
        )

    def _with_classifier_semantic_metadata(
        self,
        *,
        conversation_id: str,
        agent_document: AgentDocument,
        agent_id: str,
        agent_name: str,
        turn: RuntimeTurn,
        organization_id: str | None,
    ) -> RuntimeTurn:
        if self.intent_tags_integrator is None:
            return turn
        if turn.event_type not in {"user_message", "user_final_transcript"}:
            return turn
        conversation = self.kernel.load_conversation(conversation_id)
        if conversation is None or conversation.mode != "live":
            return turn
        resolved_organization_id = organization_id or conversation.organization_id
        try:
            step = agent_document.step_by_id(conversation.step_id)
            resolved_profile = self.preclassification_profile_resolver(
                conversation=conversation,
                agent_document=agent_document,
                step=step,
                agent_id=agent_id,
                organization_id=resolved_organization_id,
            )
            if resolved_profile is None:
                return turn
            bootstrap_result = RuntimeTurnResult(
                turn_id=turn.turn_id,
                conversation_id=conversation.conversation_id,
                step_before=conversation.step_id,
                step_after=conversation.step_id,
                semantic_events=[],
                fact_updates=[],
                chosen_action=ActionRecord(type="stay", reason="preclassification"),
                emitted_messages=[],
                tool_calls=[],
                trace_id=f"{turn.turn_id}:preclass",
            )
            classifier_result = self.intent_tags_integrator.classifier_registry.classify(
                IntentTagsClassificationRequest(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    schema_version=agent_document.version,
                    agent_document=agent_document,
                    step=step,
                    conversation=conversation,
                    turn=turn,
                    result=bootstrap_result,
                    resolved_profile=resolved_profile,
                )
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "intent-tags preclassification failed",
                extra={"conversation_id": conversation_id, "turn_id": turn.turn_id},
            )
            raise HTTPException(
                status_code=503,
                detail=f"intent classifier is enabled but unavailable: {exc}",
            ) from exc
        if not classifier_result.semantic_events:
            return turn
        metadata = dict(turn.metadata)
        metadata[CLASSIFIER_SEMANTIC_EVENTS_METADATA_KEY] = [
            event.model_dump(mode="json")
            for event in classifier_result.semantic_events
        ]
        metadata[CLASSIFIER_ADAPTER_NAME_METADATA_KEY] = classifier_result.adapter_name
        metadata[CLASSIFIER_MODEL_VERSION_METADATA_KEY] = classifier_result.model_version
        if classifier_result.metadata:
            metadata[CLASSIFIER_METADATA_METADATA_KEY] = dict(classifier_result.metadata)
        if classifier_result.slots:
            metadata[CLASSIFIER_SLOTS_METADATA_KEY] = dict(classifier_result.slots)
        if classifier_result.signals:
            metadata[CLASSIFIER_SIGNALS_METADATA_KEY] = dict(classifier_result.signals)
        if classifier_result.language is not None:
            metadata[CLASSIFIER_LANGUAGE_METADATA_KEY] = classifier_result.language
        if classifier_result.response_language is not None:
            metadata[CLASSIFIER_RESPONSE_LANGUAGE_METADATA_KEY] = classifier_result.response_language
        if classifier_result.language_confidence is not None:
            metadata[CLASSIFIER_LANGUAGE_CONFIDENCE_METADATA_KEY] = classifier_result.language_confidence
        return turn.model_copy(update={"metadata": metadata})

    def _with_response_generation_metadata(
        self,
        turn: RuntimeTurn,
        *,
        agent_id: str,
        organization_id: str | None,
    ) -> RuntimeTurn:
        try:
            settings = self.agent_settings_resolver(agent_id, organization_id=organization_id)
        except Exception:
            return turn
        # Behavioural persona lives on the *published* agent document — not the
        # draft. The runtime always uses published behaviour, so look up the
        # current published version. Falls back to None if no published version
        # exists (new agents) or anything fails defensively.
        behavioral_persona = None
        company_name: str | None = None
        try:
            registration = self.agent_registry.get_agent_registration(
                agent_id, organization_id=organization_id
            )
            published_id = registration.current_published_version_id
            if published_id is not None:
                snapshot = self.agent_registry.get_version_snapshot(
                    agent_id,
                    version_id=published_id,
                    organization_id=organization_id,
                )
                if snapshot.agent_document is not None:
                    behavioral_persona = snapshot.agent_document.behavioral_persona()
        except Exception:
            behavioral_persona = None
        try:
            company_name = self.company_name_lookup(agent_id)
        except Exception:
            company_name = None
        metadata = dict(turn.metadata)
        metadata["__ruhu_response_generation"] = {
            "provider": settings.llm_config.provider,
            "model": settings.llm_config.model,
            "system_prompt": settings.composed_system_prompt(
                behavioral=behavioral_persona,
                company_name=company_name,
            ),
            "metadata": {
                "agent_type": settings.agent_type,
                "classifier_strategy": settings.llm_config.classifier.strategy,
            },
        }
        return turn.model_copy(update={"metadata": metadata})

    def confirm_tool_invocation(
        self,
        conversation_id: str,
        invocation_id: str,
    ) -> RuntimeTurnResult:
        conversation = self.kernel.load_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        snapshot = self.agent_registry.get_version_snapshot(
            conversation.agent_version_id,
            organization_id=conversation.organization_id,
        )
        result = self.kernel.confirm_tool_invocation(
            conversation_id,
            snapshot.agent_document,
            invocation_id,
            agent_name=snapshot.name,
        )
        return self.project_intent_tags_result(
            conversation_id=conversation_id,
            agent_document=snapshot.agent_document,
            agent_name=snapshot.name,
            result=result,
        )

    def cancel_tool_invocation(
        self,
        conversation_id: str,
        invocation_id: str,
    ) -> RuntimeTurnResult:
        conversation = self.kernel.load_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        snapshot = self.agent_registry.get_version_snapshot(
            conversation.agent_version_id,
            organization_id=conversation.organization_id,
        )
        result = self.kernel.cancel_tool_invocation(
            conversation_id,
            snapshot.agent_document,
            invocation_id,
            agent_name=snapshot.name,
        )
        return self.project_intent_tags_result(
            conversation_id=conversation_id,
            agent_document=snapshot.agent_document,
            agent_name=snapshot.name,
            result=result,
        )

    def reconcile_tool_invocation_result(
        self,
        conversation_id: str,
        invocation_id: str,
    ) -> RuntimeTurnResult:
        conversation = self.kernel.load_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        snapshot = self.agent_registry.get_version_snapshot(
            conversation.agent_version_id,
            organization_id=conversation.organization_id,
        )
        result = self.kernel.reconcile_tool_invocation_result(
            conversation_id,
            snapshot.agent_document,
            invocation_id,
            agent_name=snapshot.name,
        )
        return self.project_intent_tags_result(
            conversation_id=conversation_id,
            agent_document=snapshot.agent_document,
            agent_name=snapshot.name,
            result=result,
        )

    def project_tool_invocation_progress(
        self,
        conversation_id: str,
        invocation_id: str,
    ) -> RuntimeTurnResult | None:
        conversation = self.kernel.load_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        snapshot = self.agent_registry.get_version_snapshot(
            conversation.agent_version_id,
            organization_id=conversation.organization_id,
        )
        result = self.kernel.project_tool_invocation_progress(
            conversation_id,
            snapshot.agent_document,
            invocation_id,
            agent_name=snapshot.name,
        )
        if result is None:
            return None
        return self.project_intent_tags_result(
            conversation_id=conversation_id,
            agent_document=snapshot.agent_document,
            agent_name=snapshot.name,
            result=result,
        )
