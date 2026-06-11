"""Tests for src/ruhu/classifier/vertex_gemini_backend.py.

Tests use ``http_post`` + ``access_token_loader`` callable injection so
the full Vertex Gemini classifier path is exercised without a live
Vertex cluster.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from ruhu.classifier.protocol import ClassificationRequest
from ruhu.classifier.vertex_gemini_backend import (
    DEFAULT_FALLBACK_CONFIDENCE,
    DEFAULT_LOCATION,
    DEFAULT_MODEL,
    UNKNOWN_LABEL,
    VertexGeminiClassifierBackend,
)


def _request(
    *,
    user_text: str = "where is my money?",
    candidate_labels: dict[str, str] | None = None,
    lora_name: str | None = None,
) -> ClassificationRequest:
    return ClassificationRequest(
        agent_id="a1",
        agent_version_id="v1",
        step_id="entry",
        step_name="Entry",
        step_summary="Triage.",
        user_text=user_text,
        candidate_labels=(
            {"transfer_status": "x", "kyc_help": "y"}
            if candidate_labels is None
            else candidate_labels
        ),
        prefix=None,
        suffix=None,
        lora_name=lora_name,
    )


def _success_response(intent: str = "transfer_status") -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": json.dumps({"intent": intent})}]},
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 200,
            "candidatesTokenCount": 8,
        },
    }


# ── happy path ─────────────────────────────────────────────────────────────


def test_classify_returns_intent_with_fallback_confidence() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _success_response("transfer_status")

    backend = VertexGeminiClassifierBackend(
        project="proj-x",
        http_post=fake_post,
        access_token_loader=lambda: "stub-token",
    )
    result = backend.classify(_request())

    assert result.chosen_label == "transfer_status"
    assert result.confidence == DEFAULT_FALLBACK_CONFIDENCE
    assert result.backend == "vertex_gemini"
    assert result.error is None
    assert "/projects/proj-x/" in captured["url"]
    assert f"/locations/{DEFAULT_LOCATION}/" in captured["url"]
    assert f"/models/{DEFAULT_MODEL}:generateContent" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer stub-token"


def test_classify_uses_configured_project_location_and_model() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["url"] = url
        return _success_response()

    backend = VertexGeminiClassifierBackend(
        project="prod-classifier",
        location="us-central1",
        model="gemini-2.5-flash",
        http_post=fake_post,
        access_token_loader=lambda: "tok",
    )
    backend.classify(_request())
    assert (
        captured["url"]
        == "https://aiplatform.googleapis.com/v1/projects/prod-classifier"
        "/locations/us-central1/publishers/google/models/gemini-2.5-flash:generateContent"
    )


def test_classify_payload_pins_temperature_zero_and_disables_thinking() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["json"] = json
        return _success_response()

    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=fake_post,
        access_token_loader=lambda: "tok",
    )
    backend.classify(_request())
    gen_config = captured["json"]["generationConfig"]
    assert gen_config["temperature"] == 0.0
    assert gen_config["responseMimeType"] == "application/json"
    assert gen_config["thinkingConfig"]["thinkingBudget"] == 0


def test_classify_propagates_lora_name_through_to_result() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: _success_response(),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request(lora_name="agent-a-lora-v3"))
    assert result.lora_name == "agent-a-lora-v3"


def test_classify_records_elapsed_ms() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: _success_response(),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.elapsed_ms >= 0


def test_classify_uses_custom_fallback_confidence() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        fallback_confidence=0.5,
        http_post=lambda **_: _success_response(),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.confidence == 0.5


# ── unknown / out-of-catalog handling ─────────────────────────────────────


def test_classify_unknown_intent_returns_none_no_error() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: _success_response(intent=UNKNOWN_LABEL),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.chosen_label is None
    assert result.error is None


def test_classify_intent_outside_catalog_records_error() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: _success_response(intent="made_up"),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.chosen_label is None
    assert result.error and "intent_outside_catalog" in result.error


def test_classify_empty_response_returns_no_choices_error() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: {"candidates": []},
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.chosen_label is None
    assert result.error == "no_choices"


def test_classify_completely_empty_body_returns_empty_response_error() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: {},
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.error == "empty_response"


def test_classify_short_circuits_on_empty_user_text() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: pytest.fail("must not call vertex for empty user_text"),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request(user_text=""))
    assert result.error == "empty_request"


def test_classify_short_circuits_on_no_candidate_labels() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: pytest.fail("must not call vertex for empty intents"),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request(candidate_labels={}))
    assert result.error == "empty_request"


# ── error coercion ─────────────────────────────────────────────────────────


def test_classify_propagates_connection_error() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: (_ for _ in ()).throw(ConnectionError("vertex pod down")),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.chosen_label is None
    assert result.error == "connection_error"


def test_classify_timeout_classified_correctly() -> None:
    class _MockTimeout(Exception):
        pass

    _MockTimeout.__name__ = "TimeoutException"

    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: (_ for _ in ()).throw(_MockTimeout("upstream timed out")),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.error == "timeout"


def test_classify_unknown_exception_falls_through_to_classname() -> None:
    backend = VertexGeminiClassifierBackend(
        project="p",
        http_post=lambda **_: (_ for _ in ()).throw(ValueError("oops")),
        access_token_loader=lambda: "tok",
    )
    result = backend.classify(_request())
    assert result.error and result.error.startswith("valueerror")


# ── factory dispatch ───────────────────────────────────────────────────────


def test_factory_builds_vertex_gemini_with_project() -> None:
    from ruhu.classifier.factory import (
        ClassifierBackendConfig,
        build_classifier,
    )

    backend = build_classifier(
        ClassifierBackendConfig(
            kind="vertex_gemini",
            vertex_project="my-project",
            vertex_location="europe-west2",
            model="gemini-2.5-flash",
            vertex_fallback_confidence=0.85,
            timeout_ms=8000,
        )
    )
    assert isinstance(backend, VertexGeminiClassifierBackend)
    assert backend.project == "my-project"
    assert backend.location == "europe-west2"
    assert backend.model == "gemini-2.5-flash"
    assert backend.fallback_confidence == 0.85
    assert backend.timeout_seconds == pytest.approx(8.0)


def test_factory_vertex_gemini_requires_project() -> None:
    from ruhu.classifier.factory import (
        ClassifierBackendConfig,
        build_classifier,
    )

    with pytest.raises(ValueError, match="vertex_project"):
        build_classifier(ClassifierBackendConfig(kind="vertex_gemini"))


def test_factory_vertex_gemini_default_model_when_none() -> None:
    from ruhu.classifier.factory import (
        ClassifierBackendConfig,
        build_classifier,
    )
    from ruhu.classifier.vertex_gemini_backend import DEFAULT_MODEL

    backend = build_classifier(
        ClassifierBackendConfig(
            kind="vertex_gemini",
            vertex_project="p",
            model=None,
        )
    )
    assert isinstance(backend, VertexGeminiClassifierBackend)
    assert backend.model == DEFAULT_MODEL
    # Sanity-check that the documented default is the cost-effective Flash
    # model, not Pro — see vertex_gemini_backend.DEFAULT_MODEL comment.
    assert "flash" in backend.model.lower()


def test_factory_vertex_gemini_end_to_end_classify() -> None:
    """Build via the factory, then exercise classify with mocked http_post."""
    from ruhu.classifier.factory import (
        ClassifierBackendConfig,
        build_classifier,
    )

    backend = build_classifier(
        ClassifierBackendConfig(
            kind="vertex_gemini",
            vertex_project="p",
        )
    )
    assert isinstance(backend, VertexGeminiClassifierBackend)
    backend.http_post = lambda **_: _success_response("transfer_status")
    backend.access_token_loader = lambda: "tok"
    result = backend.classify(_request())
    assert result.chosen_label == "transfer_status"
    assert result.backend == "vertex_gemini"
