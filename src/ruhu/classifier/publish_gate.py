"""WI-4.5 — tokenizer-pass publish-gate validation.

Walks every outcome event reachable from an ``AgentDocument`` and warns
when the chosen base model's tokenizer expands the event to more than
``max_tokens`` tokens (default 3 per spec). Long outcome events hurt the
prefill-first design two ways:

1. Constrained-decode latency grows linearly with the number of tokens
   the model has to emit for the chosen label. A 3-token event decodes
   3× as long as a single-token event. Above ~4 tokens the per-call
   latency budget starts to bite into the SLO.

2. Confidence collapses multiplicatively across tokens. A label that
   tokenizes to 5 tokens with 0.95 per-token logprob has a joint
   probability of ``0.95**5 ≈ 0.77`` — the kernel's
   ``Step.confidence_threshold=0.85`` would suppress it incorrectly.

The gate **warns**, never blocks publish — authors keep the freedom to
ship long events when the trade-off is conscious. The runtime adapts
either way.

Spec: ``docs/pre-fill-intent-classifier-design/07-work-items.md`` §WI-4.5.

Wiring:

- The function is tokenizer-agnostic — callers pass a
  ``Callable[[str], int]`` that returns token count for a given
  string. Production wires in
  ``transformers.AutoTokenizer.from_pretrained(model).encode``;
  smoke tests use a heuristic counter.

- Returns ``AgentValidationIssue`` so ``agent_review.py``'s existing
  publish-review pipeline can drop the warnings straight into its
  ``issues`` list. No new render path needed.
"""
from __future__ import annotations

from typing import Callable

from ..agent_document import AgentDocument, AgentValidationIssue
from ..schemas import OutcomeCondition

DEFAULT_MAX_TOKENS = 3


def tokenizer_pass_warnings(
    document: AgentDocument,
    *,
    token_counter: Callable[[str], int],
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[AgentValidationIssue]:
    """Walk every step's authored outcomes; warn on events over ``max_tokens``.

    Universal outcomes (the kernel-injected ``audio_check`` and friends in
    ``classifier.prompt.UNIVERSAL_OUTCOMES``) are intentionally **not**
    walked here — authors can't change those names, so a warning would
    be noise. We check only ``OutcomeCondition`` transitions the author
    wrote.

    Each unique outcome event is checked exactly once even if it shows up
    in several steps — the warning carries the *first* scenario / step
    where the event was seen so authors can navigate to it from the
    publish-review UI.
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")

    seen: dict[str, tuple[str, str]] = {}
    issues: list[AgentValidationIssue] = []
    for scenario in document.scenarios:
        for step in scenario.steps:
            for transition in step.transitions:
                when = transition.when
                if not isinstance(when, OutcomeCondition):
                    continue
                outcome_event = when.event
                if outcome_event in seen:
                    continue
                token_count = token_counter(outcome_event)
                seen[outcome_event] = (scenario.id, step.id)
                if token_count > max_tokens:
                    issues.append(
                        AgentValidationIssue(
                            severity="warning",
                            code="classifier.outcome_event_long",
                            message=(
                                f"Outcome event {outcome_event!r} tokenizes to "
                                f"{token_count} tokens (> {max_tokens}). Consider a "
                                "shorter event id; long labels increase classifier "
                                "latency and depress joint-confidence."
                            ),
                            scenario_id=scenario.id,
                            step_id=step.id,
                        )
                    )
    return issues


def heuristic_token_counter(text: str) -> int:
    """Conservative heuristic — splits on whitespace and underscores.

    For pre-flight checks when the real tokenizer isn't loaded. Tends
    to *under*-estimate token count vs HF tokenizers (which often split
    further on subwords like 'tion', 'ed', etc.), so heuristic warnings
    are a lower bound — anything flagged here is definitely a problem;
    cleaner events may still trip the real tokenizer at deploy time.
    """
    if not text:
        return 0
    pieces: list[str] = []
    for chunk in text.replace("__", " ").split():
        pieces.extend(part for part in chunk.split("_") if part)
    return max(len(pieces), 1)


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "heuristic_token_counter",
    "tokenizer_pass_warnings",
]
