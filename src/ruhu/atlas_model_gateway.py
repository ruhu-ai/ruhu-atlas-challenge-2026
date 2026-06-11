from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import re
import time
from time import perf_counter
from typing import Any, Callable, Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from .atlas_readiness_models import (
    AtlasCancellationToken,
    AtlasProviderInvocationMetadata,
    AtlasReadinessEvent,
    AtlasReadinessProviderPolicy,
    new_atlas_readiness_event_id,
)

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_DEFAULT_MAX_RETRIES = 2
_RETRY_BACKOFF_BASE_SECONDS = 0.2
_RETRY_BACKOFF_CAP_SECONDS = 2.0


def _is_transient_exception(exc: Exception) -> bool:
    """A provider failure worth retrying: timeout, transport/network error, or
    an HTTP status in the transient set (429/5xx/408/409/425)."""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code in _TRANSIENT_STATUS_CODES
    return False


def _execute_with_retry(
    call: Callable[[], dict[str, Any]],
    *,
    max_retries: int,
    sleep: Callable[[float], None],
) -> tuple[dict[str, Any] | None, int, Exception | None]:
    """Run ``call`` with bounded exponential backoff on transient failures.

    Returns ``(body, retry_count, error)``: on success ``error`` is None; on a
    non-transient failure or once retries are exhausted, ``body`` is None and
    ``error`` holds the last exception. ``retry_count`` is the number of
    retries performed (0 on first-try success).
    """
    attempt = 0
    while True:
        try:
            return call(), attempt, None
        except Exception as exc:  # noqa: BLE001 - re-raised via the error channel
            if attempt >= max_retries or not _is_transient_exception(exc):
                return None, attempt, exc
            sleep(min(_RETRY_BACKOFF_CAP_SECONDS, _RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)))
            attempt += 1


AtlasProviderRole = Literal[
    "orchestrator",
    "workflow_understanding",
    "draft_generator",
    "case_generator",
    "trace_repair_planner",
    "patch_rationale",
    "report_writer",
    "fallback_planner",
]
AtlasTemperaturePolicy = Literal["deterministic", "diverse"]


class AtlasModelAdapter(Protocol):
    provider: str

    def generate_structured(
        self,
        *,
        role: AtlasProviderRole,
        schema_name: str,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: dict[str, object],
        timeout_seconds: float,
        temperature_policy: AtlasTemperaturePolicy,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> tuple[BaseModel, AtlasProviderInvocationMetadata]: ...

    def generate_structured_stream(
        self,
        *,
        run_id: str,
        role: AtlasProviderRole,
        schema_name: str,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: dict[str, object],
        timeout_seconds: float,
        temperature_policy: AtlasTemperaturePolicy,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> Iterator[AtlasReadinessEvent]: ...


class DeterministicAtlasModelAdapter:
    provider = "deterministic"

    def generate_structured(
        self,
        *,
        role: AtlasProviderRole,
        schema_name: str,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: dict[str, object],
        timeout_seconds: float,
        temperature_policy: AtlasTemperaturePolicy,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> tuple[BaseModel, AtlasProviderInvocationMetadata]:
        start = perf_counter()
        cancellation_token.throw_if_cancelled() if cancellation_token is not None else None
        payload = trace_context.get("deterministic_response")
        if isinstance(payload, response_model):
            result = payload
        elif isinstance(payload, dict):
            result = response_model.model_validate(payload)
        else:
            result = _empty_model(response_model)
        cancellation_token.throw_if_cancelled() if cancellation_token is not None else None
        metadata = AtlasProviderInvocationMetadata(
            provider=self.provider,
            model="deterministic",
            role=role,
            latency_ms=max(0, int((perf_counter() - start) * 1000)),
            prompt_tokens=len(prompt.split()) if prompt else 0,
            completion_tokens=0,
            estimated_cost_usd=Decimal("0"),
            validation_outcome="valid",
            timeout_seconds=timeout_seconds,
            cancelled=False,
        )
        return result, metadata

    def generate_structured_stream(
        self,
        *,
        run_id: str,
        role: AtlasProviderRole,
        schema_name: str,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: dict[str, object],
        timeout_seconds: float,
        temperature_policy: AtlasTemperaturePolicy,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> Iterator[AtlasReadinessEvent]:
        now = datetime.now(timezone.utc)
        yield AtlasReadinessEvent(
            event_id=new_atlas_readiness_event_id(),
            run_id=run_id,
            sequence_number=0,
            type="provider_call_started",
            payload={"provider": self.provider, "role": role, "schema_name": schema_name},
            created_at=now,
        )
        _result, metadata = self.generate_structured(
            role=role,
            schema_name=schema_name,
            prompt=prompt,
            response_model=response_model,
            trace_context=trace_context,
            timeout_seconds=timeout_seconds,
            temperature_policy=temperature_policy,
            cancellation_token=cancellation_token,
        )
        yield AtlasReadinessEvent(
            event_id=new_atlas_readiness_event_id(),
            run_id=run_id,
            sequence_number=0,
            type="provider_call_finished",
            payload=metadata.model_dump(mode="json"),
            created_at=datetime.now(timezone.utc),
        )


class ConfiguredFallbackAtlasModelAdapter(DeterministicAtlasModelAdapter):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        configured: bool,
        fallback_reason: str,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self.provider = provider
        self.model = model
        self.configured = configured
        self.fallback_reason = fallback_reason
        self.max_retries = max(0, int(max_retries))
        # Overridable in tests so retry backoff doesn't actually sleep.
        self._sleep: Callable[[float], None] = time.sleep

    def generate_structured(
        self,
        *,
        role: AtlasProviderRole,
        schema_name: str,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: dict[str, object],
        timeout_seconds: float,
        temperature_policy: AtlasTemperaturePolicy,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> tuple[BaseModel, AtlasProviderInvocationMetadata]:
        result, metadata = super().generate_structured(
            role=role,
            schema_name=schema_name,
            prompt=prompt,
            response_model=response_model,
            trace_context=trace_context,
            timeout_seconds=timeout_seconds,
            temperature_policy=temperature_policy,
            cancellation_token=cancellation_token,
        )
        return (
            result,
            metadata.model_copy(
                update={
                    "provider": self.provider,
                    "model": self.model,
                    "fallback_reason": None if self.configured else self.fallback_reason,
                }
            ),
        )


class GeminiAtlasModelAdapter(ConfiguredFallbackAtlasModelAdapter):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        configured: bool,
        fallback_reason: str,
        api_key: str | None = None,
        vertex_project: str | None = None,
        vertex_location: str = "us-central1",
        endpoint_base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        http_post: Callable[..., dict[str, Any]] | None = None,
        access_token_loader: Callable[[], str] | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        super().__init__(
            provider=provider,
            model=model,
            configured=configured,
            fallback_reason=fallback_reason,
            max_retries=max_retries,
        )
        self.api_key = api_key
        self.vertex_project = vertex_project
        self.vertex_location = vertex_location
        self.endpoint_base_url = endpoint_base_url.rstrip("/")
        self.http_post = http_post
        self.access_token_loader = access_token_loader

    @classmethod
    def from_env(cls) -> "GeminiAtlasModelAdapter":
        model = (
            os.getenv("RUHU_ATLAS_GEMINI_ORCHESTRATOR_MODEL")
            or os.getenv("RUHU_ATLAS_GEMINI_FAST_MODEL")
            or "gemini-3.1-pro-preview"
        ).strip()
        api_key = (os.getenv("RUHU_GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip() or None
        vertex_project = (
            os.getenv("RUHU_ATLAS_GOOGLE_VERTEX_PROJECT")
            or os.getenv("RUHU_VERTEX_AI_PROJECT")
            or os.getenv("VERTEX_AI_PROJECT")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or ""
        ).strip() or None
        vertex_location = (
            os.getenv("RUHU_ATLAS_GOOGLE_VERTEX_LOCATION")
            or os.getenv("RUHU_VERTEX_AI_LOCATION")
            or os.getenv("VERTEX_AI_LOCATION")
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or "europe-west2"
        ).strip()
        return cls(
            provider="gemini",
            model=model,
            configured=bool(api_key or vertex_project),
            fallback_reason="google_provider_not_configured",
            api_key=api_key,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
        )

    def generate_structured(
        self,
        *,
        role: AtlasProviderRole,
        schema_name: str,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: dict[str, object],
        timeout_seconds: float,
        temperature_policy: AtlasTemperaturePolicy,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> tuple[BaseModel, AtlasProviderInvocationMetadata]:
        if not self.configured:
            return super().generate_structured(
                role=role,
                schema_name=schema_name,
                prompt=prompt,
                response_model=response_model,
                trace_context=trace_context,
                timeout_seconds=timeout_seconds,
                temperature_policy=temperature_policy,
                cancellation_token=cancellation_token,
            )
        start = perf_counter()
        cancellation_token.throw_if_cancelled() if cancellation_token is not None else None
        payload = _build_gemini_payload(
            prompt=prompt,
            schema_name=schema_name,
            response_model=response_model,
            temperature_policy=temperature_policy,
        )
        body, retry_count, post_error = _execute_with_retry(
            lambda: self._post_json(payload=payload, timeout_seconds=timeout_seconds),
            max_retries=self.max_retries,
            sleep=self._sleep,
        )
        try:
            if post_error is not None:
                raise post_error
            text = _extract_gemini_text(body)
            parsed = _parse_structured_text(text, response_model=response_model)
            validation_outcome: Literal["valid", "invalid", "repaired", "blocked"] = "valid"
            fallback_reason = None
        except ValidationError as exc:
            logger.warning("atlas gemini structured response failed validation", extra={"role": role, "model": self.model})
            parsed = _fallback_model(response_model, trace_context)
            validation_outcome = "invalid"
            fallback_reason = f"validation_error:{exc.errors()[0].get('type', 'unknown') if exc.errors() else 'unknown'}"
        except Exception as exc:
            # Log the classified reason, never raw str(exc): an httpx error
            # message contains the request URL, which must not reach logs.
            # Distinguish a provider outage (retries exhausted) from bad model
            # output so downstream consumers don't treat an outage as a real
            # 'blocked' result.
            classified = _classify_provider_exception(exc)
            # 'provider_unavailable' only for a genuine outage: a transient
            # error that survived all retries. Non-transient failures (403/401)
            # keep their classified reason.
            fallback_reason = (
                f"provider_unavailable:{classified}"
                if post_error is not None and _is_transient_exception(post_error)
                else classified
            )
            logger.warning(
                "atlas gemini structured generation failed",
                extra={"role": role, "model": self.model, "error": fallback_reason, "retry_count": retry_count},
            )
            parsed = _fallback_model(response_model, trace_context)
            validation_outcome = "blocked"
        cancelled = cancellation_token.is_cancelled() if cancellation_token is not None else False
        metadata = AtlasProviderInvocationMetadata(
            provider=self.provider,
            model=self.model,
            role=role,
            latency_ms=max(0, int((perf_counter() - start) * 1000)),
            prompt_tokens=_gemini_prompt_tokens(body),
            completion_tokens=_gemini_completion_tokens(body),
            estimated_cost_usd=None,
            validation_outcome=validation_outcome,
            fallback_reason=fallback_reason,
            retry_count=retry_count,
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
        )
        return parsed, metadata

    def _post_json(self, *, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        url, kwargs = self._request_target()
        if self.http_post is not None:
            return self.http_post(url=url, json=payload, timeout=timeout_seconds, **kwargs)
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = client.post(url, json=payload, **kwargs)
        if response.status_code in _TRANSIENT_STATUS_CODES:
            raise httpx.HTTPStatusError(
                f"transient atlas gemini status {response.status_code}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("gemini returned non-object response")
        return body

    def _request_target(self) -> tuple[str, dict[str, Any]]:
        if self.api_key:
            # Pass the key as a header, never a query param: an httpx error
            # (raise_for_status) embeds the full request URL in its message,
            # and that message is logged — a ?key= query string would leak
            # the secret into application logs.
            return (
                f"{self.endpoint_base_url}/models/{self.model}:generateContent",
                {"headers": {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}},
            )
        if self.vertex_project:
            url = (
                f"https://aiplatform.googleapis.com/v1/projects/{self.vertex_project}/locations/{self.vertex_location}"
                f"/publishers/google/models/{self.model}:generateContent"
            )
            return url, {"headers": {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"}}
        raise RuntimeError("google_provider_not_configured")

    def _access_token(self) -> str:
        if self.access_token_loader is not None:
            return str(self.access_token_loader() or "")
        try:
            import google.auth  # type: ignore[import-not-found]
            from google.auth.transport.requests import Request as AuthRequest  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("google_auth_not_installed") from exc
        credentials, _ = google.auth.default(scopes=[_VERTEX_SCOPE])
        if not credentials.valid:
            credentials.refresh(AuthRequest())
        token = str(credentials.token or "")
        if not token:
            raise RuntimeError("google_auth_token_unavailable")
        return token


class ClaudeAtlasModelAdapter(ConfiguredFallbackAtlasModelAdapter):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        configured: bool,
        fallback_reason: str,
        api_key: str | None = None,
        endpoint_base_url: str = "https://api.anthropic.com/v1/messages",
        anthropic_version: str = "2023-06-01",
        max_output_tokens: int = 2048,
        http_post: Callable[..., dict[str, Any]] | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        super().__init__(
            provider=provider,
            model=model,
            configured=configured,
            fallback_reason=fallback_reason,
            max_retries=max_retries,
        )
        self.api_key = api_key
        self.endpoint_base_url = endpoint_base_url
        self.anthropic_version = anthropic_version
        self.max_output_tokens = max_output_tokens
        self.http_post = http_post

    @classmethod
    def from_env(cls) -> "ClaudeAtlasModelAdapter":
        model = (
            os.getenv("RUHU_ATLAS_ANTHROPIC_MODEL")
            or os.getenv("RUHU_ATLAS_CLAUDE_VERTEX_MODEL")
            or os.getenv("RUHU_ATLAS_GENERATOR_MODEL")
            or "claude-sonnet"
        ).strip()
        api_key = (os.getenv("RUHU_ATLAS_GENERATOR_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or "").strip() or None
        return cls(
            provider="anthropic",
            model=model,
            configured=bool(api_key),
            fallback_reason="anthropic_provider_not_configured",
            api_key=api_key,
            endpoint_base_url=(os.getenv("RUHU_ATLAS_ANTHROPIC_ENDPOINT") or "https://api.anthropic.com/v1/messages").strip(),
            anthropic_version=(os.getenv("RUHU_ATLAS_ANTHROPIC_VERSION") or "2023-06-01").strip(),
        )

    def generate_structured(
        self,
        *,
        role: AtlasProviderRole,
        schema_name: str,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: dict[str, object],
        timeout_seconds: float,
        temperature_policy: AtlasTemperaturePolicy,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> tuple[BaseModel, AtlasProviderInvocationMetadata]:
        if not self.configured:
            return super().generate_structured(
                role=role,
                schema_name=schema_name,
                prompt=prompt,
                response_model=response_model,
                trace_context=trace_context,
                timeout_seconds=timeout_seconds,
                temperature_policy=temperature_policy,
                cancellation_token=cancellation_token,
            )
        start = perf_counter()
        cancellation_token.throw_if_cancelled() if cancellation_token is not None else None
        body: dict[str, Any] | None = None
        body, retry_count, post_error = _execute_with_retry(
            lambda: self._post_json(
                payload=_build_anthropic_payload(
                    model=self.model,
                    prompt=prompt,
                    schema_name=schema_name,
                    response_model=response_model,
                    temperature_policy=temperature_policy,
                    max_output_tokens=self.max_output_tokens,
                ),
                timeout_seconds=timeout_seconds,
            ),
            max_retries=self.max_retries,
            sleep=self._sleep,
        )
        try:
            if post_error is not None:
                raise post_error
            text = _extract_anthropic_text(body)
            parsed = _parse_structured_text(text, response_model=response_model)
            validation_outcome: Literal["valid", "invalid", "repaired", "blocked"] = "valid"
            fallback_reason = None
        except ValidationError as exc:
            logger.warning("atlas anthropic structured response failed validation", extra={"role": role, "model": self.model})
            parsed = _fallback_model(response_model, trace_context)
            validation_outcome = "invalid"
            fallback_reason = f"validation_error:{exc.errors()[0].get('type', 'unknown') if exc.errors() else 'unknown'}"
        except Exception as exc:
            # Classified reason only — never raw str(exc), which embeds the
            # request URL. Distinguish a provider outage (retries exhausted)
            # from bad model output.
            classified = _classify_provider_exception(exc)
            # 'provider_unavailable' only for a genuine outage: a transient
            # error that survived all retries. Non-transient failures (403/401)
            # keep their classified reason.
            fallback_reason = (
                f"provider_unavailable:{classified}"
                if post_error is not None and _is_transient_exception(post_error)
                else classified
            )
            logger.warning(
                "atlas anthropic structured generation failed",
                extra={"role": role, "model": self.model, "error": fallback_reason, "retry_count": retry_count},
            )
            parsed = _fallback_model(response_model, trace_context)
            validation_outcome = "blocked"
        metadata = AtlasProviderInvocationMetadata(
            provider=self.provider,
            model=self.model,
            role=role,
            latency_ms=max(0, int((perf_counter() - start) * 1000)),
            prompt_tokens=_anthropic_prompt_tokens(body),
            completion_tokens=_anthropic_completion_tokens(body),
            estimated_cost_usd=None,
            validation_outcome=validation_outcome,
            fallback_reason=fallback_reason,
            retry_count=retry_count,
            timeout_seconds=timeout_seconds,
            cancelled=cancellation_token.is_cancelled() if cancellation_token is not None else False,
        )
        return parsed, metadata

    def _post_json(self, *, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        headers = {
            "x-api-key": str(self.api_key or ""),
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }
        if self.http_post is not None:
            return self.http_post(url=self.endpoint_base_url, json=payload, headers=headers, timeout=timeout_seconds)
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = client.post(self.endpoint_base_url, json=payload, headers=headers)
        if response.status_code in _TRANSIENT_STATUS_CODES:
            raise httpx.HTTPStatusError(
                f"transient atlas anthropic status {response.status_code}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("anthropic returned non-object response")
        return body


def _fallback_model(response_model: type[BaseModel], trace_context: dict[str, object]) -> BaseModel:
    payload = trace_context.get("deterministic_response")
    if isinstance(payload, response_model):
        return payload
    if isinstance(payload, dict):
        try:
            return response_model.model_validate(payload)
        except ValidationError:
            pass
    return _empty_model(response_model)


def _empty_model(response_model: type[BaseModel]) -> BaseModel:
    try:
        return response_model()
    except ValidationError:
        return response_model.model_construct()


def _schema_instruction(*, schema_name: str, response_model: type[BaseModel]) -> str:
    try:
        schema = json.dumps(response_model.model_json_schema(), sort_keys=True)
    except Exception:
        schema = "{}"
    return (
        f"Return only a strict JSON object named {schema_name}. "
        "Do not include markdown, prose, or code fences. "
        f"The JSON must satisfy this schema: {schema}"
    )


def _build_gemini_payload(
    *,
    prompt: str,
    schema_name: str,
    response_model: type[BaseModel],
    temperature_policy: AtlasTemperaturePolicy,
) -> dict[str, Any]:
    temperature = 0.7 if temperature_policy == "diverse" else 0.0
    full_prompt = f"{_schema_instruction(schema_name=schema_name, response_model=response_model)}\n\n{prompt}"
    return {
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }


def _build_anthropic_payload(
    *,
    model: str,
    prompt: str,
    schema_name: str,
    response_model: type[BaseModel],
    temperature_policy: AtlasTemperaturePolicy,
    max_output_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": max_output_tokens,
        "temperature": 0.7 if temperature_policy == "diverse" else 0.0,
        "system": _schema_instruction(schema_name=schema_name, response_model=response_model),
        "messages": [{"role": "user", "content": prompt}],
    }


def _parse_structured_text(text: str | None, *, response_model: type[BaseModel]) -> BaseModel:
    if not text or not text.strip():
        raise ValueError("empty provider response")
    cleaned = _strip_json_fence(text.strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match is None:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("provider response JSON was not an object")
    return response_model.model_validate(payload)


def _strip_json_fence(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _extract_gemini_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return None
    parts: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        raw_parts = content.get("parts")
        if not isinstance(raw_parts, list):
            continue
        for part in raw_parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "".join(parts).strip() or None


def _extract_anthropic_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts).strip() or None


def _gemini_prompt_tokens(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        return None
    value = usage.get("promptTokenCount")
    return int(value) if isinstance(value, int) else None


def _gemini_completion_tokens(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        return None
    value = usage.get("candidatesTokenCount")
    return int(value) if isinstance(value, int) else None


def _anthropic_prompt_tokens(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("input_tokens")
    return int(value) if isinstance(value, int) else None


def _anthropic_completion_tokens(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("output_tokens")
    return int(value) if isinstance(value, int) else None


def _classify_provider_exception(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "provider_timeout"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return f"provider_http_{exc.response.status_code}"
    if isinstance(exc, (httpx.NetworkError, httpx.TransportError)):
        return "provider_network_error"
    return str(exc) or exc.__class__.__name__


class AtlasModelGateway:
    def __init__(
        self,
        *,
        provider_policy: AtlasReadinessProviderPolicy = "deterministic",
        deterministic_adapter: AtlasModelAdapter | None = None,
        gemini_adapter: AtlasModelAdapter | None = None,
        claude_adapter: AtlasModelAdapter | None = None,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.provider_policy = provider_policy
        self.timeout_seconds = timeout_seconds
        self._deterministic = deterministic_adapter or DeterministicAtlasModelAdapter()
        self._gemini = gemini_adapter or GeminiAtlasModelAdapter.from_env()
        self._claude = claude_adapter or ClaudeAtlasModelAdapter.from_env()

    def adapter_for_role(self, role: AtlasProviderRole) -> AtlasModelAdapter:
        if self.provider_policy == "deterministic":
            return self._deterministic
        if self.provider_policy == "google_only":
            if self._gemini is None:
                return self._deterministic
            return self._gemini
        if self.provider_policy == "anthropic_only":
            if self._claude is None:
                return self._deterministic
            return self._claude
        if self.provider_policy == "hybrid":
            if role in {"trace_repair_planner", "fallback_planner"} and self._claude is not None:
                return self._claude
            return self._gemini or self._deterministic
        return self._deterministic

    def health(self) -> dict[str, object]:
        gemini_configured = bool(getattr(self._gemini, "configured", False))
        claude_configured = bool(getattr(self._claude, "configured", False))
        warnings: list[str] = []
        if self.provider_policy in {"google_only", "hybrid"} and not gemini_configured:
            warnings.append("gemini_provider_not_configured")
        if self.provider_policy in {"anthropic_only", "hybrid"} and not claude_configured:
            warnings.append("anthropic_provider_not_configured")
        return {
            "provider_policy": self.provider_policy,
            "gemini_configured": gemini_configured,
            "anthropic_configured": claude_configured,
            "warnings": warnings,
        }

    def generate_structured(
        self,
        *,
        role: AtlasProviderRole,
        schema_name: str,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: dict[str, object],
        temperature_policy: AtlasTemperaturePolicy,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> tuple[BaseModel, AtlasProviderInvocationMetadata]:
        adapter = self.adapter_for_role(role)
        return adapter.generate_structured(
            role=role,
            schema_name=schema_name,
            prompt=prompt,
            response_model=response_model,
            trace_context=trace_context,
            timeout_seconds=self.timeout_seconds,
            temperature_policy=temperature_policy,
            cancellation_token=cancellation_token,
        )
