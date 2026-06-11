"""Constrained decoding for the prefill-first classifier.

A ``LogitsProcessor`` that masks the model's output distribution at every decode
step so only legal label tokens can be emitted. The model is *physically*
incapable of producing an out-of-catalog string.

Implementation: a token-level FSM over the catalog. At decode step *t*, given
the partial output prefix ``p[:t]``, only tokens that can lead to a complete
label string get non-``-inf`` logits.

See docs/pre-fill-intent-classifier-design/02-architecture-spec.md
§Constrained decoding.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import torch
    from transformers import PreTrainedTokenizerBase


# Sentinel inserted at the end of every label catalog so the model has an
# explicit "no match" exit. Authors don't need to declare this themselves.
UNKNOWN_LABEL = "unknown"


@dataclass(slots=True)
class LabelTokenIds:
    """Tokenized label catalog with prefix-trie metadata for constrained decode.

    The trie answers, given a partial decode prefix (sequence of token ids),
    which next tokens are legal. Built once per ``(tokenizer, labels)`` and
    cached by callers.
    """

    labels: tuple[str, ...]
    label_token_ids: tuple[tuple[int, ...], ...]
    eos_token_id: int | None
    _trie: dict[tuple[int, ...], frozenset[int]] = field(default_factory=dict)
    _terminals: frozenset[tuple[int, ...]] = frozenset()

    @classmethod
    def build(
        cls,
        labels: list[str],
        tokenizer: "PreTrainedTokenizerBase",
    ) -> "LabelTokenIds":
        """Tokenize each label and build the trie.

        Labels are tokenized exactly as the model would emit them at decode
        time (no special tokens, no chat template). Multi-token labels are
        supported.
        """
        token_id_lists: list[tuple[int, ...]] = []
        for label in labels:
            ids = tokenizer.encode(label, add_special_tokens=False)
            if not ids:
                raise ValueError(f"label {label!r} tokenized to empty sequence")
            token_id_lists.append(tuple(ids))

        trie: dict[tuple[int, ...], set[int]] = {}
        terminals: set[tuple[int, ...]] = set()
        for tokens in token_id_lists:
            for cut in range(len(tokens)):
                prefix = tokens[:cut]
                next_token = tokens[cut]
                trie.setdefault(prefix, set()).add(next_token)
            terminals.add(tokens)

        return cls(
            labels=tuple(labels),
            label_token_ids=tuple(token_id_lists),
            eos_token_id=tokenizer.eos_token_id,
            _trie={k: frozenset(v) for k, v in trie.items()},
            _terminals=frozenset(terminals),
        )

    def allowed_next_tokens(self, decoded_prefix: tuple[int, ...]) -> frozenset[int]:
        """Return the set of token ids allowed at the next decode step.

        - Empty prefix → first tokens of every label.
        - Mid-label prefix → next tokens that continue any matching label.
        - Terminal prefix (matches a complete label) → only ``eos_token_id``
          (or empty if eos is undefined; caller should stop).
        """
        if decoded_prefix in self._terminals:
            if self.eos_token_id is None:
                return frozenset()
            return frozenset({self.eos_token_id})
        return self._trie.get(decoded_prefix, frozenset())

    def match_label(self, decoded_prefix: tuple[int, ...]) -> str | None:
        """If ``decoded_prefix`` exactly matches a label's tokenization, return it."""
        if decoded_prefix not in self._terminals:
            return None
        for label, tokens in zip(self.labels, self.label_token_ids):
            if tokens == decoded_prefix:
                return label
        return None


class ConstrainedLabelProcessor:
    """A HuggingFace-compatible ``LogitsProcessor``.

    Subclasses ``transformers.LogitsProcessor`` at runtime to avoid importing
    transformers at module-load (so this file can be type-checked without the
    extra installed). The shape matches HF's contract:

    ``__call__(input_ids: LongTensor[B, T], scores: FloatTensor[B, V]) -> FloatTensor[B, V]``

    For each batch row, we walk back through ``input_ids`` to find the decode
    prefix (the part *after* the prompt), then mask ``scores`` so only tokens
    in ``allowed_next_tokens(prefix)`` retain their original logits; everything
    else becomes ``-inf``.
    """

    def __init__(
        self,
        labels: LabelTokenIds,
        prompt_lengths: list[int],
    ) -> None:
        """
        ``prompt_lengths[batch_idx]`` is the input-ids length for that batch
        before any decoded tokens. Used to slice off the prompt and recover the
        decode prefix.
        """
        self.labels = labels
        self.prompt_lengths = prompt_lengths

    def __call__(self, input_ids: Any, scores: Any) -> Any:
        import torch

        masked_scores = torch.full_like(scores, float("-inf"))
        for batch_idx in range(input_ids.shape[0]):
            prompt_len = self.prompt_lengths[batch_idx]
            decoded_prefix = tuple(int(t) for t in input_ids[batch_idx, prompt_len:].tolist())
            allowed = self.labels.allowed_next_tokens(decoded_prefix)
            if not allowed:
                continue
            allowed_tensor = torch.tensor(
                sorted(allowed),
                dtype=torch.long,
                device=scores.device,
            )
            masked_scores[batch_idx, allowed_tensor] = scores[batch_idx, allowed_tensor]
        return masked_scores


def confidence_from_token_logprobs(token_logprobs: list[float]) -> float:
    """Joint softmax probability of the full multi-token label.

    ``confidence = exp(sum(logprob_t for t in label_tokens))``

    This is the model's probability of emitting the chosen label given the
    prompt, conditioned on the constrained-decode mask. See
    docs/pre-fill-intent-classifier-design/02-architecture-spec.md
    §Confidence for the rationale.
    """
    if not token_logprobs:
        return 0.0
    return math.exp(sum(token_logprobs))


def build_label_catalog(
    valid_intents: dict[str, str],
    *,
    include_unknown: bool = True,
) -> list[str]:
    """Return the ordered label catalog the constrained decoder enforces.

    Sorted deterministically so the prefix-cache key in
    ``classifier.prompt`` stays byte-identical across runs at a given step
    (Stage 4 prerequisite). ``unknown`` is always last when included.
    """
    intents = sorted(valid_intents.keys())
    if include_unknown:
        intents = [name for name in intents if name != UNKNOWN_LABEL]
        intents.append(UNKNOWN_LABEL)
    return intents
