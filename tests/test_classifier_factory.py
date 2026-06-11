"""Tests for src/ruhu/classifier/factory.py — WI-3.4."""
from __future__ import annotations

from threading import Lock

import pytest

from ruhu.classifier.factory import (
    ClassifierBackendConfig,
    build_classifier,
    build_classifier_from_env,
    build_classifier_from_settings,
)
from ruhu.classifier.transformers_backend import TransformersClassifierBackend
from ruhu.classifier.vllm_backend import VLLMClassifierBackend
from ruhu.runtime_config import RuntimeSettings


# ── build_classifier dispatch ──────────────────────────────────────────────


def test_build_classifier_returns_vllm_backend_with_correct_config() -> None:
    backend = build_classifier(
        ClassifierBackendConfig(
            kind="vllm",
            base_url="http://vllm.classifier.svc.cluster.local:8000",
            model="Qwen/Qwen3-8B",
            timeout_ms=300,
            guided_decoding_backend="outlines",
        )
    )
    assert isinstance(backend, VLLMClassifierBackend)
    assert backend.base_url == "http://vllm.classifier.svc.cluster.local:8000"
    assert backend.base_model == "Qwen/Qwen3-8B"
    assert backend.timeout_seconds == pytest.approx(0.3)
    assert backend.guided_decoding_backend == "outlines"


def test_build_classifier_returns_transformers_backend_with_supplied_model() -> None:
    fake_model = object()
    fake_processor = object()
    backend = build_classifier(
        ClassifierBackendConfig(
            kind="transformers",
            transformers_model=fake_model,
            transformers_processor=fake_processor,
        )
    )
    assert isinstance(backend, TransformersClassifierBackend)
    assert backend.model is fake_model
    assert backend.processor is fake_processor


def test_build_classifier_vertex_gemini_requires_response_generator() -> None:
    """vertex_gemini backend needs a GCP project (since WI-5.3 the backend
    issues direct Vertex REST calls — credential / project plumbing is
    explicit). Detailed adapter coverage lives in
    test_classifier_vertex_gemini_backend.py."""
    with pytest.raises(ValueError, match="vertex_project"):
        build_classifier(ClassifierBackendConfig(kind="vertex_gemini"))


def test_build_classifier_unsupported_kind_raises() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        build_classifier(ClassifierBackendConfig(kind="bogus"))  # type: ignore[arg-type]


# ── required-field validation ──────────────────────────────────────────────


def test_build_classifier_vllm_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        build_classifier(
            ClassifierBackendConfig(kind="vllm", base_url=None, model="m")
        )


def test_build_classifier_vllm_requires_model() -> None:
    with pytest.raises(ValueError, match="model"):
        build_classifier(
            ClassifierBackendConfig(kind="vllm", base_url="http://x:8000", model=None)
        )


def test_build_classifier_transformers_requires_model_and_processor() -> None:
    with pytest.raises(ValueError, match="transformers_model"):
        build_classifier(ClassifierBackendConfig(kind="transformers"))


def test_build_classifier_transformers_requires_processor_when_model_present() -> None:
    with pytest.raises(ValueError, match="transformers_processor"):
        build_classifier(
            ClassifierBackendConfig(
                kind="transformers",
                transformers_model=object(),
                transformers_processor=None,
            )
        )


# ── timeout coercion ───────────────────────────────────────────────────────


def test_vllm_timeout_minimum_clamps_to_one_ms() -> None:
    """Even with timeout_ms=0 the backend gets >0 (avoid httpx ValueError)."""
    backend = build_classifier(
        ClassifierBackendConfig(
            kind="vllm",
            base_url="http://x:8000",
            model="m",
            timeout_ms=0,
        )
    )
    assert isinstance(backend, VLLMClassifierBackend)
    assert backend.timeout_seconds > 0.0


# ── env-driven entry point ────────────────────────────────────────────────


def test_build_from_env_defaults_to_transformers(monkeypatch) -> None:
    monkeypatch.delenv("RUHU_CLASSIFIER_BACKEND", raising=False)
    backend = build_classifier_from_env(
        transformers_model=object(),
        transformers_processor=object(),
    )
    assert isinstance(backend, TransformersClassifierBackend)


def test_build_from_env_picks_vllm_with_required_vars(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CLASSIFIER_BACKEND", "vllm")
    monkeypatch.setenv("RUHU_CLASSIFIER_BASE_URL", "http://vllm:8000")
    monkeypatch.setenv("RUHU_CLASSIFIER_MODEL", "Qwen/Qwen3-8B")
    monkeypatch.setenv("RUHU_CLASSIFIER_TIMEOUT_MS", "750")

    backend = build_classifier_from_env()
    assert isinstance(backend, VLLMClassifierBackend)
    assert backend.base_url == "http://vllm:8000"
    assert backend.base_model == "Qwen/Qwen3-8B"
    assert backend.timeout_seconds == pytest.approx(0.75)


def test_build_from_env_invalid_backend_kind_raises(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CLASSIFIER_BACKEND", "fictional")
    with pytest.raises(ValueError, match="vllm/transformers/vertex_gemini"):
        build_classifier_from_env()


def test_build_from_env_invalid_timeout_raises(monkeypatch) -> None:
    """Invalid timeout fails fast — RuntimeSettings is the single parse path."""
    monkeypatch.setenv("RUHU_CLASSIFIER_BACKEND", "vllm")
    monkeypatch.setenv("RUHU_CLASSIFIER_BASE_URL", "http://vllm:8000")
    monkeypatch.setenv("RUHU_CLASSIFIER_MODEL", "Qwen/Qwen3-8B")
    monkeypatch.setenv("RUHU_CLASSIFIER_TIMEOUT_MS", "not-an-int")
    with pytest.raises(ValueError):
        build_classifier_from_env()


def test_build_from_env_vllm_without_base_url_raises_actionable_error(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CLASSIFIER_BACKEND", "vllm")
    monkeypatch.delenv("RUHU_CLASSIFIER_BASE_URL", raising=False)
    monkeypatch.setenv("RUHU_CLASSIFIER_MODEL", "m")
    with pytest.raises(ValueError, match="RUHU_CLASSIFIER_BASE_URL"):
        build_classifier_from_env()


# ── build_classifier_from_settings (consolidation: single env-parse path) ──


def _vllm_settings(**overrides) -> RuntimeSettings:
    base = dict(
        classifier_backend="vllm",
        classifier_base_url="http://vllm:8000",
        classifier_model="Qwen/Qwen3-8B",
        classifier_timeout_ms=300,
    )
    base.update(overrides)
    return RuntimeSettings(**base)  # type: ignore[arg-type]


def test_build_from_settings_returns_vllm_backend_when_kind_is_vllm() -> None:
    backend = build_classifier_from_settings(_vllm_settings())
    assert isinstance(backend, VLLMClassifierBackend)
    assert backend.base_url == "http://vllm:8000"
    assert backend.base_model == "Qwen/Qwen3-8B"
    assert backend.timeout_seconds == pytest.approx(0.3)


def test_build_from_settings_returns_transformers_backend_when_kind_is_transformers() -> None:
    fake_model = object()
    fake_processor = object()
    settings = RuntimeSettings(classifier_backend="transformers")
    backend = build_classifier_from_settings(
        settings,
        transformers_model=fake_model,
        transformers_processor=fake_processor,
    )
    assert isinstance(backend, TransformersClassifierBackend)
    assert backend.model is fake_model


def test_build_from_settings_normalises_uppercase_backend() -> None:
    """Even though RuntimeSettings already lowercases, the factory tolerates raw input."""
    settings = RuntimeSettings(
        classifier_backend="VLLM",  # bypass lowercasing for this direct constructor
        classifier_base_url="http://vllm:8000",
        classifier_model="m",
    )
    backend = build_classifier_from_settings(settings)
    assert isinstance(backend, VLLMClassifierBackend)


def test_build_from_settings_rejects_unknown_backend_kind() -> None:
    settings = RuntimeSettings(classifier_backend="fictional")
    with pytest.raises(ValueError, match="vllm/transformers/vertex_gemini"):
        build_classifier_from_settings(settings)


def test_build_from_settings_passes_vertex_project_through() -> None:
    """vertex_gemini backend takes a GCP project (post-WI-5.3 direct REST shape)."""
    settings = RuntimeSettings(
        classifier_backend="vertex_gemini",
        classifier_model="gemini-2.5-pro",
    )
    from ruhu.classifier.vertex_gemini_backend import VertexGeminiClassifierBackend

    backend = build_classifier_from_settings(
        settings,
        vertex_project="ruhu-prod",
        vertex_location="europe-west2",
        vertex_provider="vertex",
        vertex_fallback_confidence=0.75,
    )
    assert isinstance(backend, VertexGeminiClassifierBackend)
    assert backend.project == "ruhu-prod"
    assert backend.location == "europe-west2"
    assert backend.fallback_confidence == 0.75


def test_build_from_env_now_delegates_to_build_from_settings(monkeypatch) -> None:
    """build_classifier_from_env should be a thin shim — single parse path."""
    monkeypatch.setenv("RUHU_CLASSIFIER_BACKEND", "vllm")
    monkeypatch.setenv("RUHU_CLASSIFIER_BASE_URL", "http://vllm:8000")
    monkeypatch.setenv("RUHU_CLASSIFIER_MODEL", "Qwen/Qwen3-8B")
    monkeypatch.setenv("RUHU_CLASSIFIER_TIMEOUT_MS", "750")
    backend = build_classifier_from_env()
    assert isinstance(backend, VLLMClassifierBackend)
    assert backend.timeout_seconds == pytest.approx(0.75)


def test_build_from_settings_uses_minimum_timeout_for_zero_ms() -> None:
    settings = _vllm_settings(classifier_timeout_ms=0)
    backend = build_classifier_from_settings(settings)
    assert isinstance(backend, VLLMClassifierBackend)
    assert backend.timeout_seconds > 0.0
