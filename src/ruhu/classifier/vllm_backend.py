"""WI-3.2 — vLLM HTTP backend for the prefill-first classifier.

Implements ``PrefillClassifier`` against vLLM's OpenAI-compatible
``/v1/completions`` endpoint with ``guided_choice`` constrained
decoding. Replaces ``TransformersClassifierBackend`` in production
once Stage 3 cluster is stood up — backend selection is config-driven
via ``RUHU_CLASSIFIER_BACKEND=vllm``.

Spec: ``docs/pre-fill-intent-classifier-design/02-architecture-spec.md``
§vLLM HTTP request shape and ``04-runtime-spec.md`` §Disaster recovery.

The exact ``guided_choice`` placement (top-level vs ``extra_body``) and
the endpoint (``/v1/completions`` vs ``/v1/chat/completions``) varies
across vLLM releases. Per WI-4.6 this *must* be probed against the
pinned production cluster before being trusted in production. We send
``guided_choice`` at the top level *and* in ``extra_body`` to maximise
compatibility with the documented vLLM versions; the backend will be
re-pinned when WI-4.6 lands.

Errors are coerced to a ``ClassificationResult`` with ``chosen_label=None``
and ``error=<kind>`` instead of raising. The kernel never sees an
exception from the classifier — it falls back to ``unknown`` and the
caller's failover policy (``RUHU_CLASSIFIER_FAILOVER_TO_MAIN_LLM``)
takes over from there.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .constrained import UNKNOWN_LABEL, confidence_from_token_logprobs
from .protocol import ClassificationRequest, ClassificationResult

DEFAULT_TIMEOUT_SECONDS = 0.5  # spec: RUHU_CLASSIFIER_TIMEOUT_MS=500
DEFAULT_COMPLETIONS_PATH = "/v1/completions"
DEFAULT_GUIDED_BACKEND = "outlines"
DEFAULT_MAX_TOKENS = 8


@dataclass(slots=True)
class VLLMClassifierBackend:
    """vLLM HTTP backend.

    Constructor knobs are roughly the spec's ``RUHU_CLASSIFIER_*`` env
    vars; the factory (WI-3.4) wires them from ``runtime_config``. The
    ``http_post`` and ``access_token_loader`` callables exist so tests
    exercise the full code path without network or auth.
    """

    base_url: str
    base_model: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    completions_path: str = DEFAULT_COMPLETIONS_PATH
    guided_decoding_backend: str = DEFAULT_GUIDED_BACKEND
    max_tokens: int = DEFAULT_MAX_TOKENS
    http_post: Callable[..., Any] | None = None
    access_token_loader: Callable[[], str | None] | None = None
    _backend_name: str = "vllm"

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        if not request.user_text or not request.candidate_labels:
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend=self._backend_name,
                lora_name=request.lora_name,
                error="empty_request",
            )
        if request.prefix is None or request.suffix is None:
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend=self._backend_name,
                lora_name=request.lora_name,
                error="missing_prefix_suffix",
            )

        guided_choices = sorted(list(request.candidate_labels.keys()) + [UNKNOWN_LABEL])
        model_name = request.lora_name or self.base_model
        prompt = request.prefix + request.suffix
        payload: dict[str, Any] = {
            "model": model_name,
            "prompt": prompt,
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
            "logprobs": 1,
            "guided_choice": guided_choices,
            "extra_body": {
                "guided_choice": guided_choices,
                "guided_decoding_backend": self.guided_decoding_backend,
            },
        }
        url = self.base_url.rstrip("/") + self.completions_path
        headers = self._build_headers()

        start = time.perf_counter()
        try:
            response_body = self._post(url=url, json=payload, headers=headers)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend=self._backend_name,
                lora_name=request.lora_name,
                elapsed_ms=elapsed_ms,
                error=_classify_exception(exc),
            )
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        return _parse_vllm_response(
            response_body,
            request=request,
            backend_name=self._backend_name,
            elapsed_ms=elapsed_ms,
        )

    def _post(self, *, url: str, json: dict, headers: dict) -> dict:  # noqa: A002
        if self.http_post is not None:
            return self.http_post(
                url=url, json=json, headers=headers, timeout=self.timeout_seconds
            )
        import httpx  # type: ignore[import-not-found]

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, json=json, headers=headers)
            response.raise_for_status()
            return response.json()

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token_loader is not None:
            token = self.access_token_loader()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers


# ── response parsing ────────────────────────────────────────────────────────


def _parse_vllm_response(
    body: dict | None,
    *,
    request: ClassificationRequest,
    backend_name: str,
    elapsed_ms: int,
) -> ClassificationResult:
    if not body:
        return ClassificationResult(
            chosen_label=None,
            confidence=0.0,
            backend=backend_name,
            lora_name=request.lora_name,
            elapsed_ms=elapsed_ms,
            error="empty_response",
        )
    choices = body.get("choices") or []
    if not choices:
        return ClassificationResult(
            chosen_label=None,
            confidence=0.0,
            backend=backend_name,
            lora_name=request.lora_name,
            elapsed_ms=elapsed_ms,
            error="no_choices",
        )

    first = choices[0] or {}
    chosen_text = str(first.get("text") or "").strip()

    if chosen_text == UNKNOWN_LABEL or chosen_text == "":
        chosen_intent: str | None = None
        error: str | None = "unknown_label" if chosen_text == "" else None
    elif chosen_text in request.candidate_labels:
        chosen_intent = chosen_text
        error = None
    else:
        chosen_intent = None
        error = f"intent_outside_catalog: {chosen_text}"

    token_logprobs = _extract_token_logprobs(first)
    confidence = (
        confidence_from_token_logprobs(token_logprobs) if token_logprobs else 0.0
    )

    decode_logprobs: dict[str, float] = {}
    if chosen_intent and token_logprobs:
        decode_logprobs[chosen_intent] = sum(token_logprobs)

    usage = body.get("usage") or {}
    prefill_tokens = int(usage.get("prompt_tokens") or 0)
    decode_tokens = int(
        usage.get("completion_tokens") or len(token_logprobs) or 0
    )

    cache_hit = bool(_extract_cache_hit_signal(body))

    return ClassificationResult(
        chosen_label=chosen_intent,
        confidence=confidence,
        decode_logprobs=decode_logprobs,
        cache_hit=cache_hit,
        prefill_tokens=prefill_tokens,
        decode_tokens=decode_tokens,
        lora_name=request.lora_name,
        backend=backend_name,
        elapsed_ms=elapsed_ms,
        error=error,
    )


def _extract_token_logprobs(choice: dict) -> list[float]:
    logprobs_block = choice.get("logprobs") or {}
    raw = logprobs_block.get("token_logprobs")
    if not raw:
        return []
    out: list[float] = []
    for item in raw:
        if item is None:
            continue
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _extract_cache_hit_signal(body: dict) -> bool:
    """Best-effort cache-hit detection.

    vLLM does not include a per-call cache-hit field in the standard
    OpenAI-compatible response. Some forks surface ``prefix_cache_hit`` in
    ``extra_body`` or alongside ``usage``. We honour both shapes and fall
    back to ``False`` when neither is present. WI-4.6 will pin the
    response shape and either confirm this signal or remove it.
    """
    extra = body.get("extra_body") or {}
    if "prefix_cache_hit" in extra:
        return bool(extra["prefix_cache_hit"])
    usage = body.get("usage") or {}
    if "prefix_cache_hit" in usage:
        return bool(usage["prefix_cache_hit"])
    return False


def _classify_exception(exc: Exception) -> str:
    """Coarse error_kind tag matching the Prometheus label conventions."""
    name = type(exc).__name__
    text = str(exc).lower()
    if name in {"TimeoutException", "ReadTimeout", "ConnectTimeout"} or "timeout" in text:
        return "timeout"
    if name in {"ConnectError", "ConnectionError", "RemoteProtocolError"}:
        return "connection_error"
    if "5" in text and "status" in text:
        return "5xx"
    return f"{name.lower()}: {exc}"
