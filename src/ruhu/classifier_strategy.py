"""Strategy-aware workflow-outcome classifier interpreter.

Implements the per-agent classifier-strategy contract from
``api_models.AgentClassifierConfig``:

- ``off``     → no routing events emitted; kernel routes on facts/tool
                outcomes/``otherwise`` only.
- ``main_llm`` (default for new agents) → Vertex Gemini Flash classifies
                each turn against the step's outcome catalog. Cold-start
                safe.
- ``prefill`` → small prefill-first classifier (Gemma/Qwen + production
                LoRA). Selectable only when the agent has a promoted
                LoRA that passed eval; otherwise this interpreter
                emits a loud ``classifier_unavailable`` event so the
                kernel routes through deterministic fallbacks rather
                than silently switching strategy.

The kernel constructs one ``StrategyAwareInterpreter`` at startup and
calls ``interpret()`` per turn. The instance reads the agent's strategy
through ``settings_resolver`` so settings changes take effect on the
next turn without restart.

Routing contract emitted to the kernel:

- ``family="routing", name="outcome_resolved"`` with payload
  ``{event, transition_id, classifier_trace}`` when the classifier picks
  an outcome.
- ``family="routing", name="classifier_unavailable"`` with a ``reason``
  payload when the classifier is unreachable, returns UNKNOWN, or returns
  a label outside the step's catalog. The kernel preserves these in the
  trace and downgrades to ``OtherwiseCondition`` routing.

The legacy ``family="intent_detected"`` and ``intent_tags:classifier_unavailable``
shapes are gone — they are reserved for the analytics subsystem
(``analytics_tagging/runtime_integration.py``), which emits its own intents/tags
post-turn for analytics, not for routing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from .agent_document import AgentDocument, Step
from .api_models import AgentSettings, ClassifierStrategy
from .classifier.prompt import build_classifier_prompt, outcome_catalog_for_step
from .classifier.protocol import (
    ClassificationRequest,
    ClassificationResult,
    PrefillClassifier,
)
from .interpreter import SemanticInterpreter
from .schemas import OutcomeCondition, RuntimeTurn, SemanticEventRecord
from .state_summary import summarize_step

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoRAEligibility:
    """Result of checking whether an agent can run on the prefill backend.

    ``available`` is True iff the agent has a LoRA in production status
    that passed the eval threshold per ``classifier.publish_gate``. The
    ``reason`` string is surfaced to the operator and recorded on
    ``classifier_unavailable`` events for diagnostics.
    """
    available: bool
    lora_name: str | None = None
    reason: str = ""


SettingsResolver = Callable[[str], AgentSettings | None]
LoRAEligibilityResolver = Callable[[str, str], LoRAEligibility]


@dataclass(slots=True)
class StrategyAwareInterpreter(SemanticInterpreter):
    """Per-turn dispatch on ``agent_settings.llm_config.classifier.strategy``."""

    # Resolves an agent's full settings. Returning None means "fall back
    # to defaults" — i.e. main_llm strategy. Tests can pass a fake.
    settings_resolver: SettingsResolver | None = None

    # The Vertex Gemini (or any frontier-LLM) classifier backend used for
    # ``main_llm``. None means main_llm is unconfigured; if a turn lands
    # there we emit ``classifier_unavailable``.
    main_llm_classifier: PrefillClassifier | None = None
    main_llm_model_name: str | None = None

    # The pre-existing prefill stack wrapped in a SemanticInterpreter
    # (typically ``GemmaLocalInterpreter`` or a vLLM-backed equivalent).
    # None means prefill is unconfigured.
    prefill_interpreter: SemanticInterpreter | None = None

    # Per-agent LoRA eligibility check. Receives (agent_id, step_id) and
    # returns whether the prefill backend should accept this turn. Tests
    # can pass a fake. Prod resolver consults
    # ``classifier.registry`` + ``classifier.publish_gate``.
    lora_eligibility_resolver: LoRAEligibilityResolver | None = None

    # Default strategy when no settings_resolver is wired or the resolver
    # returns None. ``main_llm`` is the conservative default.
    default_strategy: ClassifierStrategy = "main_llm"

    # Optional: callbacks for telemetry. Kept here so tests can verify
    # routing decisions without spinning up the metrics stack.
    on_decision: Callable[[str, str, str], None] | None = field(default=None)

    def interpret(
        self,
        *,
        agent_document: AgentDocument,
        step: Step,
        agent_id: str,
        agent_name: str,
        conversation_facts: dict[str, object],
        turn: RuntimeTurn,
    ) -> list[SemanticEventRecord]:
        text = (turn.text or "").strip()
        if not text:
            return []

        strategy = self._resolve_strategy(agent_id)
        if self.on_decision is not None:
            try:
                self.on_decision(agent_id, step.id, strategy)
            except Exception:
                logger.debug("on_decision callback failed", exc_info=True)

        if strategy == "off":
            return []

        if strategy == "prefill":
            return self._interpret_prefill(
                agent_document=agent_document,
                step=step,
                agent_id=agent_id,
                agent_name=agent_name,
                conversation_facts=conversation_facts,
                turn=turn,
            )

        # main_llm (default)
        return self._interpret_main_llm(
            agent_document=agent_document,
            step=step,
            agent_id=agent_id,
            agent_name=agent_name,
            conversation_facts=conversation_facts,
            turn=turn,
        )

    # ─── Strategy resolution ────────────────────────────────────────────

    def _resolve_strategy(self, agent_id: str) -> ClassifierStrategy:
        if self.settings_resolver is None:
            return self.default_strategy
        try:
            settings = self.settings_resolver(agent_id)
        except Exception:
            logger.warning(
                "classifier strategy: settings resolver failed for %s; "
                "defaulting to %s",
                agent_id,
                self.default_strategy,
                exc_info=True,
            )
            return self.default_strategy
        if settings is None:
            return self.default_strategy
        try:
            return settings.llm_config.classifier.strategy
        except AttributeError:
            return self.default_strategy

    # ─── prefill branch ─────────────────────────────────────────────────

    def _interpret_prefill(
        self,
        *,
        agent_document: AgentDocument,
        step: Step,
        agent_id: str,
        agent_name: str,
        conversation_facts: dict[str, object],
        turn: RuntimeTurn,
    ) -> list[SemanticEventRecord]:
        if self.prefill_interpreter is None:
            return classifier_unavailable_event(
                reason="prefill_not_configured",
                strategy="prefill",
            )
        eligibility = self._check_prefill_eligibility(agent_id, step.id)
        if not eligibility.available:
            return classifier_unavailable_event(
                reason=eligibility.reason or "prefill_no_production_lora",
                strategy="prefill",
            )
        return self.prefill_interpreter.interpret(
            agent_document=agent_document,
            step=step,
            agent_id=agent_id,
            agent_name=agent_name,
            conversation_facts=conversation_facts,
            turn=turn,
        )

    def _check_prefill_eligibility(self, agent_id: str, step_id: str) -> LoRAEligibility:
        if self.lora_eligibility_resolver is None:
            # No eligibility checker wired at all → reject conservatively.
            # Operators must wire the resolver to allow prefill.
            return LoRAEligibility(
                available=False,
                reason="lora_eligibility_resolver_missing",
            )
        try:
            return self.lora_eligibility_resolver(agent_id, step_id)
        except Exception as exc:
            logger.warning(
                "classifier strategy: LoRA eligibility check failed for %s/%s: %s",
                agent_id,
                step_id,
                exc,
                exc_info=True,
            )
            return LoRAEligibility(
                available=False,
                reason=f"lora_eligibility_error:{type(exc).__name__}",
            )

    # ─── main_llm branch ────────────────────────────────────────────────

    def _interpret_main_llm(
        self,
        *,
        agent_document: AgentDocument,
        step: Step,
        agent_id: str,
        agent_name: str,
        conversation_facts: dict[str, object],
        turn: RuntimeTurn,
    ) -> list[SemanticEventRecord]:
        if self.main_llm_classifier is None:
            # No dedicated frontier-LLM classifier wired — fall through to
            # the configured kernel interpreter (keyword/Gemma/etc.). This
            # keeps dev and test environments working without Vertex creds
            # and matches the pre-strategy-split behaviour where the
            # configured interpreter ran as the agent's classifier.
            if self.prefill_interpreter is not None:
                return self.prefill_interpreter.interpret(
                    agent_document=agent_document,
                    step=step,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    conversation_facts=conversation_facts,
                    turn=turn,
                )
            return classifier_unavailable_event(
                reason="main_llm_not_configured",
                strategy="main_llm",
            )
        text = (turn.text or "").strip()
        candidate_labels = outcome_catalog_for_step(step)
        if not candidate_labels:
            # Step has no outcome transitions and no universal outcomes
            # apply (impossible today since universal outcomes are always
            # appended, but keep the guard in case that invariant ever
            # changes). Skip the classifier call rather than waste tokens.
            return []
        try:
            prefix, suffix = build_classifier_prompt(
                agent_document, step, user_text=text, facts=conversation_facts
            )
        except Exception:
            logger.exception(
                "classifier strategy: failed to build classifier prompt for %s/%s",
                agent_id,
                step.id,
            )
            return classifier_unavailable_event(
                reason="prompt_build_failed",
                strategy="main_llm",
            )
        request = ClassificationRequest(
            agent_id=agent_id,
            agent_version_id=agent_document.version,
            step_id=step.id,
            step_name=step.name,
            step_summary=summarize_step(step),
            user_text=text,
            candidate_labels=candidate_labels,
            prefix=prefix,
            suffix=suffix,
        )
        try:
            result = self.main_llm_classifier.classify(request)
        except Exception as exc:
            # The Vertex backend already coerces errors to a result with
            # ``error`` set, but anything else (network panics, type errors
            # from a misconfigured backend) lands here.
            logger.warning(
                "classifier strategy: main_llm classifier raised: %s",
                exc,
                exc_info=True,
            )
            return classifier_unavailable_event(
                reason=f"main_llm_exception:{type(exc).__name__}",
                strategy="main_llm",
            )
        return result_to_routing_events(
            result,
            step=step,
            candidate_labels=candidate_labels,
            model_name=self.main_llm_model_name,
            strategy="main_llm",
        )


# ─── Helpers ────────────────────────────────────────────────────────────


def resolve_transition_id_for_event(step: Step, event: str) -> str | None:
    """Find the step transition whose outcome event matches ``event``.

    Returns the transition's ``id`` if exactly one matches; ``None`` if
    no authored transition does (the event may still be a universal
    outcome handled framework-side by the kernel). Per-step uniqueness of
    ``OutcomeCondition.event`` is validated on the ``Step`` model, so at
    most one match is possible.
    """
    for transition in step.transitions:
        when = transition.when
        if isinstance(when, OutcomeCondition) and when.event == event:
            return transition.id
    return None


def result_to_routing_events(
    result: ClassificationResult,
    *,
    step: Step,
    candidate_labels: dict[str, str],
    model_name: str | None,
    strategy: ClassifierStrategy,
) -> list[SemanticEventRecord]:
    if result.error:
        return classifier_unavailable_event(
            reason=f"{strategy}_backend_error:{result.error}",
            strategy=strategy,
        )
    if result.chosen_label is None:
        # Backend returned a successful response but couldn't pick a label
        # (UNKNOWN). Surface at WARNING so we can diagnose silent
        # routing failures from the runtime log; the kernel still falls
        # through to ``OtherwiseCondition`` regardless.
        logger.warning(
            "classifier returned no outcome (strategy=%s, backend=%s): "
            "model produced UNKNOWN against catalog %s",
            strategy,
            result.backend,
            sorted(candidate_labels.keys()),
        )
        return classifier_unavailable_event(
            reason=f"{strategy}_unknown",
            strategy=strategy,
        )
    if result.chosen_label not in candidate_labels:
        # Returned label is not in the catalog. Treat as no-route rather
        # than a hard failure so the kernel falls through to
        # ``OtherwiseCondition``. This is the silent-miss path that masks
        # classifier mis-mapping in prod — log loudly so it's diagnosable
        # from the runtime log.
        logger.warning(
            "classifier label not in catalog (strategy=%s, backend=%s): "
            "returned %r, valid labels=%s",
            strategy,
            result.backend,
            result.chosen_label,
            sorted(candidate_labels.keys()),
        )
        return classifier_unavailable_event(
            reason=f"{strategy}_label_out_of_catalog:{result.chosen_label}",
            strategy=strategy,
        )

    transition_id = resolve_transition_id_for_event(step, result.chosen_label)
    logger.info(
        "classifier outcome (strategy=%s, backend=%s): %r -> transition=%s",
        strategy,
        result.backend,
        result.chosen_label,
        transition_id or "<universal-or-unrouted>",
    )

    classifier_trace = {
        "backend": result.backend,
        "model": model_name,
        "lora_name": result.lora_name,
        "chosen_label": result.chosen_label,
        "transition_id": transition_id,
        "confidence": result.confidence,
        "decode_logprobs": dict(result.decode_logprobs or {}),
        "cache_hit": result.cache_hit,
        "prefill_tokens": result.prefill_tokens,
        "decode_tokens": result.decode_tokens,
        "elapsed_ms": result.elapsed_ms,
        "error": result.error,
        "strategy": strategy,
    }
    return [
        SemanticEventRecord(
            family="routing",
            name="outcome_resolved",
            source="classifier",
            confidence=result.confidence,
            payload={
                "event": result.chosen_label,
                "transition_id": transition_id,
                "classifier_trace": classifier_trace,
            },
        )
    ]


def classifier_unavailable_event(
    *,
    reason: str,
    strategy: ClassifierStrategy,
) -> list[SemanticEventRecord]:
    """Emit the workflow-routing classifier-unavailable signal.

    The kernel preserves this on the trace + sets
    ``decision_observability.degraded_mode = "classifier_unavailable"``.
    Distinct from the analytics subsystem's ``intent_tags:classifier_unavailable``
    event (which the hosted intent-tags adapter emits on its own outage
    paths) — workflow routing is a separate concern.
    """
    return [
        SemanticEventRecord(
            family="routing",
            name="classifier_unavailable",
            source="system",
            confidence=1.0,
            payload={
                "strategy": strategy,
                "reason": reason,
            },
        )
    ]
