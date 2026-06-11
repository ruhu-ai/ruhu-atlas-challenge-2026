"""Phase 2c — Topic enforcement decorator on ``ResponseGenerator``.

A ``TopicEnforcingResponseGenerator`` wraps any ``ResponseGenerator``
(``GeminiDialogueGenerator`` in production, mocks in tests) and enforces
``BehavioralPersona.restricted_topics`` post-render.

This module ships **Stage 1 (keyword) only**; the optional ``Stage 2``
classifier is exposed as a constructor seam (``classifier``) so a follow-up
PR can wire in semantic detection without changing the decorator's public
surface.

Production characteristics (see ``docs/persona/phase-2.md`` Track 2c
production-readiness checklist):

* **Stateless / thread-safe** — constructor-only state; no per-render
  mutation. Two concurrent renders cannot interfere.
* **Fail-open** — every error path falls back to the inner result. A
  slow or broken guardrail is worse than no guardrail because it stalls
  turns. ``settings_lookup`` raise → pass through. Retry render fails →
  deflect (deterministic, no LLM). Coerce fails → pass through.
* **Zero-cost passthrough** when ``policy == off`` or ``topics == []`` or
  ``settings_lookup`` returns ``None``. Locked by tests.
* **Streaming-aware** — when ``on_first_sentence`` is non-None we know the
  user has already seen text the decorator cannot redact. The decorator
  force-downgrades that turn to ``log_only`` (detect + audit, never
  block). Otherwise we'd silently break streaming for ``block_and_retry``
  agents.
* **Bounded retry** — exactly one retry, never recursive.
* **RenderOutput preservation** — retry/deflection preserves
  ``RenderOutput`` shape (``claimed_class``, ``acknowledged_fact_keys``)
  via ``model_copy``. Plain ``str`` results stay ``str``.
* **Audit emission** — ``structlog`` with stable event names. Field names
  match what the formal audit router eventually consumes (no rename
  later); see ``src/ruhu/audit/emitter.py`` for the destination shape.

Plumbing: the decorator is wrapped at ``api.py``'s kernel-construction
site. The lookup callable closes over the agent registry to read
``BehavioralPersona`` from the published agent document. ``kernel.py`` is
not touched — the kernel calls ``render_from_context()`` exactly as it
does today; the wrapper sits in front of the inner generator.

Runtime kill-switch: ``RUHU_TOPIC_ENFORCEMENT_ENABLED=false`` causes the
api.py wiring to skip wrapping entirely. Use during incident response.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, NamedTuple, Protocol

import structlog

from .persona import TopicEnforcementPolicy

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from .response_generation import (
        MoveSelectionRequest,
        ResponseGenerationRequest,
        ResponseGenerator,
    )
    from .schemas import RenderContext, RenderOutput


logger = structlog.get_logger(__name__)


# ── Settings + lookup contract ───────────────────────────────────────────────


class TopicSettings(NamedTuple):
    """Per-render snapshot of the topic-enforcement settings for an agent.

    Returned by ``TopicSettingsLookup``. Caller is responsible for caching
    if needed; the decorator calls the lookup on every render to avoid
    holding stale settings after a publish.
    """

    policy: TopicEnforcementPolicy
    topics: tuple[str, ...]


# Args: (agent_id, organization_id) → TopicSettings | None
# Returning None is treated identically to ``policy=off``.
TopicSettingsLookup = Callable[[str, str | None], "TopicSettings | None"]


# ── Stage 2 classifier seam (defined now, wired later) ───────────────────────


class TopicClassifier(Protocol):
    """Optional Stage 2 semantic classifier. Phase 2c v1 ships without one;
    a follow-up PR plugs in the existing ``classifier/dispatcher`` infra.

    A classifier returns the topics it judges the response to violate. An
    empty list means "no violation". The decorator's keyword Stage 1
    runs first; Stage 2 only fires when Stage 1 returns no matches AND a
    classifier was provided to the constructor.
    """

    def classify_violations(
        self,
        response_text: str,
        topics: tuple[str, ...],
        *,
        timeout_ms: int,
    ) -> tuple[str, ...]:
        ...


# ── Public deflection factory ────────────────────────────────────────────────


DeflectionTextFactory = Callable[[tuple[str, ...]], str]


def default_deflection_text(violations: tuple[str, ...]) -> str:
    """Default deflection used when ``block_and_retry`` cannot produce a
    clean response. Brand-neutral and topic-agnostic by design — naming
    the topic in the response (e.g. "I can't discuss pricing") would
    re-mention it, which violates the spirit of enforcement.
    """
    del violations  # intentionally unused
    return (
        "I'm not able to help with that here. Is there something else I "
        "can do for you?"
    )


# ── Stage 1 keyword match ────────────────────────────────────────────────────


def _stage1_keyword_match(
    text: str,
    topics: tuple[str, ...],
) -> tuple[str, ...]:
    """Return the ordered tuple of topics that appear in ``text``.

    Single-word topics use ``\\b…\\b`` so "pricing" does not match
    "uncoupling". Multi-word topics use plain substring match because the
    word order already constrains scope. Comparisons are case-insensitive
    via ``str.casefold`` (handles Turkish dotted-i and German ß correctly,
    unlike ``.lower()``).
    """
    if not topics:
        return ()
    text_cf = text.casefold()
    matched: list[str] = []
    for topic in topics:
        topic_cf = topic.casefold().strip()
        if not topic_cf:
            continue
        if " " in topic_cf:
            if topic_cf in text_cf:
                matched.append(topic)
        else:
            pattern = rf"\b{re.escape(topic_cf)}\b"
            if re.search(pattern, text_cf):
                matched.append(topic)
    return tuple(matched)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _coerce_to_text(result: object) -> str | None:
    """Extract the user-visible text from ``inner.render_from_context``
    return values without importing ``RenderOutput`` at module load time
    (avoids circular imports — schemas → response_generation → us)."""
    if result is None:
        return None
    if isinstance(result, str):
        return result
    text_attr = getattr(result, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    return None


def _replace_text(original: object, new_text: str) -> object:
    """Preserve ``RenderOutput`` shape (``claimed_class``,
    ``acknowledged_fact_keys``, etc.) but swap in the new text. Plain
    ``str`` results are returned as ``str``. Anything else falls back
    to the new text directly — defensive default."""
    if isinstance(original, str):
        return new_text
    if hasattr(original, "model_copy"):
        try:
            return original.model_copy(update={"text": new_text})
        except Exception:
            return new_text
    return new_text


def _retry_with_constraint(
    inner: "ResponseGenerator",
    context: "RenderContext",
    provider: str | None,
    model: str | None,
    violations: tuple[str, ...],
) -> object | None:
    """Rerun the inner render with a stronger constraint appended to
    ``system_prompt``. Streaming is intentionally disabled on retry
    (``on_first_sentence=None``) — we just verified streaming caused the
    violation, so a second streamed pass would have the same blind-spot.

    Returns the raw inner result (str | RenderOutput | None), not text.
    Caller coerces."""
    constraint = (
        "\n\nCRITICAL CONSTRAINT: Your previous response touched a "
        "forbidden topic. You MUST NOT discuss or reference: "
        f"{'; '.join(violations)}. Rephrase without these topics."
    )
    base_prompt = context.system_prompt or ""
    retry_ctx = context.model_copy(
        update={"system_prompt": base_prompt + constraint},
    )
    try:
        return inner.render_from_context(
            retry_ctx,
            provider=provider,
            model=model,
            on_first_sentence=None,
        )
    except Exception:
        logger.warning(
            "topic_enforcement.retry_render_failed",
            agent_id=context.agent_id,
            organization_id=context.organization_id,
            conversation_id=context.conversation_id,
            exc_info=True,
        )
        return None


# ── Decorator ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class TopicEnforcingResponseGenerator:
    """Decorator over ``ResponseGenerator`` that enforces
    ``BehavioralPersona.restricted_topics``.

    See module docstring for design rationale. This class implements the
    ``ResponseGenerator`` Protocol — duck-typing means we don't subclass
    ``Protocol`` here, but mypy will catch divergence at the kernel
    injection site.
    """

    inner: "ResponseGenerator"
    settings_lookup: TopicSettingsLookup
    classifier: TopicClassifier | None = None
    classifier_timeout_ms: int = 200
    deflection_text_factory: DeflectionTextFactory = field(
        default=default_deflection_text,
    )

    # ── Protocol passthroughs ────────────────────────────────────────────

    def generate(
        self,
        request: "ResponseGenerationRequest",
        on_first_sentence: Callable[[str], None] | None = None,
    ) -> str | None:
        """Move-time generation. Phase 2c does NOT enforce on this path —
        the move-selection prompt is internal-only, never user-visible.
        Pass through unchanged."""
        return self.inner.generate(request, on_first_sentence)

    def select_move(
        self,
        request: "MoveSelectionRequest",
    ) -> str | None:
        return self.inner.select_move(request)

    # ── The enforcement path ─────────────────────────────────────────────

    def render_from_context(
        self,
        context: "RenderContext",
        *,
        provider: str | None = None,
        model: str | None = None,
        on_first_sentence: Callable[[str], None] | None = None,
    ) -> "str | RenderOutput | None":
        result = self.inner.render_from_context(
            context,
            provider=provider,
            model=model,
            on_first_sentence=on_first_sentence,
        )
        if result is None:
            return None

        # Look up settings; fail-open on any error.
        try:
            settings = self.settings_lookup(
                context.agent_id, context.organization_id
            )
        except Exception:
            logger.warning(
                "topic_enforcement.settings_lookup_failed",
                agent_id=context.agent_id,
                organization_id=context.organization_id,
                conversation_id=context.conversation_id,
                exc_info=True,
            )
            return result

        if settings is None or settings.policy == TopicEnforcementPolicy.off:
            return result
        if not settings.topics:
            return result

        result_text = _coerce_to_text(result)
        if result_text is None:
            # Inner produced a non-text shape we can't inspect (shouldn't
            # happen for the contracts we know about — RenderOutput exposes
            # ``.text``). Pass through.
            return result

        # Stage 1 keyword match.
        violations = _stage1_keyword_match(result_text, settings.topics)

        # Stage 2 classifier — only if Stage 1 missed AND a classifier is
        # wired in. Wrapped in try/except to keep fail-open semantics.
        if not violations and self.classifier is not None:
            try:
                violations = self.classifier.classify_violations(
                    result_text,
                    settings.topics,
                    timeout_ms=self.classifier_timeout_ms,
                )
            except Exception:
                logger.warning(
                    "topic_enforcement.classifier_failed",
                    agent_id=context.agent_id,
                    organization_id=context.organization_id,
                    conversation_id=context.conversation_id,
                    exc_info=True,
                )
                violations = ()

        if not violations:
            return result

        # Streaming caveat: if on_first_sentence has fired, the user has
        # already seen text we can't redact. Force-downgrade to log_only
        # for this turn — we still detect + audit, just don't block.
        # This is the honest behaviour; promising enforcement while text
        # has already left the building would be worse.
        effective_policy = settings.policy
        if on_first_sentence is not None:
            effective_policy = TopicEnforcementPolicy.log_only

        logger.info(
            "topic_enforcement.violation_detected",
            agent_id=context.agent_id,
            organization_id=context.organization_id,
            conversation_id=context.conversation_id,
            policy=effective_policy.value,
            requested_policy=settings.policy.value,
            topics_matched=list(violations),
            stage="keyword",
            streaming_downgrade=(effective_policy != settings.policy),
        )

        if effective_policy == TopicEnforcementPolicy.log_only:
            return result

        # block_and_retry: exactly one retry attempt.
        retry_result = _retry_with_constraint(
            self.inner, context, provider, model, violations,
        )
        retry_text = _coerce_to_text(retry_result)

        if retry_text is None:
            return self._deflect(context, result, violations)

        retry_violations = _stage1_keyword_match(retry_text, settings.topics)
        if retry_violations:
            return self._deflect(context, result, retry_violations)

        logger.info(
            "topic_enforcement.retry_succeeded",
            agent_id=context.agent_id,
            organization_id=context.organization_id,
            conversation_id=context.conversation_id,
            topics_matched=list(violations),
        )
        # Preserve original RenderOutput shape (claim class etc.) but
        # swap in the retry text.
        return _replace_text(result, retry_text)

    # ── Deflection ───────────────────────────────────────────────────────

    def _deflect(
        self,
        context: "RenderContext",
        original_result: object,
        violations: tuple[str, ...],
    ) -> object:
        deflection_text = self.deflection_text_factory(violations)
        logger.info(
            "topic_enforcement.deflected",
            agent_id=context.agent_id,
            organization_id=context.organization_id,
            conversation_id=context.conversation_id,
            topics_matched=list(violations),
        )
        return _replace_text(original_result, deflection_text)


__all__ = [
    "DeflectionTextFactory",
    "TopicClassifier",
    "TopicEnforcingResponseGenerator",
    "TopicSettings",
    "TopicSettingsLookup",
    "default_deflection_text",
]
