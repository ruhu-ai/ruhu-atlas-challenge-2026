from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

from ruhu.agent_document import AgentDocument, Step, step_capability_flags
from ruhu.interpreter import SemanticInterpreter
from ruhu.interpreters import build_named_interpreter
from ruhu.schemas import ConversationState, RuntimeTurn, RuntimeTurnResult, SemanticEventRecord

from ..secret_sources import load_text_secret, normalize_gcp_secret_version
from .models import ResolvedClassifierProfile


@dataclass(slots=True)
class IntentTagsClassificationRequest:
    agent_id: str
    agent_name: str
    schema_version: str
    agent_document: AgentDocument
    step: Step
    conversation: ConversationState
    turn: RuntimeTurn
    result: RuntimeTurnResult
    resolved_profile: ResolvedClassifierProfile


@dataclass(slots=True)
class IntentTagsClassificationResult:
    semantic_events: list[SemanticEventRecord]
    adapter_name: str
    model_version: str
    metadata: dict[str, Any] = field(default_factory=dict)
    language: str | None = None
    response_language: str | None = None
    language_confidence: float | None = None
    tool_route: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    provider_cost_payload: dict[str, Any] | None = None


class IntentTagsClassifierAdapter(Protocol):
    adapter_name: str
    model_version: str

    def classify(self, request: IntentTagsClassificationRequest) -> IntentTagsClassificationResult: ...


@dataclass(frozen=True, slots=True)
class HostedHTTPClassifierConfig:
    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 5.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.25


class HostedHTTPClassifierError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        category: str,
        status_code: int | None = None,
        request_id: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.category = category
        self.status_code = status_code
        self.request_id = request_id
        self.latency_ms = latency_ms

    def as_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": self.message,
            "retryable": self.retryable,
            "category": self.category,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        if self.latency_ms is not None:
            payload["latency_ms"] = self.latency_ms
        return payload


@dataclass(frozen=True, slots=True)
class HostedHTTPClassifierResponse:
    payload: dict[str, Any]
    status_code: int
    latency_ms: int
    request_id: str | None = None


def _model_version_for_interpreter(name: str, *, model_path: str | Path) -> str:
    if name == "gemma_local":
        return f"gemma_local:{Path(model_path).name}"
    return f"{name}-v1"


def _string_value(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _coerce_probability(value: object | None, *, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, numeric))


def _normalize_machine_name(value: object | None, *, default: str = "unknown") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    normalized: list[str] = []
    previous_underscore = False
    for char in raw:
        if char.isalnum():
            normalized.append(char)
            previous_underscore = False
            continue
        if previous_underscore:
            continue
        normalized.append("_")
        previous_underscore = True
    result = "".join(normalized).strip("_")
    if not result:
        return default
    if result[0].isdigit():
        result = f"x_{result}"
    return result[:100]


def _bounded_int(value: object | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, resolved))


def _transcript_window_limit(profile: ResolvedClassifierProfile) -> int:
    policy_profile = dict(profile.policy_profile or {})
    return _bounded_int(
        policy_profile.get("transcript_window_limit", policy_profile.get("context_window_size")),
        default=12,
        minimum=1,
        maximum=24,
    )


def _transcript_window(
    request: IntentTagsClassificationRequest,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    turn_text = _string_value(request.turn.text)
    if turn_text is not None:
        entries.append(
            {
                "role": "user",
                "event_type": request.turn.event_type,
                "channel": request.turn.channel,
                "content": turn_text,
            }
        )
    for message in request.result.emitted_messages:
        text = _string_value(message.text)
        if text is None:
            continue
        entries.append(
            {
                "role": message.role,
                "event_type": "assistant_emitted",
                "channel": request.turn.channel,
                "content": text,
            }
        )
    return entries[-limit:]


def _policy_snippets(profile: ResolvedClassifierProfile) -> list[str]:
    snippets: list[str] = []
    policy_profile = dict(profile.policy_profile or {})
    raw_snippets = policy_profile.get("policy_snippets") or policy_profile.get("snippets")
    if isinstance(raw_snippets, list):
        for item in raw_snippets:
            text = _string_value(item)
            if text is not None:
                snippets.append(text)
    if snippets:
        return snippets[:16]
    if not policy_profile:
        return []
    return [json.dumps(policy_profile, ensure_ascii=False, sort_keys=True)]


def _system_prompt(request: IntentTagsClassificationRequest) -> str:
    policy_profile = dict(request.resolved_profile.policy_profile or {})
    configured = _string_value(policy_profile.get("system_prompt") or policy_profile.get("classifier_prompt"))
    if configured is not None:
        return configured
    return (
        "You classify the current user turn for Ruhu. "
        "Choose the best intent from stable.valid_intents when possible, otherwise use \"unknown\". "
        "Use stable.valid_tools for tool_route when a route is clear. "
        "Return only a JSON object with keys: intent, language, response_language, "
        "language_confidence, tool_route, slots, confidence, signals."
    )


def _context_payload(request: IntentTagsClassificationRequest) -> dict[str, Any]:
    transcript_window_limit = _transcript_window_limit(request.resolved_profile)
    turn_language = _string_value(
        request.turn.metadata.get("language") or request.turn.metadata.get("detected_asr_language")
    )
    response_language_hint = _string_value(
        request.turn.metadata.get("response_language") or request.turn.metadata.get("response_language_hint")
    )
    return {
        "stable": {
            "tenant_prompt": _string_value(
                (request.resolved_profile.policy_profile or {}).get("tenant_prompt")
                or (request.resolved_profile.policy_profile or {}).get("system_prompt")
            ),
            "policy_snippets": _policy_snippets(request.resolved_profile),
            "tool_schemas": list(request.resolved_profile.effective_tool_catalog),
            "user_profile": {
                "facts": dict(request.conversation.facts),
                "metadata": dict(request.conversation.metadata),
            },
            "valid_intents": list(request.resolved_profile.effective_intent_catalog),
            "valid_tools": list(request.resolved_profile.effective_tool_catalog),
            "current_channel": request.conversation.channel or request.turn.channel,
            "scenario_context": {
                "agent_id": request.agent_id,
                "agent_name": request.agent_name,
                "schema_version": request.schema_version,
                "step": {
                    "step_id": request.step.id,
                    "step_name": request.step.name,
                    "step_capabilities": _step_capabilities(request.step),
                    "fact_requirements": [
                        {
                            "name": requirement.name,
                            "purpose": requirement.purpose,
                        }
                        for requirement in request.step.fact_requirements
                    ],
                    "available_tool_refs": [
                        binding.ref
                        for binding in request.step.tool_policy
                        if binding.ref
                    ],
                },
                "conversation": {
                    "conversation_id": request.conversation.conversation_id,
                    "status": request.conversation.status,
                    "outcome": request.conversation.outcome,
                    "step_before": request.result.step_before,
                    "step_after": request.result.step_after,
                },
            },
            "profile_metadata": dict(request.resolved_profile.profile_metadata),
        },
        "dynamic": {
            "transcript_window": _transcript_window(request, limit=transcript_window_limit),
            "current_utterance": request.turn.text,
            "detected_asr_language": turn_language,
            "response_language_hint": response_language_hint,
            "semantic_event_keys": [event.key for event in request.result.semantic_events],
            "tool_calls": [call.model_dump(mode="json") for call in request.result.tool_calls],
        },
    }


def _hosted_retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def _normalize_hosted_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("intent-tags hosted classifier base URL must be a valid http or https URL")
    return normalized


def _resolve_hosted_api_key(
    *,
    api_key: str | None,
    api_key_secret_version: str | None,
) -> str | None:
    resolved_api_key = _string_value(api_key)
    resolved_secret_version = _string_value(api_key_secret_version)
    if resolved_api_key is not None and resolved_secret_version is not None:
        raise ValueError(
            "configure either RUHU_INTENT_TAGS_CLASSIFIER_API_KEY or "
            "RUHU_INTENT_TAGS_CLASSIFIER_API_KEY_SECRET_VERSION, not both"
        )
    if resolved_secret_version is None:
        return resolved_api_key
    normalize_gcp_secret_version(resolved_secret_version)
    return _string_value(load_text_secret(resolved_secret_version))


def _hosted_request_id(response: httpx.Response) -> str | None:
    for key in ("x-request-id", "x-correlation-id", "x-trace-id"):
        value = _string_value(response.headers.get(key))
        if value is not None:
            return value
    return None


def _provider_cost_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    keys = (
        "provider_cost_records",
        "provider_cost_record",
        "provider_cost_usd",
        "cost_usd",
        "amount_usd",
    )
    captured = {key: deepcopy(payload[key]) for key in keys if key in payload}
    return captured or None


def _try_build_named_interpreter(
    name: str,
    *,
    model_path: str | Path,
) -> SemanticInterpreter | None:
    try:
        return build_named_interpreter(name, model_path=model_path)
    except ValueError:
        return None


def _step_capabilities(step: Step) -> dict[str, bool]:
    return step_capability_flags(step)


@dataclass(slots=True)
class SemanticInterpreterClassifierAdapter:
    adapter_name: str
    interpreter: SemanticInterpreter
    model_version: str

    def classify(self, request: IntentTagsClassificationRequest) -> IntentTagsClassificationResult:
        semantic_events = self.interpreter.interpret(
            agent_document=request.agent_document,
            step=request.step,
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            conversation_facts=dict(request.conversation.facts),
            turn=request.turn,
        )
        return IntentTagsClassificationResult(
            semantic_events=list(semantic_events),
            adapter_name=self.adapter_name,
            model_version=self.model_version,
            metadata={"driver": "semantic_interpreter"},
        )


@dataclass(slots=True)
class HostedHTTPClassifierAdapter:
    adapter_name: str
    config: HostedHTTPClassifierConfig

    @property
    def model_version(self) -> str:
        return f"{self.adapter_name}-hosted"

    def classify(self, request: IntentTagsClassificationRequest) -> IntentTagsClassificationResult:
        context = _context_payload(request)
        response = self._request_classifier(
            system_prompt=_system_prompt(request),
            adapter_name=_string_value(
                (request.resolved_profile.policy_profile or {}).get("hosted_adapter_name")
            ) or self.adapter_name,
            context=context,
        )
        response_payload = response.payload
        intent_name = _normalize_machine_name(response_payload.get("intent"), default="unknown")
        confidence = _coerce_probability(response_payload.get("confidence"), default=0.0)
        language = _string_value(response_payload.get("language")) or "und"
        response_language = _string_value(response_payload.get("response_language")) or language
        raw_tool_route = _string_value(response_payload.get("tool_route"))
        tool_route = None if raw_tool_route is None else raw_tool_route[:255]
        slots = response_payload.get("slots") if isinstance(response_payload.get("slots"), dict) else {}
        signals = response_payload.get("signals") if isinstance(response_payload.get("signals"), dict) else {}
        model_version = _string_value(response_payload.get("model")) or self.model_version
        semantic_events = [
            SemanticEventRecord(
                family="intent_detected",
                name=intent_name,
                source="classifier",
                confidence=confidence,
                payload={
                    "language": language,
                    "response_language": response_language,
                    "tool_route": tool_route,
                },
            )
        ]
        if tool_route is not None:
            semantic_events.append(
                SemanticEventRecord(
                    family="tool_route",
                    name=tool_route,
                    source="classifier",
                    confidence=confidence,
                )
            )
        for signal_name in ("uncertain_understanding", "terminal_requested"):
            if signals.get(signal_name):
                semantic_events.append(
                    SemanticEventRecord(
                        family=signal_name,
                        name="detected",
                        source="classifier",
                        confidence=confidence,
                    )
                )
        return IntentTagsClassificationResult(
            semantic_events=semantic_events,
            adapter_name=self.adapter_name,
            model_version=model_version,
            metadata={
                "driver": "hosted_http",
                "base_url": self.config.base_url,
                "request_context": context,
                "request_latency_ms": response.latency_ms,
                "response_status_code": response.status_code,
                "response_request_id": response.request_id,
                "language_confidence": _coerce_probability(
                    response_payload.get("language_confidence"),
                    default=0.0,
                ),
            },
            language=language,
            response_language=response_language,
            language_confidence=_coerce_probability(
                response_payload.get("language_confidence"),
                default=0.0,
            ),
            tool_route=tool_route,
            slots=dict(slots),
            signals={str(key): value for key, value in signals.items()},
            provider_cost_payload=_provider_cost_payload(response_payload),
        )

    def _request_classifier(
        self,
        *,
        system_prompt: str,
        adapter_name: str,
        context: dict[str, Any],
    ) -> HostedHTTPClassifierResponse:
        payload = {
            "system_prompt": system_prompt,
            "adapter": adapter_name,
            "context": context,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        endpoint = f"{self.config.base_url}/v1/classifier/decision"
        attempts = max(1, self.config.max_retries + 1)
        for attempt in range(1, attempts + 1):
            request_started_at = time.monotonic()
            try:
                with httpx.Client(
                    timeout=self.config.timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise HostedHTTPClassifierError(
                        "hosted classifier returned a non-object response",
                        retryable=False,
                        category="invalid_response",
                        status_code=response.status_code,
                        request_id=_hosted_request_id(response),
                        latency_ms=int(round((time.monotonic() - request_started_at) * 1000)),
                    )
                return HostedHTTPClassifierResponse(
                    payload=body,
                    status_code=response.status_code,
                    latency_ms=int(round((time.monotonic() - request_started_at) * 1000)),
                    request_id=_hosted_request_id(response),
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= attempts:
                    raise HostedHTTPClassifierError(
                        f"hosted classifier request failed: {exc}",
                        retryable=True,
                        category="network_error",
                        latency_ms=int(round((time.monotonic() - request_started_at) * 1000)),
                    ) from exc
            except httpx.HTTPStatusError as exc:
                request_id = _hosted_request_id(exc.response)
                latency_ms = int(round((time.monotonic() - request_started_at) * 1000))
                if attempt >= attempts or not _hosted_retryable_status(exc.response.status_code):
                    detail = exc.response.text.strip()
                    message = detail or f"hosted classifier returned HTTP {exc.response.status_code}"
                    raise HostedHTTPClassifierError(
                        message[:500],
                        retryable=_hosted_retryable_status(exc.response.status_code),
                        category=(
                            "http_retryable"
                            if _hosted_retryable_status(exc.response.status_code)
                            else "http_rejected"
                        ),
                        status_code=exc.response.status_code,
                        request_id=request_id,
                        latency_ms=latency_ms,
                    ) from exc
            except HostedHTTPClassifierError:
                raise
            except Exception as exc:
                raise HostedHTTPClassifierError(
                    f"hosted classifier returned an invalid response: {exc}",
                    retryable=False,
                    category="invalid_response",
                    latency_ms=int(round((time.monotonic() - request_started_at) * 1000)),
                ) from exc
            if self.config.retry_backoff_seconds > 0:
                time.sleep(self.config.retry_backoff_seconds * attempt)
        raise HostedHTTPClassifierError(
            "hosted classifier request failed",
            retryable=True,
            category="network_error",
        )


@dataclass(slots=True)
class IntentTagsClassifierRegistry:
    adapters: dict[str, IntentTagsClassifierAdapter] = field(default_factory=dict)
    default_interpreter_name: str | None = None
    agent_interpreters: dict[str, str] = field(default_factory=dict)
    model_path: Path = Path("/tmp/gemma-4-E4B-it")
    hosted_classifier: HostedHTTPClassifierConfig | None = None

    def classify(self, request: IntentTagsClassificationRequest) -> IntentTagsClassificationResult:
        requested_name = (request.resolved_profile.adapter_name or "").strip() or "ruhu-general"
        if requested_name in {"ruhu-general", "kernel-semantics"}:
            return self.project_runtime_semantics(request)

        adapter = self.adapters.get(requested_name)
        if adapter is None:
            adapter = self._build_adapter(requested_name)
            if adapter is not None:
                self.adapters[requested_name] = adapter
        if adapter is None:
            # The adapter the operator asked for couldn't be built — most often
            # because the local Gemma weights aren't installed at the expected
            # path, or no hosted_classifier env was provided. Without this
            # warning the call silently degrades to "echo the empty bootstrap
            # result" and the agent loops on its `otherwise` transition with
            # zero diagnostic surface (an operator hits this and spends an
            # afternoon wondering why the classifier "isn't running").
            #
            # Surface why with a loud log and an explicit
            # `classifier_unavailable` semantic event the agent author can
            # route on.
            logger.warning(
                "Intent-tags classifier adapter %s could not be built; "
                "falling back to runtime-semantics no-op. "
                "If using gemma_local, verify weights at the configured "
                "model_path. If hosted, verify RUHU_HOSTED_CLASSIFIER_* env. "
                "Agent will receive an `intent_tags:classifier_unavailable` "
                "semantic event so transitions can react.",
                requested_name,
                extra={
                    "agent_id": request.agent_id,
                    "step_id": request.step.id,
                    "requested_adapter_name": requested_name,
                    "model_path": str(self.model_path),
                    "hosted_classifier_configured": self.hosted_classifier is not None,
                },
            )
            return self.project_runtime_semantics(
                request,
                requested_adapter_name=requested_name,
                fallback_metadata={
                    "fallback_applied": True,
                    "requested_adapter_name": requested_name,
                    "fallback_reason": {
                        "category": "adapter_unavailable",
                        "message": (
                            f"adapter {requested_name!r} is not installed; "
                            "model_path missing or hosted classifier not configured"
                        ),
                        "retryable": False,
                    },
                },
                extra_events=[
                    SemanticEventRecord(
                        family="intent_tags",
                        name="classifier_unavailable",
                        source="system",
                        confidence=1.0,
                        payload={"requested_adapter_name": requested_name},
                    )
                ],
            )
        try:
            return adapter.classify(request)
        except HostedHTTPClassifierError as exc:
            return self.project_runtime_semantics(
                request,
                requested_adapter_name=requested_name,
                fallback_metadata={
                    "fallback_applied": True,
                    "requested_adapter_name": requested_name,
                    "fallback_reason": exc.as_metadata(),
                },
            )
        except Exception as exc:
            if not isinstance(adapter, HostedHTTPClassifierAdapter):
                raise
            return self.project_runtime_semantics(
                request,
                requested_adapter_name=requested_name,
                fallback_metadata={
                    "fallback_applied": True,
                    "requested_adapter_name": requested_name,
                    "fallback_reason": {
                        "message": str(exc)[:500] or exc.__class__.__name__,
                        "retryable": False,
                        "category": "unexpected_error",
                    },
                },
            )

    def project_runtime_semantics(
        self,
        request: IntentTagsClassificationRequest,
        *,
        requested_adapter_name: str | None = None,
        fallback_metadata: dict[str, Any] | None = None,
        extra_events: list[SemanticEventRecord] | None = None,
    ) -> IntentTagsClassificationResult:
        runtime_adapter_name = self.runtime_interpreter_name(request.agent_id) or "kernel-semantics"
        metadata = {
            "driver": "kernel_runtime",
            "requested_adapter_name": requested_adapter_name,
        }
        if fallback_metadata:
            metadata.update(fallback_metadata)
        events = list(request.result.semantic_events)
        if extra_events:
            events.extend(extra_events)
        return IntentTagsClassificationResult(
            semantic_events=events,
            adapter_name=runtime_adapter_name,
            model_version=f"{runtime_adapter_name}-runtime-v1",
            metadata=metadata,
        )

    def runtime_interpreter_name(self, agent_id: str | None) -> str | None:
        if agent_id is not None:
            agent_specific = self.agent_interpreters.get(agent_id)
            if agent_specific:
                return agent_specific
        return self.default_interpreter_name

    def _build_adapter(self, name: str) -> IntentTagsClassifierAdapter | None:
        interpreter = _try_build_named_interpreter(name, model_path=self.model_path)
        if interpreter is not None:
            return SemanticInterpreterClassifierAdapter(
                adapter_name=name,
                interpreter=interpreter,
                model_version=_model_version_for_interpreter(name, model_path=self.model_path),
            )
        if self.hosted_classifier is None:
            return None
        return HostedHTTPClassifierAdapter(adapter_name=name, config=self.hosted_classifier)


def build_intent_tags_classifier_registry(
    *,
    default_interpreter_name: str | None = None,
    agent_interpreters: dict[str, str] | None = None,
    model_path: str | Path = "/tmp/gemma-4-E4B-it",
    hosted_classifier_base_url: str | None = None,
    hosted_classifier_api_key: str | None = None,
    hosted_classifier_api_key_secret_version: str | None = None,
    hosted_classifier_timeout_seconds: float = 5.0,
    hosted_classifier_max_retries: int = 2,
    hosted_classifier_retry_backoff_seconds: float = 0.25,
) -> IntentTagsClassifierRegistry:
    resolved_agent_interpreters = dict(agent_interpreters or {})
    names = {
        name.strip()
        for name in [default_interpreter_name, *resolved_agent_interpreters.values()]
        if isinstance(name, str) and name.strip()
    }
    adapters: dict[str, IntentTagsClassifierAdapter] = {}
    for name in sorted(names):
        interpreter = _try_build_named_interpreter(name, model_path=model_path)
        if interpreter is None:
            continue
        adapters[name] = SemanticInterpreterClassifierAdapter(
            adapter_name=name,
            interpreter=interpreter,
            model_version=_model_version_for_interpreter(name, model_path=model_path),
        )
    hosted_classifier = None
    if hosted_classifier_base_url:
        hosted_classifier = HostedHTTPClassifierConfig(
            base_url=_normalize_hosted_base_url(hosted_classifier_base_url),
            api_key=_resolve_hosted_api_key(
                api_key=hosted_classifier_api_key,
                api_key_secret_version=hosted_classifier_api_key_secret_version,
            ),
            timeout_seconds=hosted_classifier_timeout_seconds,
            max_retries=max(0, hosted_classifier_max_retries),
            retry_backoff_seconds=max(0.0, hosted_classifier_retry_backoff_seconds),
        )
    return IntentTagsClassifierRegistry(
        adapters=adapters,
        default_interpreter_name=default_interpreter_name,
        agent_interpreters=resolved_agent_interpreters,
        model_path=Path(model_path),
        hosted_classifier=hosted_classifier,
    )
