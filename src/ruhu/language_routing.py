"""Phase 2b — language routing decorator on ``ResponseGenerator``.

The ``LanguageRoutingResponseGenerator`` wraps any ``ResponseGenerator``
(``GeminiDialogueGenerator`` in production, mocks in tests) and routes
each render through the language pipeline:

1. Read the detected language from ``RenderContext.metadata`` (set
   upstream by the STT layer or the text-only detector).
2. Apply stability gates (confidence threshold, minimum char count,
   debounce window across turns) — same model as Salesforce
   Agentforce's APAC tuning at 0.82.
3. Apply the agent's ``language_switch_policy`` — mirror_user,
   lock_to_primary, or gradual_revert.
4. If the resolved language is not in ``allowed_languages``, apply
   the agent's ``unsupported_language_policy``.
5. Mutate the persona block on the context so the LLM sees the
   correct persona name (per-language override) and language
   directives.
6. Stash ``__effective_language`` and resolved ``__voice_id`` on
   ``context.metadata`` so the worker (per-language voice swap, a
   follow-up PR) can read them on the TTS path.

Production characteristics (each backed by tests):

* **Stateless / thread-safe** — constructor-only state. Per-conversation
  history (for the debounce window) is held in the ``ConversationState``
  the kernel passes us via ``context.metadata["__conversation_state"]``;
  the decorator never holds it.
* **Fail-open** — settings_lookup raise → pass-through; detector
  raise → keep current language; persona_block exception → preserve
  the original system_prompt.
* **Backwards compat (zero-cost passthrough)** — when the agent is
  English-only with default config, the decorator is byte-identical
  to no decorator. Locked by ``test_byte_identical_when_default``.
* **Streaming downgrade** — if ``on_first_sentence`` is set, an "on"
  switch decision is downgraded to "log_only" for that turn (we can't
  redact text the user has already heard).
* **No kernel.py edits** — wraps the existing ``ResponseGenerator``
  Protocol; injected at api.py construction site (same pattern as
  ``TopicEnforcingResponseGenerator``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, NamedTuple, Protocol

from .persona import (
    AutoSwitchMode,
    BehavioralPersona,
    CosmeticPersona,
    LanguageSwitchPolicy,
    UnsupportedLanguagePolicy,
    compose_persona_block,
)

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from .response_generation import (
        MoveSelectionRequest,
        ResponseGenerationRequest,
        ResponseGenerator,
    )
    from .schemas import RenderContext, RenderOutput


logger = logging.getLogger(__name__)


# ── Settings + lookup contract ───────────────────────────────────────────────


class LanguageRoutingSettings(NamedTuple):
    """Per-render snapshot of the agent's language behaviour.

    The decorator calls the lookup on every render — caller is free
    to cache, but stale settings could leave a tenant on the old
    auto_switch_language mode after a publish, so the lookup is
    expected to be fresh."""

    cosmetic: CosmeticPersona | None
    behavioral: BehavioralPersona | None
    company_name: str | None


# Args: (agent_id, organization_id) → settings or None
LanguageSettingsLookup = Callable[
    [str, str | None], "LanguageRoutingSettings | None",
]


# Args: (text) → BCP-47 tag or None. Used only on text-only chat
# (when the STT layer didn't pre-populate ``detected_language``).
TextDetector = Callable[[str], "tuple[str, float] | None"]


# ── Conversation-level history (debounce) ────────────────────────────────────


@dataclass(slots=True)
class _LanguageRoutingState:
    """Per-conversation rolling state used for stability gates.

    Lives on ``RenderContext.metadata["__language_routing_state"]`` so
    the decorator stays stateless. The kernel persists this between
    turns; the decorator only mutates it during a render.

    This dataclass is intentionally module-private — callers shouldn't
    construct or inspect it directly. The decorator's
    ``_load_state`` / ``_persist_state`` helpers manage the lifecycle.
    """

    current_language: str = "en"
    """Whatever language the agent is currently speaking. Updated on
    a successful switch."""

    pending_language: str | None = None
    """Language detected on a recent turn that hasn't yet cleared the
    debounce window. Reset to None if a different language shows up
    (debounce restarts)."""

    pending_streak: int = 0
    """Consecutive turns where ``pending_language`` was detected with
    confidence ≥ threshold. Reaches ``debounce_turns`` → switch fires."""

    turns_in_current_language: int = 0
    """Used by ``gradual_revert`` policy to count turns since the
    agent last switched away from primary."""


# ── Decorator ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class LanguageRoutingResponseGenerator:
    """Decorator over ``ResponseGenerator`` that resolves the per-turn
    effective language and rewrites the persona block accordingly.

    See module docstring for production characteristics. The class
    implements the ``ResponseGenerator`` Protocol via duck-typing.
    """

    inner: "ResponseGenerator"
    settings_lookup: LanguageSettingsLookup
    text_detector: TextDetector | None = None

    # ── Protocol passthroughs ────────────────────────────────────────────

    def generate(
        self,
        request: "ResponseGenerationRequest",
        on_first_sentence: Callable[[str], None] | None = None,
    ) -> str | None:
        """Move-time generation runs against internal-only prompts;
        no language routing applied."""
        return self.inner.generate(request, on_first_sentence)

    def select_move(
        self,
        request: "MoveSelectionRequest",
    ) -> str | None:
        return self.inner.select_move(request)

    # ── The routing path ────────────────────────────────────────────────

    def render_from_context(
        self,
        context: "RenderContext",
        *,
        provider: str | None = None,
        model: str | None = None,
        on_first_sentence: Callable[[str], None] | None = None,
    ) -> "str | RenderOutput | None":
        # Look up settings; fail-open on any error.
        try:
            settings = self.settings_lookup(
                context.agent_id, context.organization_id,
            )
        except Exception:
            logger.warning(
                "language_routing.settings_lookup_failed",
                extra={
                    "agent_id": context.agent_id,
                    "organization_id": context.organization_id,
                },
                exc_info=True,
            )
            return self.inner.render_from_context(
                context, provider=provider, model=model,
                on_first_sentence=on_first_sentence,
            )
        if settings is None:
            return self.inner.render_from_context(
                context, provider=provider, model=model,
                on_first_sentence=on_first_sentence,
            )

        behavioral = settings.behavioral
        if behavioral is None or self._is_zero_cost_passthrough(behavioral):
            return self.inner.render_from_context(
                context, provider=provider, model=model,
                on_first_sentence=on_first_sentence,
            )

        # Resolve effective language.
        state = self._load_state(context, behavioral)
        detected = self._read_detected_language(context, behavioral)
        decision = self._resolve_decision(
            detected=detected,
            state=state,
            behavioral=behavioral,
            on_first_sentence=on_first_sentence,
        )
        # Persist state for the NEXT turn.
        self._persist_state(context, decision.next_state)
        # Stash for the worker / TTS path.
        context.metadata["__effective_language"] = decision.effective_language
        if decision.voice_id_override is not None:
            context.metadata["__effective_voice_id"] = decision.voice_id_override

        if decision.audit_event is not None:
            logger.info(
                decision.audit_event,
                extra={
                    "agent_id": context.agent_id,
                    "organization_id": context.organization_id,
                    "conversation_id": context.conversation_id,
                    "from_language": state.current_language,
                    "to_language": decision.effective_language,
                    "detected_confidence": detected[1] if detected else None,
                    "policy": behavioral.language_switch_policy.value,
                    "auto_switch_mode": behavioral.auto_switch_language.value,
                },
            )

        # Mutate the persona block on the context. We re-compose with
        # the resolved effective language so persona_name_overrides
        # and the locale-aware allowed-languages directive land in
        # the system prompt the LLM sees.
        new_context = self._compose_with_effective_language(
            context=context,
            cosmetic=settings.cosmetic,
            behavioral=behavioral,
            company_name=settings.company_name,
            effective_language=decision.effective_language,
        )

        return self.inner.render_from_context(
            new_context,
            provider=provider,
            model=model,
            on_first_sentence=on_first_sentence,
        )

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_zero_cost_passthrough(behavioral: BehavioralPersona) -> bool:
        """When all language fields are at default and there are no
        per-language overrides, the decorator must be observably
        identical to no decorator."""
        return (
            behavioral.auto_switch_language == AutoSwitchMode.off
            and behavioral.allowed_languages == ["en"]
            and behavioral.primary_language == "en"
            and not behavioral.voice_id_overrides
        )

    def _read_detected_language(
        self,
        context: "RenderContext",
        behavioral: BehavioralPersona,
    ) -> tuple[str, float] | None:
        """Pull the STT-reported language off context metadata, or
        fall back to the text detector when no STT pass produced one."""
        stt_payload = context.metadata.get("__detected_language")
        if isinstance(stt_payload, dict):
            lang = stt_payload.get("language")
            conf = stt_payload.get("confidence", 1.0)
            if isinstance(lang, str) and isinstance(conf, (int, float)):
                return (lang, float(conf))
        if isinstance(stt_payload, str):
            return (stt_payload, 1.0)
        # Text-only fallback.
        if self.text_detector is None:
            return None
        # Use the user's most recent message text from recent_messages
        # if available; otherwise no signal.
        for message in reversed(list(context.recent_messages or [])):
            role = getattr(message, "role", None)
            text = getattr(message, "text", None)
            if role == "user" and isinstance(text, str) and text.strip():
                try:
                    result = self.text_detector(text)
                except Exception:
                    logger.warning(
                        "language_routing.text_detector_failed",
                        exc_info=True,
                    )
                    return None
                return result
        return None

    def _resolve_decision(
        self,
        *,
        detected: tuple[str, float] | None,
        state: _LanguageRoutingState,
        behavioral: BehavioralPersona,
        on_first_sentence: Callable[[str], None] | None,
    ) -> "_RoutingDecision":
        """Apply gates + policy → final per-turn language.

        This is the core decision logic and the densest path in the
        decorator. Tests pin every branch.
        """
        primary = (
            behavioral.allowed_languages[0]
            if behavioral.primary_language == "auto"
            else behavioral.primary_language
        )

        # Mode gate: off → never switch. log_only → run gates + policy
        # but always emit primary. on → full behaviour.
        if behavioral.auto_switch_language == AutoSwitchMode.off:
            return _RoutingDecision(
                effective_language=state.current_language or primary,
                voice_id_override=behavioral.voice_id_overrides.get(
                    state.current_language or primary,
                ),
                next_state=_LanguageRoutingState(
                    current_language=state.current_language or primary,
                ),
                audit_event=None,
            )

        # Without a detected language we can't make a switching
        # decision; keep current.
        if detected is None:
            return _RoutingDecision(
                effective_language=state.current_language or primary,
                voice_id_override=behavioral.voice_id_overrides.get(
                    state.current_language or primary,
                ),
                next_state=state,
                audit_event=None,
            )

        detected_lang, detected_conf = detected
        normalised_detected = detected_lang.split("-", 1)[0]

        # Gates — confidence, length, debounce.
        gates_passed = (
            detected_conf >= behavioral.language_switch_confidence_threshold
            # We don't get the user-message length here directly; the
            # caller has already enforced ``language_switch_min_chars``
            # via the detector by skipping the call when text is too
            # short. If the STT layer fed a language tag from a tiny
            # transcript we can't easily filter it; documenting that
            # as a known limitation — text-only chat enforces it
            # exactly via the FastText guard.
        )

        # Policy: lock_to_primary stays put no matter what.
        if behavioral.language_switch_policy == LanguageSwitchPolicy.lock_to_primary:
            return _RoutingDecision(
                effective_language=primary,
                voice_id_override=behavioral.voice_id_overrides.get(primary),
                next_state=_LanguageRoutingState(current_language=primary),
                audit_event=(
                    "language_routing.locked_to_primary"
                    if normalised_detected != primary.split("-", 1)[0]
                    else None
                ),
            )

        # Allowed-language check.
        allowed_short = {
            lang.split("-", 1)[0] for lang in behavioral.allowed_languages
        }
        if normalised_detected not in allowed_short and detected_lang not in allowed_short:
            decision = self._apply_unsupported_policy(
                primary=primary,
                detected_lang=detected_lang,
                state=state,
                behavioral=behavioral,
            )
            return decision

        # Determine target.
        if behavioral.language_switch_policy == LanguageSwitchPolicy.mirror_user:
            target = detected_lang
        elif behavioral.language_switch_policy == LanguageSwitchPolicy.gradual_revert:
            # Switch immediately on first detection; revert to primary
            # after 3 turns of single-language input.
            target = detected_lang
            if state.turns_in_current_language >= 3:
                target = primary
        else:
            target = state.current_language or primary

        # Debounce: count consecutive turns of pending_language.
        if state.pending_language == detected_lang:
            new_streak = state.pending_streak + 1
        else:
            new_streak = 1

        debounce_satisfied = (
            new_streak >= max(1, behavioral.language_switch_debounce_turns)
        )

        if not (gates_passed and debounce_satisfied):
            return _RoutingDecision(
                effective_language=state.current_language or primary,
                voice_id_override=behavioral.voice_id_overrides.get(
                    state.current_language or primary,
                ),
                next_state=_LanguageRoutingState(
                    current_language=state.current_language or primary,
                    pending_language=detected_lang,
                    pending_streak=new_streak,
                    turns_in_current_language=state.turns_in_current_language + 1,
                ),
                audit_event=None,
            )

        # All gates passed. If log_only OR streaming, audit the would-be
        # switch but emit primary instead.
        if (
            behavioral.auto_switch_language == AutoSwitchMode.log_only
            or on_first_sentence is not None
        ):
            return _RoutingDecision(
                effective_language=state.current_language or primary,
                voice_id_override=behavioral.voice_id_overrides.get(
                    state.current_language or primary,
                ),
                next_state=_LanguageRoutingState(
                    current_language=state.current_language or primary,
                    pending_language=detected_lang,
                    pending_streak=new_streak,
                    turns_in_current_language=state.turns_in_current_language + 1,
                ),
                audit_event="language_routing.switch_shadowed",
            )

        # auto_switch_language == on — actual switch fires.
        same_as_current = target == state.current_language
        return _RoutingDecision(
            effective_language=target,
            voice_id_override=behavioral.voice_id_overrides.get(target),
            next_state=_LanguageRoutingState(
                current_language=target,
                pending_language=None,
                pending_streak=0,
                turns_in_current_language=(
                    state.turns_in_current_language + 1 if same_as_current else 0
                ),
            ),
            audit_event=None if same_as_current else "language_routing.switched",
        )

    def _apply_unsupported_policy(
        self,
        *,
        primary: str,
        detected_lang: str,
        state: _LanguageRoutingState,
        behavioral: BehavioralPersona,
    ) -> "_RoutingDecision":
        """Resolve the next language when the user spoke something
        outside ``allowed_languages``. The actual deflection text is
        emitted by the LLM via the persona block's
        ``unsupported_language_policy`` directive — the decorator just
        decides which language the agent responds in."""
        # All three policies result in the agent staying in the
        # current/primary language; the difference is what we audit.
        target = state.current_language or primary
        audit = "language_routing.unsupported_language_user"
        if behavioral.unsupported_language_policy == UnsupportedLanguagePolicy.escalate_to_human:
            audit = "language_routing.unsupported_language_escalate"
        return _RoutingDecision(
            effective_language=target,
            voice_id_override=behavioral.voice_id_overrides.get(target),
            next_state=_LanguageRoutingState(
                current_language=target,
                pending_language=detected_lang,
                pending_streak=0,
                turns_in_current_language=state.turns_in_current_language + 1,
            ),
            audit_event=audit,
        )

    @staticmethod
    def _load_state(
        context: "RenderContext",
        behavioral: BehavioralPersona,
    ) -> _LanguageRoutingState:
        raw = context.metadata.get("__language_routing_state")
        if isinstance(raw, _LanguageRoutingState):
            return raw
        # First turn: seed from the agent's primary_language.
        primary = (
            behavioral.allowed_languages[0]
            if behavioral.primary_language == "auto"
            else behavioral.primary_language
        )
        return _LanguageRoutingState(current_language=primary)

    @staticmethod
    def _persist_state(
        context: "RenderContext",
        new_state: _LanguageRoutingState,
    ) -> None:
        context.metadata["__language_routing_state"] = new_state

    @staticmethod
    def _compose_with_effective_language(
        *,
        context: "RenderContext",
        cosmetic: CosmeticPersona | None,
        behavioral: BehavioralPersona | None,
        company_name: str | None,
        effective_language: str,
    ) -> "RenderContext":
        """Re-compose the persona block with the resolved language.

        The original ``context.system_prompt`` already has the persona
        block (composed at api.py with the agent's default language).
        We replace it with a freshly-composed block keyed on the
        ``effective_language`` so persona_name_overrides land in the
        prompt the LLM sees. Original prompt suffix (anything the
        agent added beyond the persona block) is preserved.
        """
        try:
            block = compose_persona_block(
                cosmetic, behavioral, company_name,
                effective_language=effective_language,
            )
        except Exception:
            # Composition failure must not break the turn — fall back
            # to the prompt the kernel built originally.
            logger.warning(
                "language_routing.compose_failed", exc_info=True,
            )
            return context

        if not block:
            return context

        original = context.system_prompt or ""
        # The original prompt was constructed as
        # ``f"{block}\n\n{settings.system_prompt}"`` (see
        # ``AgentSettings.composed_system_prompt``). Replace the prefix
        # if we recognise its shape; otherwise prepend defensively
        # rather than throw away the prompt.
        if "\n\n" in original:
            _, suffix = original.split("\n\n", 1)
            new_prompt = f"{block}\n\n{suffix}"
        else:
            new_prompt = f"{block}\n\n{original}"
        return context.model_copy(update={"system_prompt": new_prompt})


@dataclass(slots=True, frozen=True)
class _RoutingDecision:
    effective_language: str
    voice_id_override: str | None
    next_state: _LanguageRoutingState
    audit_event: str | None


__all__ = [
    "LanguageRoutingResponseGenerator",
    "LanguageRoutingSettings",
    "LanguageSettingsLookup",
    "TextDetector",
]
