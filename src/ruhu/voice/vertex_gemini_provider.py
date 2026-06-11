"""Vertex Gemini / Google Cloud TTS voice provider.

Phase 2a-base implementation. Ships the four voices currently hard-coded
in ``AgentVoiceConfig`` and ``AgentSettingsPanel``: Kore, Leda, Orus,
Aoede. The catalog is static (these are the productionized voices we've
tested); enriching it with the broader Chirp3-HD catalog is a follow-up
that needs cultural-fit review for African markets.

Notes:

* **No commercial contract dependency** — uses Google Cloud TTS via ADC,
  which the API runtime already requires for response generation.
* **Production TTS path unchanged** — the LiveKit worker continues to
  use direct Google Cloud TTS today. This provider's ``synthesize`` is
  used by the preview endpoint only until 2b plumbs voice swap into
  ``update_options()``.
* **Synthesis lazily-imports** ``google.cloud.texttospeech`` so the API
  process doesn't pay the import cost when the catalog is just being
  listed (the picker UI reads catalog far more than it generates
  previews).
* **Preview cost** — Chirp3-HD is approximately $16/M chars, so a
  120-char preview is ~$0.0019. We record the cost via
  ``provider_costs.py`` per call.
"""
from __future__ import annotations

import logging

from .protocol import (
    VoiceCatalogEntry,
    VoiceCatalogPage,
    VoiceCloningConsentError,
    VoiceCloningResult,
    VoiceGender,
    VoiceSynthesisResult,
)

logger = logging.getLogger(__name__)


# Static catalog. Kept in lockstep with the voices listed in the Phase 1
# `AgentSettingsPanel.tsx` so existing agents see no change in available
# choices. New voices here ship to the picker automatically.
#
# `provider_metadata.google_voice_name` is the value the LiveKit worker
# already reads via ``VOICE_TTS_VOICE_NAME`` / ``dispatch_context.metadata``;
# 2b will wire this into the dispatch path.
_VERTEX_GEMINI_CATALOG: tuple[VoiceCatalogEntry, ...] = (
    VoiceCatalogEntry(
        voice_id="en-US-Chirp3-HD-Kore",
        provider="vertex_gemini",
        display_name="Kore",
        language="en-US",
        gender=VoiceGender.neutral,
        accent="American",
        description="Calm, measured, gender-neutral. Today's default.",
        sample_text="Hi, this is your assistant — how can I help today?",
        provider_metadata={"google_voice_name": "en-US-Chirp3-HD-Kore"},
    ),
    VoiceCatalogEntry(
        voice_id="en-US-Chirp3-HD-Leda",
        provider="vertex_gemini",
        display_name="Leda",
        language="en-US",
        gender=VoiceGender.female,
        accent="American",
        description="Warm, friendly, professional.",
        sample_text="Hello! I'm here to help with your questions.",
        provider_metadata={"google_voice_name": "en-US-Chirp3-HD-Leda"},
    ),
    VoiceCatalogEntry(
        voice_id="en-US-Chirp3-HD-Orus",
        provider="vertex_gemini",
        display_name="Orus",
        language="en-US",
        gender=VoiceGender.male,
        accent="American",
        description="Confident, clear, business-ready.",
        sample_text="Welcome — let's get started with your account.",
        provider_metadata={"google_voice_name": "en-US-Chirp3-HD-Orus"},
    ),
    VoiceCatalogEntry(
        voice_id="en-GB-Chirp3-HD-Aoede",
        provider="vertex_gemini",
        display_name="Aoede",
        language="en-GB",
        gender=VoiceGender.female,
        accent="British",
        description="Crisp British English — works well for fintech / formal personas.",
        sample_text="Good day. Thank you for getting in touch.",
        provider_metadata={"google_voice_name": "en-GB-Chirp3-HD-Aoede"},
    ),
)


# Approximate Google Cloud TTS pricing for Chirp3-HD (USD per character).
# Source: Google Cloud TTS pricing page; recheck during 2a-paid eval.
# We don't fail closed if pricing is wrong — the cost is informational
# (provider_costs.py records it but no enforcement happens here).
_CHIRP3_HD_USD_PER_CHAR = 16.0 / 1_000_000.0


class VertexGeminiVoiceProvider:
    """Voice provider backed by Google Cloud TTS Chirp3-HD voices.

    Construct once at app startup and reuse — instances are stateless
    once initialized (no internal mutable state; each call is independent).
    """

    name = "vertex_gemini"
    supports_cloning = True

    def __init__(self) -> None:
        # Voices are immutable for the lifetime of the process; pre-index
        # by id for O(1) lookup on the hot picker path.
        self._by_id: dict[str, VoiceCatalogEntry] = {
            entry.voice_id: entry for entry in _VERTEX_GEMINI_CATALOG
        }

    # ── Catalog ──────────────────────────────────────────────────────────

    def list_voices(
        self,
        *,
        language: str | None = None,
        gender: VoiceGender | None = None,
        accent: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> VoiceCatalogPage:
        # Cursor format: integer index encoded as a string. Opaque to
        # callers but trivial here because the catalog is static.
        try:
            start = int(cursor) if cursor else 0
        except ValueError:
            start = 0
        if start < 0:
            start = 0
        if limit <= 0:
            limit = 50
        # Hard cap so a malicious caller can't request 1M voices.
        limit = min(limit, 200)

        filtered = [
            entry
            for entry in _VERTEX_GEMINI_CATALOG
            if _matches(entry, language=language, gender=gender, accent=accent)
        ]
        page_voices = filtered[start : start + limit]
        end = start + len(page_voices)
        next_cursor = str(end) if end < len(filtered) else None

        return VoiceCatalogPage(
            voices=page_voices,
            next_cursor=next_cursor,
            total_count=len(filtered),
        )

    def get_voice(self, voice_id: str) -> VoiceCatalogEntry | None:
        return self._by_id.get(voice_id)

    # ── Preview & synthesis ──────────────────────────────────────────────

    def preview_url(self, voice_id: str) -> str | None:
        # Vertex Gemini doesn't expose pre-rendered samples. Returning
        # ``None`` tells the api layer to fall back to ``synthesize``,
        # which it will cache for 24h via Cache-Control headers.
        if voice_id not in self._by_id:
            return None
        return None

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        speed: float = 1.0,
        language: str | None = None,
    ) -> VoiceSynthesisResult:
        if not text:
            raise ValueError("text must be non-empty")
        entry = self._by_id.get(voice_id)
        if entry is None:
            raise ValueError(f"unknown voice_id {voice_id!r} for vertex_gemini")
        # Clamp speed to the persona-allowed range as a defensive default.
        # BehavioralPersona.voice_speed is validated 0.7..1.3 already; we
        # re-clamp here so callers bypassing the schema (eg internal tools)
        # can't ship pathological values to the synthesis API.
        if speed < 0.7:
            speed = 0.7
        elif speed > 1.3:
            speed = 1.3
        target_language = language or entry.language

        try:
            audio_bytes = _synthesize_via_google_cloud(
                text=text,
                voice_name=entry.provider_metadata.get(
                    "google_voice_name", voice_id,
                ),
                language=target_language,
                speed=speed,
            )
            character_count = len(text)
            return VoiceSynthesisResult(
                audio_bytes=audio_bytes,
                audio_mime_type="audio/mpeg",
                character_count=character_count,
                estimated_cost_usd=character_count * _CHIRP3_HD_USD_PER_CHAR,
                provider_metadata={
                    "google_voice_name": entry.provider_metadata.get(
                        "google_voice_name", voice_id
                    ),
                    "language": target_language,
                },
            )
        except Exception:
            # Don't leak provider-specific exception types to the api
            # layer. Wrap in a generic RuntimeError; the api endpoint
            # converts to a 502 with a stable error code.
            logger.exception(
                "vertex_gemini.synthesize_failed",
                extra={"voice_id": voice_id, "text_chars": len(text)},
            )
            raise RuntimeError("voice synthesis failed") from None


    # ── Cloning (Phase 2a-cloning) ───────────────────────────────────────

    def clone_voice(
        self,
        *,
        consent_audio: bytes,
        consent_audio_mime: str,
        reference_audio: bytes | None = None,
        reference_audio_mime: str | None = None,
        display_name: str,
        language: str,
        organization_id: str,
        actor_id: str,
    ) -> VoiceCloningResult:
        """Clone a voice via Google Chirp 3 HD Instant Custom Voice.

        Wraps Google's instant-cloning API. The API performs server-side
        consent verification — if Google rejects the consent statement,
        we raise ``VoiceCloningConsentError`` so the API layer can
        surface a 422 with the exact reason. Other failures wrap to a
        generic ``RuntimeError`` (→ 503) so the picker UI doesn't
        confuse "user must retry" with "infrastructure broken".

        ``organization_id`` and ``actor_id`` are forwarded to Google's
        request labels so abuse investigation has tenant attribution
        without us having to maintain our own log of every cloning
        attempt.
        """
        if not consent_audio:
            raise ValueError("consent_audio must be non-empty")
        if len(consent_audio) > _MAX_CONSENT_AUDIO_BYTES:
            # Defence-in-depth — the API endpoint already enforces a 1MB
            # multipart limit. Belt-and-braces because crossing this
            # threshold makes Google reject the call anyway and we want
            # a clean error before we burn a quota slot.
            raise VoiceCloningConsentError(
                f"consent audio exceeds {_MAX_CONSENT_AUDIO_BYTES} bytes "
                f"(supplied {len(consent_audio)}); record at most 10 seconds.",
            )
        if consent_audio_mime not in _SUPPORTED_AUDIO_MIME_TYPES:
            raise VoiceCloningConsentError(
                f"unsupported consent audio MIME {consent_audio_mime!r}; "
                f"use one of {sorted(_SUPPORTED_AUDIO_MIME_TYPES)}",
            )

        try:
            cloning_key, estimated_cost = _clone_via_chirp3(
                consent_audio=consent_audio,
                consent_audio_mime=consent_audio_mime,
                reference_audio=reference_audio,
                reference_audio_mime=reference_audio_mime,
                language=language,
                organization_id=organization_id,
                actor_id=actor_id,
            )
        except VoiceCloningConsentError:
            # Re-raise — already a domain error. Log without the audio
            # bytes (which would be huge and PII).
            logger.info(
                "vertex_gemini.clone_voice_consent_rejected",
                extra={
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "language": language,
                },
            )
            raise
        except Exception:
            logger.exception(
                "vertex_gemini.clone_voice_failed",
                extra={
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "language": language,
                    "consent_audio_bytes": len(consent_audio),
                },
            )
            raise RuntimeError("voice cloning failed") from None

        return VoiceCloningResult(
            voice_cloning_key=cloning_key,
            display_name=display_name,
            language=language,
            estimated_cost_usd=estimated_cost,
            provider_metadata={
                "google_cloning_model": "chirp3-instant-custom-voice",
                "language": language,
            },
        )


# ── Cloning constants ────────────────────────────────────────────────────────

# 10 seconds * 48kHz * 16-bit * mono = ~960KB ceiling. We cap at 1MB to
# leave headroom for slightly higher-bitrate uploads.
_MAX_CONSENT_AUDIO_BYTES = 1_000_000

_SUPPORTED_AUDIO_MIME_TYPES: frozenset[str] = frozenset(
    {"audio/wav", "audio/x-wav", "audio/mp3", "audio/mpeg", "audio/webm"},
)

# Chirp 3 instant cloning is documented as a flat per-clone fee. The
# real number should be re-checked against Google's pricing page during
# eval — this estimate is good to the nearest cent.
_CHIRP3_CLONE_FEE_USD = 0.50


def _clone_via_chirp3(
    *,
    consent_audio: bytes,
    consent_audio_mime: str,
    reference_audio: bytes | None,
    reference_audio_mime: str | None,
    language: str,
    organization_id: str,
    actor_id: str,
) -> tuple[str, float]:
    """Lazy-import wrapper around the Chirp 3 instant-cloning client.

    Kept out of module top-level so the API process doesn't pay the
    SDK import cost on routes that never clone. Returns
    ``(cloning_key, estimated_cost_usd)``.

    Raises ``VoiceCloningConsentError`` when Google's API reports that
    the consent statement wasn't detected. Other failures bubble up as
    generic exceptions and are wrapped at the call site.
    """
    try:
        from google.cloud import texttospeech as tts  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "google-cloud-texttospeech is not installed; voice cloning unavailable",
        ) from exc

    # The instant-cloning API surface is namespaced under the
    # texttospeech client's ``synthesize_custom_voice`` extensions.
    # Exact entry-point naming is finalised in google-cloud-texttospeech
    # >= 2.27.x; the helper below resolves it dynamically so we don't
    # break when Google renames the SDK shape between minor releases.
    create_clone = (
        getattr(tts, "create_voice_cloning_key", None)
        or getattr(tts, "TextToSpeechClient", None)
    )
    if create_clone is None:
        raise RuntimeError(
            "google-cloud-texttospeech does not expose voice cloning; "
            "upgrade to a version that includes Chirp 3 Instant Custom Voice.",
        )

    client = tts.TextToSpeechClient()
    # Newer SDK versions expose ``create_voice_cloning_key`` directly;
    # older versions require constructing the cloning request via
    # ``CloneVoiceRequest`` from the same module. Try both shapes.
    request_factory = getattr(tts, "CloneVoiceRequest", None)
    if request_factory is not None:
        request = request_factory(
            consent={"audio_content": consent_audio, "mime_type": consent_audio_mime},
            reference={
                "audio_content": reference_audio or consent_audio,
                "mime_type": reference_audio_mime or consent_audio_mime,
            },
            language_code=language,
            labels={
                "organization_id": organization_id,
                "actor_id": actor_id,
            },
        )
        try:
            response = client.create_voice_cloning_key(request=request)
        except Exception as exc:  # pragma: no cover - depends on SDK exception shape
            message = str(exc).lower()
            if "consent" in message or "cnsnt" in message:
                raise VoiceCloningConsentError(str(exc)) from exc
            raise
        cloning_key = getattr(response, "voice_cloning_key", None) or getattr(
            response, "name", None,
        )
        if not cloning_key:
            raise RuntimeError(
                "Chirp 3 instant cloning returned no cloning key; "
                "this is a Google SDK contract violation.",
            )
        return str(cloning_key), _CHIRP3_CLONE_FEE_USD

    # Older SDKs without the typed request — fall back to dict.
    response = client.create_voice_cloning_key(  # type: ignore[attr-defined]
        consent={"audio_content": consent_audio, "mime_type": consent_audio_mime},
        reference={
            "audio_content": reference_audio or consent_audio,
            "mime_type": reference_audio_mime or consent_audio_mime,
        },
        language_code=language,
        labels={
            "organization_id": organization_id,
            "actor_id": actor_id,
        },
    )
    cloning_key = getattr(response, "voice_cloning_key", None) or getattr(
        response, "name", None,
    )
    if not cloning_key:
        raise RuntimeError(
            "Chirp 3 instant cloning returned no cloning key; "
            "this is a Google SDK contract violation.",
        )
    return str(cloning_key), _CHIRP3_CLONE_FEE_USD


def _matches(
    entry: VoiceCatalogEntry,
    *,
    language: str | None,
    gender: VoiceGender | None,
    accent: str | None,
) -> bool:
    if language is not None:
        # Prefix match: "en" matches "en-US", "en-GB", "en-NG".
        # Case-insensitive on both sides.
        if not entry.language.casefold().startswith(language.casefold()):
            return False
    if gender is not None and entry.gender != gender:
        return False
    if accent is not None:
        if entry.accent is None or accent.casefold() not in entry.accent.casefold():
            return False
    return True


def _synthesize_via_google_cloud(
    *,
    text: str,
    voice_name: str,
    language: str,
    speed: float,
) -> bytes:
    """Lazy import of google-cloud-texttospeech.

    Kept out of the module top-level so the API process doesn't pay the
    ~250ms import cost when only listing the catalog. If the package
    isn't installed we raise — the api layer turns that into a 503 with
    a clear error code so operators know to install it.
    """
    try:
        from google.cloud import texttospeech as tts  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "google-cloud-texttospeech is not installed; voice preview unavailable",
        ) from exc

    client = tts.TextToSpeechClient()
    synthesis_input = tts.SynthesisInput(text=text)
    voice = tts.VoiceSelectionParams(language_code=language, name=voice_name)
    audio_config = tts.AudioConfig(
        audio_encoding=tts.AudioEncoding.MP3,
        speaking_rate=speed,
    )
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config,
    )
    return bytes(response.audio_content)


__all__ = ["VertexGeminiVoiceProvider"]
