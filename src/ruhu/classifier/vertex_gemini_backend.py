"""Vertex Gemini classifier backend.

This is the **production default** classifier for agents that don't yet
have a per-agent LoRA. It's selected when an agent's
``classifier.strategy = "main_llm"`` (the default for new agents) and
maps directly onto the same Vertex Gemini infrastructure the dialogue
generator uses. Operators flip an agent to ``strategy = "prefill"``
once a fine-tuned LoRA has been promoted to production status and
passed eval — until then the main-LLM path is the accuracy floor.

It also remains the **disaster-recovery failback** path per
``docs/pre-fill-intent-classifier-design/04-runtime-spec.md`` for
environments that flip ``RUHU_CLASSIFIER_BACKEND=vertex_gemini``
globally.

The adapter issues the REST call inline using the same pattern that
``classifier.training.teacher_relabel.VertexTeacherBackend`` uses
(httpx + Application Default Credentials). The two paths converge on
the same Vertex endpoint shape; this one targets the classifier use
case (short prompt, single-intent JSON response).

Adapter behavior:

- Vertex Gemini does not return per-token logprobs; we report a
  fixed ``fallback_confidence`` (default 1.0) on accepted intents and
  0.0 on unknown / out-of-catalog. Authors who require strict
  confidence gating should pair this with ``strategy = "prefill"``
  once a LoRA is available.
- ``ClassificationRequest.prefix`` / ``suffix`` are ignored because this
  backend builds its own prompt and prefix-cache discipline does not apply.
- Errors coerce to ``ClassificationResult`` with ``chosen_label=None``
  + an ``error`` tag rather than raising. Callers (e.g. the strategy
  resolver) read ``result.error`` to emit a ``classifier_unavailable``
  semantic event so the kernel can route to the step's deterministic
  fallback rather than silently switching strategy.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from .protocol import ClassificationRequest, ClassificationResult

DEFAULT_PROVIDER = "vertex"
# Flash, not Pro: classification is a small-output task where Pro's price
# (~15-30x Flash) is not justified. Operators who want Pro for accuracy can
# override via constructor or the agent_settings.llm_config.model field.
DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_LOCATION = "europe-west2"
DEFAULT_FALLBACK_CONFIDENCE = 1.0
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_MAX_OUTPUT_TOKENS = 64
UNKNOWN_LABEL = "unknown"


@dataclass(slots=True)
class VertexGeminiClassifierBackend:
    """Direct Vertex Gemini classify call as a ``PrefillClassifier``.

    ``http_post`` and ``access_token_loader`` are injected for test
    isolation — the same pattern used by ``VertexTeacherBackend``.
    Production wiring leaves both ``None`` so the backend reaches for
    httpx and google.auth ADC respectively.
    """

    project: str
    location: str = DEFAULT_LOCATION
    model: str = DEFAULT_MODEL
    provider: str = DEFAULT_PROVIDER
    fallback_confidence: float = DEFAULT_FALLBACK_CONFIDENCE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    http_post: Callable[..., Any] | None = None
    access_token_loader: Callable[[], str] | None = None

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        if not request.user_text or not request.candidate_labels:
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend="vertex_gemini",
                lora_name=request.lora_name,
                error="empty_request",
            )

        prompt = _build_classify_prompt(request)
        payload = _build_payload(prompt, max_output_tokens=self.max_output_tokens)
        url = (
            f"https://aiplatform.googleapis.com/v1/projects/{self.project}"
            f"/locations/{self.location}/publishers/google/models/{self.model}:generateContent"
        )

        start = time.perf_counter()
        try:
            response_body = self._post(url=url, json=payload)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend="vertex_gemini",
                lora_name=request.lora_name,
                elapsed_ms=elapsed_ms,
                error=_classify_exception(exc),
            )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return _parse_response(
            response_body,
            request=request,
            elapsed_ms=elapsed_ms,
            fallback_confidence=self.fallback_confidence,
        )

    def _post(self, *, url: str, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        if self.http_post is not None:
            return self.http_post(
                url=url,
                json=json,
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        import httpx  # type: ignore[import-not-found]

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, json=json, headers=self._headers())
            response.raise_for_status()
            return response.json()

    def _headers(self) -> dict[str, str]:
        token = self._access_token()
        return {
            "Authorization": f"Bearer {token}" if token else "",
            "Content-Type": "application/json",
        }

    def _access_token(self) -> str:
        if self.access_token_loader is not None:
            value = self.access_token_loader()
            return str(value or "")
        try:
            import google.auth  # type: ignore[import-not-found]
            from google.auth.transport.requests import Request as AuthRequest  # type: ignore[import-not-found]
        except ImportError:
            return ""
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        if not credentials.valid:
            credentials.refresh(AuthRequest())
        return str(credentials.token or "")


# ── helpers ────────────────────────────────────────────────────────────────


def _build_classify_prompt(request: ClassificationRequest) -> str:
    user_text = (request.user_text or "").strip()
    intent_lines = "\n".join(
        f"- {name}: {description}"
        for name, description in sorted(request.candidate_labels.items())
    )
    exact_ids = ", ".join(sorted(request.candidate_labels.keys()))
    return (
        "You classify the user's current turn for a step-native assistant.\n"
        f"Step: {request.step_name}\n"
        f"Step summary: {request.step_summary}\n"
        f"User message: {user_text}\n"
        "Choose the single best intent from the list below.\n"
        f"If none match confidently, respond with {{\"intent\":\"{UNKNOWN_LABEL}\"}}.\n"
        "Return only strict JSON with one field: intent.\n"
        f"The intent value must be one of these exact ids: {exact_ids}.\n"
        f"Valid intents:\n{intent_lines}"
    )


def _build_payload(prompt: str, *, max_output_tokens: int) -> dict[str, Any]:
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
            # Thinking on classify produces thoughtSignature that breaks
            # strict-JSON parsing — disable per response_generation prior art.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }


def _parse_response(
    body: dict[str, Any] | None,
    *,
    request: ClassificationRequest,
    elapsed_ms: int,
    fallback_confidence: float,
) -> ClassificationResult:
    if not body:
        return ClassificationResult(
            chosen_label=None,
            confidence=0.0,
            backend="vertex_gemini",
            lora_name=request.lora_name,
            elapsed_ms=elapsed_ms,
            error="empty_response",
        )
    text = _extract_gemini_text(body)
    if not text:
        return ClassificationResult(
            chosen_label=None,
            confidence=0.0,
            backend="vertex_gemini",
            lora_name=request.lora_name,
            elapsed_ms=elapsed_ms,
            error="no_choices",
        )
    try:
        parsed = json.loads(text)
        chosen = str(parsed.get("intent") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        chosen = text.strip()

    if not chosen or chosen == UNKNOWN_LABEL:
        return ClassificationResult(
            chosen_label=None,
            confidence=0.0,
            backend="vertex_gemini",
            lora_name=request.lora_name,
            elapsed_ms=elapsed_ms,
            error="unknown_label" if not chosen else None,
        )
    if chosen not in request.candidate_labels:
        return ClassificationResult(
            chosen_label=None,
            confidence=0.0,
            backend="vertex_gemini",
            lora_name=request.lora_name,
            elapsed_ms=elapsed_ms,
            error=f"intent_outside_catalog: {chosen}",
        )
    return ClassificationResult(
        chosen_label=chosen,
        confidence=fallback_confidence,
        decode_logprobs={},
        cache_hit=False,
        prefill_tokens=0,
        decode_tokens=0,
        lora_name=request.lora_name,
        backend="vertex_gemini",
        elapsed_ms=elapsed_ms,
        error=None,
    )


def _extract_gemini_text(body: dict[str, Any]) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(str(part.get("text") or "") for part in parts).strip()


def _classify_exception(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc).lower()
    if name in {"TimeoutException", "ReadTimeout", "ConnectTimeout"} or "timeout" in text:
        return "timeout"
    if name in {"ConnectError", "ConnectionError", "RemoteProtocolError"}:
        return "connection_error"
    if "5" in text and "status" in text:
        return "5xx"
    return f"{name.lower()}: {exc}"
