"""WI-3.4 — backend factory for the prefill-first classifier.

Picks one of ``vllm`` / ``transformers`` / ``vertex_gemini`` based on
config. Three entry points for different call sites:

- ``build_classifier(config)`` — explicit ``ClassifierBackendConfig``,
  used when the caller knows exactly what backend it wants.
- ``build_classifier_from_settings(settings, ...)`` — derives the
  config from a ``RuntimeSettings`` instance. Production wiring goes
  here.
- ``build_classifier_from_env(...)`` — thin shim around
  ``RuntimeSettings.from_env()``; for one-line scripts and smoke tests.

All three converge on ``build_classifier`` so backend instantiation
logic lives in exactly one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..runtime_config import RuntimeSettings
from .protocol import ClassificationRequest, ClassificationResult, PrefillClassifier
from .vllm_backend import (
    DEFAULT_GUIDED_BACKEND,
    DEFAULT_TIMEOUT_SECONDS,
    VLLMClassifierBackend,
)

ClassifierBackendKind = Literal["vllm", "transformers", "vertex_gemini"]
_ALLOWED_BACKEND_KINDS: frozenset[str] = frozenset(
    {"vllm", "transformers", "vertex_gemini"}
)


@dataclass(slots=True, frozen=True)
class ClassifierBackendConfig:
    """Plain-data constructor input for the factory.

    Fields not relevant to the chosen ``kind`` are ignored. The factory
    raises a clear error when a required field is missing for the
    selected backend.
    """

    kind: ClassifierBackendKind
    # vLLM-specific
    base_url: str | None = None
    model: str | None = None
    timeout_ms: int = 500
    guided_decoding_backend: str = DEFAULT_GUIDED_BACKEND
    # Transformers-specific (model + processor injected by caller)
    transformers_model: object | None = None
    transformers_processor: object | None = None
    # Vertex-Gemini-specific fallback path
    vertex_project: str | None = None
    vertex_location: str = "europe-west2"
    vertex_provider: str = "vertex"
    vertex_fallback_confidence: float = 1.0


def build_classifier(config: ClassifierBackendConfig) -> PrefillClassifier:
    """Build the configured ``PrefillClassifier`` instance."""
    if config.kind == "vllm":
        return _build_vllm(config)
    if config.kind == "transformers":
        return _build_transformers(config)
    if config.kind == "vertex_gemini":
        return _build_vertex_gemini(config)
    raise ValueError(
        f"unsupported classifier backend kind: {config.kind!r}; "
        f"must be one of vllm/transformers/vertex_gemini"
    )


def build_classifier_from_settings(
    settings: RuntimeSettings,
    *,
    transformers_model: object | None = None,
    transformers_processor: object | None = None,
    vertex_project: str | None = None,
    vertex_location: str = "europe-west2",
    vertex_provider: str = "vertex",
    vertex_fallback_confidence: float = 1.0,
    guided_decoding_backend: str = DEFAULT_GUIDED_BACKEND,
) -> PrefillClassifier:
    """Build a ``PrefillClassifier`` from a ``RuntimeSettings`` instance.

    Reads ``settings.classifier_backend``, ``classifier_base_url``,
    ``classifier_model``, ``classifier_timeout_ms``. Backend-construction
    inputs that aren't env-loadable (HF model + processor for
    ``transformers``; GCP project for ``vertex_gemini``) come in as
    keyword arguments so the runtime can wire them from its own state.

    For ``vertex_gemini``, since WI-5.3 the backend issues direct Vertex
    REST calls (no ResponseGenerator wrap), so ``vertex_project`` is
    required.
    """
    return build_classifier(
        ClassifierBackendConfig(
            kind=_normalise_backend_kind(settings.classifier_backend),
            base_url=settings.classifier_base_url,
            model=settings.classifier_model,
            timeout_ms=settings.classifier_timeout_ms,
            guided_decoding_backend=guided_decoding_backend,
            transformers_model=transformers_model,
            transformers_processor=transformers_processor,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_provider=vertex_provider,
            vertex_fallback_confidence=vertex_fallback_confidence,
        )
    )


def build_classifier_from_env(
    *,
    transformers_model: object | None = None,
    transformers_processor: object | None = None,
    vertex_project: str | None = None,
) -> PrefillClassifier:
    """Convenience shim — reads the spec env vars via ``RuntimeSettings.from_env``.

    For one-line scripts and smoke tests. Production callers should use
    ``build_classifier_from_settings`` directly with the
    ``RuntimeSettings`` they already hold.
    """
    return build_classifier_from_settings(
        RuntimeSettings.from_env(),
        transformers_model=transformers_model,
        transformers_processor=transformers_processor,
        vertex_project=vertex_project,
    )


def _normalise_backend_kind(value: str) -> ClassifierBackendKind:
    normalised = (value or "").strip().lower()
    if normalised not in _ALLOWED_BACKEND_KINDS:
        raise ValueError(
            f"unsupported classifier backend kind: {value!r}; "
            f"must be one of vllm/transformers/vertex_gemini"
        )
    return normalised  # type: ignore[return-value]


def _build_vllm(config: ClassifierBackendConfig) -> PrefillClassifier:
    if not config.base_url:
        raise ValueError("vllm backend requires base_url (RUHU_CLASSIFIER_BASE_URL)")
    if not config.model:
        raise ValueError("vllm backend requires model (RUHU_CLASSIFIER_MODEL)")
    return VLLMClassifierBackend(
        base_url=config.base_url,
        base_model=config.model,
        timeout_seconds=max(config.timeout_ms / 1000.0, 0.001),
        guided_decoding_backend=config.guided_decoding_backend,
    )


def _build_transformers(config: ClassifierBackendConfig) -> PrefillClassifier:
    """Construct ``TransformersClassifierBackend``.

    The HF model + processor are heavyweight and the factory does not
    load them — callers pass them via
    ``ClassifierBackendConfig.transformers_model/processor``. The dev
    server bootstraps them via ``GemmaLocalInterpreter``'s usual path
    (``TransformersGemmaBackend._ensure_loaded``).
    """
    from threading import Lock

    from .transformers_backend import TransformersClassifierBackend

    if config.transformers_model is None or config.transformers_processor is None:
        raise ValueError(
            "transformers backend requires transformers_model and transformers_processor; "
            "load them via TransformersGemmaBackend._ensure_loaded() or equivalent"
        )
    return TransformersClassifierBackend(
        model=config.transformers_model,
        processor=config.transformers_processor,
        _catalog_cache={},
        _catalog_lock=Lock(),
    )


def _build_vertex_gemini(config: ClassifierBackendConfig) -> PrefillClassifier:
    """Build the Vertex Gemini classifier backend.

    Used both as the production default for ``classifier.strategy = "main_llm"``
    (per-agent setting in ``api_models.AgentClassifierConfig``) and as
    the disaster-recovery failback per ``04-runtime-spec.md``.

    The backend issues direct REST calls to Vertex. Authentication uses
    Application Default Credentials.
    """
    from .vertex_gemini_backend import (
        DEFAULT_MODEL as _DEFAULT_VERTEX_MODEL,
        VertexGeminiClassifierBackend,
    )

    if not config.vertex_project:
        raise ValueError(
            "vertex_gemini backend requires vertex_project (the GCP "
            "project hosting the Vertex Gemini deployment)."
        )
    return VertexGeminiClassifierBackend(
        project=config.vertex_project,
        location=config.vertex_location,
        model=config.model or _DEFAULT_VERTEX_MODEL,
        provider=config.vertex_provider,
        fallback_confidence=config.vertex_fallback_confidence,
        timeout_seconds=max(config.timeout_ms / 1000.0, 0.001),
    )


__all__ = [
    "ClassificationRequest",
    "ClassificationResult",
    "ClassifierBackendConfig",
    "ClassifierBackendKind",
    "PrefillClassifier",
    "build_classifier",
    "build_classifier_from_env",
    "build_classifier_from_settings",
]
