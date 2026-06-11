"""Voice provider factory.

Reads ``RUHU_VOICE_PROVIDER`` (default ``"vertex_gemini"``) and returns
the configured provider. Phase 2a-base only knows about ``vertex_gemini``;
``elevenlabs`` and ``cartesia`` raise a clear "not yet available" error
that operators see when they prematurely flip the env var.

Failure mode: an unrecognised provider name returns the Vertex default
with a warning, NOT an exception. A misconfigured env var should not
brick the API; the operator should still get voice listings.
"""
from __future__ import annotations

import logging
import os

from .protocol import VoiceProvider
from .vertex_gemini_provider import VertexGeminiVoiceProvider

logger = logging.getLogger(__name__)


_KNOWN_KEYS: frozenset[str] = frozenset(
    {"vertex_gemini", "elevenlabs", "cartesia"}
)


def build_voice_provider_from_env() -> VoiceProvider:
    """Construct the configured voice provider.

    Operators set ``RUHU_VOICE_PROVIDER`` to choose. Default is
    ``vertex_gemini``. ``elevenlabs`` and ``cartesia`` are reserved for
    Phase 2a-paid; selecting them today raises a clear error so the
    operator knows what's missing instead of silently degrading.
    """
    raw = os.getenv("RUHU_VOICE_PROVIDER", "").strip().lower() or "vertex_gemini"
    if raw == "vertex_gemini":
        return VertexGeminiVoiceProvider()
    if raw in {"elevenlabs", "cartesia"}:
        raise RuntimeError(
            f"voice provider {raw!r} requires Phase 2a-paid (commercial "
            f"contract pending). Set RUHU_VOICE_PROVIDER=vertex_gemini or "
            f"unset it to use the default."
        )
    if raw not in _KNOWN_KEYS:
        # Unknown values: fall back to default and warn. Don't brick
        # the API on a typo'd env var.
        logger.warning(
            "voice_provider_factory.unknown_key",
            extra={"requested": raw, "fallback": "vertex_gemini"},
        )
        return VertexGeminiVoiceProvider()
    # Should be unreachable given the membership check above, but
    # static analyzers like the explicit fallthrough.
    return VertexGeminiVoiceProvider()


__all__ = ["build_voice_provider_from_env"]
