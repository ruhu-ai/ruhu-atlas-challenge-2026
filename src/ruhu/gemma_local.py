"""Local Gemma classifier path (Stage 1 of the prefill-first migration).

Wraps a Hugging Face ``transformers`` model behind the same
``SemanticInterpreter`` interface the kernel expects, so a developer
can run an end-to-end agent locally without standing up a vLLM cluster.
The actual classification happens via a ``PrefillClassifier`` backend
(today: ``TransformersClassifierBackend``); this module only owns:

- weight loading + SHA validation (``TransformersGemmaBackend``);
- catalog construction + result projection onto authored outcome edges
  (``GemmaLocalInterpreter``).

Edge-owned-outcomes contract: the catalog comes from each step's
``OutcomeCondition`` transitions plus the kernel's universal outcomes
(via ``classifier.prompt.outcome_catalog_for_step``). Result projection
emits ``family="routing", name="outcome_resolved"`` and is shared with
``classifier_strategy.StrategyAwareInterpreter`` to keep behaviour
identical across both code paths.
"""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_document import AgentDocument, Step
from .classifier.prompt import build_classifier_prompt, outcome_catalog_for_step
from .classifier.protocol import (
    ClassificationRequest,
    PrefillClassifier,
)
from .classifier_strategy import result_to_routing_events
from .interpreter import SemanticInterpreter
from .schemas import RuntimeTurn, SemanticEventRecord
from .state_summary import summarize_step

# Pinned per-snapshot SHA-256 hashes for known-good weight downloads.
# Keys are filenames under the model directory; values are the expected
# digest of ``model.safetensors`` for that snapshot.
KNOWN_GEMMA_MODEL_SHA256: dict[str, str] = {}


class GemmaLocalRuntimeError(RuntimeError):
    """Raised when the local-Gemma path can't proceed (weights, deps, etc.)."""


def _sha256_file(path: str | Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _expected_sha256_for_model_path(model_path: str | Path) -> str | None:
    override = os.getenv("RUHU_GEMMA_MODEL_SHA256")
    if override:
        return override.strip() or None
    return KNOWN_GEMMA_MODEL_SHA256.get(Path(model_path).name)


class TransformersGemmaBackend:
    """Loads Gemma 4 weights via Hugging Face Transformers.

    The model + processor it exposes are fed to a
    ``classifier.transformers_backend.TransformersClassifierBackend`` which
    runs the actual classification. This class owns weight loading + SHA
    validation; classification logic lives next door.
    """

    def __init__(self, model_path: str | Path, expected_sha256: str | None = None) -> None:
        self._model_path = str(model_path)
        self._expected_sha256 = expected_sha256 or _expected_sha256_for_model_path(model_path)
        self._lock = threading.Lock()
        self._loaded = False
        self._processor: Any = None
        self._model: Any = None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            import torch  # noqa: F401  - exercised below; fail fast if missing

            model_path = Path(self._model_path)
            if not model_path.exists():
                raise GemmaLocalRuntimeError(
                    f"Gemma model path does not exist: {self._model_path}. "
                    "Point --model-path at a downloaded Gemma 4 directory."
                )
            model_file = model_path / "model.safetensors"
            if not model_file.exists():
                raise GemmaLocalRuntimeError(
                    f"Gemma model weights do not exist at {model_file}. "
                    "Re-download the model into a complete directory before running local Gemma."
                )
            if self._expected_sha256:
                actual_sha256 = _sha256_file(model_file)
                if actual_sha256 != self._expected_sha256:
                    raise GemmaLocalRuntimeError(
                        "Gemma weights failed checksum validation. "
                        f"expected={self._expected_sha256} actual={actual_sha256}. "
                        "The local file is corrupt or from the wrong snapshot; re-download "
                        "the model into a fresh directory."
                    )

            try:
                from safetensors import safe_open
            except Exception as exc:  # pragma: no cover - import failure depends on local env
                raise GemmaLocalRuntimeError(
                    "The local Gemma runtime needs safetensors installed. "
                    "Install the gemma-local extras or run with the Gemma venv."
                ) from exc

            try:
                with safe_open(str(model_file), framework="pt", device="cpu") as tensors:
                    tensors.keys()
            except Exception as exc:
                raise GemmaLocalRuntimeError(
                    f"Gemma weights at {self._model_path} are unreadable. "
                    "The local model file is likely incomplete or corrupt; re-download Gemma 4 "
                    "or point --model-path at a verified directory."
                ) from exc

            try:
                from transformers import AutoModelForCausalLM, AutoProcessor
            except Exception as exc:  # pragma: no cover - import failure depends on local env
                raise GemmaLocalRuntimeError(
                    "The local Python environment cannot import the Gemma 4 Transformers classes. "
                    "Use a Gemma-capable runtime such as /tmp/ruhu-gemma-arm-venv/bin/python."
                ) from exc

            self._processor = AutoProcessor.from_pretrained(self._model_path)
            self._model = AutoModelForCausalLM.from_pretrained(self._model_path)
            self._loaded = True

    @property
    def processor(self) -> Any:
        self._ensure_loaded()
        return self._processor

    @property
    def model(self) -> Any:
        self._ensure_loaded()
        return self._model


@dataclass(slots=True)
class GemmaLocalInterpreter(SemanticInterpreter):
    """SemanticInterpreter that delegates to a ``PrefillClassifier``.

    The classifier emits one label from the step's outcome catalog; the
    interpreter projects the label onto an authored ``OutcomeCondition``
    transition (or onto a kernel-injected universal outcome) via the
    shared ``result_to_routing_events`` helper. UNKNOWN, out-of-catalog,
    and backend errors all land on ``family="routing", name="classifier_unavailable"``
    so the kernel falls through to ``OtherwiseCondition`` consistently.
    """

    classifier: PrefillClassifier
    model_name: str | None = None

    def interpret(
        self,
        *,
        agent_document: AgentDocument,
        step: Step,
        agent_id: str,
        agent_name: str,
        conversation_facts: dict[str, object],
        turn: RuntimeTurn,
    ) -> list[SemanticEventRecord]:
        text = (turn.text or "").strip()
        if not text:
            return []

        candidate_labels = outcome_catalog_for_step(step)
        if not candidate_labels:
            return []

        prefix, suffix = build_classifier_prompt(
            agent_document, step, user_text=text, facts=conversation_facts
        )
        request = ClassificationRequest(
            agent_id=agent_id,
            agent_version_id=agent_document.version,
            step_id=step.id,
            step_name=step.name,
            step_summary=summarize_step(step),
            user_text=text,
            candidate_labels=candidate_labels,
            prefix=prefix,
            suffix=suffix,
        )
        result = self.classifier.classify(request)
        return result_to_routing_events(
            result,
            step=step,
            candidate_labels=candidate_labels,
            model_name=self.model_name,
            strategy="prefill",
        )


def build_gemma_local_interpreter(
    model_path: str | Path = "/tmp/gemma-4-E4B-it",
    *,
    expected_sha256: str | None = None,
) -> GemmaLocalInterpreter:
    """Construct a Gemma-backed interpreter ready for the kernel.

    Wires ``TransformersGemmaBackend`` (weight loader) →
    ``TransformersClassifierBackend`` (constrained-decode classifier) →
    ``GemmaLocalInterpreter`` (SemanticInterpreter shim).
    """
    from .classifier.transformers_backend import TransformersClassifierBackend

    weights = TransformersGemmaBackend(model_path, expected_sha256=expected_sha256)
    classifier = TransformersClassifierBackend.from_gemma_backend(weights)
    return GemmaLocalInterpreter(
        classifier=classifier,
        model_name=Path(model_path).name,
    )
