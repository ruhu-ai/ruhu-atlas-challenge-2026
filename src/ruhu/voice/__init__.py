"""Voice subsystem.

Two unrelated concerns historically lived under this package:

* **TTS provider abstraction** (Phase 2a-base, this PR): the pluggable
  ``VoiceProvider`` Protocol + ``VertexGeminiVoiceProvider`` shipped as
  the default backing the persona Voice picker.
* **Realtime voice utilities** (pre-existing): ``concurrency.py``,
  ``transcript_dedup.py`` are helpers for the LiveKit worker. Unchanged
  by this PR — same imports, same surface.

Public re-exports below cover only the provider abstraction. Realtime
utilities should still be imported via their full path.
"""
from .factory import build_voice_provider_from_env
from .protocol import (
    VoiceCatalogEntry,
    VoiceCatalogPage,
    VoiceCloningConsentError,
    VoiceCloningResult,
    VoiceGender,
    VoiceProvider,
    VoiceSynthesisResult,
)
from .vertex_gemini_provider import VertexGeminiVoiceProvider

__all__ = [
    "VertexGeminiVoiceProvider",
    "VoiceCatalogEntry",
    "VoiceCatalogPage",
    "VoiceCloningConsentError",
    "VoiceCloningResult",
    "VoiceGender",
    "VoiceProvider",
    "VoiceSynthesisResult",
    "build_voice_provider_from_env",
]
