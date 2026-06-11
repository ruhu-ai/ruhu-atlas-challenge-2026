"""Phase 2a-base — voice provider tests.

Tests are organised by surface:

- Protocol contract: any provider claiming to satisfy ``VoiceProvider``
  must support these calls without raising.
- ``VertexGeminiVoiceProvider`` specifics: catalog shape, filtering, ID
  validation. Synthesis tests use monkey-patching to avoid real Google
  Cloud TTS calls.
- Factory dispatch: env-var driven, fail-safe defaults.
- ``BehavioralPersona`` voice field validation (clamping, bounds).
"""
from __future__ import annotations

import os

import pytest

from ruhu.persona import BehavioralPersona
from ruhu.voice import (
    VertexGeminiVoiceProvider,
    VoiceCatalogEntry,
    VoiceCatalogPage,
    VoiceGender,
    VoiceProvider,
    VoiceSynthesisResult,
    build_voice_provider_from_env,
)


# ── Protocol contract ────────────────────────────────────────────────────────


class TestVoiceProviderProtocol:
    """Anything claiming to satisfy ``VoiceProvider`` must implement
    every method on the Protocol. ``runtime_checkable`` lets us assert
    this with ``isinstance``."""

    def test_vertex_gemini_satisfies_protocol(self):
        provider = VertexGeminiVoiceProvider()
        assert isinstance(provider, VoiceProvider)

    def test_provider_name_is_stable(self):
        """The ``name`` attribute is stored on cost records and catalog
        entries — changing it without a migration would silently corrupt
        analytics. Lock down the value."""
        assert VertexGeminiVoiceProvider().name == "vertex_gemini"


# ── Vertex catalog ───────────────────────────────────────────────────────────


class TestVertexGeminiCatalog:
    def test_catalog_includes_phase1_voices(self):
        """Backwards-compat guard: Phase 1's hard-coded picker had Kore /
        Leda / Orus / Aoede. The catalog MUST keep these so existing
        agents see no change in available choices."""
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices()
        ids = {entry.voice_id for entry in page.voices}
        assert ids == {
            "en-US-Chirp3-HD-Kore",
            "en-US-Chirp3-HD-Leda",
            "en-US-Chirp3-HD-Orus",
            "en-GB-Chirp3-HD-Aoede",
        }

    def test_catalog_entries_have_required_fields(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices()
        for entry in page.voices:
            assert entry.provider == "vertex_gemini"
            assert entry.display_name
            assert entry.language
            assert entry.gender in {
                VoiceGender.male,
                VoiceGender.female,
                VoiceGender.neutral,
            }
            # provider_metadata['google_voice_name'] is what the LiveKit
            # worker eventually consumes; presence is contract.
            assert "google_voice_name" in entry.provider_metadata

    def test_get_voice_returns_none_for_unknown(self):
        provider = VertexGeminiVoiceProvider()
        assert provider.get_voice("does-not-exist") is None

    def test_get_voice_returns_entry_for_known(self):
        provider = VertexGeminiVoiceProvider()
        entry = provider.get_voice("en-US-Chirp3-HD-Kore")
        assert entry is not None
        assert entry.voice_id == "en-US-Chirp3-HD-Kore"


class TestVertexGeminiFiltering:
    def test_language_prefix_match(self):
        """``language='en'`` must include both en-US and en-GB voices."""
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(language="en")
        languages = {entry.language for entry in page.voices}
        assert "en-US" in languages
        assert "en-GB" in languages

    def test_language_full_match_narrows_to_region(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(language="en-GB")
        for entry in page.voices:
            assert entry.language == "en-GB"

    def test_gender_filter_restricts_to_match(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(gender=VoiceGender.female)
        for entry in page.voices:
            assert entry.gender == VoiceGender.female

    def test_accent_filter_substring(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(accent="British")
        assert page.voices  # at least one
        for entry in page.voices:
            assert entry.accent and "british" in entry.accent.casefold()

    def test_filters_combine_with_AND(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(language="en-US", gender=VoiceGender.male)
        for entry in page.voices:
            assert entry.language == "en-US"
            assert entry.gender == VoiceGender.male


class TestVertexGeminiPagination:
    def test_first_page_no_cursor_returns_full_set(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(limit=100)
        assert len(page.voices) == page.total_count
        assert page.next_cursor is None

    def test_limit_below_total_yields_cursor(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(limit=2)
        assert len(page.voices) == 2
        assert page.next_cursor is not None
        # Continuation returns the rest.
        next_page = provider.list_voices(limit=10, cursor=page.next_cursor)
        assert next_page.next_cursor is None

    def test_limit_clamped_to_max(self):
        """A malicious caller can't request 1M voices in one page."""
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(limit=10_000)
        assert len(page.voices) <= 200

    def test_negative_limit_falls_back_to_default(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(limit=-1)
        assert page.voices  # didn't return empty

    def test_invalid_cursor_starts_from_zero(self):
        provider = VertexGeminiVoiceProvider()
        page = provider.list_voices(cursor="not-a-number")
        # Doesn't raise; behaves as cursor=None.
        assert len(page.voices) == page.total_count


class TestVertexGeminiPreview:
    def test_preview_url_returns_none_for_unknown_voice(self):
        provider = VertexGeminiVoiceProvider()
        assert provider.preview_url("nope") is None

    def test_preview_url_returns_none_for_known_voice(self):
        """Vertex doesn't expose pre-rendered samples — the api layer
        falls back to ``synthesize`` instead."""
        provider = VertexGeminiVoiceProvider()
        assert provider.preview_url("en-US-Chirp3-HD-Kore") is None


class TestVertexGeminiSynthesizeContract:
    def test_synthesize_unknown_voice_raises(self):
        provider = VertexGeminiVoiceProvider()
        with pytest.raises(ValueError, match="unknown voice_id"):
            provider.synthesize("hello", voice_id="nope")

    def test_synthesize_empty_text_raises(self):
        provider = VertexGeminiVoiceProvider()
        with pytest.raises(ValueError, match="non-empty"):
            provider.synthesize("", voice_id="en-US-Chirp3-HD-Kore")

    def test_synthesize_clamps_speed_within_range(self, monkeypatch):
        captured = {}

        def fake(*, text, voice_name, language, speed):
            captured["speed"] = speed
            return b"fake-audio"

        monkeypatch.setattr(
            "ruhu.voice.vertex_gemini_provider._synthesize_via_google_cloud",
            fake,
        )
        provider = VertexGeminiVoiceProvider()
        provider.synthesize(
            "hi", voice_id="en-US-Chirp3-HD-Kore", speed=2.5,
        )
        assert captured["speed"] == 1.3

    def test_synthesize_returns_cost_estimate(self, monkeypatch):
        monkeypatch.setattr(
            "ruhu.voice.vertex_gemini_provider._synthesize_via_google_cloud",
            lambda *, text, voice_name, language, speed: b"fake-audio",
        )
        provider = VertexGeminiVoiceProvider()
        result = provider.synthesize(
            "hi", voice_id="en-US-Chirp3-HD-Kore",
        )
        assert isinstance(result, VoiceSynthesisResult)
        assert result.character_count == 2
        # ~$16/M chars => 2 chars ≈ $0.000032
        assert 0 < result.estimated_cost_usd < 0.001

    def test_synthesize_uses_voice_default_language(self, monkeypatch):
        captured = {}

        def fake(*, text, voice_name, language, speed):
            captured["language"] = language
            return b"x"

        monkeypatch.setattr(
            "ruhu.voice.vertex_gemini_provider._synthesize_via_google_cloud",
            fake,
        )
        provider = VertexGeminiVoiceProvider()
        provider.synthesize("hi", voice_id="en-GB-Chirp3-HD-Aoede")
        assert captured["language"] == "en-GB"

    def test_synthesize_explicit_language_overrides(self, monkeypatch):
        captured = {}

        def fake(*, text, voice_name, language, speed):
            captured["language"] = language
            return b"x"

        monkeypatch.setattr(
            "ruhu.voice.vertex_gemini_provider._synthesize_via_google_cloud",
            fake,
        )
        provider = VertexGeminiVoiceProvider()
        provider.synthesize(
            "hi", voice_id="en-US-Chirp3-HD-Kore", language="en-NG",
        )
        assert captured["language"] == "en-NG"

    def test_synthesize_provider_failure_wraps_to_runtime_error(self, monkeypatch):
        """A google-cloud-texttospeech import error or runtime failure
        must surface as a generic RuntimeError so the api layer can
        return a clean 503 — not leak Google's exception types."""

        def boom(*, text, voice_name, language, speed):
            raise RuntimeError("ADC missing")

        monkeypatch.setattr(
            "ruhu.voice.vertex_gemini_provider._synthesize_via_google_cloud",
            boom,
        )
        provider = VertexGeminiVoiceProvider()
        with pytest.raises(RuntimeError, match="voice synthesis failed"):
            provider.synthesize("hi", voice_id="en-US-Chirp3-HD-Kore")


# ── Factory ──────────────────────────────────────────────────────────────────


class TestFactory:
    def test_default_returns_vertex(self, monkeypatch):
        monkeypatch.delenv("RUHU_VOICE_PROVIDER", raising=False)
        provider = build_voice_provider_from_env()
        assert provider.name == "vertex_gemini"

    def test_explicit_vertex(self, monkeypatch):
        monkeypatch.setenv("RUHU_VOICE_PROVIDER", "vertex_gemini")
        provider = build_voice_provider_from_env()
        assert provider.name == "vertex_gemini"

    def test_paid_provider_keys_raise_clear_error(self, monkeypatch):
        """Setting RUHU_VOICE_PROVIDER=elevenlabs before 2a-paid lands
        must produce a clear 'contract pending' error, not a silent
        fallback that confuses operators."""
        for key in ("elevenlabs", "cartesia"):
            monkeypatch.setenv("RUHU_VOICE_PROVIDER", key)
            with pytest.raises(RuntimeError, match="Phase 2a-paid"):
                build_voice_provider_from_env()

    def test_unknown_key_falls_back_safely(self, monkeypatch, caplog):
        """Typo'd env var (e.g. RUHU_VOICE_PROVIDER=verteks) must NOT
        brick the API. Fall back to default + warn."""
        monkeypatch.setenv("RUHU_VOICE_PROVIDER", "verteks")
        provider = build_voice_provider_from_env()
        assert provider.name == "vertex_gemini"


# ── Persona schema ──────────────────────────────────────────────────────────


class TestBehavioralPersonaVoiceFields:
    def test_defaults_match_phase1_voice_id(self):
        """Backwards-compat: the default ``voice_id`` matches the
        Phase 1 hard-coded default on AgentVoiceConfig — agents that
        haven't picked anything explicit see Kore."""
        persona = BehavioralPersona()
        assert persona.voice_id == "en-US-Chirp3-HD-Kore"
        assert persona.voice_provider == "vertex_gemini"

    def test_voice_speed_clamped_low(self):
        with pytest.raises(ValueError):
            BehavioralPersona(voice_speed=0.5)

    def test_voice_speed_clamped_high(self):
        with pytest.raises(ValueError):
            BehavioralPersona(voice_speed=2.0)

    def test_voice_speed_in_range(self):
        for speed in (0.7, 1.0, 1.3):
            persona = BehavioralPersona(voice_speed=speed)
            assert persona.voice_speed == speed

    def test_voice_id_max_length(self):
        with pytest.raises(ValueError):
            BehavioralPersona(voice_id="x" * 200)

    def test_budget_must_be_non_negative(self):
        with pytest.raises(ValueError):
            BehavioralPersona(voice_monthly_budget_cents=-1)

    def test_budget_none_is_unlimited(self):
        persona = BehavioralPersona(voice_monthly_budget_cents=None)
        assert persona.voice_monthly_budget_cents is None
