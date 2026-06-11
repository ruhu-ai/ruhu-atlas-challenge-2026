"""Tests for src/ruhu/classifier/training/teacher_relabel.py — WI-6.2."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepTransition,
)
from ruhu.classifier.prompt import (
    build_classifier_prefix,
    build_classifier_suffix,
    reset_prefix_cache,
)
from ruhu.classifier.training.teacher_relabel import (
    UNKNOWN_LABEL,
    ClaudeOpusTeacherBackend,
    FakeTeacherBackend,
    TeacherRequest,
    TeacherResult,
    VertexTeacherBackend,
    build_teacher_request,
    main as cli_main,
    parse_teacher_response,
    read_raw_traces,
    relabel_rows,
    render_teacher_prompt,
    select_inter_rater_indices,
    select_rows_for_relabel,
    write_teacher_labeled,
)
from ruhu.schemas import AgentCapabilityManifest, OutcomeCondition


@pytest.fixture(autouse=True)
def _clear_prefix_cache() -> None:
    reset_prefix_cache()
    yield
    reset_prefix_cache()


def _make_doc() -> AgentDocument:
    step = Step(
        id="entry",
        name="Entry",
        description="Triage the user's reason for contacting MelonPay.",
        transitions=[
            StepTransition(
                id="t_transfer_status",
                when=OutcomeCondition(
                    event="transfer_status",
                    description="User asks about a transfer status, where their money is, or transfer history.",
                ),
                to_step_id="entry",
            ),
            StepTransition(
                id="t_kyc_help",
                when=OutcomeCondition(
                    event="kyc_help",
                    description="User has a KYC verification question or document upload need.",
                ),
                to_step_id="entry",
            ),
        ],
    )
    return AgentDocument(
        version="v1",
        start_scenario_id="main",
        scenarios=[
            Scenario(id="main", name="Main", start_step_id="entry", steps=[step]),
        ],
        agent_capability_manifest=AgentCapabilityManifest(
            assistant_identity="I am MelonPay's support assistant.",
            capabilities=["transfer status lookups", "KYC guidance"],
            limitations=["I only use configured tools."],
        ),
    )


def _row(
    *,
    bucket: str = "low_conf",
    confidence: float | None = 0.5,
    user_text: str = "where is my money?",
    intent: str | None = "transfer_status",
    agent_id: str = "a1",
    agent_version_id: str = "v1",
    step_id: str = "entry",
) -> dict:
    document = _make_doc()
    step = document.step_by_id(step_id) if step_id == "entry" else None
    context = build_classifier_prefix(document, step) if step else "Step: entry\n"
    return {
        "context": context,
        "input_window": build_classifier_suffix(user_text),
        "labels": [intent] if intent is not None else [],
        "_metadata": {
            "bucket": bucket,
            "weight": 1.0,
            "needs_teacher_relabel": bucket in {"low_conf", "confusion_pair"},
            "cancellation_pattern": False,
            "agent_id": agent_id,
            "agent_version_id": agent_version_id,
            "step_id": step_id,
            "confidence": confidence,
            "conversation_id": "c1",
            "turn_recorded_at": "2026-05-01T12:00:00+00:00",
        },
    }


# ── render_teacher_prompt ───────────────────────────────────────────────────


def test_render_teacher_prompt_layout_matches_spec() -> None:
    request = TeacherRequest(
        user_text="where is my money?",
        assistant_identity="I am MelonPay's support assistant.",
        agent_capabilities=["transfer status lookups", "KYC guidance"],
        step_name="Entry",
        step_summary="Triage the user.",
        candidate_labels={
            "transfer_status": "User asks about a transfer.",
            "kyc_help": "User has a KYC question.",
        },
        student_labels=["transfer_status"],
        agent_id="a1",
        agent_version_id="v1",
        step_id="entry",
    )
    prompt = render_teacher_prompt(request)

    assert prompt.startswith(
        "You are reviewing customer support conversations to label the intent"
    )
    assert "The agent is: I am MelonPay's support assistant." in prompt
    assert "The agent's capabilities: transfer status lookups, KYC guidance\n" in prompt
    assert "The current step: Entry — Triage the user.\n" in prompt
    assert "- kyc_help: User has a KYC question.\n" in prompt
    assert "- transfer_status: User asks about a transfer.\n" in prompt
    assert f"- {UNKNOWN_LABEL}: none of the above match the user's message\n" in prompt
    assert "User's message: \"where is my money?\"\n" in prompt
    assert "strict JSON" in prompt


def test_render_teacher_prompt_sorts_intents() -> None:
    request = TeacherRequest(
        user_text="x",
        assistant_identity="x",
        agent_capabilities=[],
        step_name="x",
        step_summary="x",
        candidate_labels={"zeta": "z", "alpha": "a", "mike": "m"},
        student_labels=[],
        agent_id="a", agent_version_id="v", step_id="s",
    )
    prompt = render_teacher_prompt(request)
    available = prompt.split("Available intents:\n", 1)[1]
    lines = [line for line in available.splitlines() if line.startswith("- ")]
    label_ids = [line[2:].split(":", 1)[0] for line in lines]
    assert label_ids == ["alpha", "mike", "zeta", UNKNOWN_LABEL]


def test_render_teacher_prompt_handles_no_capabilities() -> None:
    request = TeacherRequest(
        user_text="x",
        assistant_identity="A",
        agent_capabilities=[],
        step_name="x",
        step_summary="x",
        candidate_labels={"a": "a"},
        student_labels=[],
        agent_id="a", agent_version_id="v", step_id="s",
    )
    assert "The agent's capabilities: none\n" in render_teacher_prompt(request)


# ── parse_teacher_response ─────────────────────────────────────────────────


def test_parse_teacher_response_happy_path() -> None:
    text = json.dumps({"intent": "transfer_status", "confidence": 0.92, "reasoning": "clear"})
    result = parse_teacher_response(text, {"transfer_status": "x"})
    assert result.intent == "transfer_status"
    assert result.confidence == 0.92
    assert result.reasoning == "clear"


def test_parse_teacher_response_handles_unknown() -> None:
    text = json.dumps({"intent": "unknown", "confidence": 0.1, "reasoning": "off-topic"})
    result = parse_teacher_response(text, {"transfer_status": "x"})
    assert result.intent is None
    assert result.confidence == 0.1


def test_parse_teacher_response_rejects_intent_outside_catalog() -> None:
    text = json.dumps({"intent": "made_up", "confidence": 0.95, "reasoning": "drift"})
    result = parse_teacher_response(text, {"transfer_status": "x"})
    assert result.intent is None
    assert "intent_outside_catalog" in result.reasoning


def test_parse_teacher_response_strips_code_fences() -> None:
    text = "```json\n" + json.dumps({"intent": "x", "confidence": 0.5, "reasoning": ""}) + "\n```"
    result = parse_teacher_response(text, {"x": "x"})
    assert result.intent == "x"


def test_parse_teacher_response_falls_back_on_unparseable() -> None:
    result = parse_teacher_response("not json at all", {"x": "x"})
    assert result.intent is None
    assert result.reasoning == "parse_failed"


def test_parse_teacher_response_clamps_out_of_range_confidence() -> None:
    text = json.dumps({"intent": "x", "confidence": 1.7, "reasoning": ""})
    assert parse_teacher_response(text, {"x": "x"}).confidence == 1.0
    text = json.dumps({"intent": "x", "confidence": -0.3, "reasoning": ""})
    assert parse_teacher_response(text, {"x": "x"}).confidence == 0.0


# ── selection ──────────────────────────────────────────────────────────────


def test_select_rows_for_relabel_takes_all_low_conf_and_confusion_pair() -> None:
    rows = [
        _row(bucket="low_conf"),
        _row(bucket="confusion_pair"),
        _row(bucket="other"),
    ]
    selected = select_rows_for_relabel(rows, qa_sample_rate=0.0, seed=0)
    buckets = [r["_metadata"]["bucket"] for r in selected]
    assert "low_conf" in buckets
    assert "confusion_pair" in buckets
    assert "other" not in buckets


def test_select_rows_for_relabel_samples_high_conf_completion() -> None:
    high_rows = [_row(bucket="high_conf_completion") for _ in range(200)]
    selected = select_rows_for_relabel(high_rows, qa_sample_rate=0.10, seed=42)
    assert 5 < len(selected) < 35  # ~10% of 200


def test_select_rows_for_relabel_skips_other_bucket() -> None:
    rows = [_row(bucket="other") for _ in range(20)]
    assert select_rows_for_relabel(rows, qa_sample_rate=1.0, seed=0) == []


def test_select_rows_for_relabel_validates_rate() -> None:
    with pytest.raises(ValueError):
        select_rows_for_relabel([], qa_sample_rate=2.0)


def test_select_inter_rater_indices_returns_subset() -> None:
    indices = select_inter_rater_indices(100, rate=0.2, seed=42)
    assert 0 < len(indices) < 100
    assert all(0 <= i < 100 for i in indices)


def test_select_inter_rater_indices_deterministic_for_seed() -> None:
    a = select_inter_rater_indices(50, rate=0.3, seed=7)
    b = select_inter_rater_indices(50, rate=0.3, seed=7)
    assert a == b


# ── build_teacher_request ──────────────────────────────────────────────────


def test_build_teacher_request_uses_doc_lookup_when_provided() -> None:
    document = _make_doc()
    row = _row(user_text="where is my money?", intent="transfer_status")
    req = build_teacher_request(row, agent_doc_lookup=lambda *_: document)
    assert req.user_text == "where is my money?"
    assert req.assistant_identity == document.agent_capability_manifest.assistant_identity
    assert req.agent_capabilities == list(document.agent_capability_manifest.capabilities)
    assert req.step_name == "Entry"
    assert "transfer_status" in req.candidate_labels
    assert req.student_labels == ["transfer_status"]


def test_build_teacher_request_degrades_without_lookup() -> None:
    row = _row()
    req = build_teacher_request(row, agent_doc_lookup=None)
    assert req.assistant_identity == ""
    assert req.agent_capabilities == []
    # Step name + intents are parsed back out of the prefix.
    assert req.step_name == "Entry"
    assert "transfer_status" in req.candidate_labels
    assert "kyc_help" in req.candidate_labels


def test_build_teacher_request_recovers_user_text_from_input_window() -> None:
    row = _row(user_text="hello with: weird\ncharacters")
    req = build_teacher_request(row, agent_doc_lookup=None)
    assert req.user_text == "hello with: weird\ncharacters"


# ── orchestrator ───────────────────────────────────────────────────────────


def test_relabel_rows_replaces_labels_and_records_student_labels() -> None:
    document = _make_doc()
    rows = [_row(intent="transfer_status")]
    teacher = FakeTeacherBackend(intent="kyc_help", confidence=0.88, backend_name="fake-teacher")
    out = relabel_rows(rows, teacher=teacher, agent_doc_lookup=lambda *_: document)
    assert len(out) == 1
    assert out[0]["labels"] == ["kyc_help"]
    assert out[0]["teacher_confidence"] == 0.88
    assert out[0]["_metadata"]["student_labels"] == ["transfer_status"]
    assert out[0]["_metadata"]["teacher"]["backend"] == "fake-teacher"
    assert out[0]["_metadata"]["teacher"]["intent"] == "kyc_help"


def test_relabel_rows_unknown_intent_yields_empty_labels() -> None:
    rows = [_row()]
    teacher = FakeTeacherBackend(intent=None, confidence=0.2, force_unknown=True)
    out = relabel_rows(rows, teacher=teacher, agent_doc_lookup=None)
    assert out[0]["labels"] == []
    assert out[0]["teacher_confidence"] == 0.2


def test_relabel_rows_only_processes_selected_buckets() -> None:
    rows = [
        _row(bucket="low_conf"),
        _row(bucket="other"),
        _row(bucket="confusion_pair"),
    ]
    teacher = FakeTeacherBackend(intent="transfer_status")
    out = relabel_rows(rows, teacher=teacher, agent_doc_lookup=None, qa_sample_rate=0.0)
    assert len(out) == 2
    assert all(r["_metadata"]["bucket"] in {"low_conf", "confusion_pair"} for r in out)


def test_relabel_rows_inter_rater_annotates_disagreement() -> None:
    rows = [_row(bucket="low_conf") for _ in range(20)]
    primary = FakeTeacherBackend(intent="transfer_status", backend_name="primary")
    second = FakeTeacherBackend(intent="kyc_help", backend_name="second")
    out = relabel_rows(
        rows,
        teacher=primary,
        second_teacher=second,
        agent_doc_lookup=None,
        qa_sample_rate=0.0,
        inter_rater_rate=1.0,  # always sample
        seed=0,
    )
    for relabeled in out:
        inter = relabeled["_metadata"]["inter_rater"]
        assert inter["backend"] == "second"
        assert inter["agree"] is False
        assert inter["needs_human_review"] is True


def test_relabel_rows_inter_rater_marks_agreement_when_intents_match() -> None:
    rows = [_row(bucket="low_conf")]
    primary = FakeTeacherBackend(intent="transfer_status", backend_name="primary")
    second = FakeTeacherBackend(intent="transfer_status", backend_name="second")
    out = relabel_rows(
        rows,
        teacher=primary,
        second_teacher=second,
        agent_doc_lookup=None,
        inter_rater_rate=1.0,
    )
    inter = out[0]["_metadata"]["inter_rater"]
    assert inter["agree"] is True
    assert inter["needs_human_review"] is False


def test_relabel_rows_no_second_teacher_skips_inter_rater_block() -> None:
    rows = [_row(bucket="low_conf")]
    primary = FakeTeacherBackend(intent="transfer_status")
    out = relabel_rows(rows, teacher=primary, agent_doc_lookup=None)
    assert "inter_rater" not in out[0]["_metadata"]


# ── JSONL i/o ──────────────────────────────────────────────────────────────


def test_read_raw_traces_skips_blank_lines(tmp_path) -> None:
    p = tmp_path / "raw.jsonl"
    p.write_text(
        "\n"
        + json.dumps({"context": "x", "input_window": "y", "labels": [], "_metadata": {}})
        + "\n\n"
    )
    assert len(read_raw_traces(p)) == 1


def test_read_raw_traces_invalid_json_raises(tmp_path) -> None:
    p = tmp_path / "raw.jsonl"
    p.write_text("not json\n")
    with pytest.raises(ValueError, match="invalid JSON"):
        read_raw_traces(p)


def test_write_teacher_labeled_creates_parent_dir_and_returns_count(tmp_path) -> None:
    target = tmp_path / "nested" / "teacher_labeled.jsonl"
    rows = [{"a": 1}, {"a": 2}]
    written = write_teacher_labeled(rows, target)
    assert written == 2
    assert len(target.read_text().strip().splitlines()) == 2


# ── VertexTeacherBackend ──────────────────────────────────────────────────


def test_vertex_backend_renders_payload_and_parses_response() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url: str, json: dict, headers: dict, timeout: float) -> dict:
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"intent": "transfer_status", "confidence": 0.91, "reasoning": "ok"}'}
                        ]
                    }
                }
            ]
        }

    backend = VertexTeacherBackend(
        project="proj",
        location="europe-west2",
        model="gemini-2.5-pro",
        http_post=fake_post,
        access_token_loader=lambda: "stub-token",
    )
    request = TeacherRequest(
        user_text="x",
        assistant_identity="A",
        agent_capabilities=["c"],
        step_name="Entry",
        step_summary="x",
        candidate_labels={"transfer_status": "x"},
        student_labels=[],
        agent_id="a", agent_version_id="v", step_id="entry",
    )
    result = backend.label(request)
    assert result.intent == "transfer_status"
    assert result.confidence == 0.91
    assert "europe-west2" in captured["url"]
    assert "/publishers/google/models/gemini-2.5-pro:generateContent" in captured["url"]
    assert captured["payload"]["generationConfig"]["temperature"] == 0.0
    assert captured["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["payload"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
    assert captured["headers"]["Authorization"] == "Bearer stub-token"


def test_vertex_backend_handles_empty_response() -> None:
    backend = VertexTeacherBackend(
        project="proj",
        http_post=lambda **_: {"candidates": []},
        access_token_loader=lambda: "tok",
    )
    request = TeacherRequest(
        user_text="x", assistant_identity="A", agent_capabilities=[],
        step_name="s", step_summary="s", candidate_labels={"x": "x"},
        student_labels=[], agent_id="a", agent_version_id="v", step_id="s",
    )
    result = backend.label(request)
    assert result.intent is None
    assert result.reasoning == "empty_vertex_response"


def test_vertex_backend_propagates_outside_catalog_to_unknown() -> None:
    backend = VertexTeacherBackend(
        project="proj",
        http_post=lambda **_: {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"intent": "made_up", "confidence": 0.9, "reasoning": ""}'}
                        ]
                    }
                }
            ]
        },
        access_token_loader=lambda: "tok",
    )
    request = TeacherRequest(
        user_text="x", assistant_identity="A", agent_capabilities=[],
        step_name="s", step_summary="s", candidate_labels={"transfer_status": "x"},
        student_labels=[], agent_id="a", agent_version_id="v", step_id="s",
    )
    result = backend.label(request)
    assert result.intent is None


# ── Claude stub ────────────────────────────────────────────────────────────


def test_claude_stub_raises_with_actionable_message() -> None:
    backend = ClaudeOpusTeacherBackend()
    assert backend.name == "claude-opus-4-7"
    with pytest.raises(NotImplementedError, match="Anthropic SDK"):
        backend.label(
            TeacherRequest(
                user_text="x", assistant_identity="A", agent_capabilities=[],
                step_name="s", step_summary="s", candidate_labels={},
                student_labels=[], agent_id="a", agent_version_id="v", step_id="s",
            )
        )


# ── CLI smoke ──────────────────────────────────────────────────────────────


def test_cli_main_synthetic_round_trip(tmp_path) -> None:
    input_path = tmp_path / "raw.jsonl"
    output_path = tmp_path / "teacher_labeled.jsonl"
    rows = [_row(bucket="low_conf"), _row(bucket="confusion_pair")]
    input_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    rc = cli_main(
        [
            "--input", str(input_path),
            "--output", str(output_path),
            "--backend", "synthetic",
        ]
    )
    assert rc == 0
    out_lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(out_lines) == 2
    parsed = [json.loads(line) for line in out_lines]
    for row in parsed:
        assert "teacher_confidence" in row
        assert row["_metadata"]["teacher"]["backend"] == "synthetic"


def test_cli_main_inter_rater_synthetic(tmp_path) -> None:
    input_path = tmp_path / "raw.jsonl"
    output_path = tmp_path / "teacher_labeled.jsonl"
    rows = [_row(bucket="low_conf") for _ in range(10)]
    input_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    rc = cli_main(
        [
            "--input", str(input_path),
            "--output", str(output_path),
            "--backend", "synthetic",
            "--inter-rater-backend", "synthetic",
            "--inter-rater-rate", "1.0",
        ]
    )
    assert rc == 0
    parsed = [json.loads(line) for line in output_path.read_text().splitlines()]
    assert all("inter_rater" in row["_metadata"] for row in parsed)


def test_cli_main_vertex_without_project_exits(tmp_path, monkeypatch) -> None:
    """--backend vertex without --vertex-project / env should SystemExit cleanly."""
    monkeypatch.delenv("RUHU_VERTEX_PROJECT", raising=False)
    input_path = tmp_path / "raw.jsonl"
    input_path.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="vertex-project"):
        cli_main(
            [
                "--input", str(input_path),
                "--output", str(tmp_path / "out.jsonl"),
                "--backend", "vertex",
            ]
        )
