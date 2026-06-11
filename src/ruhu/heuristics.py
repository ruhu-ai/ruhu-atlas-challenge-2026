from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .agent_document import AgentDocument, Step
from .interpreter import SemanticInterpreter
from .schemas import RuntimeTurn, SemanticEventRecord

# Pluggable registry so tests (or out-of-tree packages) can register
# named interpreters without baking demo content into production code.
_INTERPRETER_FACTORIES: dict[str, Callable[[], SemanticInterpreter]] = {}


def register_interpreter_factory(
    name: str, factory: Callable[[], SemanticInterpreter]
) -> None:
    """Register a named interpreter factory.

    Intended for test fixtures and extension packages.  Production code
    should not rely on this registry.
    """
    _INTERPRETER_FACTORIES[name] = factory


def unregister_interpreter_factory(name: str) -> None:
    _INTERPRETER_FACTORIES.pop(name, None)


@dataclass(slots=True)
class KeywordInterpreter(SemanticInterpreter):
    """Reference/demo interpreter only.

    This is intentionally outside the kernel so the runtime remains generic.
    Production systems should replace this with a bounded classifier service.
    """

    rules: dict[str, tuple[str, ...]] = field(default_factory=dict)

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
        text = (turn.text or "").strip().lower()
        if not text:
            return []

        events: list[SemanticEventRecord] = []
        for event_name, keywords in self.rules.items():
            if not any(keyword in text for keyword in keywords):
                continue
            if event_name == "close":
                # Terminal signal — kept distinct from workflow routing so
                # analytics adapters can detect explicit goodbyes.
                events.append(
                    SemanticEventRecord(
                        family="terminal_requested",
                        name=event_name,
                        source="classifier",
                        confidence=0.75,
                    )
                )
                continue
            # Edge-owned outcomes: emit a ``routing.outcome_resolved``
            # event the kernel matches against ``OutcomeCondition.event``.
            # The kernel resolves ``transition_id`` via its own walk, so
            # we leave it absent here.
            events.append(
                SemanticEventRecord(
                    family="routing",
                    name="outcome_resolved",
                    source="classifier",
                    confidence=0.75,
                    payload={"event": event_name},
                )
            )
            # Analytics: also emit the ``intent_detected`` event so the
            # ``analytics_tagging`` subsystem records a classification for
            # downstream review/dashboards. Workflow routing and analytics
            # are independent concerns — production replaces this test
            # interpreter with separate writers per concern.
            events.append(
                SemanticEventRecord(
                    family="intent_detected",
                    name=event_name,
                    source="classifier",
                    confidence=0.75,
                )
            )
        # The kernel auto-emits ``uncertain_understanding:fallback_text``
        # when no ``routing.outcome_resolved`` is present (see
        # ``ConversationKernel._resolve_semantic_events``), so we don't
        # duplicate the signal here.
        return events


@dataclass
class NullInterpreter(SemanticInterpreter):
    """No-op interpreter for the WI-5.5 ``RUHU_CLASSIFIER_MODE=off`` kill-switch.

    Returns an empty event list regardless of input. The kernel's
    ``_resolve_semantic_events`` then treats the turn the same way it
    would if the configured interpreter had no intent: pre_classified
    events still pass through, and no-intent turns follow the normal
    unknown-understanding path. Use this when the prefill classifier subsystem
    itself is the failure mode and disaster recovery needs to disable
    classification entirely without unloading the runtime.
    """

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
        return []


def interpreter_for_classifier_mode(
    interpreter: SemanticInterpreter | None,
    *,
    classifier_mode: str,
) -> SemanticInterpreter | None:
    """Apply the WI-5.5 kill-switch.

    When ``classifier_mode == "off"`` the configured interpreter is
    replaced with ``NullInterpreter`` regardless of what the operator
    wired up. Other values pass the interpreter through unchanged.
    """
    if classifier_mode == "off":
        return NullInterpreter()
    return interpreter


def interpreter_by_name(name: str | None) -> SemanticInterpreter | None:
    """Resolve a named interpreter from the registry.

    Tests register keyword interpreters via ``tests/_fixtures/interpreters.py``
    on test-session startup (``tests/conftest.py``).  Production registers
    LLM-backed interpreters (``gemma_local``, ``vertex``, etc.) via
    ``register_interpreter_factory``.
    """
    if not name:
        return None
    factory = _INTERPRETER_FACTORIES.get(name)
    if factory is not None:
        return factory()
    raise ValueError(f"unknown interpreter: {name}")
