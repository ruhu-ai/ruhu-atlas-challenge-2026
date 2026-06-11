"""Deterministic prompt assembler for the prefill-first classifier.

The prefix is the load-bearing optimisation of the prefill-first design:
vLLM's prefix cache hits only when the bytes match exactly across turns at
the same step. This module is the single source of truth for that prefix —
every classifier backend (transformers, vLLM, future ones) and every
training-data emitter must call ``build_classifier_prefix`` so they all
agree on what bytes constitute "the prefix" for a given
``(agent_version_id, step_id)``.

Spec: ``docs/pre-fill-intent-classifier-design/02-architecture-spec.md``
§Prompt assembly. Cache key per the spec:
``(agent_id, agent_version_id, step_id)`` — but ``agent_version_id`` alone
is globally unique in Ruhu (UUID primary key on ``agent_versions``), so the
cache uses ``(agent_version_id, step_id)``.

The classifier's catalog is **edge-owned**: every entry comes from an
``OutcomeCondition`` transition on the step. Universal outcomes
(``audio_check`` and friends) the kernel handles framework-side are
appended below so the classifier always has them available.
"""
from __future__ import annotations

from threading import Lock

from ..agent_document import AgentDocument, Step, step_capability_flags
from ..schemas import OutcomeCondition
from ..state_summary import summarize_step
from .constrained import UNKNOWN_LABEL

SYSTEM_MESSAGE = (
    "You classify the user's current turn for a Ruhu step-native assistant."
)

# Universal outcomes the kernel handles framework-side (see
# ``ConversationKernel._generic_intent_response``). They are appended to
# every step's catalog so a classifier trained on agent-specific outcomes
# can still pick them up. Author-defined outcomes whose ``event`` collides
# with one of these names will shadow the universal description — the
# author's intent wins (validated per-step by ``OutcomeCondition.event``
# uniqueness within the step).
UNIVERSAL_OUTCOMES: dict[str, str] = {
    "audio_check": (
        "The user is checking whether the assistant can hear them, is "
        "connected, or is still on the line."
    ),
    "agent_identity_question": (
        "The user is asking who the assistant is, what role it has, or "
        "what kind of assistant it is."
    ),
    "agent_capability_question": (
        "The user is asking what the assistant can do, what help it can "
        "provide, or what it can do for them."
    ),
    "activity_status_question": (
        "The user is asking what the assistant is doing right now or "
        "what is happening in the conversation."
    ),
}

_PREFIX_CACHE: dict[tuple[str, str], str] = {}
_PREFIX_CACHE_LOCK = Lock()


def build_classifier_prefix(
    agent_document: AgentDocument,
    step: Step,
) -> str:
    """Return the byte-identical classifier prefix.

    Memoised by ``(agent_document.version, step.id)``. Two calls with
    semantically equivalent inputs produce the *same Python string object*
    after the first call, so the caller can compare prefixes by identity
    when validating cache-key invariants.
    """
    cache_key = (agent_document.version, step.id)
    cached = _PREFIX_CACHE.get(cache_key)
    if cached is not None:
        return cached
    with _PREFIX_CACHE_LOCK:
        cached = _PREFIX_CACHE.get(cache_key)
        if cached is not None:
            return cached
        built = _assemble_prefix(step)
        _PREFIX_CACHE[cache_key] = built
        return built


def build_classifier_suffix(
    user_text: str,
    *,
    facts: dict[str, object] | None = None,
    fact_names: list[str] | None = None,
) -> str:
    """Return the variable per-turn suffix.

    NOT cached — every component here varies per request. Spec-pinned
    format is ``"User message: {text}\\nOutcome:"`` with the trailing
    ``Outcome:`` as the prefill anchor.

    When ``fact_names`` is non-empty AND ``facts`` carries values for any
    of them (per WI-6.12 ``Step.classifier_uses_facts``), the named facts
    are injected as a ``Known facts:`` block *before* the user message.
    Names are emitted in ``fact_names`` order; missing facts and ``None``
    values are skipped silently. Fact values land in the suffix only —
    never in the cached prefix — so prefix-cache hits survive.
    """
    fact_block = ""
    if fact_names and facts:
        rendered = [
            f"{name}={facts[name]}"
            for name in fact_names
            if name in facts and facts[name] is not None
        ]
        if rendered:
            fact_block = "Known facts: " + ", ".join(rendered) + "\n"
    return f"{fact_block}User message: {user_text}\nOutcome:"


def build_classifier_prompt(
    agent_document: AgentDocument,
    step: Step,
    *,
    user_text: str,
    facts: dict[str, object] | None = None,
) -> tuple[str, str]:
    """Convenience: returns ``(prefix, suffix)`` together.

    Equivalent to ``(build_classifier_prefix(...), build_classifier_suffix(...))``.
    Use the individual helpers when you need only one half (training-data
    export needs only the prefix; per-turn dispatch needs both).

    When ``step.classifier_uses_facts`` is set and ``facts`` carries
    matching values, those facts land in the suffix per WI-6.12.
    """
    prefix = build_classifier_prefix(agent_document, step)
    suffix = build_classifier_suffix(
        user_text,
        facts=facts,
        fact_names=step.classifier_uses_facts,
    )
    return prefix, suffix


def outcome_catalog_for_step(step: Step) -> dict[str, str]:
    """Return the ``{event: description}`` outcome catalog for one step.

    Sources:

    - Every ``OutcomeCondition`` transition on the step contributes its
      ``event`` (label) and ``description`` (LLM-evaluated meaning).
    - The universal outcomes the kernel handles framework-side are
      appended unless the step already authors a transition for the same
      event (uniqueness invariant on the step ensures this is well-defined).

    Sorted by event ascending — ordering is part of the cache-key
    invariant, so author re-ordering of transitions does not invalidate
    the prefix cache.
    """
    catalog: dict[str, str] = {}
    for transition in step.transitions:
        when = transition.when
        if isinstance(when, OutcomeCondition):
            catalog[when.event] = when.description
    for event, description in UNIVERSAL_OUTCOMES.items():
        catalog.setdefault(event, description)
    return {event: catalog[event] for event in sorted(catalog)}


def reset_prefix_cache() -> None:
    """Clear the module-level prefix cache. For tests only."""
    with _PREFIX_CACHE_LOCK:
        _PREFIX_CACHE.clear()


def _assemble_prefix(step: Step) -> str:
    catalog = outcome_catalog_for_step(step)
    catalog_lines = [
        f"- {event}: {_normalise_description(description)}"
        for event, description in catalog.items()
    ]
    catalog_lines.append(
        f"- {UNKNOWN_LABEL}: none of the above match the user's message"
    )
    return (
        f"{SYSTEM_MESSAGE}\n"
        "\n"
        f"Step: {_normalise_inline(step.name)}\n"
        f"Step summary: {_normalise_inline(summarize_step(step))}\n"
        f"Step capabilities: {_format_capabilities(step)}\n"
        "\n"
        "Workflow outcomes (choose exactly one):\n"
        + "\n".join(catalog_lines)
        + "\n\n"
    )


def _format_capabilities(step: Step) -> str:
    flags = step_capability_flags(step)
    enabled = sorted(name for name, on in flags.items() if on)
    return ", ".join(enabled) if enabled else "none"


def _normalise_inline(text: str) -> str:
    """Collapse internal whitespace runs to single spaces; strip ends.

    Spec §Prompt assembly rule 2: normalise whitespace so two
    semantically-equivalent inputs produce byte-identical prefixes.
    """
    return " ".join((text or "").split())


def _normalise_description(text: str) -> str:
    return _normalise_inline(text)
