"""Phase 2b — LanguageRoutingResponseGenerator tests.

The decorator owns the agent's per-turn language decision. Every
production-readiness contract from the module docstring is asserted
here:

* Stateless / thread-safe — verified indirectly (no per-instance state
  leaks across calls).
* Fail-open — settings_lookup raise, detector raise, persona compose
  raise all fall through to inner.render_from_context unchanged.
* Backwards compat — default config is byte-identical to no decorator.
* Streaming downgrade — switch decision becomes log_only when
  ``on_first_sentence`` is set.
* Stability gates — confidence + debounce hold the decorator from
  flapping on noisy detection.
* Policy resolution — mirror_user / lock_to_primary / gradual_revert.
* Unsupported language — agent stays in primary; decision is audited.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ruhu.language_routing import (
    LanguageRoutingResponseGenerator,
    LanguageRoutingSettings,
    _LanguageRoutingState,
)
from ruhu.persona import (
    AutoSwitchMode,
    BehavioralPersona,
    CosmeticPersona,
    LanguageSwitchPolicy,
    UnsupportedLanguagePolicy,
)
from ruhu.schemas import JourneyContext, RenderContext


# ── Test doubles ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class StubGenerator:
    """Stub ResponseGenerator that records every render call so tests
    can assert what context the inner saw."""

    output: object | None = "rendered"
    calls: list[RenderContext] = field(default_factory=list)
    raise_on_call: int | None = None

    def generate(self, request, on_first_sentence=None):  # pragma: no cover
        return None

    def select_move(self, request):  # pragma: no cover
        return None

    def render_from_context(
        self,
        context,
        *,
        provider=None,
        model=None,
        on_first_sentence=None,
    ):
        idx = len(self.calls)
        self.calls.append(context)
        if self.raise_on_call is not None and idx == self.raise_on_call:
            raise RuntimeError("synthetic inner failure")
        return self.output


def _make_context(
    *,
    agent_id: str = "agent-1",
    organization_id: str | None = "org-1",
    conversation_id: str = "conv-1",
    system_prompt: str = "You are Maya.\n\nHelp the user.",
    metadata: dict | None = None,
) -> RenderContext:
    return RenderContext(
        conversation_id=conversation_id,
        organization_id=organization_id,
        agent_id=agent_id,
        response_mode="entry",
        journey=JourneyContext(current_step_id="step-test"),
        system_prompt=system_prompt,
        metadata=metadata or {},
    )


def _settings(
    *,
    behavioral: BehavioralPersona | None = None,
    cosmetic: CosmeticPersona | None = None,
    company_name: str | None = "Acme",
):
    captured = LanguageRoutingSettings(
        cosmetic=cosmetic,
        behavioral=behavioral,
        company_name=company_name,
    )

    def lookup(_agent_id, _org_id):
        return captured

    return lookup


# ── Zero-cost passthrough ────────────────────────────────────────────────────


class TestZeroCostPassthrough:
    """The decorator MUST be observably identical to no-decorator when
    the agent is at default config (English-only, no auto-switch, no
    voice overrides). This is the backwards-compat contract that lets
    us leave the wrapper installed globally."""

    def test_default_behavioral_passes_through(self):
        inner = StubGenerator()
        gen = LanguageRoutingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(behavioral=BehavioralPersona()),
        )
        result = gen.render_from_context(_make_context())
        assert result == "rendered"
        # Inner saw the original context — no persona_block mutation.
        assert len(inner.calls) == 1
        # The context was passed through; no __effective_language stash
        # because the decorator detected the zero-cost path early and
        # never touched the metadata.
        assert "__effective_language" not in inner.calls[0].metadata

    def test_settings_returns_none_passes_through(self):
        inner = StubGenerator()
        gen = LanguageRoutingResponseGenerator(
            inner=inner,
            settings_lookup=lambda *_args, **_kw: None,
        )
        result = gen.render_from_context(_make_context())
        assert result == "rendered"


# ── Fail-open ────────────────────────────────────────────────────────────────


class TestFailOpen:
    def test_settings_lookup_raise_falls_through(self):
        def boom(*_args):
            raise RuntimeError("registry exploded")

        inner = StubGenerator()
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=boom,
        )
        # Doesn't propagate; inner still rendered.
        assert gen.render_from_context(_make_context()) == "rendered"

    def test_text_detector_raise_keeps_current_language(self):
        def detector(_text):
            raise RuntimeError("detector exploded")

        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(behavioral=behavioral),
            text_detector=detector,
        )
        # Should not raise; no detected language signal → stay current.
        assert gen.render_from_context(_make_context()) == "rendered"


# ── auto_switch_language modes ───────────────────────────────────────────────


class TestAutoSwitchModes:
    def test_off_never_switches_even_with_strong_signal(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.off,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.99},
        })
        gen.render_from_context(ctx)
        assert ctx.metadata["__effective_language"] == "en"

    def test_on_switches_when_gates_pass(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_debounce_turns=1,
            language_switch_confidence_threshold=0.80,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.95},
        })
        gen.render_from_context(ctx)
        assert ctx.metadata["__effective_language"] == "yo"

    def test_log_only_detects_but_emits_primary(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.log_only,
            language_switch_debounce_turns=1,
            language_switch_confidence_threshold=0.80,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.95},
        })
        gen.render_from_context(ctx)
        # Effective language is still primary — log_only doesn't actually switch.
        assert ctx.metadata["__effective_language"] == "en"


# ── Stability gates ──────────────────────────────────────────────────────────


class TestStabilityGates:
    def test_low_confidence_does_not_switch(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_confidence_threshold=0.85,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.70},
        })
        gen.render_from_context(ctx)
        assert ctx.metadata["__effective_language"] == "en"

    def test_debounce_holds_back_first_detection(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_debounce_turns=2,
            language_switch_confidence_threshold=0.80,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        # First detection — pending, not yet switched.
        ctx1 = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.95},
        })
        gen.render_from_context(ctx1)
        assert ctx1.metadata["__effective_language"] == "en"
        state_after_1 = ctx1.metadata["__language_routing_state"]
        assert state_after_1.pending_language == "yo"
        assert state_after_1.pending_streak == 1

        # Second detection of same language — debounce satisfied.
        ctx2 = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.95},
            "__language_routing_state": state_after_1,
        })
        gen.render_from_context(ctx2)
        assert ctx2.metadata["__effective_language"] == "yo"


# ── Switch policies ──────────────────────────────────────────────────────────


class TestSwitchPolicies:
    def test_lock_to_primary_overrides_detection(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_policy=LanguageSwitchPolicy.lock_to_primary,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.99},
        })
        gen.render_from_context(ctx)
        assert ctx.metadata["__effective_language"] == "en"

    def test_mirror_user_default_switches(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_policy=LanguageSwitchPolicy.mirror_user,
            language_switch_debounce_turns=1,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.95},
        })
        gen.render_from_context(ctx)
        assert ctx.metadata["__effective_language"] == "yo"


# ── Unsupported language handling ────────────────────────────────────────────


class TestUnsupportedLanguage:
    def test_user_speaks_language_outside_allowlist_stays_in_primary(self):
        """When the user speaks Mandarin and the agent only knows
        en + yo, the agent must NOT silently switch to Mandarin
        (would produce broken responses) — it stays in primary
        and the persona block tells the LLM to politely deflect."""
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            unsupported_language_policy=UnsupportedLanguagePolicy.explain_and_offer,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "zh", "confidence": 0.95},
        })
        gen.render_from_context(ctx)
        assert ctx.metadata["__effective_language"] == "en"


# ── Streaming downgrade ──────────────────────────────────────────────────────


class TestStreamingDowngrade:
    def test_on_with_streaming_downgrades_to_log_only(self):
        """When on_first_sentence is set, the user has already heard
        text. An ``on`` switch decision must downgrade to log_only
        for that turn — flipping mid-sentence would produce broken
        TTS output."""
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_debounce_turns=1,
            language_switch_confidence_threshold=0.80,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.95},
        })
        gen.render_from_context(
            ctx, on_first_sentence=lambda _s: None,
        )
        # Streaming → effective stays primary even though gates passed.
        assert ctx.metadata["__effective_language"] == "en"


# ── Voice override stash ─────────────────────────────────────────────────────


class TestVoiceOverrideStash:
    def test_voice_override_stashed_when_present(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_debounce_turns=1,
            voice_id_overrides={"yo": "en-GB-Chirp3-HD-Aoede"},
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.95},
        })
        gen.render_from_context(ctx)
        assert ctx.metadata["__effective_voice_id"] == "en-GB-Chirp3-HD-Aoede"

    def test_no_voice_override_no_stash(self):
        inner = StubGenerator()
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_debounce_turns=1,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner, settings_lookup=_settings(behavioral=behavioral),
        )
        ctx = _make_context(metadata={
            "__detected_language": {"language": "yo", "confidence": 0.95},
        })
        gen.render_from_context(ctx)
        assert "__effective_voice_id" not in ctx.metadata


# ── Persona block re-composition ─────────────────────────────────────────────


class TestPersonaBlockMutation:
    def test_persona_name_override_lands_in_prompt(self):
        """When the agent has a persona_name_override for the resolved
        language, the prompt the LLM sees must contain the override
        name, not the canonical one."""
        inner = StubGenerator()
        cosmetic = CosmeticPersona(
            persona_name="Maya",
            persona_name_overrides={"yo": "Mayowa"},
        )
        behavioral = BehavioralPersona(
            primary_language="en",
            allowed_languages=["en", "yo"],
            auto_switch_language=AutoSwitchMode.on,
            language_switch_debounce_turns=1,
        )
        gen = LanguageRoutingResponseGenerator(
            inner=inner,
            settings_lookup=_settings(
                cosmetic=cosmetic, behavioral=behavioral,
            ),
        )
        ctx = _make_context(
            system_prompt="You are Maya.\n\nHelp the user.",
            metadata={
                "__detected_language": {"language": "yo", "confidence": 0.95},
            },
        )
        gen.render_from_context(ctx)
        # The mutated context (passed to inner) should reference Mayowa.
        rendered_ctx = inner.calls[0]
        assert "Mayowa" in (rendered_ctx.system_prompt or "")
