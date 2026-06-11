from __future__ import annotations

import json

import httpx

from ruhu.atlas_generator import AtlasGeneratorContext, AtlasProposalGenerator
from ruhu.atlas_protocol import AtlasProposedChanges


def _context() -> AtlasGeneratorContext:
    return AtlasGeneratorContext(
        agent_id="sales",
        scope="agent_authoring",
        user_message='rename this step to "Qualified lead"',
        selected_scenario_id="main",
        selected_scenario_name="Main",
        selected_step_id="discover",
        selected_step_name="Discover",
        scenario_ids=["main"],
        step_ids=["discover"],
        fact_names=["email"],
        tool_refs=["knowledge.lookup"],
    )


def _valid_response_text() -> str:
    return json.dumps(
        {
            "assistant_rationale": "Rename the selected step.",
            "generator_blockers": [],
            "proposed_changes": {
                "agent_metadata_deltas": [],
                "scenario_deltas": [],
                "step_deltas": [
                    {
                        "agent_id": "sales",
                        "scenario_id": "main",
                        "step_id": "discover",
                        "delta_id": "atlas_delta_1",
                        "operation": "update",
                        "status": "proposed",
                        "change_type": "rename_step",
                        "depends_on_delta_ids": [],
                        "payload": {"name": "Qualified lead"},
                        "summary": "Rename the selected step.",
                    }
                ],
                "scenario_route_deltas": [],
                "channel_policy_deltas": [],
                "rule_deltas": [],
                "knowledge_deltas": [],
                "integration_binding_deltas": [],
            },
        }
    )


class _FakeClient:
    """httpx.Client stand-in serving a scripted sequence of response texts."""

    texts: list[str] = []
    calls: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def post(self, url, *, json=None, headers=None):  # noqa: A002 - httpx kwarg name
        prompt = json["messages"][0]["content"]
        type(self).calls.append(prompt)
        text = type(self).texts[min(len(type(self).calls) - 1, len(type(self).texts) - 1)]

        class _Response:
            status_code = 200
            request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"content": [{"type": "text", "text": text}]}

        return _Response()


def _install_fake_client(monkeypatch, texts: list[str]) -> type[_FakeClient]:
    _FakeClient.texts = texts
    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    return _FakeClient


def test_atlas_generator_from_env_prefers_anthropic_api_key(monkeypatch) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("RUHU_ATLAS_GENERATOR_MODEL", "claude-test")

    generator = AtlasProposalGenerator.from_env(
        fallback_generate=lambda context, compiled_document: AtlasProposedChanges(),
    )

    assert generator.api_key == "test-anthropic-key"
    assert generator.model == "claude-test"


def test_extract_json_object_falls_through_fenced_prose_to_brace_scan() -> None:
    """AR-3.7: a fence wrapping prose around the object still yields the object."""
    from ruhu.atlas_generator import _extract_json_object

    text = '```json\nHere is the result: {"a": 1, "b": {"c": 2}}\n```'
    block = _extract_json_object(text)
    assert block is not None
    import json

    assert json.loads(block) == {"a": 1, "b": {"c": 2}}


def test_atlas_generator_from_env_survives_malformed_numeric_env(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_ATLAS_GENERATOR_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.setenv("RUHU_ATLAS_GENERATOR_MAX_RETRIES", "two")
    monkeypatch.setenv("RUHU_ATLAS_GENERATOR_RETRY_BACKOFF_SECONDS", "")

    generator = AtlasProposalGenerator.from_env(
        fallback_generate=lambda context, compiled_document: AtlasProposedChanges(),
    )

    assert generator.timeout_seconds == 12.0
    assert generator.max_retries == 2
    assert generator.retry_backoff_seconds == 0.25


def test_atlas_generator_returns_fallback_without_api_key() -> None:
    fallback_called = {"value": False, "compiled_document": None}

    def fallback_generate(context: AtlasGeneratorContext, compiled_document) -> AtlasProposedChanges:
        fallback_called["value"] = True
        fallback_called["compiled_document"] = compiled_document
        return AtlasProposedChanges()

    generator = AtlasProposalGenerator(
        fallback_generate=fallback_generate,
        api_key=None,
    )

    sentinel_document = object()
    output = generator.generate(_context(), compiled_document=sentinel_document)

    assert output.generation_mode == "fallback"
    assert output.generation_model is None
    assert fallback_called["value"] is True
    # The compiled document is threaded through explicitly — the generator
    # holds no per-request instance state that concurrent turns could race on.
    assert fallback_called["compiled_document"] is sentinel_document
    assert output.proposed_changes == AtlasProposedChanges()


def test_atlas_generator_uses_anthropic_when_response_is_valid(monkeypatch) -> None:
    fake = _install_fake_client(monkeypatch, [_valid_response_text()])

    generator = AtlasProposalGenerator(
        fallback_generate=lambda context, compiled_document: AtlasProposedChanges(),
        api_key="test-anthropic-key",
        model="claude-test",
    )

    output = generator.generate(_context())

    assert output.generation_mode == "anthropic"
    assert output.generation_model == "claude-test"
    assert output.assistant_rationale == "Rename the selected step."
    assert output.proposed_changes.step_deltas[0].change_type == "rename_step"
    assert len(fake.calls) == 1


def test_atlas_generator_repairs_unparseable_response_once(monkeypatch) -> None:
    fake = _install_fake_client(monkeypatch, ["definitely not json", _valid_response_text()])

    generator = AtlasProposalGenerator(
        fallback_generate=lambda context, compiled_document: AtlasProposedChanges(),
        api_key="test-anthropic-key",
        model="claude-test",
        max_retries=0,
    )

    output = generator.generate(_context())

    assert output.generation_mode == "anthropic"
    assert output.proposed_changes.step_deltas[0].change_type == "rename_step"
    assert len(fake.calls) == 2
    assert "could not be used" in fake.calls[1]


def test_atlas_generator_falls_back_when_repair_also_fails(monkeypatch) -> None:
    fake = _install_fake_client(monkeypatch, ["not json", "still not json"])

    fallback_called = {"value": False}

    def fallback_generate(context: AtlasGeneratorContext, compiled_document) -> AtlasProposedChanges:
        fallback_called["value"] = True
        return AtlasProposedChanges()

    generator = AtlasProposalGenerator(
        fallback_generate=fallback_generate,
        api_key="test-anthropic-key",
        max_retries=0,
    )

    output = generator.generate(_context())

    assert output.generation_mode == "fallback"
    assert output.generation_model is None
    assert fallback_called["value"] is True
    # Exactly one repair attempt — no unbounded loops.
    assert len(fake.calls) == 2


def test_atlas_generator_parses_blocking_questions(monkeypatch) -> None:
    text = json.dumps(
        {
            "assistant_rationale": "I need one decision before changing anything.",
            "generator_blockers": [],
            "blocking_questions": [
                {
                    "question_id": "q1",
                    "question": "Which step should the new transition target?",
                    "help_text": None,
                    "options": ["discover", "qualify"],
                    "required": True,
                    "target_ref": "discover",
                }
            ],
            "proposed_changes": {},
        }
    )
    _install_fake_client(monkeypatch, [text])

    generator = AtlasProposalGenerator(
        fallback_generate=lambda context, compiled_document: AtlasProposedChanges(),
        api_key="test-anthropic-key",
    )

    output = generator.generate(_context())

    assert output.generation_mode == "anthropic"
    assert len(output.blocking_questions) == 1
    assert output.blocking_questions[0].options == ["discover", "qualify"]


def test_atlas_generator_rejects_unknown_change_type(monkeypatch) -> None:
    bad = json.loads(_valid_response_text())
    bad["proposed_changes"]["step_deltas"][0]["change_type"] = "made_up_change"
    _install_fake_client(monkeypatch, [json.dumps(bad), json.dumps(bad)])

    fallback_called = {"value": False}

    def fallback_generate(context: AtlasGeneratorContext, compiled_document) -> AtlasProposedChanges:
        fallback_called["value"] = True
        return AtlasProposedChanges()

    generator = AtlasProposalGenerator(
        fallback_generate=fallback_generate,
        api_key="test-anthropic-key",
        max_retries=0,
    )

    output = generator.generate(_context())

    # Typed change_type: contract violations fail at parse (then repair, then
    # fallback) instead of leaking into review.
    assert output.generation_mode == "fallback"
    assert fallback_called["value"] is True


def test_atlas_generator_semantic_repair_feedback_lands_in_prompt(monkeypatch) -> None:
    fake = _install_fake_client(monkeypatch, [_valid_response_text()])

    generator = AtlasProposalGenerator(
        fallback_generate=lambda context, compiled_document: AtlasProposedChanges(),
        api_key="test-anthropic-key",
    )

    context = _context().model_copy(update={"repair_feedback": "rename_step: unknown step 'ghost'"})
    generator.generate(context)

    assert "REPAIR PASS" in fake.calls[0]
    assert "unknown step 'ghost'" in fake.calls[0]
