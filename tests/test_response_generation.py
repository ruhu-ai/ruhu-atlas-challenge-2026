from __future__ import annotations

import json

import httpx
import pytest

from ruhu.response_generation import (
    GeminiDialogueGenerator,
    MoveSelectionRequest,
    ResponseGenerationContext,
    ResponseGenerationRequest,
    _extract_render_output,
    _resolve_intent_name,
    build_move_selection_prompt,
    build_response_generator_from_env,
    parse_move_selection_output,
)
from ruhu.schemas import (
    AuthoredStepGuidance,
    JourneyContext,
    MoveSelection,
    MoveSelectionContext,
    MoveSequence,
    MoveType,
    PendingFactContext,
    RenderContext,
    RouteBranch,
)


def _request(
    *,
    provider: str,
    model: str = "gemini-3-flash-preview",
    metadata: dict[str, object] | None = None,
) -> ResponseGenerationRequest:
    return ResponseGenerationRequest(
        conversation_id="conv-1",
        organization_id="org-1",
        agent_id="sales",
        agent_version_id="gv-1",
        step_id="s1",
        step_name="Triage",
        step_summary="Collect requirements",
        channel="web_widget",
        event_type="user_message",
        user_text="Need invoice help",
        fallback_text="Let me check that for you.",
        context=ResponseGenerationContext(
            provider=provider,
            model=model,
            system_prompt="Be concise.",
            metadata={} if metadata is None else metadata,
        ),
    )


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _render_context() -> RenderContext:
    return RenderContext(
        conversation_id="conv-1",
        organization_id="org-1",
        agent_id="sales",
        response_mode="answer_question",
        channel="web_widget",
        journey=JourneyContext(
            current_step_id="s1",
            current_step_type="conversation",
            current_step_name="Triage",
            current_step_purpose="Collect requirements",
            current_user_text="Need invoice help",
        ),
    )


def _move_selection_request(
    *,
    provider: str,
    model: str = "gemini-3-flash-preview",
    metadata: dict[str, object] | None = None,
    prompt: str = '{"selection":{"move_type":"answer","rationale":"user asked a product question","confidence":0.9}}',
) -> MoveSelectionRequest:
    return MoveSelectionRequest(
        conversation_id="conv-1",
        organization_id="org-1",
        agent_id="sales",
        agent_version_id="gv-1",
        step_id="discover",
        step_name="Discover",
        step_summary="Understand the user's goal and route naturally",
        channel="web_widget",
        event_type="user_message",
        user_text="I want to book a demo.",
        prompt=prompt,
        context=ResponseGenerationContext(
            provider=provider,
            model=model,
            system_prompt="Return only valid JSON.",
            metadata={} if metadata is None else metadata,
        ),
    )


def test_gemini_generator_uses_api_key_route(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_post(self, url, json, params=None, headers=None):  # noqa: ANN001
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        assert json["contents"][0]["parts"][0]["text"]
        return _Response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "Generated API key answer"}]}},
                ]
            }
        )

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    generator = GeminiDialogueGenerator(api_key="test-key")

    result = generator.generate(_request(provider="gemini"))

    assert result == "Generated API key answer"
    assert str(captured["url"]).endswith("/models/gemini-3-flash-preview:generateContent")
    assert captured["params"] == {"key": "test-key"}
    assert captured["headers"] is None


def test_gemini_generator_records_request_metrics_on_generate(monkeypatch) -> None:
    observed: list[dict[str, object]] = []

    def _fake_post(self, url, json, params=None, headers=None):  # noqa: ANN001
        return _Response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "Generated API key answer"}]}},
                ],
                "usageMetadata": {"promptTokenCount": 123, "candidatesTokenCount": 45},
            }
        )

    def _record_success(start, *, provider, model, stage, response_body=None):  # noqa: ANN001
        observed.append(
            {
                "provider": provider,
                "model": model,
                "stage": stage,
                "response_body": response_body,
            }
        )

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    monkeypatch.setattr("ruhu.response_generation._observe_llm_success", _record_success)
    generator = GeminiDialogueGenerator(api_key="test-key")

    result = generator.generate(_request(provider="gemini"))

    assert result == "Generated API key answer"
    assert observed == [
        {
            "provider": "gemini",
            "model": "gemini-3-flash-preview",
            "stage": "generate",
            "response_body": {
                "candidates": [{"content": {"parts": [{"text": "Generated API key answer"}]}}],
                "usageMetadata": {"promptTokenCount": 123, "candidatesTokenCount": 45},
            },
        }
    ]


def test_gemini_generator_uses_vertex_route_for_vertex_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_post(self, url, json, params=None, headers=None):  # noqa: ANN001
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        assert json["contents"][0]["parts"][0]["text"]
        return _Response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "Generated Vertex answer"}]}},
                ]
            }
        )

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    monkeypatch.setattr(
        "ruhu.response_generation.GeminiDialogueGenerator._load_vertex_access_token",
        lambda self: "vertex-access-token",
    )
    generator = GeminiDialogueGenerator(
        api_key=None,
        use_vertex=True,
        vertex_project="ruhu-ai-dev",
        vertex_location="europe-west2",
    )

    result = generator.generate(_request(provider="vertex"))

    assert result == "Generated Vertex answer"
    assert "/projects/ruhu-ai-dev/locations/global/publishers/google/models/gemini-3-flash-preview:generateContent" in str(
        captured["url"]
    )
    assert captured["params"] is None
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer vertex-access-token"


def test_gemini_generator_skips_vertex_request_when_project_missing(monkeypatch) -> None:
    def _fail_post(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("HTTP call should not run without a Vertex project")

    monkeypatch.setattr(httpx.Client, "post", _fail_post)
    generator = GeminiDialogueGenerator(use_vertex=True, vertex_project=None)

    result = generator.generate(_request(provider="vertex"))

    assert result is None


def test_gemini_render_from_context_records_request_metrics(monkeypatch) -> None:
    observed: list[dict[str, object]] = []

    def _fake_post(self, url, json, params=None, headers=None):  # noqa: ANN001
        return _Response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '{"text":"Rendered reply","claimed_class":"partial"}'}]}},
                ],
                "usageMetadata": {"promptTokenCount": 60, "candidatesTokenCount": 9},
            }
        )

    def _record_success(start, *, provider, model, stage, response_body=None):  # noqa: ANN001
        observed.append({"provider": provider, "model": model, "stage": stage, "response_body": response_body})

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    monkeypatch.setattr("ruhu.response_generation._observe_llm_success", _record_success)
    generator = GeminiDialogueGenerator(api_key="test-key")

    result = generator.render_from_context(_render_context(), provider="gemini")

    assert result is not None
    assert result.text == "Rendered reply"
    assert observed and observed[0]["stage"] == "render"


def test_gemini_select_move_uses_api_key_route(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_post(self, url, json, params=None, headers=None):  # noqa: ANN001
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        assert json["contents"][0]["parts"][0]["text"]
        assert json["generationConfig"]["responseMimeType"] == "application/json"
        return _Response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '{"selection":{"move_type":"answer","rationale":"help the user","confidence":0.82}}'}]}},
                ]
            }
        )

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    generator = GeminiDialogueGenerator(api_key="test-key")

    result = generator.select_move(_move_selection_request(provider="gemini"))

    assert result == '{"selection":{"move_type":"answer","rationale":"help the user","confidence":0.82}}'
    assert str(captured["url"]).endswith("/models/gemini-3-flash-preview:generateContent")
    assert captured["params"] == {"key": "test-key"}
    assert captured["headers"] is None


def test_gemini_select_move_records_request_metrics(monkeypatch) -> None:
    observed: list[dict[str, object]] = []

    def _fake_post(self, url, json, params=None, headers=None):  # noqa: ANN001
        return _Response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '{"selection":{"move_type":"answer","rationale":"help the user","confidence":0.82}}'}]}},
                ],
                "usageMetadata": {"promptTokenCount": 77, "candidatesTokenCount": 21},
            }
        )

    def _record_success(start, *, provider, model, stage, response_body=None):  # noqa: ANN001
        observed.append({"provider": provider, "model": model, "stage": stage, "response_body": response_body})

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    monkeypatch.setattr("ruhu.response_generation._observe_llm_success", _record_success)
    generator = GeminiDialogueGenerator(api_key="test-key")

    result = generator.select_move(_move_selection_request(provider="gemini"))

    assert result is not None
    assert observed == [
        {
            "provider": "gemini",
            "model": "gemini-3-flash-preview",
            "stage": "move_select",
            "response_body": {
                "candidates": [
                    {"content": {"parts": [{"text": '{"selection":{"move_type":"answer","rationale":"help the user","confidence":0.82}}'}]}},
                ],
                "usageMetadata": {"promptTokenCount": 77, "candidatesTokenCount": 21},
            },
        }
    ]


def test_build_response_generator_from_env_supports_vertex_adc(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_DIALOGUE_AUTH_MODE", "vertex_adc")
    monkeypatch.setenv("VERTEX_AI_PROJECT", "ruhu-ai-dev")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "europe-west2")
    monkeypatch.delenv("RUHU_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    generator = build_response_generator_from_env()

    assert isinstance(generator, GeminiDialogueGenerator)
    assert generator.use_vertex is True
    assert generator.vertex_project == "ruhu-ai-dev"
    assert generator.vertex_location == "europe-west2"
    assert generator.api_key is None


def test_build_response_generator_from_env_requires_any_auth_path(monkeypatch) -> None:
    monkeypatch.delenv("RUHU_DIALOGUE_AUTH_MODE", raising=False)
    monkeypatch.delenv("RUHU_DIALOGUE_USE_VERTEX", raising=False)
    monkeypatch.delenv("RUHU_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("RUHU_VERTEX_AI_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_AI_PROJECT", raising=False)

    assert build_response_generator_from_env() is None


def test_resolve_intent_name_accepts_exact_and_short_aliases() -> None:
    valid_intents = {
        "demo_request": "User wants a product demo.",
        "pricing_question": "User asks about pricing.",
        "support_request": "User needs support.",
    }

    assert _resolve_intent_name("demo_request", valid_intents) == "demo_request"
    assert _resolve_intent_name("demo", valid_intents) == "demo_request"
    assert _resolve_intent_name("pricing", valid_intents) == "pricing_question"
    assert _resolve_intent_name("support", valid_intents) == "support_request"
    assert _resolve_intent_name("unknown", valid_intents) is None


# ─────────────────────────────────────────────────────────────────────────────
# WI-5 of doc 36: move-selection parser scaffolding tests.
# ─────────────────────────────────────────────────────────────────────────────


def _selection_payload() -> dict:
    return {
        "selection": {
            "move_type": "answer",
            "rationale": "user asked a side question",
            "confidence": 0.85,
        }
    }


def _sequence_payload() -> dict:
    return {
        "sequence": {
            "moves": [
                {
                    "move_type": "apologize",
                    "rationale": "ack confusion",
                    "confidence": 0.9,
                },
                {
                    "move_type": "answer",
                    "rationale": "answer the side question",
                    "confidence": 0.85,
                },
            ],
            "combined_response_plan": "say sorry then answer",
            "sequence_rationale": "social move + answer",
        }
    }


class TestMoveSelectionParser:
    """WI-5 acceptance: parser handles bare and fenced JSON, enforces XOR."""

    def test_bare_json_selection_parses(self) -> None:
        out = parse_move_selection_output(json.dumps(_selection_payload()))
        assert out.selection is not None
        assert out.sequence is None
        assert out.selection.move_type == MoveType.ANSWER

    def test_bare_json_sequence_parses(self) -> None:
        out = parse_move_selection_output(json.dumps(_sequence_payload()))
        assert out.sequence is not None
        assert out.selection is None
        assert len(out.sequence.moves) == 2

    def test_fenced_json_block_parses(self) -> None:
        body = json.dumps(_selection_payload())
        wrapped = f"some preamble\n```json\n{body}\n```\nsome trailer"
        out = parse_move_selection_output(wrapped)
        assert out.selection is not None
        assert out.selection.move_type == MoveType.ANSWER

    def test_fenced_block_without_language_tag_parses(self) -> None:
        body = json.dumps(_sequence_payload())
        wrapped = f"```\n{body}\n```"
        out = parse_move_selection_output(wrapped)
        assert out.sequence is not None

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_move_selection_output("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_move_selection_output("   \n\n  ")

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_move_selection_output("{not: json}")

    def test_non_object_json_raises(self) -> None:
        # Top-level array, not an object
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_move_selection_output("[1, 2, 3]")

    def test_both_selection_and_sequence_raises_xor(self) -> None:
        payload = {**_selection_payload(), **_sequence_payload()}
        with pytest.raises(ValueError, match="schema validation"):
            parse_move_selection_output(json.dumps(payload))

    def test_neither_selection_nor_sequence_raises_xor(self) -> None:
        with pytest.raises(ValueError, match="schema validation"):
            parse_move_selection_output("{}")

    def test_missing_required_field_raises(self) -> None:
        payload = {
            "selection": {
                "move_type": "answer",
                # missing 'rationale' and 'confidence'
            }
        }
        with pytest.raises(ValueError, match="schema validation"):
            parse_move_selection_output(json.dumps(payload))

    def test_normalizes_common_reason_code_aliases(self) -> None:
        payload = {
            "selection": {
                "move_type": "propose_transition",
                "rationale": "route to product qa",
                "confidence": 0.91,
                "proposed_transition": {
                    "target_step_id": "product_qa",
                    "reason_code": "intent_detected:product_question",
                    "confidence": 0.88,
                    "reasoning": "The user asked what the product is.",
                },
            }
        }

        out = parse_move_selection_output(json.dumps(payload))

        assert out.selection is not None
        assert out.selection.proposed_transition is not None
        assert out.selection.proposed_transition.reason_code.value == "user_changed_topic"

    def test_sequence_moves_default_missing_confidence(self) -> None:
        payload = {
            "sequence": {
                "moves": [
                    {
                        "move_type": "acknowledge",
                        "rationale": "say hello back",
                    },
                    {
                        "move_type": "propose_transition",
                        "rationale": "route to booking flow",
                        "proposed_transition": {
                            "target_step_id": "submit_lead",
                            "reason_code": "booking_request",
                            "confidence": 0.87,
                            "reasoning": "The user still wants to book a demo.",
                        },
                    },
                ],
                "combined_response_plan": "Acknowledge and continue with the booking flow.",
                "sequence_rationale": "The user both greeted and asked to proceed.",
            }
        }

        out = parse_move_selection_output(json.dumps(payload))

        assert out.sequence is not None
        assert out.sequence.moves[0].confidence == 0.8
        assert out.sequence.moves[1].confidence == 0.87
        assert out.sequence.moves[1].proposed_transition is not None
        assert (
            out.sequence.moves[1].proposed_transition.reason_code.value
            == "user_requested_help"
        )


class TestMoveSelectionPromptBuilder:
    """WI-5 (doc 39): full move vocabulary + tool outcomes prompt template."""

    def test_returns_string_with_state_metadata(self) -> None:
        ctx = MoveSelectionContext(
            current_step_id="capture_email",
            current_step_name="Capture Email",
            current_step_type="capture",
            current_step_goal="capture email",
            current_user_text="my email is alice@example.com",
            allowed_move_types=[MoveType.ANSWER, MoveType.ASK_FOR_MISSING_INFO],
            transition_targets=["submit_lead"],
            transition_target_summaries={
                "submit_lead": "Submit Lead: send the captured lead to the CRM.",
            },
            event_hints={
                "booking_request": "The user wants to book a demo.",
            },
            missing_facts=["email"],
        )
        prompt = build_move_selection_prompt(ctx)
        assert "capture_email" in prompt
        assert "Capture Email" in prompt
        assert "capture" in prompt
        assert "my email is alice@example.com" in prompt
        assert "answer" in prompt
        assert "ask_for_missing_info" in prompt
        assert "submit_lead" in prompt
        assert "book a demo" in prompt
        assert "email" in prompt

    def test_includes_per_move_descriptions(self) -> None:
        ctx = MoveSelectionContext(
            current_step_id="s",
            current_step_type="capture",
            current_step_goal="g",
            allowed_move_types=[MoveType.APOLOGIZE, MoveType.REPAIR],
        )
        prompt = build_move_selection_prompt(ctx)
        assert "Apologize" in prompt or "apologize" in prompt
        assert "Reconcile" in prompt or "reconcile" in prompt

    def test_includes_recent_tool_outcomes(self) -> None:
        from datetime import datetime, timezone
        from ruhu.schemas import ToolOutcomeRecord
        ctx = MoveSelectionContext(
            current_step_id="s",
            current_step_type="capture",
            current_step_goal="g",
            allowed_move_types=[MoveType.ANSWER],
            recent_tool_outcomes=[
                ToolOutcomeRecord(
                    tool_name="crm.submit_lead",
                    invocation_id="i1",
                    invoked_at=datetime.now(timezone.utc),
                    status="success",
                    output_summary="crm.submit_lead → success (lead_id=42)",
                ),
            ],
        )
        prompt = build_move_selection_prompt(ctx)
        assert "Recent Tool Outcomes" in prompt
        assert "crm.submit_lead" in prompt
        assert "lead_id=42" in prompt

    def test_includes_pending_action_summary(self) -> None:
        ctx = MoveSelectionContext(
            current_step_id="s",
            current_step_type="capture",
            current_step_goal="g",
            allowed_move_types=[MoveType.ACKNOWLEDGE],
            pending_action_summary="Looking up calendar (running)",
        )
        prompt = build_move_selection_prompt(ctx)
        assert "Pending Action" in prompt
        assert "Looking up calendar" in prompt

    def test_output_instruction_mentions_both_shapes(self) -> None:
        ctx = MoveSelectionContext(
            current_step_id="s",
            current_step_type="capture",
            current_step_goal="g",
            allowed_move_types=[MoveType.ANSWER],
        )
        prompt = build_move_selection_prompt(ctx)
        assert '"selection"' in prompt
        assert '"sequence"' in prompt
        assert '"move_type"' in prompt
        assert "confidence" in prompt
        assert "user_provided_requested_fact" in prompt
        assert "Do not use event names" in prompt

    def test_prompt_prefers_answer_before_capture_for_hypothetical_request(self) -> None:
        ctx = MoveSelectionContext(
            current_step_id="pricing_qa",
            current_step_name="Pricing Q&A",
            current_step_type="conversation",
            current_step_goal="Handle pricing follow-up and route if needed.",
            current_user_text="Can you tell me more about whether I can book a demo and what else you can do?",
            allowed_move_types=[MoveType.ANSWER, MoveType.PROPOSE_TRANSITION],
            transition_targets=["collect_contact_info"],
            transition_target_summaries={
                "collect_contact_info": "Collect Email: start collecting the attendee email. Use this when the user clearly wants to schedule a demo now.",
            },
        )
        prompt = build_move_selection_prompt(ctx)
        assert "Prefer answering an explicit user question before starting a new" in prompt
        assert "Only transition into a step that collects a missing detail" in prompt
        assert "asking hypothetically what the assistant can do" in prompt

    def test_prompt_includes_journey_context_and_guidance(self) -> None:
        ctx = MoveSelectionContext(
            current_step_id="collect_contact_info",
            current_step_name="Collect Contact Info",
            current_step_type="capture",
            current_step_goal="Collect contact details",
            current_user_text="Why do you need my email?",
            allowed_move_types=[MoveType.ANSWER, MoveType.ASK_FOR_MISSING_INFO],
            journey_context=JourneyContext(
                current_step_id="collect_contact_info",
                current_step_type="capture",
                current_step_name="Collect Contact Info",
                current_step_purpose="Collect the user's email so the invite can be sent",
                previous_step_id="pricing_qa",
                previous_step_name="Pricing Q&A",
                transition_natural_reason="because the user asked to book a demo",
                pending_facts={
                    "email": PendingFactContext(
                        purpose="to send the invite",
                        triggered_by="booking_request",
                        triggered_in_state="pricing_qa",
                    )
                },
                route_horizon=[
                    RouteBranch(
                        target_step_id="collect_booking_time",
                        branch_when_to_use="Use this when the user is ready to provide their preferred meeting time.",
                    )
                ],
                authored_guidance=AuthoredStepGuidance(
                    repair_response="Explain the purpose before repeating the request.",
                ),
            ),
        )
        prompt = build_move_selection_prompt(ctx)
        assert "Journey Context" in prompt
        assert "because the user asked to book a demo" in prompt
        assert "triggered_by=booking_request" in prompt
        assert "repair_response" in prompt

    def test_token_budget_for_realistic_context(self) -> None:
        # Build the largest realistic context: all 12 moves allowed,
        # 5 tool outcomes, pending action, missing facts.  Approximate
        # token count via len(prompt) // 4 (rough English heuristic).
        from datetime import datetime, timezone
        from ruhu.schemas import ToolOutcomeRecord
        ctx = MoveSelectionContext(
            current_step_id="capture_email",
            current_step_type="capture",
            current_step_goal="Capture the user's email and route to the lead-submission action.",
            allowed_move_types=list(MoveType),
            transition_targets=["submit_lead", "discover", "closed"],
            tool_affordances=["crm.submit_lead"],
            required_execution_facts=["email"],
            accepted_facts={"name": "Alice"},
            missing_facts=["email"],
            pending_action_summary="Verifying CRM credentials (running)",
            recent_tool_outcomes=[
                ToolOutcomeRecord(
                    tool_name=f"tool_{i}",
                    invocation_id=f"i{i}",
                    invoked_at=datetime.now(timezone.utc),
                    status="success",
                    output_summary=f"tool_{i} → success (some longer output here for token budget testing)",
                )
                for i in range(5)
            ],
        )
        prompt = build_move_selection_prompt(ctx)
        approx_tokens = len(prompt) // 4
        assert approx_tokens < 2000, (
            f"prompt too long: {approx_tokens} tokens (cap 2000)"
        )


# ── _extract_render_output ──────────────────────────────────────────────────


class TestExtractRenderOutput:
    """The dialogue generator returns JSON; the kernel extracts ``.text``
    from it before showing it to the user. A regression in production
    surfaced when Gemini wrapped the response in a one-element JSON array
    (`[{"text": "...", "claimed_class": "repair"}]`) — the extractor's
    ``isinstance(parsed, dict)`` check failed and the entire raw JSON
    string was emitted as user-visible chat. These tests pin the contract.
    """

    def test_unwraps_single_object_array(self) -> None:
        """Regression: when Gemini returns ``[{...}]`` the extractor must
        unwrap and read fields from the first dict, not leak the array
        as user-visible text."""
        ctx = _render_context()
        ctx = ctx.model_copy(update={"allowed_claim_classes": ["partial", "repair"]})
        raw = json.dumps(
            [
                {
                    "text": "I'd love to explain that.",
                    "claimed_class": "repair",
                    "acknowledged_fact_keys": [],
                }
            ]
        )
        out = _extract_render_output(raw, ctx)
        assert out is not None
        assert out.text == "I'd love to explain that."
        assert out.claimed_class == "repair"
        # And the raw JSON string never leaks into the rendered text.
        assert "claimed_class" not in out.text
        assert not out.text.lstrip().startswith("[")

    def test_extracts_from_bare_object(self) -> None:
        ctx = _render_context()
        ctx = ctx.model_copy(update={"allowed_claim_classes": ["partial"]})
        raw = json.dumps({"text": "Sure thing.", "claimed_class": "partial"})
        out = _extract_render_output(raw, ctx)
        assert out is not None
        assert out.text == "Sure thing."
        assert out.claimed_class == "partial"

    def test_falls_back_to_raw_text_when_invalid_json(self) -> None:
        """Plain prose response (no JSON) is treated as the message itself."""
        ctx = _render_context()
        ctx = ctx.model_copy(update={"allowed_claim_classes": ["partial"]})
        out = _extract_render_output("Plain English answer.", ctx)
        assert out is not None
        assert out.text == "Plain English answer."
        assert out.claimed_class == "partial"

    def test_array_with_no_dict_falls_back(self) -> None:
        """An array of scalars (`["a", "b"]`) cannot be unwrapped — fall
        back to the raw stringified form so we don't crash, but the
        kernel's response validator will likely reject it downstream."""
        ctx = _render_context()
        ctx = ctx.model_copy(update={"allowed_claim_classes": ["partial"]})
        raw = json.dumps(["a", "b"])
        out = _extract_render_output(raw, ctx)
        assert out is not None
        assert out.text == raw  # documented fallback behaviour

    def test_empty_text_returns_none(self) -> None:
        ctx = _render_context()
        ctx = ctx.model_copy(update={"allowed_claim_classes": ["partial"]})
        out = _extract_render_output(json.dumps({"text": "   "}), ctx)
        assert out is None

    def test_picks_first_dict_in_array_with_mixed_items(self) -> None:
        """If the array contains junk before the dict, the extractor
        skips past it and uses the first dict. Mirrors how flexible
        Gemini's response shapes can be in practice."""
        ctx = _render_context()
        ctx = ctx.model_copy(update={"allowed_claim_classes": ["partial"]})
        raw = json.dumps(
            [
                "ignored leading string",
                {"text": "Real reply.", "claimed_class": "partial"},
                {"text": "second turn"},
            ]
        )
        out = _extract_render_output(raw, ctx)
        assert out is not None
        assert out.text == "Real reply."
        assert out.claimed_class == "partial"
