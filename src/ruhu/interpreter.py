from __future__ import annotations

from dataclasses import dataclass

from .agent_document import AgentDocument, Step
from .schemas import RuntimeTurn, SemanticEventRecord


@dataclass(slots=True)
class SemanticInterpreter:
    """Generic semantic interpreter interface.

    The kernel stays domain-agnostic. Any domain-specific or customer-specific
    understanding logic must live in a separate interpreter implementation.
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
