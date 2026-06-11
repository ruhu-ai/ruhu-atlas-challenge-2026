"""Phase 2b — text-only language detector tests.

The detectors are stubbed in this PR (FastText package not in deps;
Vertex Gemini wiring lands with the same follow-up that activates
real Vertex calls in classifier/vertex_gemini_backend.py). Tests pin
the public contract so the seam stays correct when real
implementations land:

* Returns ``LanguageDetectionResult | None`` regardless of provider.
* Never raises (the router treats raises as None anyway, but
  detectors should fail-open inside their bodies).
* Empty / very short text returns None.
* Factory dispatches on the env var.
"""
from __future__ import annotations

from ruhu.language_detection import (
    FastTextLanguageDetector,
    LanguageDetectionResult,
    VertexGeminiLanguageDetector,
    build_language_detector_from_env,
)


class TestFastTextDetector:
    """In this PR the detector returns None when ``fasttext`` isn't
    installed (the default state in CI). Tests pin the seam."""

    def test_empty_text_returns_none(self):
        detector = FastTextLanguageDetector()
        assert detector.detect("") is None

    def test_whitespace_only_returns_none(self):
        detector = FastTextLanguageDetector()
        assert detector.detect("   ") is None

    def test_short_text_returns_none(self):
        """Less than 3 chars short-circuits before any model call —
        per the FastText accuracy floor on tiny inputs."""
        detector = FastTextLanguageDetector()
        assert detector.detect("hi") is None

    def test_no_model_installed_returns_none(self):
        """When fasttext isn't installed, detect() falls through to
        None instead of raising. The router treats None as 'no
        signal — keep current language'."""
        detector = FastTextLanguageDetector()
        result = detector.detect("This is a longer sentence in English.")
        # In CI the package is absent — None is the correct result.
        assert result is None

    def test_name_attribute_is_stable(self):
        """``name`` is what gets recorded in audit / metrics. Don't
        change it without a migration."""
        assert FastTextLanguageDetector.name == "fasttext"


class TestVertexGeminiDetector:
    def test_stub_returns_none(self):
        """The Vertex stub always returns None until the Vertex call
        is wired. Audit + tests assume this contract."""
        detector = VertexGeminiLanguageDetector()
        assert detector.detect("Hello world.") is None

    def test_name_is_stable(self):
        assert VertexGeminiLanguageDetector.name == "vertex_gemini"


class TestFactory:
    def test_default_returns_fasttext(self, monkeypatch):
        monkeypatch.delenv("RUHU_TEXT_LANGUAGE_DETECTION", raising=False)
        detector = build_language_detector_from_env()
        assert detector.name == "fasttext"

    def test_explicit_vertex(self, monkeypatch):
        monkeypatch.setenv("RUHU_TEXT_LANGUAGE_DETECTION", "vertex")
        detector = build_language_detector_from_env()
        assert detector.name == "vertex_gemini"

    def test_unknown_value_falls_back_to_default(self, monkeypatch):
        """A typo'd env var must NOT brick text detection. Fall back
        to FastText and warn, same shape as the voice-provider
        factory."""
        monkeypatch.setenv("RUHU_TEXT_LANGUAGE_DETECTION", "verteks")
        detector = build_language_detector_from_env()
        assert detector.name == "fasttext"


class TestResultShape:
    """LanguageDetectionResult is the public contract — anything
    matching it is acceptable from a detector. Lock the shape."""

    def test_result_has_language_and_confidence(self):
        result = LanguageDetectionResult(language="en", confidence=0.95)
        assert result.language == "en"
        assert result.confidence == 0.95

    def test_result_is_frozen(self):
        """Immutable so caller code can't accidentally mutate the
        confidence and corrupt subsequent decisions."""
        import dataclasses

        result = LanguageDetectionResult(language="en", confidence=0.95)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.confidence = 0.5  # type: ignore[misc]


import pytest  # noqa: E402 — placed after the class to keep the file readable
