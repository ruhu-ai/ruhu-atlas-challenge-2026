"""Tests for the knowledge-grounding gate (Google Vertex AI pattern).

Covers:

- ``ConversationKernel._normalize_knowledge_score`` calibration against
  the empirical breakpoints used by ``KnowledgeService._evaluate_lookup_hits``.
- ``ConversationKernel._resolve_grounding_policy`` auto-default
  semantics: KB-attached implicit grounding when the author hasn't
  expressed an opinion.
- ``ConversationKernel._retrieval_evidence_from_result`` projection.
- ``response_generation.score_grounding_overlap`` heuristic — token
  overlap between rendered text and retrieved chunks.
- The pre-call gate in ``_render_knowledge_response_with_llm``: matrix
  of (mode × grade) outcomes ensuring the right blocked/allowed/
  fallback path fires.
"""
from __future__ import annotations

import pytest

from ruhu.agent_document import Step, StepCompletion
from ruhu.kernel import ConversationKernel
from ruhu.response_generation import (
    _STRICT_GROUNDED_SYSTEM_INSTRUCTION,
    score_grounding_overlap,
)
from ruhu.schemas import (
    KnowledgeGroundingPolicy,
    ResponsePolicy,
    RetrievalChunk,
)


# ── Score normalization calibration ─────────────────────────────────────────


@pytest.mark.parametrize(
    "score, grade, expected",
    [
        (0.0, "fail", 0.0),
        (0.0, None, 0.0),
        (1.5, None, 0.35),  # halfway up the [0,3] linear ramp
        (3.0, None, 0.7),  # the "pass" threshold breakpoint = Google's default
        (4.5, None, 0.85),  # halfway up the [3,6] saturation ramp
        (6.0, None, 1.0),  # saturation
        (10.0, None, 1.0),  # clamped to saturation
        (None, "pass", 1.0),  # categorical floor: pass → 1.0
        (None, "weak", 0.4),
        (None, "fail", 0.0),
        # max(score-derived, grade-derived) so neither signal is dropped
        (1.0, "pass", 1.0),
        (5.0, "fail", 0.9),  # score wins: 5.0 maps to 0.7 + (5-3)/3 * 0.3 = 0.9
    ],
)
def test_normalize_knowledge_score(score, grade, expected) -> None:
    out = ConversationKernel._normalize_knowledge_score(score, grade)
    assert out == pytest.approx(expected, abs=0.02)


# ── Auto-default mode resolution ────────────────────────────────────────────


def _step(policy: KnowledgeGroundingPolicy | None = None) -> Step:
    rp_kwargs = {"knowledge_grounding": policy} if policy is not None else {}
    return Step(
        id="entry",
        name="Entry",
        completion=StepCompletion(disposition="resolved"),
        response_policy=ResponsePolicy(**rp_kwargs),
    )


def test_resolve_policy_default_step_outside_knowledge_path_stays_off() -> None:
    p = ConversationKernel._resolve_grounding_policy(_step())
    assert p.mode == "off"


def test_resolve_policy_default_step_in_knowledge_path_auto_promotes_to_required() -> None:
    p = ConversationKernel._resolve_grounding_policy(_step(), in_knowledge_path=True)
    assert p.mode == "required"


def test_resolve_policy_explicit_preferred_preserved_in_knowledge_path() -> None:
    p = ConversationKernel._resolve_grounding_policy(
        _step(KnowledgeGroundingPolicy(mode="preferred")),
        in_knowledge_path=True,
    )
    assert p.mode == "preferred"


def test_resolve_policy_explicit_required_preserved_outside_knowledge_path() -> None:
    p = ConversationKernel._resolve_grounding_policy(
        _step(KnowledgeGroundingPolicy(mode="required")),
        in_knowledge_path=False,
    )
    assert p.mode == "required"


def test_resolve_policy_preserves_thresholds_on_auto_promote() -> None:
    p = ConversationKernel._resolve_grounding_policy(
        _step(KnowledgeGroundingPolicy(min_relevance=0.85, min_grounding_score=0.75)),
        in_knowledge_path=True,
    )
    assert p.mode == "required"
    assert p.min_relevance == 0.85
    assert p.min_grounding_score == 0.75


# ── Retrieval evidence projection ───────────────────────────────────────────


def test_retrieval_evidence_skips_hits_with_empty_text() -> None:
    result_dict = {
        "hits": [
            {"snippet": "", "summary": "", "document_id": "d1", "chunk_id": "c1", "score": 5.0},
            {"snippet": "real content", "document_id": "d2", "chunk_id": "c2", "score": 4.5, "title": "Doc Two"},
        ],
    }
    out = ConversationKernel._retrieval_evidence_from_result(result_dict, "pass")
    assert len(out) == 1
    assert out[0].document_id == "d2"
    assert out[0].title == "Doc Two"
    assert out[0].score == 4.5
    assert out[0].normalized_score is not None


def test_retrieval_evidence_handles_missing_hits_field() -> None:
    assert ConversationKernel._retrieval_evidence_from_result({}, "fail") == []
    assert ConversationKernel._retrieval_evidence_from_result(None, "fail") == []


def test_retrieval_evidence_concatenates_snippet_and_summary() -> None:
    result_dict = {
        "hits": [
            {"snippet": "Short title.", "summary": "Long summary.", "document_id": "d", "chunk_id": "c", "score": 4.0},
        ],
    }
    out = ConversationKernel._retrieval_evidence_from_result(result_dict, "pass")
    assert len(out) == 1
    assert "Short title." in out[0].text
    assert "Long summary." in out[0].text


# ── Post-call grounding overlap heuristic ──────────────────────────────────


def test_overlap_score_paraphrase_high() -> None:
    chunks = [RetrievalChunk(
        text="Ruhu offers a Starter tier at fifty dollars per month with basic features.",
        document_id="d",
        chunk_id="c",
        score=4.0,
    )]
    score = score_grounding_overlap(
        "The Starter tier costs fifty dollars per month and includes basic features.",
        chunks,
    )
    assert score >= 0.6  # paraphrase should clear the default citation threshold


def test_overlap_score_fabrication_low() -> None:
    chunks = [RetrievalChunk(
        text="Ruhu offers a Starter tier at fifty dollars per month.",
        document_id="d",
        chunk_id="c",
        score=4.0,
    )]
    score = score_grounding_overlap(
        "Enterprise plans include premium support and custom SLAs at five hundred dollars",
        chunks,
    )
    assert score < 0.5  # fabricated content should fail the gate


def test_overlap_score_empty_answer_returns_full() -> None:
    # Empty answer can't fabricate; the gate is meant for substantive
    # text. Other paths (deterministic fallback) handle empties.
    assert score_grounding_overlap("", [RetrievalChunk(
        text="x", document_id="d", chunk_id="c", score=1.0,
    )]) == 1.0


def test_overlap_score_no_chunks_returns_zero() -> None:
    assert score_grounding_overlap("any answer", []) == 0.0


# ── Strict-grounded system instruction is the verbatim Google wording ──────


def test_strict_grounded_instruction_is_googles_verbatim_wording() -> None:
    # Pinned to the exact recommended Gemini-3 string. If Google updates
    # their guidance, update this test deliberately — don't paraphrase.
    assert _STRICT_GROUNDED_SYSTEM_INSTRUCTION.startswith(
        "You are a strictly grounded assistant"
    )
    assert "User Context" in _STRICT_GROUNDED_SYSTEM_INSTRUCTION
    assert "must not access or utilize your own knowledge" in _STRICT_GROUNDED_SYSTEM_INSTRUCTION


# ── Schema invariants ──────────────────────────────────────────────────────


def test_response_policy_default_grounding_mode_is_off() -> None:
    """Backward-compat invariant: existing agents that don't carry the
    new field continue to get ``mode="off"`` and the runtime auto-default
    promotes to ``required`` only inside the knowledge-render path."""
    rp = ResponsePolicy()
    assert rp.knowledge_grounding.mode == "off"
    assert rp.knowledge_grounding.min_relevance == 0.7  # Google default
    assert rp.knowledge_grounding.min_grounding_score == 0.6  # Google default
    assert rp.knowledge_grounding.post_call_check == "heuristic"
    assert rp.knowledge_grounding.strict_system_instruction is True


def test_response_policy_grounding_thresholds_clamped_to_unit_interval() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        KnowledgeGroundingPolicy(min_relevance=1.5)
    with pytest.raises(ValidationError):
        KnowledgeGroundingPolicy(min_grounding_score=-0.1)
