"""In-process Transformers backend for the prefill-first classifier.

Stage-1 implementation. Wraps an existing ``TransformersGemmaBackend`` (or any
HF model + processor) and runs a constrained ``model.generate`` so the output
is physically restricted to the legal label catalog. Confidence is the joint
softmax probability of the chosen multi-token label, derived from per-step
logprobs.

Stage 3 (vLLM backend) replaces this for production. Until then, this is the
backend for ``RUHU_INTERPRETER=gemma_local`` agents.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from .constrained import (
    UNKNOWN_LABEL,
    ConstrainedLabelProcessor,
    LabelTokenIds,
    build_label_catalog,
    confidence_from_token_logprobs,
)
from .protocol import ClassificationRequest, ClassificationResult


@dataclass(slots=True)
class TransformersClassifierBackend:
    """Implements ``PrefillClassifier`` against an in-process HF model.

    The ``model`` and ``processor`` come from
    ``ruhu.gemma_local.TransformersGemmaBackend._ensure_loaded`` (or any
    ``transformers.AutoModelForCausalLM`` + ``AutoProcessor`` pair). This class
    does not load weights itself; it expects them already initialized.

    Catalog tokenization is cached per ``(agent_version_id, step_id)`` because
    rebuilding the trie on every call is wasted work and the catalog is stable
    across turns at the same step.
    """

    model: Any
    processor: Any
    _catalog_cache: dict[tuple[str, str, str], LabelTokenIds]
    _catalog_lock: Lock
    _backend_name: str = "transformers"

    @classmethod
    def from_gemma_backend(cls, gemma_backend: Any) -> "TransformersClassifierBackend":
        """Convenience constructor for the existing in-process Gemma path.

        Triggers weight load on the wrapped backend if it hasn't loaded yet,
        then captures the model + processor handles.
        """
        gemma_backend._ensure_loaded()
        return cls(
            model=gemma_backend._model,
            processor=gemma_backend._processor,
            _catalog_cache={},
            _catalog_lock=Lock(),
        )

    def _resolve_catalog(
        self,
        request: ClassificationRequest,
    ) -> LabelTokenIds:
        cache_key = (request.agent_id, request.step_id, request.agent_version_id)
        with self._catalog_lock:
            cached = self._catalog_cache.get(cache_key)
            if cached is not None:
                return cached
            labels = build_label_catalog(request.candidate_labels, include_unknown=True)
            # Gemma 4 loads as a multimodal `Gemma4Processor` (text + vision +
            # audio); LabelTokenIds.build needs the underlying text tokenizer's
            # `.encode`. Fall back to the processor itself for older / pure
            # text models that don't wrap a tokenizer.
            tokenizer = getattr(self.processor, "tokenizer", self.processor)
            built = LabelTokenIds.build(labels, tokenizer)
            self._catalog_cache[cache_key] = built
            return built

    def _build_prompt(self, request: ClassificationRequest) -> str:
        """Return the prompt bytes to feed the model.

        Callers must populate ``request.prefix`` and ``request.suffix`` via
        ``classifier.prompt.build_classifier_prompt`` so the prefix is
        byte-identical for the cache key and matches what training and
        eval used. The dispatcher (and ``GemmaLocalInterpreter``) handle
        this; backend implementations only concatenate.
        """
        if request.prefix is None or request.suffix is None:
            raise ValueError(
                "ClassificationRequest is missing prefix/suffix; "
                "use classifier.prompt.build_classifier_prompt before dispatch"
            )
        return request.prefix + request.suffix

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        result = self._classify_inner(request)
        _emit_classifier_metrics(request, result)
        return result

    def _classify_inner(self, request: ClassificationRequest) -> ClassificationResult:
        if not request.user_text or not request.candidate_labels:
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend=self._backend_name,
                error="empty_request",
            )

        try:
            import torch
        except ImportError as exc:
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend=self._backend_name,
                error=f"torch_unavailable: {exc}",
            )

        catalog = self._resolve_catalog(request)
        prompt = self._build_prompt(request)

        model_device = getattr(self.model, "device", None)
        if model_device is None:
            model_device = next(self.model.parameters()).device
        inputs = self.processor(text=prompt, return_tensors="pt").to(model_device)
        prompt_length = int(inputs["input_ids"].shape[-1])

        max_label_tokens = max(len(ids) for ids in catalog.label_token_ids)
        max_new_tokens = max_label_tokens + 1  # +1 for optional eos

        start = time.perf_counter()
        try:
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    output_scores=True,
                    return_dict_in_generate=True,
                    logits_processor=[
                        ConstrainedLabelProcessor(
                            labels=catalog,
                            prompt_lengths=[prompt_length],
                        )
                    ],
                )
        except Exception as exc:
            return ClassificationResult(
                chosen_label=None,
                confidence=0.0,
                backend=self._backend_name,
                error=f"generate_failed: {exc}",
            )
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        sequences = outputs.sequences[0]
        decoded_token_ids = tuple(int(t) for t in sequences[prompt_length:].tolist())

        chosen_token_ids = decoded_token_ids
        if catalog.eos_token_id is not None:
            for idx, tok in enumerate(decoded_token_ids):
                if tok == catalog.eos_token_id:
                    chosen_token_ids = decoded_token_ids[:idx]
                    break

        chosen_label = catalog.match_label(chosen_token_ids)

        per_token_logprobs: list[float] = []
        for step_idx, scores_at_step in enumerate(outputs.scores):
            if step_idx >= len(chosen_token_ids):
                break
            log_softmax = torch.log_softmax(scores_at_step[0], dim=-1)
            per_token_logprobs.append(float(log_softmax[chosen_token_ids[step_idx]].item()))

        confidence = confidence_from_token_logprobs(per_token_logprobs)

        chosen_intent: str | None
        if chosen_label is None or chosen_label == UNKNOWN_LABEL:
            chosen_intent = None
        elif chosen_label not in request.candidate_labels:
            chosen_intent = None
        else:
            chosen_intent = chosen_label

        decode_logprobs = (
            {chosen_label: sum(per_token_logprobs)} if chosen_label else {}
        )

        return ClassificationResult(
            chosen_label=chosen_intent,
            confidence=confidence,
            decode_logprobs=decode_logprobs,
            cache_hit=False,
            prefill_tokens=prompt_length,
            decode_tokens=len(chosen_token_ids),
            lora_name=request.lora_name,
            backend=self._backend_name,
            elapsed_ms=elapsed_ms,
            error=None if chosen_intent is not None else "unknown_label",
        )


def _emit_classifier_metrics(
    request: ClassificationRequest,
    result: ClassificationResult,
) -> None:
    """Emit Prometheus metrics for one classify() call.

    Defined at module level so it survives the import-time guard around
    ``observability.metrics`` if Prometheus is unavailable in some
    environments. Errors here never bubble out — instrumentation must
    not break classification.
    """
    try:
        from ..observability.metrics import (
            classifier_confidence,
            classifier_decisions_total,
            classifier_errors_total,
            classifier_prefill_tokens_total,
            classifier_request_duration_seconds,
            classifier_unknown_total,
        )
    except Exception:
        return

    backend = str(result.backend or "unknown")
    lora = str(result.lora_name or "base")
    cache_hit_label = "true" if result.cache_hit else "false"

    try:
        classifier_request_duration_seconds.labels(
            agent_id=request.agent_id,
            step_id=request.step_id,
            backend=backend,
            lora=lora,
            cache_hit=cache_hit_label,
        ).observe(result.elapsed_ms / 1000.0)

        if result.chosen_label is not None:
            classifier_decisions_total.labels(
                agent_id=request.agent_id,
                step_id=request.step_id,
                chosen_label=result.chosen_label,
                backend=backend,
                lora=lora,
            ).inc()
        else:
            classifier_unknown_total.labels(
                agent_id=request.agent_id,
                step_id=request.step_id,
                backend=backend,
            ).inc()

        if result.confidence is not None and result.confidence > 0.0:
            classifier_confidence.labels(
                agent_id=request.agent_id,
                step_id=request.step_id,
            ).observe(result.confidence)

        if result.prefill_tokens:
            classifier_prefill_tokens_total.labels(
                agent_id=request.agent_id,
                step_id=request.step_id,
                cache_hit=cache_hit_label,
            ).inc(result.prefill_tokens)

        if result.error and result.chosen_label is None:
            classifier_errors_total.labels(
                error_kind=result.error.split(":")[0],
                backend=backend,
            ).inc()
    except Exception:
        # Never let instrumentation break the request path.
        pass
