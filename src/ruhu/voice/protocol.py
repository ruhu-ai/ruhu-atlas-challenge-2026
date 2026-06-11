"""Phase 2a-base — voice provider Protocol + catalog data model.

Pluggable abstraction over TTS providers. Phase 2a-base ships the
``VertexGeminiVoiceProvider`` only — Phase 2a-paid adds ElevenLabs and
Cartesia adapters once commercial agreements are in place. The Protocol
is the contract both must satisfy.

Design notes (production-readiness checklist baked in):

* **Stateless Protocol** — providers are constructed once and called
  concurrently from request handlers. No per-call mutable state.
* **Catalog separation** — ``list_voices`` is metadata-only and safe to
  cache (24h on the API edge per spec). ``synthesize`` is paid; cost
  telemetry hooks ride alongside.
* **Cost surface** — ``VoiceSynthesisResult.estimated_cost_usd`` lets the
  api layer record costs via the existing ``provider_costs.py`` infra
  without leaking provider-specific fields.
* **Backwards compatibility** — production TTS still flows through the
  ``livekit_worker`` direct Google Cloud TTS path today. The Protocol's
  ``synthesize`` is initially used only for preview clips (and future
  2a-paid replacements). Threading ``BehavioralPersona.voice_id`` into
  the worker dispatch is a follow-up tracked in 2b's per-language
  ``update_options()`` work.
* **No leaky abstractions** — provider-specific identifiers go in
  ``VoiceCatalogEntry.provider_metadata`` so consumers don't depend on
  e.g. Vertex's ``en-US-Chirp3-HD-Kore`` format. Vertex providers can
  parse it; ElevenLabs providers ignore it.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


# ── Vocabulary ───────────────────────────────────────────────────────────────


class VoiceGender(StrEnum):
    """Voice gender presentation. ``neutral`` covers androgynous / non-binary
    voices. We deliberately don't represent voice age, accent, or other
    facets as enums because the search space is provider-specific — those
    are free-text strings on the catalog entry."""

    male = "male"
    female = "female"
    neutral = "neutral"


# ── Catalog ──────────────────────────────────────────────────────────────────


class VoiceCatalogEntry(BaseModel):
    """A single voice in a provider's catalog.

    Field stability contract: anything in this model is part of the
    public API surface and must not change shape without a migration.
    Provider-specific extensions go in ``provider_metadata`` (opaque
    dict).
    """

    model_config = ConfigDict(extra="forbid")

    voice_id: str = Field(min_length=1, max_length=128)
    """Provider-specific identifier. Pass back into ``synthesize`` and
    ``preview_url``."""

    provider: str = Field(min_length=1, max_length=64)
    """Which provider owns this voice (``"vertex_gemini"``,
    ``"elevenlabs"``, ``"cartesia"``)."""

    display_name: str = Field(min_length=1, max_length=64)
    """Human-friendly label for the picker UI."""

    language: str = Field(min_length=2, max_length=16)
    """BCP-47 language tag (``"en"``, ``"en-US"``, ``"yo-NG"``)."""

    gender: VoiceGender

    accent: str | None = Field(default=None, max_length=64)
    """Free-text accent label (``"American"``, ``"British"``,
    ``"Lagos Nigerian"``). Optional — providers without per-voice accent
    metadata leave it blank."""

    description: str | None = Field(default=None, max_length=256)
    """Short editorial description for the picker. Optional."""

    sample_text: str | None = Field(default=None, max_length=128)
    """Recommended preview script. Optional — preview endpoint falls back
    to a generic line."""

    provider_metadata: dict[str, str] = Field(default_factory=dict)
    """Opaque per-provider extension. Don't read from outside the
    matching provider — there's no schema guarantee."""


class VoiceCatalogPage(BaseModel):
    """Paginated slice of a provider catalog.

    Pagination is forward-only with an opaque cursor. Total count is
    optional because some providers (ElevenLabs) don't expose it
    cheaply.
    """

    model_config = ConfigDict(extra="forbid")

    voices: list[VoiceCatalogEntry] = Field(default_factory=list)
    next_cursor: str | None = None
    total_count: int | None = None


# ── Synthesis ────────────────────────────────────────────────────────────────


class VoiceSynthesisResult(BaseModel):
    """Output of a ``synthesize`` call.

    The bytes are the audio payload (encoding stated in
    ``audio_mime_type``). Cost is the provider's best estimate so the
    api layer can land a ``ProviderCostRecord`` without re-pricing.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    audio_bytes: bytes
    audio_mime_type: str = Field(default="audio/mpeg", max_length=64)
    character_count: int = Field(ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    provider_metadata: dict[str, str] = Field(default_factory=dict)


# ── Voice cloning (Phase 2a-cloning) ─────────────────────────────────────────


class VoiceCloningConsentError(RuntimeError):
    """Raised when the consent audio fails verification at the provider.

    Producers (e.g. ``VertexGeminiVoiceProvider``) raise this when:

    * Google's instant-cloning API rejects the consent statement (wrong
      script / wrong language / unintelligible).
    * The audio length exceeds the provider's documented limit.
    * The audio encoding is unsupported.

    The API layer turns this into a 422 with the provider's reason so
    the wizard UI can show a clear error to the author. Don't widen this
    to other failure modes — networking errors and DB errors should
    surface as 5xx so operators can distinguish "user must retry" from
    "infrastructure broken".
    """


class VoiceCloningResult(BaseModel):
    """Output of a ``clone_voice`` call.

    The ``voice_cloning_key`` is the opaque token returned by Google's
    Chirp 3 HD instant-cloning API. Per Google's design, keys are
    stored client-side; Ruhu persists them encrypted in the
    ``voice_clones`` table (see ``ruhu.voice_cloning.VoiceCloneStore``).
    """

    model_config = ConfigDict(extra="forbid")

    voice_cloning_key: str = Field(min_length=1)
    """Opaque cloning token. Treat as a credential — never log in
    plaintext, encrypt at rest with AAD binding to (org_id, clone_id)."""

    display_name: str = Field(min_length=1, max_length=64)
    """Author-supplied label echoed back so the catalog entry matches."""

    language: str = Field(min_length=2, max_length=16)
    """The language the consent recording was provided in. Subsequent
    synthesize() calls can request transfer to other supported
    languages — see Google's multilingual transfer matrix."""

    estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    """Cost of the cloning operation itself (not subsequent synth)."""

    provider_metadata: dict[str, str] = Field(default_factory=dict)
    """Provider-specific opaque extension. Don't rely on the shape from
    consumer code; the matching provider knows what's in there."""


# ── Provider Protocol ────────────────────────────────────────────────────────


@runtime_checkable
class VoiceProvider(Protocol):
    """A pluggable TTS provider.

    Implementations live under ``src/ruhu/voice/``. Phase 2a-base ships
    ``VertexGeminiVoiceProvider``; Phase 2a-cloning extends it with
    voice-cloning support via Chirp 3 HD Instant Custom Voice.

    Concurrency: implementations MUST be safe to call concurrently from
    multiple request handlers. They typically wrap a thread-safe HTTP
    client and don't hold per-call state.
    """

    name: str
    """Stable provider key (``"vertex_gemini"``). Stored on
    ``VoiceCatalogEntry.provider`` and ``ProviderCostRecord.provider``."""

    supports_cloning: bool
    """Static capability flag. ``True`` means ``clone_voice()`` is
    implemented; ``False`` means callers should never reach the cloning
    code path (the API endpoint short-circuits with 501). This is a
    static class attribute, not a method, because the answer is the
    same for every call site and we want the API layer to be able to
    check it before opening a multipart connection. Defaults to
    ``False`` for safety — providers must opt in."""

    def list_voices(
        self,
        *,
        language: str | None = None,
        gender: VoiceGender | None = None,
        accent: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> VoiceCatalogPage:
        """Return a page of voices, optionally filtered.

        Filters are AND-combined. ``language`` accepts a prefix match
        (``"en"`` matches ``"en-US"``, ``"en-GB"``, ``"en-NG"``).
        ``cursor`` is an opaque continuation token from a prior page;
        ``None`` returns the first page.
        """
        ...

    def get_voice(self, voice_id: str) -> VoiceCatalogEntry | None:
        """Return the catalog entry for a specific voice, or ``None`` if
        the provider doesn't recognize the id. Don't raise on unknown
        ids — that path is hot for the picker UI."""
        ...

    def preview_url(self, voice_id: str) -> str | None:
        """Return a URL to a 5-second sample of this voice.

        Implementations may return:

        * A pre-signed CDN URL (preferred — cacheable, no synthesis
          cost on every preview click).
        * A URL pointing back at our own ``/persona/voices/.../preview``
          endpoint, which then materialises bytes via ``synthesize``.
        * ``None`` if the provider can't produce a preview without a
          full synthesis call (caller falls back to ``synthesize``).
        """
        ...

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        speed: float = 1.0,
        language: str | None = None,
    ) -> VoiceSynthesisResult:
        """Synthesize ``text`` with the given voice. Used by the preview
        endpoint and (eventually) by per-language runtime swaps.

        Production TTS in the LiveKit worker continues to bypass this
        Protocol via direct Google Cloud TTS until per-language voice
        swap lands in 2b.

        ``speed`` is provider-clamped to ``[0.7, 1.3]`` per
        ``BehavioralPersona.voice_speed`` validation.
        """
        ...

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
        """Clone a voice from a consented reference recording.

        Returns a ``VoiceCloningResult`` whose ``voice_cloning_key`` is
        the credential the API layer persists encrypted in the
        ``voice_clones`` table. Subsequent ``synthesize()`` calls
        receive the cloning key via the catalog entry's
        ``provider_metadata``.

        Raises ``VoiceCloningConsentError`` when the provider rejects
        the consent statement; raises ``RuntimeError`` for transient
        provider failures (the API layer turns the former into 422 and
        the latter into 503).

        ``language`` is the BCP-47 tag the consent recording was made
        in. Multilingual transfer (using the same key in another
        language) is provider-specific — Google's Chirp 3 HD documents
        the supported transfer matrix.

        ``organization_id`` and ``actor_id`` are passed in for the
        provider's own audit / abuse-prevention hooks; they are NOT
        the same as the AAD used for at-rest encryption (that's
        applied by the store layer, not the provider).

        Default implementation raises ``NotImplementedError``. Override
        only if ``supports_cloning = True``.
        """
        ...


__all__ = [
    "VoiceCatalogEntry",
    "VoiceCatalogPage",
    "VoiceCloningConsentError",
    "VoiceCloningResult",
    "VoiceGender",
    "VoiceProvider",
    "VoiceSynthesisResult",
]
