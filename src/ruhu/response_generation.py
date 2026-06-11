from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
import json
import logging
import os
import re
import time as _time_module
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError as _PydanticValidationError

from .schemas import (
    JourneyContext,
    MoveSelectionContext,
    MoveSelectionOutput,
    RenderOutput,
    StepCapabilities,
    TransitionReasonCode,
)

if TYPE_CHECKING:
    from .schemas import RenderContext

import httpx

_time_monotonic = _time_module.monotonic


def _observe_llm_latency(start: float, *, provider: str) -> None:
    try:
        from .observability.metrics import llm_response_wait_seconds
        llm_response_wait_seconds.labels(provider=provider).observe(_time_monotonic() - start)
    except Exception:
        pass


def _observe_llm_request(
    start: float,
    *,
    provider: str,
    model: str,
    stage: str,
    outcome: str,
    response_body: object | None = None,
) -> None:
    """Record bounded request metrics for an instrumented LLM call."""
    try:
        from .observability.cost import _canonical_model_name, record_llm_cost
        from .observability.metrics import llm_request_duration_seconds, provider_error_total

        canonical_model = _canonical_model_name(model)
        llm_request_duration_seconds.labels(
            provider=provider,
            model=canonical_model,
            stage=stage,
            outcome=outcome,
        ).observe(max(0.0, _time_monotonic() - start))

        if outcome == "error":
            provider_error_total.labels(provider=provider, kind="http_error").inc()
            return

        input_tokens = 0
        output_tokens = 0
        if isinstance(response_body, dict):
            usage = response_body.get("usageMetadata")
            if isinstance(usage, dict):
                input_tokens = int(usage.get("promptTokenCount") or 0)
                output_tokens = int(usage.get("candidatesTokenCount") or 0)
        record_llm_cost(
            provider,
            canonical_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except Exception:
        pass


def _extract_usage_metadata(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        return None
    return usage


def _response_body_for_metrics(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    usage = _extract_usage_metadata(payload)
    if usage is None:
        return None
    return {"usageMetadata": usage}


def _merge_usage_metadata(
    current: dict[str, object] | None,
    payload: object,
) -> dict[str, object] | None:
    next_payload = _response_body_for_metrics(payload)
    return next_payload if next_payload is not None else current


def _observe_llm_error(start: float, *, provider: str, model: str, stage: str) -> None:
    _observe_llm_latency(start, provider=provider)
    _observe_llm_request(
        start,
        provider=provider,
        model=model,
        stage=stage,
        outcome="error",
    )


def _observe_llm_success(
    start: float,
    *,
    provider: str,
    model: str,
    stage: str,
    response_body: object | None = None,
) -> None:
    _observe_llm_latency(start, provider=provider)
    _observe_llm_request(
        start,
        provider=provider,
        model=model,
        stage=stage,
        outcome="ok",
        response_body=response_body,
    )


logger = logging.getLogger(__name__)
_DEPRECATED_GEMINI_FLASH_PATTERN = re.compile(
    r"^gemini-2\.[1-9]\d*-flash(?:-preview(?:-\d{2}-\d{2})?)?$"
)
_DEPRECATED_GEMINI_PRO_PATTERN = re.compile(
    r"^gemini-2\.[1-9]\d*-pro(?:-preview(?:-\d{2}-\d{2})?)?$"
)
_VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Verbatim Gemini-3 strict-grounding system instruction from
# https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/get-started-with-gemini-3.
# Injected as a *prefix* to the renderer's existing system text whenever
# the step's ``KnowledgeGroundingPolicy.mode`` is ``preferred`` or
# ``required`` AND ``strict_system_instruction=True``. Authors who need
# the LLM to paraphrase or summarise beyond retrieved facts can opt out
# per-step via ``strict_system_instruction=False``.
_STRICT_GROUNDED_SYSTEM_INSTRUCTION = (
    "You are a strictly grounded assistant limited to the information "
    "provided in the User Context. In your answers, rely only on the "
    "facts that are directly mentioned in that context. You must not "
    "access or utilize your own knowledge or common sense to answer. "
    "Do not assume or infer from the provided facts; simply report them "
    "exactly as they appear."
)


# Tokens to skip when scoring grounding overlap. Mirrors
# ``ConversationKernel._topic_tokens`` in spirit but kept local to the
# renderer so it can score post-hoc without importing kernel internals.
_GROUNDING_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "about", "as", "at", "be", "been", "being",
    "but", "by", "can", "could", "did", "do", "does", "for", "from",
    "had", "has", "have", "having", "he", "her", "here", "hers", "him",
    "his", "how", "i", "if", "in", "into", "is", "it", "its", "may",
    "me", "might", "must", "my", "no", "nor", "not", "now", "of", "off",
    "on", "or", "our", "ours", "out", "over", "own", "should", "so",
    "such", "than", "that", "the", "their", "theirs", "them", "then",
    "there", "these", "they", "this", "those", "to", "too", "us", "was",
    "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "why", "will", "with", "would", "you", "your", "yours",
})


def _grounding_tokens(text: str) -> set[str]:
    """Lowercase content tokens for grounding-overlap scoring."""
    if not text:
        return set()
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return {tok for tok in cleaned.split() if tok and tok not in _GROUNDING_STOP_WORDS}


def score_grounding_overlap(
    rendered_text: str,
    chunks: list,
) -> float:
    """Compute a 0–1 score of how much of the rendered answer's content
    is covered by the retrieved chunks.

    This is the heuristic post-call check — it asks: of the meaningful
    tokens in the answer, what fraction also appear in the retrieved
    knowledge? An answer that introduces new entities, prices, or
    proper nouns not in the chunks scores lower; a paraphrase that
    reuses chunk vocabulary scores higher.

    Used only when ``KnowledgeGroundingPolicy.post_call_check ==
    "heuristic"``. The ``"llm"`` mode (a secondary grading call) is
    declared in the schema but reserved for v2 — it doubles latency.
    """
    answer_tokens = _grounding_tokens(rendered_text)
    if not answer_tokens:
        return 1.0  # empty answers can't fabricate; let other gates handle them
    chunk_tokens: set[str] = set()
    for chunk in chunks or []:
        chunk_text = getattr(chunk, "text", None)
        if not chunk_text and isinstance(chunk, dict):
            chunk_text = chunk.get("text")
        chunk_tokens |= _grounding_tokens(chunk_text or "")
    if not chunk_tokens:
        return 0.0
    overlap = len(answer_tokens & chunk_tokens)
    return overlap / len(answer_tokens)


@dataclass(slots=True, frozen=True)
class ResponseGenerationContext:
    provider: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ResponseGenerationRequest:
    conversation_id: str
    organization_id: str | None
    agent_id: str
    agent_version_id: str
    step_id: str
    step_name: str
    step_summary: str
    channel: str
    event_type: str
    user_text: str
    fallback_text: str
    context: ResponseGenerationContext


@dataclass(slots=True, frozen=True)
class IntentClassificationRequest:
    conversation_id: str
    organization_id: str | None
    agent_id: str
    agent_version_id: str
    step_id: str
    step_name: str
    step_summary: str
    channel: str
    event_type: str
    user_text: str
    valid_intents: dict[str, str]
    context: ResponseGenerationContext
    # Prefill-first fields (WI-4.2). When the dispatcher has built the
    # canonical prefix + suffix via classifier.prompt.build_classifier_prompt,
    # those bytes flow through to backends that honour vLLM's prefix
    # cache. Default None lets direct callers build the prompt in place.
    prefix: str | None = None
    suffix: str | None = None


@dataclass(slots=True, frozen=True)
class MoveSelectionRequest:
    """Request payload for the move-selection LLM call (doc 37 WI-5).

    Mirrors the shape of :class:`ResponseGenerationRequest` so providers can
    reuse credential / metadata plumbing.  ``prompt`` is the rendered
    move-selection prompt produced by :func:`build_move_selection_prompt`;
    providers send it to the LLM and return raw text for
    :func:`parse_move_selection_output` to validate.
    """

    conversation_id: str
    organization_id: str | None
    agent_id: str
    agent_version_id: str
    step_id: str
    step_name: str
    step_summary: str
    channel: str
    event_type: str
    user_text: str
    prompt: str
    context: ResponseGenerationContext


class ResponseGenerator(Protocol):
    # Intent classification is owned by the classifier subsystem
    # (ClassifierDispatcher -> PrefillClassifier backends). Response
    # generation only renders assistant text and move-selection output.
    def generate(
        self,
        request: ResponseGenerationRequest,
        on_first_sentence: Callable[[str], None] | None = None,
    ) -> str | None:
        ...

    def render_from_context(
        self,
        context: "RenderContext",
        *,
        provider: str | None = None,
        model: str | None = None,
        on_first_sentence: Callable[[str], None] | None = None,
    ) -> "str | RenderOutput | None":
        ...

    def select_move(self, request: MoveSelectionRequest) -> str | None:
        ...


@dataclass(slots=True)
class GeminiDialogueGenerator:
    api_key: str | None = None
    default_model: str = "gemini-3-flash-preview"
    timeout_seconds: float = 20.0
    max_output_tokens: int = 1024
    endpoint_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    use_vertex: bool = False
    vertex_project: str | None = None
    vertex_location: str = "europe-west2"
    _vertex_credentials: object | None = field(default=None, init=False, repr=False)

    def generate(
        self,
        request: ResponseGenerationRequest,
        on_first_sentence: Callable[[str], None] | None = None,
    ) -> str | None:
        provider = (request.context.provider or "").strip().lower()
        if provider not in {"gemini", "google", "vertex"}:
            return None
        model = _normalize_gemini_model_name(request.context.model or self.default_model) or self.default_model
        user_text = request.user_text.strip()
        if not user_text:
            return None

        prompt = (
            "You are replying to a live customer message inside a step-native assistant.\n"
            f"Step: {request.step_name}\n"
            f"Step summary: {request.step_summary}\n"
            f"User message: {user_text}\n"
            f"Fallback reply if needed: {request.fallback_text}\n"
            "Return one concise assistant reply as plain text."
        )
        payload: dict[str, object] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": max(32, int(self.max_output_tokens)),
            },
        }
        system_prompt = (request.context.system_prompt or "").strip()
        if system_prompt:
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}

        vertex_mode = self._use_vertex_for_request(provider=provider, request=request)
        try:
            url, request_kwargs = self._resolve_request_target(
                request=request, provider=provider, model=model, vertex_mode=vertex_mode,
            )
            if url is None:
                return None
        except Exception:
            logger.exception(
                "gemini response generation failed to resolve auth path",
                extra={
                    "conversation_id": request.conversation_id,
                    "agent_id": request.agent_id,
                    "provider": provider,
                    "model": model,
                },
            )
            return None

        # When a callback is provided, use streaming to deliver the first
        # sentence to the caller as soon as it is available (for early TTS).
        if on_first_sentence is not None:
            return self._generate_streaming(
                url=url,
                payload=payload,
                request_kwargs=request_kwargs,
                request=request,
                model=model,
                on_first_sentence=on_first_sentence,
            )

        _start = _time_monotonic()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload, **request_kwargs)
                response.raise_for_status()
                response_body = response.json()
        except Exception:
            _observe_llm_error(_start, provider=provider, model=model, stage="generate")
            logger.exception(
                "gemini response generation failed",
                extra={
                    "conversation_id": request.conversation_id,
                    "agent_id": request.agent_id,
                    "model": model,
                },
            )
            return None
        _observe_llm_success(
            _start,
            provider=provider,
            model=model,
            stage="generate",
            response_body=response_body,
        )
        return _extract_gemini_text(response_body)

    def _resolve_request_target(
        self,
        *,
        request: ResponseGenerationRequest,
        provider: str,
        model: str,
        vertex_mode: bool,
    ) -> tuple[str | None, dict[str, object]]:
        """Return (url, request_kwargs) for a Gemini API call."""
        if vertex_mode:
            project, location = self._resolve_vertex_target(request=request, model=model)
            if project is None or location is None:
                return None, {}
            url = (
                f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/{location}"
                f"/publishers/google/models/{model}:generateContent"
            )
            headers = {
                "Authorization": f"Bearer {self._load_vertex_access_token()}",
                "Content-Type": "application/json",
            }
            return url, {"headers": headers}
        if not self.api_key:
            logger.warning(
                "gemini response generation skipped: missing API key",
                extra={
                    "conversation_id": request.conversation_id,
                    "agent_id": request.agent_id,
                    "provider": provider,
                },
            )
            return None, {}
        url = f"{self.endpoint_base_url.rstrip('/')}/models/{model}:generateContent"
        return url, {"params": {"key": self.api_key}}

    def _generate_streaming(
        self,
        *,
        url: str,
        payload: dict[str, object],
        request_kwargs: dict[str, object],
        request: ResponseGenerationRequest,
        model: str,
        on_first_sentence: Callable[[str], None],
    ) -> str | None:
        """Stream from Gemini and fire ``on_first_sentence`` as soon as a
        complete sentence is available, while continuing to collect the full
        response text for the kernel.
        """
        # Use Gemini's streamGenerateContent endpoint (SSE).
        stream_url = url.replace(":generateContent", ":streamGenerateContent")
        stream_url += ("&" if "?" in stream_url else "?") + "alt=sse"
        first_sentence_fired = False
        collected_text = ""
        usage_payload: dict[str, object] | None = None
        start = _time_monotonic()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                with client.stream("POST", stream_url, json=payload, **request_kwargs) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        json_str = line[len("data: "):]
                        try:
                            chunk = json.loads(json_str)
                        except json.JSONDecodeError:
                            continue
                        usage_payload = _merge_usage_metadata(usage_payload, chunk)
                        text_part = _extract_gemini_text(chunk)
                        if text_part:
                            collected_text += text_part
                        # Fire callback on first complete sentence.
                        if not first_sentence_fired and collected_text:
                            sentence = _extract_first_sentence(collected_text)
                            if sentence:
                                first_sentence_fired = True
                                try:
                                    on_first_sentence(sentence)
                                except Exception:
                                    logger.exception("on_first_sentence callback failed")
        except Exception:
            _observe_llm_error(start, provider=request.context.provider or "gemini", model=model, stage="generate")
            logger.exception(
                "gemini streaming response generation failed",
                extra={
                    "conversation_id": request.conversation_id,
                    "agent_id": request.agent_id,
                    "model": model,
                },
            )
            if collected_text.strip():
                return collected_text.strip()
            return None
        _observe_llm_success(
            start,
            provider=request.context.provider or "gemini",
            model=model,
            stage="generate",
            response_body=usage_payload,
        )
        return collected_text.strip() or None

    # Intent classification lives in the prefill-first classifier
    # subsystem. The Vertex fallback is served by
    # classifier.vertex_gemini_backend.VertexGeminiClassifierBackend,
    # which issues its own Vertex REST call.

    def select_move(self, request: MoveSelectionRequest) -> str | None:
        """Run the move-selection prompt against Gemini/Vertex and return raw text.

        The kernel owns parsing and validation of the returned payload. This
        method only handles provider routing, bounded observability, and
        returning the model's raw text response.
        """
        provider = (request.context.provider or "").strip().lower()
        if provider not in {"gemini", "google", "vertex"}:
            return None

        model = _normalize_gemini_model_name(request.context.model or self.default_model) or self.default_model
        prompt = request.prompt.strip()
        if not prompt:
            return None

        payload: dict[str, object] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": max(128, int(self.max_output_tokens)),
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        system_prompt = (request.context.system_prompt or "").strip()
        if system_prompt:
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}

        vertex_mode = self._use_vertex_for_request(provider=provider, request=request)
        start = _time_monotonic()
        try:
            url, request_kwargs = self._resolve_request_target(
                request=request, provider=provider, model=model, vertex_mode=vertex_mode,
            )
            if url is None:
                return None
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload, **request_kwargs)
                response.raise_for_status()
                response_body = response.json()
        except Exception:
            _observe_llm_error(start, provider=provider, model=model, stage="move_select")
            logger.exception(
                "gemini move selection failed",
                extra={
                    "conversation_id": request.conversation_id,
                    "agent_id": request.agent_id,
                    "step_id": request.step_id,
                    "model": model,
                },
            )
            return None

        _observe_llm_success(
            start,
            provider=provider,
            model=model,
            stage="move_select",
            response_body=response_body,
        )
        return _extract_gemini_text(response_body)

    def _use_vertex_for_request(self, *, provider: str, request: ResponseGenerationRequest) -> bool:
        if provider == "vertex":
            return True
        metadata = request.context.metadata
        auth_mode = _stringify_metadata(metadata.get("auth_mode"))
        if auth_mode == "vertex_adc":
            return True
        if _coerce_bool(metadata.get("use_vertex"), default=False):
            return True
        return self.use_vertex

    def _resolve_vertex_target(
        self,
        *,
        request: ResponseGenerationRequest,
        model: str,
    ) -> tuple[str | None, str | None]:
        metadata = request.context.metadata
        project = (
            _stringify_metadata(metadata.get("vertex_project"))
            or _stringify_metadata(metadata.get("project"))
            or self.vertex_project
        )
        location = (
            _stringify_metadata(metadata.get("vertex_location"))
            or _stringify_metadata(metadata.get("location"))
            or self.vertex_location
        )
        if not project:
            logger.warning(
                "gemini response generation skipped: missing Vertex project",
                extra={
                    "conversation_id": request.conversation_id,
                    "agent_id": request.agent_id,
                    "provider": request.context.provider,
                },
            )
            return None, None
        resolved_location = _resolve_vertex_location(model=model, location=location or "europe-west2")
        return project, resolved_location

    def render_from_context(
        self,
        context: "RenderContext",
        *,
        provider: str | None = None,
        model: str | None = None,
        on_first_sentence: Callable[[str], None] | None = None,
    ) -> "str | RenderOutput | None":
        """Render a user-visible message from structured ``RenderContext``.

        This is the new rendering path that replaces the thin prompt in
        ``generate()``.  The LLM receives facts, recent messages, action
        outcomes, and constraints — not just state name + user text.
        """
        resolved_provider = (provider or "vertex").strip().lower()
        if resolved_provider not in {"gemini", "google", "vertex"}:
            return None
        resolved_model = _normalize_gemini_model_name(
            model or self.default_model
        ) or self.default_model

        journey = context.journey

        # Build structured prompt
        facts_block = ""
        if context.facts:
            facts_lines = "\n".join(f"  {k}: {v}" for k, v in context.facts.items())
            facts_block = f"\nKnown facts:\n{facts_lines}\n"

        messages_block = ""
        if context.recent_messages:
            msg_lines = "\n".join(
                f"  {m.role}: {m.text}" for m in context.recent_messages[-8:]
            )
            messages_block = f"\nRecent conversation:\n{msg_lines}\n"

        outcome_block = ""
        if context.latest_action_outcome.status != "none":
            outcome = context.latest_action_outcome
            outcome_lines = [f"  status: {outcome.status}"]
            if outcome.summary:
                outcome_lines.append(f"  summary: {outcome.summary}")
            if outcome.user_visible_fields:
                for k, v in outcome.user_visible_fields.items():
                    outcome_lines.append(f"  {k}: {v}")
            outcome_block = "\nLatest action result:\n" + "\n".join(outcome_lines) + "\n"

        # Strict-grounded User Context block — Google's pattern requires
        # the model to cite retrieved facts verbatim, so we surface the
        # chunks in a structured block the system instruction can refer
        # to ("...the User Context..."). Only emitted when grounding is
        # active AND chunks are present (the pre-call gate already
        # guarantees this, but be defensive).
        user_context_block = ""
        grounding_policy = context.grounding_policy
        grounding_active = (
            grounding_policy is not None and grounding_policy.mode != "off"
        )
        if grounding_active and context.retrieval_evidence:
            ctx_lines = []
            for idx, chunk in enumerate(context.retrieval_evidence, start=1):
                title = chunk.title or chunk.document_id or f"chunk_{idx}"
                ctx_lines.append(f"[{idx}] {title}: {chunk.text}")
            user_context_block = (
                "\nUser Context (retrieved facts — your only allowed source):\n"
                + "\n".join(ctx_lines)
                + "\n"
            )

        pending_facts_block = ""
        if journey and journey.pending_facts:
            pending_lines = []
            for fact_name, pending in journey.pending_facts.items():
                pending_lines.append(f"  {fact_name}: purpose={pending.purpose}")
                if pending.triggered_by:
                    pending_lines.append(f"    triggered_by={pending.triggered_by}")
                if pending.triggered_in_step:
                    pending_lines.append(f"    triggered_in_step={pending.triggered_in_step}")
                if pending.ask_for_fact:
                    pending_lines.append(f"    ask_for_fact={pending.ask_for_fact}")
            pending_facts_block = "\nPending facts:\n" + "\n".join(pending_lines) + "\n"

        capability_manifest_block = ""
        capability_manifest = journey.agent_capability_manifest if journey is not None else None
        if capability_manifest is not None:
            manifest_lines = []
            if capability_manifest.assistant_identity:
                manifest_lines.append(
                    f"  assistant_identity: {capability_manifest.assistant_identity}"
                )
            if capability_manifest.capabilities:
                manifest_lines.append(
                    f"  capabilities: {', '.join(capability_manifest.capabilities)}"
                )
            if capability_manifest.limitations:
                manifest_lines.append(
                    f"  limitations: {', '.join(capability_manifest.limitations)}"
                )
            if manifest_lines:
                capability_manifest_block = (
                    "\nAgent capability manifest:\n" + "\n".join(manifest_lines) + "\n"
                )
        constraints_block = ""
        constraint_rules = []
        if context.constraints.must_not_claim:
            constraint_rules.append(
                f"Do not invent values for: {', '.join(context.constraints.must_not_claim)}"
            )
        if context.constraints.do_not_ask_for:
            constraint_rules.append(
                f"Do not ask for: {', '.join(context.constraints.do_not_ask_for)}"
            )
        if context.constraints.must_mention:
            constraint_rules.append(
                f"Must mention: {', '.join(context.constraints.must_mention)}"
            )
        if context.constraints.response_max_sentences:
            constraint_rules.append(
                f"Maximum {context.constraints.response_max_sentences} sentences."
            )
        if constraint_rules:
            constraints_block = "\nConstraints:\n" + "\n".join(f"- {r}" for r in constraint_rules) + "\n"

        directive = ""
        if context.response_directive:
            directive = f"\nDirective: {context.response_directive}\n"

        user_msg = ""
        if journey.current_user_text:
            user_msg = f"\nUser message: {journey.current_user_text}\n"

        pending_action_block = ""
        if context.pending_action_summary:
            pending_action_lines = "\n".join(
                f"  {k}: {v}" for k, v in context.pending_action_summary.items() if v is not None
            )
            pending_action_block = f"\nPending action:\n{pending_action_lines}\n"

        pending_permission_block = ""
        if context.pending_permission_summary:
            pending_permission_lines = "\n".join(
                f"  {k}: {v}" for k, v in context.pending_permission_summary.items() if v is not None
            )
            pending_permission_block = f"\nPending permission:\n{pending_permission_lines}\n"

        grounding_block = ""
        if context.grounding_summary:
            grounding_lines = []
            ack_facts = context.grounding_summary.get("acknowledged_fact_keys") or []
            if ack_facts:
                grounding_lines.append(f"  acknowledged_fact_keys: {', '.join(map(str, ack_facts))}")
            ack_requests = context.grounding_summary.get("acknowledged_requests") or []
            if ack_requests:
                grounding_lines.append(f"  acknowledged_requests: {', '.join(map(str, ack_requests))}")
            last_status = context.grounding_summary.get("last_user_visible_status")
            if last_status:
                grounding_lines.append(f"  last_user_visible_status: {last_status}")
            unresolved = context.grounding_summary.get("unresolved_points") or []
            if unresolved:
                grounding_lines.append(f"  unresolved_points: {', '.join(map(str, unresolved))}")
            if grounding_lines:
                grounding_block = "\nGrounding summary:\n" + "\n".join(grounding_lines) + "\n"

        commitment_block = ""
        if context.commitment_summary:
            commitment_lines = "\n".join(
                f"  {k}: {v}" for k, v in context.commitment_summary.items() if v is not None
            )
            commitment_block = f"\nCommitment summary:\n{commitment_lines}\n"

        repair_block = ""
        if context.active_repair:
            repair_lines = "\n".join(
                f"  {k}: {v}" for k, v in context.active_repair.items() if v is not None
            )
            repair_block = f"\nActive repair:\n{repair_lines}\n"

        policy_block = ""
        if context.policy_outcome:
            policy_block = f"\nPolicy outcome: {context.policy_outcome}\n"

        status_trail_block = ""
        if context.status_trail_summary:
            trail_lines = "\n".join(
                f"  - {item}" for item in context.status_trail_summary
            )
            status_trail_block = f"\nStatus trail summary:\n{trail_lines}\n"

        coordination_block = ""
        conversation_projection = (
            context.metadata.get("conversation_runtime_projection")
            if isinstance(context.metadata, dict)
            else None
        )
        if isinstance(conversation_projection, dict):
            runtime = conversation_projection.get("runtime")
            interpretation = conversation_projection.get("turn_interpretation")
            narration = conversation_projection.get("narration")
            coordination_lines: list[str] = []
            if isinstance(runtime, dict):
                control = runtime.get("control")
                user_contract = runtime.get("user_contract")
                if isinstance(control, dict):
                    runtime_activity_status = control.get("runtime_activity_status")
                    if runtime_activity_status:
                        coordination_lines.append(
                            f"  runtime_activity_status: {runtime_activity_status}"
                        )
                if isinstance(user_contract, dict):
                    waiting_on = user_contract.get("waiting_on")
                    if waiting_on:
                        coordination_lines.append(f"  waiting_on: {waiting_on}")
            if isinstance(interpretation, dict):
                detected_control_intent = interpretation.get("detected_control_intent")
                if detected_control_intent:
                    coordination_lines.append(
                        f"  detected_control_intent: {detected_control_intent}"
                    )
                if interpretation.get("bridge_appropriate") is True:
                    coordination_lines.append("  bridge_appropriate: true")
            if isinstance(narration, dict):
                narration_mode = narration.get("narration_mode")
                if narration_mode:
                    coordination_lines.append(f"  narration_mode: {narration_mode}")
                must_acknowledge = narration.get("must_acknowledge")
                if isinstance(must_acknowledge, list) and must_acknowledge:
                    coordination_lines.append(
                        f"  must_acknowledge: {', '.join(map(str, must_acknowledge))}"
                    )
                if narration.get("must_not_imply_completion") is True:
                    coordination_lines.append("  must_not_imply_completion: true")
                if narration.get("must_not_repeat_prompt") is True:
                    coordination_lines.append("  must_not_repeat_prompt: true")
            if coordination_lines:
                coordination_block = (
                    "\nCoordination projection:\n" + "\n".join(coordination_lines) + "\n"
                )

        journey_block = ""
        if journey is not None:
            journey_lines = [
                f"  current_step_purpose: {journey.current_step_purpose or ''}",
            ]
            if journey.previous_step_name:
                journey_lines.append(f"  previous_step: {journey.previous_step_name}")
            if journey.transition_natural_reason:
                journey_lines.append(f"  transition_reason: {journey.transition_natural_reason}")
            if journey.topic_freshness:
                journey_lines.append(f"  topic_freshness: {journey.topic_freshness}")
            if journey.route_horizon:
                for branch in journey.route_horizon:
                    horizon = (
                    f"  branch -> {branch.target_step_id}"
                    + (
                        f" / focus: {_step_capability_prompt_label(branch.target_step_capabilities)}"
                        if _step_capability_prompt_label(branch.target_step_capabilities)
                        else ""
                    )
                        + (f": {branch.branch_when_to_use}" if branch.branch_when_to_use else "")
                    )
                    journey_lines.append(horizon)
            journey_block = "\nJourney context:\n" + "\n".join(journey_lines) + "\n"

        transition_block = ""
        if context.transition_narrative is not None:
            narrative = context.transition_narrative
            narrative_lines = [
                f"  from_step_id: {narrative.from_step_id}",
                f"  to_step_id: {narrative.to_step_id}",
            ]
            if narrative.transition_intent:
                narrative_lines.append(f"  transition_intent: {narrative.transition_intent}")
            if narrative.natural_reason:
                narrative_lines.append(f"  natural_reason: {narrative.natural_reason}")
            transition_block = "\nTransition narrative:\n" + "\n".join(narrative_lines) + "\n"

        guidance_block = ""
        guidance = journey.authored_guidance if journey is not None else None
        if guidance is not None:
            guidance_lines = []
            for key in (
                "say_on_entry",
                "say_on_transition",
                "ask_for_fact",
                "repair_response",
            ):
                value = getattr(guidance, key)
                if value:
                    guidance_lines.append(f"  {key}: {value}")
            if guidance_lines:
                guidance_block = "\nAuthored state guidance:\n" + "\n".join(guidance_lines) + "\n"

        allowed_claims = context.allowed_claim_classes or ["partial"]
        claim_block = f"\nAllowed claim classes: {', '.join(allowed_claims)}\n"
        latency_block = ""
        if context.latency_budget_ms is not None:
            latency_block = f"\nLatency budget ms: {context.latency_budget_ms}\n"

        prompt = (
            f"Step: {journey.current_step_name or context.journey.current_step_id}"
            + (
                f" / focus: {_step_capability_prompt_label(journey.current_step_capabilities)}"
                if _step_capability_prompt_label(journey.current_step_capabilities)
                else ""
            )
            + "\n"
            f"Step purpose: {journey.current_step_purpose or ''}\n"
            f"Response mode: {context.response_mode}\n"
            f"{directive}"
            f"{facts_block}"
            f"{messages_block}"
            f"{outcome_block}"
            f"{user_context_block}"
            f"{pending_action_block}"
            f"{pending_permission_block}"
            f"{grounding_block}"
            f"{commitment_block}"
            f"{repair_block}"
            f"{policy_block}"
            f"{status_trail_block}"
            f"{coordination_block}"
            f"{journey_block}"
            f"{transition_block}"
            f"{guidance_block}"
            f"{capability_manifest_block}"
            f"{pending_facts_block}"
            f"{user_msg}"
            f"{claim_block}"
            f"{latency_block}"
            f"{constraints_block}"
            "\nReturn strict JSON with fields:\n"
            "- text: string\n"
            "- claimed_class: one of the allowed claim classes\n"
            "- acknowledged_fact_keys: array of fact keys explicitly acknowledged in the reply\n"
            "Be natural and concise in text."
        )

        system_text = (
            "You are the response renderer for an interactive AI assistant.\n"
            "Your job is to write the next assistant message using only the provided context.\n"
            "Rules:\n"
            "- Be natural, helpful, and concise.\n"
            "- Treat facts, recent messages, and action results as source of truth.\n"
            "- Treat journey context, transition narrative, and authored step guidance as the authoritative interaction brief.\n"
            "- Do not quote journey context labels, transition reasons, route-branch descriptions, or instruction text verbatim; translate them into natural user-facing language.\n"
            "- Do not repeat the user's wording back as the answer unless the context explicitly requires a short acknowledgement.\n"
            "- Do not invent details not present in the context.\n"
            "- If response_mode is ask_missing_fact, ask for the missing fact naturally.\n"
            "- If response_mode is transition_bridge, explain why the conversation is moving before the next ask or action.\n"
            "- If response_mode is status_explanation, explain what is happening now and what the system is waiting on.\n"
            "- If response_mode is clarify, address the user's off-script turn briefly and keep the conversation moving.\n"
            "- If response_mode is confirm_success, confirm the outcome with concrete details.\n"
            "- If response_mode is explain_failure, explain safely and offer next steps.\n"
            "- If coordination says must_not_imply_completion, avoid language that says the work is done or committed.\n"
            "- If coordination says must_not_repeat_prompt, acknowledge briefly without repeating the same request.\n"
            "- If coordination lists must_acknowledge items, acknowledge that user move before continuing.\n"
        )
        if context.system_prompt:
            system_text = context.system_prompt + "\n\n" + system_text

        voice_style = context.voice_style or "concise"
        if context.channel in {"phone", "voice", "web_widget"} or voice_style == "concise":
            system_text += "- Keep responses short — this is a voice conversation.\n"

        # Strict-grounded preamble — Google's verbatim Gemini-3 wording.
        # Prefixed *before* the renderer's existing rules so the model
        # treats the User Context block as an authoritative source.
        if grounding_active and grounding_policy.strict_system_instruction:
            system_text = _STRICT_GROUNDED_SYSTEM_INSTRUCTION + "\n\n" + system_text

        # Lower temperature for grounded answers — Google's guidance
        # (and Stanford 2024) shows ~22% hallucination reduction at 0.0
        # vs. 0.4 with no quality loss for factual content.
        temperature = 0.0 if grounding_active else 0.4

        payload: dict[str, object] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max(32, int(self.max_output_tokens)),
                "thinkingConfig": {"thinkingBudget": 0},
                "responseMimeType": "application/json",
            },
            "system_instruction": {"parts": [{"text": system_text}]},
        }

        start = _time_monotonic()
        try:
            if self.use_vertex and self.vertex_project:
                location = _resolve_vertex_location(
                    model=resolved_model,
                    location=self.vertex_location or "europe-west2",
                )
                url = (
                    f"https://aiplatform.googleapis.com/v1/projects/{self.vertex_project}"
                    f"/locations/{location}/publishers/google/models/{resolved_model}:generateContent"
                )
                headers = {
                    "Authorization": f"Bearer {self._load_vertex_access_token()}",
                    "Content-Type": "application/json",
                }
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    response_body = response.json()
            elif self.api_key:
                url = (
                    f"{self.endpoint_base_url}/models/{resolved_model}:generateContent"
                    f"?key={self.api_key}"
                )
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, json=payload)
                    response.raise_for_status()
                    response_body = response.json()
            else:
                return None
        except Exception:
            _observe_llm_error(start, provider=resolved_provider, model=resolved_model, stage="render")
            logger.exception(
                "render_from_context failed",
                extra={"conversation_id": context.conversation_id, "step_id": context.current_step_id},
            )
            return None

        _observe_llm_success(
            start,
            provider=resolved_provider,
            model=resolved_model,
            stage="render",
            response_body=response_body,
        )
        text = _extract_gemini_text(response_body)
        rendered = _extract_render_output(text, context)

        # Post-call grounding gate. Score the rendered text against the
        # retrieved chunks; if it falls below the citation threshold,
        # the LLM has drifted away from grounded sources — return
        # ``None`` so the caller falls back to the deterministic reply.
        # This is Google's ``check-grounding`` analog, implemented as a
        # zero-latency token-overlap heuristic.
        if (
            grounding_active
            and rendered is not None
            and rendered.text
            and grounding_policy.post_call_check == "heuristic"
        ):
            from .observability.metrics import (
                knowledge_grounding_gate_total,
                knowledge_grounding_score,
            )

            score = score_grounding_overlap(
                rendered.text, list(context.retrieval_evidence)
            )
            knowledge_grounding_score.labels(
                phase="post_call", mode=grounding_policy.mode,
            ).observe(score)
            if score < grounding_policy.min_grounding_score:
                knowledge_grounding_gate_total.labels(
                    phase="post_call",
                    decision="blocked",
                    mode=grounding_policy.mode,
                    reason="below_threshold",
                ).inc()
                logger.info(
                    "post-call grounding gate blocked render: score=%.3f min=%.3f",
                    score,
                    grounding_policy.min_grounding_score,
                    extra={
                        "conversation_id": context.conversation_id,
                        "step_id": context.current_step_id,
                        "grounding_mode": grounding_policy.mode,
                    },
                )
                return None
            knowledge_grounding_gate_total.labels(
                phase="post_call",
                decision="allowed",
                mode=grounding_policy.mode,
                reason="passed",
            ).inc()
        return rendered

    def _load_vertex_access_token(self) -> str:
        try:
            import google.auth  # type: ignore[import-untyped]
            from google.auth.transport.requests import Request  # type: ignore[import-untyped]
        except Exception as exc:  # pragma: no cover - import failure is environment-specific
            raise RuntimeError("google-auth runtime is required for Vertex ADC mode") from exc

        credentials = self._vertex_credentials
        if credentials is None:
            resolved_credentials, _ = google.auth.default(scopes=[_VERTEX_SCOPE])
            credentials = resolved_credentials
            self._vertex_credentials = credentials
        token = getattr(credentials, "token", None)
        valid = bool(getattr(credentials, "valid", False))
        if not valid or not token:
            credentials.refresh(Request())
            token = getattr(credentials, "token", None)
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("unable to obtain Vertex access token from ADC")
        return token


def build_response_generator_from_env() -> ResponseGenerator | None:
    api_key = (os.getenv("RUHU_GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip() or None
    auth_mode = (os.getenv("RUHU_DIALOGUE_AUTH_MODE") or "").strip().lower()
    explicit_use_vertex = _parse_bool(os.getenv("RUHU_DIALOGUE_USE_VERTEX"), default=False)
    use_vertex = explicit_use_vertex or auth_mode == "vertex_adc"
    vertex_project = (
        (os.getenv("RUHU_VERTEX_AI_PROJECT") or "").strip()
        or (os.getenv("VERTEX_AI_PROJECT") or "").strip()
        or None
    )
    vertex_location = (
        (os.getenv("RUHU_VERTEX_AI_LOCATION") or "").strip()
        or (os.getenv("VERTEX_AI_LOCATION") or "").strip()
        or "europe-west2"
    )
    if not api_key and not vertex_project:
        logger.warning("response generator enabled but no Gemini API key or Vertex project configured")
        return None
    if use_vertex and not vertex_project:
        logger.warning("response generator vertex mode requested but VERTEX_AI_PROJECT is not configured")
        return None
    model = (os.getenv("RUHU_DIALOGUE_DEFAULT_MODEL") or "gemini-3-flash-preview").strip()
    # 12s is too tight for Gemini Flash cold-start (observed timeouts);
    # 20s matches the classifier timeout and gives the model room to
    # respond on first-call. Operators tighten via env when needed.
    timeout_seconds = _parse_float(os.getenv("RUHU_DIALOGUE_TIMEOUT_SECONDS"), default=20.0)
    max_output_tokens = _parse_int(os.getenv("RUHU_DIALOGUE_MAX_OUTPUT_TOKENS"), default=1024)
    return GeminiDialogueGenerator(
        api_key=api_key,
        default_model=model,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
        use_vertex=use_vertex,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
    )


def _extract_gemini_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        text_parts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        if text_parts:
            return "\n".join(text_parts)
    return None


def _extract_render_output(text: str | None, context: "RenderContext") -> RenderOutput | None:
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    allowed_claims = context.allowed_claim_classes or ["partial"]
    try:
        parsed = json.loads(stripped)
    except Exception:
        return RenderOutput(
            text=stripped,
            claimed_class=allowed_claims[0],
        )
    # Gemini occasionally wraps a single-turn response in a JSON array
    # (`[{"text": "...", "claimed_class": "..."}]`). Unwrap so the
    # downstream extractor still finds ``.text`` instead of leaking the
    # entire raw array as user-visible text.
    if isinstance(parsed, list):
        first_dict = next((item for item in parsed if isinstance(item, dict)), None)
        if first_dict is None:
            return RenderOutput(
                text=stripped,
                claimed_class=allowed_claims[0],
            )
        parsed = first_dict
    if not isinstance(parsed, dict):
        return RenderOutput(
            text=stripped,
            claimed_class=allowed_claims[0],
        )
    rendered_text = str(parsed.get("text") or "").strip()
    if not rendered_text:
        return None
    claimed = str(parsed.get("claimed_class") or "").strip()
    if claimed not in allowed_claims:
        claimed = allowed_claims[0]
    raw_keys = parsed.get("acknowledged_fact_keys") or []
    acknowledged_keys = [
        str(key) for key in raw_keys
        if isinstance(key, (str, int, float)) and str(key).strip()
    ]
    return RenderOutput(
        text=rendered_text,
        claimed_class=claimed,
        acknowledged_fact_keys=acknowledged_keys,
    )


def _extract_first_sentence(text: str) -> str | None:
    """Return the first complete sentence from *text*, or ``None`` if no
    sentence boundary has been reached yet."""
    match = re.search(r"[.!?;:]\s", text)
    if match:
        return text[: match.start() + 1].strip()
    # If text ends with sentence-ending punctuation, treat the whole thing
    # as a complete sentence (common for short responses).
    if text and text.rstrip()[-1] in ".!?":
        return text.strip()
    return None


def _resolve_intent_name(raw_intent: str, valid_intents: dict[str, str]) -> str | None:
    candidate = _normalize_intent_token(raw_intent)
    if not candidate:
        return None
    normalized_valid = {name: _normalize_intent_token(name) for name in valid_intents}
    for name, normalized_name in normalized_valid.items():
        if candidate == normalized_name:
            return name

    token_matches = [
        name
        for name, normalized_name in normalized_valid.items()
        if candidate in normalized_name.split("_") or normalized_name.startswith(f"{candidate}_")
    ]
    if len(token_matches) == 1:
        return token_matches[0]

    substring_matches = [
        name
        for name, normalized_name in normalized_valid.items()
        if candidate in normalized_name or normalized_name in candidate
    ]
    if len(substring_matches) == 1:
        return substring_matches[0]
    return None


def _normalize_intent_token(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_")


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _coerce_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _parse_bool(value, default=default)
    return default


def _stringify_metadata(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _normalize_gemini_model_name(model: str | None) -> str | None:
    resolved = _stringify_metadata(model)
    if resolved is None:
        return None
    if _DEPRECATED_GEMINI_FLASH_PATTERN.match(resolved):
        return "gemini-3-flash-preview"
    if _DEPRECATED_GEMINI_PRO_PATTERN.match(resolved):
        return "gemini-3-pro-preview"
    return resolved


def _resolve_vertex_location(*, model: str, location: str) -> str:
    normalized_location = location.strip() or "europe-west2"
    lowered = model.lower()
    if lowered.startswith("gemini-3") or lowered.startswith("gemini-3."):
        return "global"
    return normalized_location


# ─────────────────────────────────────────────────────────────────────────────
# WI-5 of doc 36: move-selection parser scaffolding (specs 31 / 33 / 34).
#
# These helpers exist as the boundary between the move-selection LLM call and
# the kernel validation pipeline.  They are P1 scaffolding — no production
# call site invokes them yet.  WI-6 (replay harness) records against the
# parser; WI-4 (kernel stubs) raises ``NotImplementedError`` rather than
# wiring them in.
# ─────────────────────────────────────────────────────────────────────────────


_MOVE_SELECTION_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(?P<body>\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)

_TRANSITION_REASON_CODE_ALIASES: dict[str, str] = {
    "booking_request": TransitionReasonCode.USER_REQUESTED_HELP.value,
    "help_request": TransitionReasonCode.USER_REQUESTED_HELP.value,
    "product_question": TransitionReasonCode.USER_CHANGED_TOPIC.value,
    "pricing_question": TransitionReasonCode.USER_CHANGED_TOPIC.value,
    "tool_failure_pivot": TransitionReasonCode.USER_CHANGED_TOPIC.value,
    "close": TransitionReasonCode.USER_INDICATED_COMPLETION.value,
    "fact_collection_complete": TransitionReasonCode.USER_PROVIDED_REQUESTED_FACT.value,
    "email_collected": TransitionReasonCode.USER_PROVIDED_REQUESTED_FACT.value,
    "provided_requested_fact": TransitionReasonCode.USER_PROVIDED_REQUESTED_FACT.value,
}


def _normalize_transition_reason_code(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized.startswith("intent_detected:"):
        normalized = normalized.split(":", 1)[1]
    if normalized in TransitionReasonCode._value2member_map_:
        return normalized
    return _TRANSITION_REASON_CODE_ALIASES.get(normalized, normalized)


def _normalize_move_selection_dict(payload: object) -> object:
    if isinstance(payload, list):
        return [_normalize_move_selection_dict(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    normalized = {
        key: _normalize_move_selection_dict(value)
        for key, value in payload.items()
    }

    proposed = normalized.get("proposed_transition")
    if isinstance(proposed, dict):
        proposed["reason_code"] = _normalize_transition_reason_code(
            proposed.get("reason_code")
        )
        if "confidence" not in proposed or proposed.get("confidence") in (None, ""):
            move_confidence = normalized.get("confidence")
            proposed["confidence"] = move_confidence if isinstance(move_confidence, (int, float)) else 0.8

    if "move_type" in normalized and ("confidence" not in normalized or normalized.get("confidence") in (None, "")):
        proposed_confidence = None
        if isinstance(proposed, dict):
            candidate = proposed.get("confidence")
            if isinstance(candidate, (int, float)):
                proposed_confidence = candidate
        normalized["confidence"] = proposed_confidence if proposed_confidence is not None else 0.8

    return normalized


def parse_move_selection_output(raw: str) -> MoveSelectionOutput:
    """Parse a raw LLM string into a validated ``MoveSelectionOutput``.

    Accepts:
    - bare JSON: ``{"selection": {...}}``
    - JSON wrapped in fenced block: ``\u0060\u0060\u0060json\\n{...}\\n\u0060\u0060\u0060``

    Raises ``ValueError`` with structured detail on parse or validation
    failure.  Mirrors the failure-mode behavior of ``_extract_render_output``
    but is strict (no graceful fallback) because move selection drives
    structural commits — an unparseable output should be rejected, not
    silently degraded to a free-text response.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("move-selection output is empty")

    body = raw.strip()
    fence_match = _MOVE_SELECTION_FENCE_PATTERN.search(body)
    if fence_match is not None:
        body = fence_match.group("body").strip()

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"move-selection output is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f"move-selection output must be a JSON object, got {type(parsed).__name__}"
        )

    normalized = _normalize_move_selection_dict(parsed)

    try:
        return MoveSelectionOutput.model_validate(normalized)
    except _PydanticValidationError as exc:
        raise ValueError(f"move-selection output failed schema validation: {exc}") from exc


_MOVE_TYPE_DESCRIPTIONS: dict[str, str] = {
    "answer":                "Answer the user's question without changing steps.",
    "clarify":               "Ask for clarification when user input is ambiguous.",
    "acknowledge":           "Acknowledge the user without taking further action.",
    "pause":                 "Stay quiet (e.g. user said 'hold on'); no step change.",
    "repair":                "Reconcile a misunderstanding (e.g. 'I already gave it to you').",
    "smalltalk_and_return":  "Engage briefly in small talk then return to the task.",
    "ask_for_missing_info":  "Re-ask for the missing fact required to advance.",
    "propose_transition":    "Move the conversation to another step in the workflow.",
    "propose_tool_use":      "Invoke a tool. (Out of scope for this turn.)",
    "apologize":             "Apologize when the assistant erred or for delay.",
    "thank":                 "Thank the user (only after acknowledging or answering first).",
    "confirm_understanding": "Confirm what the user said before structural commit.",
}

def _step_capability_prompt_label(
    capabilities: StepCapabilities | dict[str, object] | None,
) -> str | None:
    if capabilities is None:
        return None
    data = capabilities if isinstance(capabilities, dict) else capabilities.model_dump()
    labels: list[str] = []
    if data.get("completes"):
        labels.append("resolve the request")
    if data.get("hands_off"):
        labels.append("route to another destination")
    if data.get("uses_tooling"):
        labels.append("use tools or run side effects")
    if data.get("collects_missing_details"):
        labels.append("ask for required details")
    if not labels:
        return "respond in place"
    return ", ".join(labels)


def build_move_selection_prompt(context: MoveSelectionContext) -> str:
    """Render a move-selection prompt from a typed decision surface.

    P3 of doc 39 (WI-5): real prompt template covering the full move
    vocabulary and recent tool outcomes.  Output instructions describe
    both ``MoveSelection`` and ``MoveSequence`` shapes.

    Token budget: ≤2000 tokens for the largest realistic context.
    """
    lines: list[str] = []
    lines.append("# Move Selection Task")
    lines.append("")
    lines.append(
        "You are deciding the next assistant move. "
        "Pick a single move, or compose up to 3 moves into a sequence."
    )
    lines.append("")

    journey = context.journey_context

    # Step context
    lines.append("## Step")
    step_focus = _step_capability_prompt_label(context.current_step_capabilities)
    lines.append(
        f"- id: `{context.current_step_id}`"
        + (f" / name: `{context.current_step_name}`" if context.current_step_name else "")
        + (f" / focus: {step_focus}" if step_focus else "")
    )
    lines.append(f"- step summary: {context.current_step_goal}")
    if context.transition_targets:
        lines.append("- transition targets:")
        for target_id in context.transition_targets:
            summary = context.transition_target_summaries.get(target_id)
            if summary:
                lines.append(f"  - `{target_id}` — {summary}")
            else:
                lines.append(f"  - `{target_id}`")
    if context.tool_affordances:
        lines.append(f"- tool affordances: {list(context.tool_affordances)}")
    lines.append("")

    if journey is not None:
        lines.append("## Journey Context")
        if journey.previous_step_name:
            lines.append(f"- previous step: `{journey.previous_step_name}`")
        if journey.transition_natural_reason:
            lines.append(f"- transition reason: {journey.transition_natural_reason}")
        if journey.current_step_purpose:
            lines.append(f"- current step purpose: {journey.current_step_purpose}")
        lines.append(f"- topic freshness: `{journey.topic_freshness}`")
        if journey.pending_facts:
            lines.append("- pending facts:")
            for fact_name, pending in journey.pending_facts.items():
                details = f"purpose={pending.purpose}"
                if pending.triggered_by:
                    details += f"; triggered_by={pending.triggered_by}"
                if pending.triggered_in_step:
                    details += f"; triggered_in_step={pending.triggered_in_step}"
                lines.append(f"  - `{fact_name}` — {details}")
        if journey.route_horizon:
            lines.append("- route horizon:")
            for branch in journey.route_horizon:
                desc = branch.branch_when_to_use or branch.branch_natural_reason or ""
                lines.append(
                    f"  - `{branch.target_step_id}`"
                    + (
                        f" / focus: {_step_capability_prompt_label(branch.target_step_capabilities)}"
                        if _step_capability_prompt_label(branch.target_step_capabilities)
                        else ""
                    )
                    + (f" — {desc}" if desc else "")
                )
        if journey.authored_guidance is not None:
            lines.append("- authored step guidance:")
            guidance = journey.authored_guidance
            for key in (
                "say_on_entry",
                "say_on_transition",
                "ask_for_fact",
                "repair_response",
            ):
                value = getattr(guidance, key)
                if value:
                    lines.append(f"  - {key}: {value}")
        lines.append("")

    lines.append("## Current User Turn")
    lines.append(f"- text: {context.current_user_text or '(empty)'}")
    lines.append("")

    if context.event_hints:
        lines.append("## Step Intents")
        for name, description in context.event_hints.items():
            lines.append(f"- `{name}` — {description}")
        lines.append("")

    # Facts
    if context.required_execution_facts:
        lines.append("## Facts")
        lines.append(
            f"- required: {list(context.required_execution_facts)}"
        )
        lines.append(
            f"- accepted: {context.accepted_facts or {}}"
        )
        lines.append(
            f"- still missing: {list(context.missing_facts)}"
        )
        lines.append("")

    # Pending action
    if context.pending_action_summary:
        lines.append("## Pending Action")
        lines.append(f"- {context.pending_action_summary}")
        lines.append("")

    # Recent tool outcomes
    if context.recent_tool_outcomes:
        lines.append("## Recent Tool Outcomes (most recent first)")
        for rec in context.recent_tool_outcomes:
            lines.append(f"- {rec.output_summary}")
        lines.append("")

    # Recent dialogue
    if context.recent_turn_summaries:
        lines.append("## Recent Turns")
        for summary in context.recent_turn_summaries:
            lines.append(f"- {summary}")
        lines.append("")

    # Move vocabulary
    lines.append("## Allowed Moves")
    if context.allowed_move_types:
        for move in context.allowed_move_types:
            desc = _MOVE_TYPE_DESCRIPTIONS.get(move.value, "")
            lines.append(f"- `{move.value}` — {desc}")
    else:
        lines.append("- (no per-state restriction; choose any documented move)")
    lines.append("")

    # Output instructions
    lines.append("## Output")
    lines.append("Return a JSON object matching one of these shapes:")
    lines.append("")
    lines.append("Single move:")
    lines.append("```json")
    lines.append('{"selection": {"move_type": "...", "rationale": "...", "confidence": 0.0-1.0,')
    lines.append('  "extracted_facts": {...},')
    lines.append('  "proposed_transition": {  // only if move_type is propose_transition')
    lines.append('    "target_step_id": "...", "reason_code": "...",')
    lines.append('    "confidence": 0.0-1.0, "reasoning": "..."}}}')
    lines.append("```")
    lines.append("")
    lines.append("Sequence (up to 3 moves; max one structural commit):")
    lines.append("```json")
    lines.append('{"sequence": {"moves": [...], "combined_response_plan": "...",')
    lines.append('  "sequence_rationale": "..."}}')
    lines.append("```")
    lines.append("")
    lines.append(
        "If you use `propose_transition.reason_code`, it MUST be one of: "
        + ", ".join(code.value for code in TransitionReasonCode)
        + "."
    )
    lines.append(
        "Do not use event names such as `booking_request` or "
        "`product_question` as `reason_code`; convert them to the allowed "
        "reason-code vocabulary."
    )
    lines.append(
        "Every move in a sequence MUST include its own `confidence` field."
    )
    lines.append(
        "Confidence below 0.7 will be rejected.  Do not propose moves "
        "outside the allowed list."
    )
    lines.append(
        "If the current user turn clearly matches one of the state intents and "
        "a transition target would advance the workflow, prefer "
        "`propose_transition` over generic `clarify`."
    )
    lines.append(
        "Prefer answering an explicit user question before starting a new "
        "detail-collection or tool-backed step. Only transition into a step "
        "that collects a missing detail when the user is clearly ready to "
        "provide that detail now, not when they "
        "are asking hypothetically what the assistant can do or why a detail "
        "is needed."
    )
    lines.append(
        "If the current step is waiting on a missing detail and the user asks "
        "why that detail is needed, choose `answer` or `clarify` instead of repeating "
        "the fact request."
    )
    lines.append(
        "Use journey context and authored step guidance when deciding whether "
        "the user is exploring a route or committing to enter it."
    )

    return "\n".join(lines)
