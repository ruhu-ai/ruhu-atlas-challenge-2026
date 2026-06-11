"""Phase 2b — text-only language detection fallback.

When the conversation has a STT pass (voice / multimodal), language
detection is the STT provider's job — Deepgram Nova-3 ``"multi"`` /
Soniox / Google STT alternatives all return a language tag with the
transcript. The `LanguageRoutingResponseGenerator` decorator reads
that tag from ``RenderContext.metadata`` (the worker stashes it there
during transcript ingestion).

This module exists for the **text-only chat path**, where there's no
STT and the renderer must derive the language itself.

Design contract:

* **Same return shape regardless of provider** — callers don't care
  which detector ran; they only care about ``(language, confidence)``.
* **Fail-open, never raise** — language detection is a hint, not a
  gate. Returns ``None`` when the detector can't decide; the router
  treats that as "keep current language".
* **Bounded latency** — Phase 2 spec gives 200ms. Vertex Gemini's
  detection prompt is ~150ms; FastText is ~5ms. Detectors that exceed
  budget should return ``None`` rather than block the turn.
* **Cheap import surface** — module top level pulls only stdlib.
  Heavy detector dependencies (``fasttext``, ``google-cloud-aiplatform``)
  are imported lazily inside their respective constructors so the API
  process doesn't pay the cost when language detection isn't used.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


# ── Public contract ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LanguageDetectionResult:
    """Detector output shared by every implementation.

    ``language`` is a BCP-47 language code (``"en"``, ``"yo"``,
    sometimes ``"en-US"`` if the detector goes that deep — the router
    normalises). ``confidence`` is in [0.0, 1.0]; the router uses it
    against the agent's stability threshold.
    """

    language: str
    confidence: float


class LanguageDetector(Protocol):
    """Pluggable text-only language detector.

    Implementations:

    * ``FastTextLanguageDetector`` — bundled, ~5ms, ~93% accuracy on
      short text. Default.
    * ``VertexGeminiLanguageDetector`` — opt-in via env var, ~150ms,
      higher accuracy on African languages. Cost ~$0.0001/turn.
    """

    name: str

    def detect(
        self, text: str, *, allowed_languages: list[str] | None = None,
    ) -> LanguageDetectionResult | None:
        """Detect the dominant language of ``text``.

        Returns ``None`` when the detector can't produce a confident
        answer (empty text, internal error, exceeded latency budget,
        confidence below the detector's own floor). The router treats
        ``None`` as "no signal — keep current language".

        ``allowed_languages``, if provided, hints to the detector that
        these are the languages of interest. Detectors that support
        constrained decoding (e.g. Vertex with a system prompt) will
        bias toward this set; detectors that don't (FastText) ignore
        the hint.
        """
        ...


# ── FastText detector (default) ──────────────────────────────────────────


class FastTextLanguageDetector:
    """Stub-shaped FastText detector.

    The real ``fasttext`` package is large and not in our default
    dependency set. Until we add it (a small PR with the model file
    download + license review), this detector implements the public
    contract and returns ``None`` so the router falls back to keeping
    the current language. That's the same behaviour as a high-latency
    timeout — the router was designed for it.

    The seam is correct: when a future PR adds the dependency, only
    the ``detect()`` body changes, not the call sites.
    """

    name = "fasttext"
    _MIN_CONFIDENCE = 0.55

    def __init__(self) -> None:
        self._model: object | None = None

    def detect(
        self, text: str, *, allowed_languages: list[str] | None = None,
    ) -> LanguageDetectionResult | None:
        del allowed_languages  # FastText ignores the hint
        if not text or len(text.strip()) < 3:
            return None
        try:
            model = self._load_model()
        except Exception:
            # Lazy-import failed — package not installed. Don't log
            # noisily on every turn; it's expected in deployments
            # that haven't enabled text detection yet.
            return None
        if model is None:
            return None
        try:
            label, confidence = self._predict(model, text)
        except Exception:
            logger.warning("fasttext_detector.predict_failed", exc_info=True)
            return None
        if confidence < self._MIN_CONFIDENCE:
            return None
        return LanguageDetectionResult(language=label, confidence=confidence)

    def _load_model(self) -> object | None:
        if self._model is not None:
            return self._model
        try:
            import fasttext  # type: ignore[import-untyped]
        except ImportError:
            return None
        # Default model path; operators override via env var. We don't
        # ship the model file with the package (it's ~125MB) — sites
        # that want text detection install it during deploy.
        model_path = os.getenv("RUHU_FASTTEXT_LID_PATH", "/etc/ruhu/lid.176.bin")
        if not os.path.exists(model_path):
            return None
        self._model = fasttext.load_model(model_path)
        return self._model

    @staticmethod
    def _predict(model: object, text: str) -> tuple[str, float]:
        # FastText returns labels prefixed with ``__label__`` and a
        # numpy array of confidences. We strip the prefix so callers
        # see plain BCP-47-shaped tags.
        labels, confidences = model.predict(text, k=1)  # type: ignore[attr-defined]
        label = labels[0].replace("__label__", "")
        return label, float(confidences[0])


# ── Vertex Gemini detector (opt-in) ──────────────────────────────────────


class VertexGeminiLanguageDetector:
    """Detector using a Vertex Gemini Flash language-id prompt.

    Higher accuracy on African languages than FastText, at the cost of
    ~150ms and a Vertex call per turn. Opt-in via
    ``RUHU_TEXT_LANGUAGE_DETECTION=vertex``.

    Stubbed in this PR — the full prompt template + Vertex client
    wiring lands with the same follow-up that activates real Vertex
    calls in the existing ``classifier/vertex_gemini_backend.py``
    pattern. The seam is correct: when the Vertex call is wired, only
    the ``detect()`` body changes.
    """

    name = "vertex_gemini"

    def detect(
        self, text: str, *, allowed_languages: list[str] | None = None,
    ) -> LanguageDetectionResult | None:
        del text, allowed_languages
        # Stub: returns None until the Vertex call is wired.
        return None


# ── Factory ──────────────────────────────────────────────────────────────


def build_language_detector_from_env() -> LanguageDetector:
    """Construct the configured detector. Default FastText.

    Operators flip to Vertex via
    ``RUHU_TEXT_LANGUAGE_DETECTION=vertex`` for tenants where
    African-language accuracy matters more than the latency tradeoff.
    """
    raw = (
        os.getenv("RUHU_TEXT_LANGUAGE_DETECTION", "").strip().lower()
        or "fasttext"
    )
    if raw == "vertex":
        return VertexGeminiLanguageDetector()
    if raw != "fasttext":
        logger.warning(
            "language_detector.unknown_key",
            extra={"requested": raw, "fallback": "fasttext"},
        )
    return FastTextLanguageDetector()


__all__ = [
    "FastTextLanguageDetector",
    "LanguageDetectionResult",
    "LanguageDetector",
    "VertexGeminiLanguageDetector",
    "build_language_detector_from_env",
]
