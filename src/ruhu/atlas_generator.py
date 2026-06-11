from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

import httpx
from pydantic import BaseModel, Field

from .atlas_protocol import AtlasBlocker, AtlasProposedChanges, BlockingQuestion
from .observability.metrics import (
    atlas_generator_fallback_total,
    atlas_generator_request_duration_seconds,
    atlas_generator_requests_total,
    safe_observe,
)

logger = logging.getLogger(__name__)


class AtlasGeneratorOutput(BaseModel):
    proposed_changes: AtlasProposedChanges = Field(default_factory=AtlasProposedChanges)
    generator_blockers: list[AtlasBlocker] = Field(default_factory=list)
    blocking_questions: list[BlockingQuestion] = Field(default_factory=list)
    assistant_rationale: str | None = None
    generation_mode: Literal["anthropic", "fallback"] = "fallback"
    generation_model: str | None = None


class AtlasGeneratorContext(BaseModel):
    agent_id: str
    scope: str
    user_message: str | None = None
    selected_scenario_id: str | None = None
    selected_scenario_name: str | None = None
    selected_step_id: str | None = None
    selected_step_name: str | None = None
    scenario_ids: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    fact_names: list[str] = Field(default_factory=list)
    tool_refs: list[str] = Field(default_factory=list)
    # Grounding bundle (Generator-Spec §3.1): enough detail for the model to
    # answer review questions and target deltas without hallucinating ids.
    selected_step_detail: dict[str, Any] = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    prior_delta_summaries: list[str] = Field(default_factory=list)
    recent_messages: list[dict[str, str]] = Field(default_factory=list)
    # Set on the single repair attempt after a failed semantic validation
    # (Generator-Spec §6.1); contains the blocker text the model must address.
    repair_feedback: str | None = None


def _extract_anthropic_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    texts = [
        item.get("text", "").strip()
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    texts = [item for item in texts if item]
    return "\n".join(texts) if texts else None


def _extract_json_object(text: str) -> str | None:
    """Pull the first balanced JSON object out of ``text``.

    Modern Claude models often wrap JSON in markdown fences (```json ... ```)
    or pad it with conversational prose ("Here is the response: { ... }").
    The previous parser ran ``json.loads`` over the entire body, so any
    decoration broke generation. This helper:

    1. Strips a ```json / ``` opening fence and matching close, if present.
    2. Failing that, walks the string finding the first ``{`` and the
       balanced ``}`` that closes it (respecting strings + escapes).

    Returns the JSON object text, or None if no object can be located.
    """
    stripped = text.strip()
    if not stripped:
        return None

    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", stripped, re.DOTALL | re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        # Only trust the fenced content when it actually begins a JSON object.
        # A fence wrapping prose (e.g. "Here it is: {...}") would otherwise be
        # returned verbatim, fail json.loads, and waste a repair round-trip —
        # fall through to the brace-scan below instead.
        if candidate.startswith("{"):
            return candidate

    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    return None


@dataclass
class AtlasProposalGenerator:
    # The heuristic fallback receives the per-request compiled document
    # explicitly; the generator holds no per-request state, so one instance is
    # safe to share across concurrent turns.
    fallback_generate: Callable[[AtlasGeneratorContext, Any], AtlasProposedChanges]
    api_key: str | None = None
    model: str = "claude-sonnet-4-6"
    timeout_seconds: float = 12.0
    endpoint_base_url: str = "https://api.anthropic.com/v1/messages"
    anthropic_version: str = "2023-06-01"
    max_retries: int = 2
    retry_backoff_seconds: float = 0.25

    def _observe_request(self, *, outcome: str, started_at: float) -> None:
        duration = max(0.0, time.monotonic() - started_at)
        safe_observe(
            "atlas_generator_requests_total",
            atlas_generator_requests_total.labels(provider="anthropic", model=self.model, outcome=outcome).inc,
        )
        safe_observe(
            "atlas_generator_request_duration_seconds",
            atlas_generator_request_duration_seconds.labels(
                provider="anthropic", model=self.model, outcome=outcome
            ).observe,
            duration,
        )

    def _observe_fallback(self, *, reason: str) -> None:
        safe_observe(
            "atlas_generator_fallback_total",
            atlas_generator_fallback_total.labels(reason=reason).inc,
        )

    @classmethod
    def from_env(
        cls,
        *,
        fallback_generate: Callable[[AtlasGeneratorContext, Any], AtlasProposedChanges],
    ) -> "AtlasProposalGenerator":
        def _env_float(name: str, default: float) -> float:
            raw = (os.getenv(name) or "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning("invalid %s=%r; using default %s", name, raw, default)
                return default

        def _env_int(name: str, default: int) -> int:
            raw = (os.getenv(name) or "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("invalid %s=%r; using default %s", name, raw, default)
                return default

        api_key = (
            (os.getenv("RUHU_ATLAS_GENERATOR_API_KEY") or "").strip()
            or (os.getenv("ANTHROPIC_API_KEY") or "").strip()
            or None
        )
        model = (os.getenv("RUHU_ATLAS_GENERATOR_MODEL") or "claude-sonnet-4-6").strip()
        timeout_seconds = _env_float("RUHU_ATLAS_GENERATOR_TIMEOUT_SECONDS", 12.0)
        anthropic_version = (os.getenv("RUHU_ATLAS_GENERATOR_ANTHROPIC_VERSION") or "2023-06-01").strip()
        max_retries = _env_int("RUHU_ATLAS_GENERATOR_MAX_RETRIES", 2)
        retry_backoff_seconds = _env_float("RUHU_ATLAS_GENERATOR_RETRY_BACKOFF_SECONDS", 0.25)
        return cls(
            fallback_generate=fallback_generate,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            anthropic_version=anthropic_version,
            max_retries=max(0, max_retries),
            retry_backoff_seconds=max(0.0, retry_backoff_seconds),
        )

    def generate(
        self,
        context: AtlasGeneratorContext,
        *,
        compiled_document: Any = None,
    ) -> AtlasGeneratorOutput:
        if not (context.user_message or "").strip():
            self._observe_fallback(reason="empty_user_message")
            return AtlasGeneratorOutput(
                proposed_changes=AtlasProposedChanges(),
                generator_blockers=[],
                assistant_rationale=None,
                generation_mode="fallback",
                generation_model=None,
            )
        if self.api_key:
            generated = self._generate_with_anthropic(context)
            if generated is not None:
                return generated
            self._observe_fallback(reason="provider_failure")
            logger.warning(
                "atlas generator falling back after anthropic generation failure",
                extra={"agent_id": context.agent_id, "scope": context.scope, "model": self.model},
            )
        else:
            self._observe_fallback(reason="missing_api_key")
        return AtlasGeneratorOutput(
            proposed_changes=self.fallback_generate(context, compiled_document),
            generation_mode="fallback",
            generation_model=None,
        )

    def _call_model(self, prompt: str, *, context: AtlasGeneratorContext) -> str | None:
        """One Anthropic Messages call with transient-status retries.

        Returns the extracted text body, or None after exhausting retries.
        """
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": 0.1,
            "system": (
                "You are Atlas, an AgentDocument-native copilot. "
                "Return only valid JSON matching the requested output contract."
            ),
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": str(self.api_key),
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                    response = client.post(
                        self.endpoint_base_url,
                        json=payload,
                        headers=headers,
                    )
                    if response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
                        raise httpx.HTTPStatusError(
                            f"transient atlas generator status {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    return _extract_anthropic_text(response.json())
            except Exception as exc:
                # Only retry transient failures. A non-transient 4xx (400/401/
                # 403) will never recover, so retrying it just burns latency on
                # every turn — fail fast instead.
                transient = isinstance(
                    exc, (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)
                ) or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response is not None
                    and exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                )
                if attempt >= self.max_retries or not transient:
                    logger.exception(
                        "atlas generator anthropic call failed",
                        extra={"agent_id": context.agent_id, "scope": context.scope, "model": self.model},
                    )
                    return None
                time.sleep(self.retry_backoff_seconds * (attempt + 1))
        return None

    def _parse_output(self, text: str, *, context: AtlasGeneratorContext) -> tuple[AtlasGeneratorOutput | None, str]:
        """Parse a model response into AtlasGeneratorOutput.

        Returns (output, error_description); output is None when parsing failed
        and error_description carries the reason for the repair prompt.
        """
        json_block = _extract_json_object(text)
        if json_block is None:
            return None, "no JSON object could be located in the response"
        try:
            parsed = json.loads(json_block)
        except Exception as exc:
            return None, f"the JSON object failed to decode: {exc}"
        try:
            return AtlasGeneratorOutput.model_validate(parsed), ""
        except Exception as exc:
            return None, f"the JSON did not match the output contract: {exc}"

    def _generate_with_anthropic(self, context: AtlasGeneratorContext) -> AtlasGeneratorOutput | None:
        started_at = time.monotonic()
        prompt = self._build_prompt(context)
        text = self._call_model(prompt, context=context)
        if text is None:
            self._observe_request(outcome="error", started_at=started_at)
            return None
        if not text:
            logger.warning(
                "atlas generator anthropic returned no text payload",
                extra={"agent_id": context.agent_id, "scope": context.scope, "model": self.model},
            )
            self._observe_request(outcome="empty", started_at=started_at)
            return None
        output, parse_error = self._parse_output(text, context=context)
        if output is None:
            # Structured-generation repair: at most one repair attempt
            # (Generator-Spec §6.1) feeding back the invalid response and the
            # parse error before falling back.
            logger.warning(
                "atlas generator response unparseable; attempting one repair pass",
                extra={
                    "agent_id": context.agent_id,
                    "scope": context.scope,
                    "model": self.model,
                    "parse_error": parse_error,
                    "response_preview": text[:500],
                },
            )
            repair_prompt = (
                f"{prompt}\n\n"
                "Your previous response could not be used because "
                f"{parse_error}.\n"
                "Previous response (do not repeat its mistakes):\n"
                f"{text[:4000]}\n\n"
                "Return ONLY the corrected JSON object matching the contract above. No prose, no fences."
            )
            repaired_text = self._call_model(repair_prompt, context=context)
            if repaired_text:
                output, parse_error = self._parse_output(repaired_text, context=context)
            if output is None:
                logger.warning(
                    "atlas generator repair pass failed",
                    extra={
                        "agent_id": context.agent_id,
                        "scope": context.scope,
                        "model": self.model,
                        "parse_error": parse_error,
                    },
                )
                self._observe_request(outcome="parse_error", started_at=started_at)
                return None
        rationale = (output.assistant_rationale or "").strip()
        logger.info(
            "atlas generator anthropic generation succeeded",
            extra={
                "agent_id": context.agent_id,
                "scope": context.scope,
                "model": self.model,
                "delta_count": sum(
                    len(group)
                    for group in [
                        output.proposed_changes.agent_metadata_deltas,
                        output.proposed_changes.scenario_deltas,
                        output.proposed_changes.step_deltas,
                        output.proposed_changes.scenario_route_deltas,
                        output.proposed_changes.channel_policy_deltas,
                        output.proposed_changes.rule_deltas,
                        output.proposed_changes.knowledge_deltas,
                        output.proposed_changes.integration_binding_deltas,
                    ]
                ),
                "rationale_length": len(rationale),
                "rationale_preview": rationale[:200] if rationale else "(empty)",
            },
        )
        self._observe_request(outcome="success", started_at=started_at)
        return output.model_copy(update={"generation_mode": "anthropic", "generation_model": self.model})

    def _build_prompt(self, context: AtlasGeneratorContext) -> str:
        return (
            "You are Atlas, an AgentDocument-native copilot.\n"
            "Return only JSON matching this shape:\n"
            "{"
            '"assistant_rationale": string|null, '
            '"generator_blockers": [{"code": string, "message": string, "blocking": true, "reference_ids": [string]}], '
            '"blocking_questions": [{"question_id": string, "question": string, "help_text": string|null, '
            '"options": [string]|null, "required": true, "target_ref": string|null}], '
            '"proposed_changes": {'
            '"agent_metadata_deltas": [], "scenario_deltas": [], "step_deltas": [], '
            '"scenario_route_deltas": [], "channel_policy_deltas": [], "rule_deltas": [], '
            '"knowledge_deltas": [], "integration_binding_deltas": []'
            "}"
            "}\n"
            "Do not emit prose outside JSON.\n"
            "Use the exact AtlasProposedChanges delta families and only propose authored AgentDocument changes.\n"
            "Every delta's depends_on_delta_ids may only reference delta_ids that appear in this same response.\n"
            "\n"
            "When a change request is actionable but missing one or two specific decisions you cannot\n"
            "safely default (which step, which value, which of several plausible targets), emit\n"
            "blocking_questions instead of guessing — one entry per decision, with options when the\n"
            "choices are enumerable. Use generator_blockers only when the request cannot proceed at all.\n"
            "\n"
            "ALWAYS populate assistant_rationale with a concise, conversational reply (2-5 sentences) that\n"
            "speaks directly to the user. The rationale is shown verbatim as Atlas's chat response, so it\n"
            "must read like a copilot talking, not like a JSON note. Reference the selected scenario or step\n"
            "by name when relevant, summarize what you did or why you didn't, and call out anything the user\n"
            "should look at next.\n"
            "\n"
            "When the request asks for changes (add/rename/delete/wire/configure something specific),\n"
            "produce the appropriate deltas AND describe them in assistant_rationale.\n"
            "\n"
            "When the request is informational (review, explain, summarize, what does X do, why is Y blocked),\n"
            "produce no deltas, leave generator_blockers empty, and use assistant_rationale to deliver an\n"
            "actually useful answer grounded in the agent context provided below.\n"
            "\n"
            "Only use generator_blockers when the user clearly asked for a change but the request is too\n"
            "ambiguous to act on safely; even then, also use assistant_rationale to tell the user what's\n"
            "missing.\n"
            "\n"
            "Examples:\n"
            "1. Linked-delta example:\n"
            'User request: add fact "company_size" of type string and require fact "company_size"\n'
            "Expected behavior: emit an add_fact_schema_entry delta and an add_fact_requirement delta.\n"
            "The add_fact_requirement delta must list the fact-creation delta id in depends_on_delta_ids.\n"
            'Example JSON fragment: {"assistant_rationale":"Added a `company_size` string fact and required it on the selected step. Approve the changes to apply them.","proposed_changes":{"agent_metadata_deltas":[{"change_type":"add_fact_schema_entry","delta_id":"delta_add_company_size","operation":"create","status":"proposed","payload":{"fact":{"name":"company_size","type":"string"}},"summary":"Add fact company_size to the fact schema."}],"step_deltas":[{"change_type":"add_fact_requirement","delta_id":"delta_require_company_size","operation":"update","status":"proposed","depends_on_delta_ids":["delta_add_company_size"],"payload":{"fact_requirement":{"name":"company_size"}},"summary":"Require fact company_size in the selected step."}]}}\n'
            "2. Create-and-wire example:\n"
            'User request: add a step called "book demo" and add a transition to that step on the outcome event "book_demo" with description "User asks to book a demo."\n'
            "Expected behavior: create the new step and then add the transition. The transition may depend on the created step when needed.\n"
            "3. Review/explanation example (no deltas):\n"
            'User request: review the agent\n'
            'Expected behavior: emit no proposed_changes and no generator_blockers. Use assistant_rationale to summarize the agent: which scenarios exist, which step is selected, what the step does, and any concrete observations or next-step suggestions grounded in the context provided. Avoid generic phrases like "I reviewed it and found no issues" unless that\'s genuinely all there is to say — even then, mention what you looked at.\n'
            'Example JSON fragment: {"assistant_rationale":"Sales Agent has 4 scenarios; you\'re focused on \'Discover\' step \'Entry\'. The step has no transitions yet, so the agent will end the conversation immediately after entry. If the goal is to qualify leads, the next move is adding a fact-collection step before the entry transitions.","generator_blockers":[],"proposed_changes":{"agent_metadata_deltas":[],"scenario_deltas":[],"step_deltas":[],"scenario_route_deltas":[],"channel_policy_deltas":[],"rule_deltas":[],"knowledge_deltas":[],"integration_binding_deltas":[]}}\n'
            "4. Abstain example (change request, but ambiguous):\n"
            'User request: improve this flow\n'
            "Expected behavior: emit no proposed_changes. If one or two concrete decisions would unblock the work, emit blocking_questions for them (e.g. question 'Which outcome should the new transition cover?' with options drawn from the known steps). Otherwise populate generator_blockers explaining what missing specificity Atlas needs. In both cases use assistant_rationale to relay that to the user conversationally.\n"
            "5. Destructive example:\n"
            'User request: delete step "discover"\n'
            "Expected behavior: propose a delete_step delta only when the target is explicit; do not invent deletes. assistant_rationale should confirm what will be deleted and surface any downstream impact.\n\n"
            f"Agent: {context.agent_id}\n"
            f"Scope: {context.scope}\n"
            f"Selected scenario: {context.selected_scenario_id or ''} ({context.selected_scenario_name or ''})\n"
            f"Selected step: {context.selected_step_id or ''} ({context.selected_step_name or ''})\n"
            f"Selected step detail (authored fields): {json.dumps(context.selected_step_detail, default=str) if context.selected_step_detail else '(none)'}\n"
            f"Known scenarios: {', '.join(context.scenario_ids)}\n"
            f"Known steps: {', '.join(context.step_ids)}\n"
            f"Known facts: {', '.join(context.fact_names)}\n"
            f"Known tools: {', '.join(context.tool_refs)}\n"
            f"Current validation errors: {'; '.join(context.validation_errors) or '(none)'}\n"
            f"Current validation warnings: {'; '.join(context.validation_warnings) or '(none)'}\n"
            f"Changes already proposed in this session: {'; '.join(context.prior_delta_summaries) or '(none)'}\n"
            + (
                "Recent conversation:\n"
                + "".join(
                    f"  {item.get('role', 'user')}: {(item.get('content') or '')[:400]}\n"
                    for item in context.recent_messages
                )
                if context.recent_messages
                else ""
            )
            + (
                "\nREPAIR PASS: your previous proposal failed semantic validation against the draft "
                "document. Address every issue below; drop any delta you cannot fix safely.\n"
                f"Validation feedback: {context.repair_feedback}\n"
                if context.repair_feedback
                else ""
            )
            + f"User request: {context.user_message or ''}\n"
        )
