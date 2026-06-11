"""Backend-agnostic interface for the prefill-first classifier.

The classifier picks one label from a fixed catalog. The Protocol stays
**generic** in its naming (``candidate_labels`` / ``chosen_label``) so the
same backend can serve more than one classification *task* — workflow
outcomes today (the routing layer in ``classifier_strategy.py``), and
post-turn analytics intents/tags later (the ``analytics_tagging/`` subsystem).
The choice of label vocabulary lives in the *adapter* that calls into
this Protocol, not here.

Two implementations live under this package:

- ``transformers_backend.TransformersClassifierBackend`` — in-process
  Gemma via Hugging Face.
- ``vllm_backend.VLLMClassifierBackend`` — HTTP client to a vLLM cluster.

Plus an out-of-band failback path:

- ``vertex_gemini_backend.VertexGeminiClassifierBackend`` — direct Vertex
  Gemini REST when prefill infrastructure is not available.

The dispatcher selects between them via runtime config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(slots=True, frozen=True)
class ClassificationRequest:
    """One classification call.

    ``candidate_labels`` is the ``{label: description}`` catalog the
    classifier is constrained to. The label strings must be slug-shaped
    (per the consumer's contract — for workflow outcomes the validator
    lives on ``OutcomeCondition``); descriptions are LLM-evaluated.

    Kept narrow on purpose — the prefill classifier does not need facts,
    conversation history, or dynamic policy snippets in its prompt (see
    classifier training design. Adapters that need facts in the prompt
    bake them into ``suffix`` — never into ``prefix`` (which must stay
    byte-identical across turns at the same step for prefix-cache hits).
    """

    agent_id: str
    agent_version_id: str
    step_id: str
    step_name: str
    step_summary: str
    user_text: str
    candidate_labels: dict[str, str]
    prefix: str | None = None
    suffix: str | None = None
    lora_name: str | None = None


@dataclass(slots=True, frozen=True)
class ClassificationResult:
    """Per-call classifier output, returned by every ``PrefillClassifier``.

    ``chosen_label`` is one of ``request.candidate_labels.keys()`` (a real
    label) or ``None`` (the model returned ``unknown`` or the call failed).

    ``confidence`` is the joint softmax probability of the chosen
    multi-token label, computed by exponentiating the sum of per-token
    logprobs across the label's tokens. See
    ``docs/pre-fill-intent-classifier-design/02-architecture-spec.md``
    §Confidence for the formula.
    """

    chosen_label: str | None
    confidence: float
    decode_logprobs: dict[str, float] = field(default_factory=dict)
    cache_hit: bool = False
    prefill_tokens: int = 0
    decode_tokens: int = 0
    lora_name: str | None = None
    backend: Literal["transformers", "vllm", "vertex_gemini", "unavailable"] = "unavailable"
    elapsed_ms: int = 0
    error: str | None = None


class PrefillClassifier(Protocol):
    """Backend-agnostic classifier interface.

    Implementations:

    - ``TransformersClassifierBackend`` (Stage 1) — local Gemma via HF.
    - ``VLLMClassifierBackend`` (Stage 3) — HTTP to a vLLM cluster.
    - ``VertexGeminiClassifierBackend`` — direct Vertex REST (failback).
    """

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        ...
