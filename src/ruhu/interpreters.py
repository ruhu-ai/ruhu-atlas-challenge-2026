from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agent_document import AgentDocument, Step
from .gemma_local import build_gemma_local_interpreter
from .heuristics import interpreter_by_name
from .interpreter import SemanticInterpreter
from .schemas import RuntimeTurn, SemanticEventRecord


@dataclass(slots=True)
class LazyInterpreter(SemanticInterpreter):
    factory: Callable[[], SemanticInterpreter]
    _resolved: SemanticInterpreter | None = field(default=None, init=False, repr=False)

    def _inner(self) -> SemanticInterpreter:
        if self._resolved is None:
            self._resolved = self.factory()
        return self._resolved

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
        return self._inner().interpret(
            agent_document=agent_document,
            step=step,
            agent_id=agent_id,
            agent_name=agent_name,
            conversation_facts=conversation_facts,
            turn=turn,
        )


@dataclass(slots=True)
class AgentInterpreterRouter(SemanticInterpreter):
    default_interpreter: SemanticInterpreter | None = None
    agent_interpreters: dict[str, SemanticInterpreter] = field(default_factory=dict)

    def resolve(self, agent_id: str) -> SemanticInterpreter | None:
        return self.agent_interpreters.get(agent_id, self.default_interpreter)

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
        interpreter = self.resolve(agent_id)
        if interpreter is None:
            return []
        return interpreter.interpret(
            agent_document=agent_document,
            step=step,
            agent_id=agent_id,
            agent_name=agent_name,
            conversation_facts=conversation_facts,
            turn=turn,
        )


def build_named_interpreter(
    name: str | None,
    *,
    model_path: str | Path = "/tmp/gemma-4-E4B-it",
) -> SemanticInterpreter | None:
    if not name:
        return None
    if name == "gemma_local":
        return build_gemma_local_interpreter(model_path)
    return interpreter_by_name(name)


def build_lazy_named_interpreter(
    name: str | None,
    *,
    model_path: str | Path = "/tmp/gemma-4-E4B-it",
) -> SemanticInterpreter | None:
    if not name:
        return None
    return LazyInterpreter(
        factory=lambda n=name, p=Path(model_path): build_named_interpreter(n, model_path=p) or SemanticInterpreter()
    )


def build_interpreter_router(
    *,
    default_interpreter_name: str | None = None,
    agent_interpreters: dict[str, str] | None = None,
    model_path: str | Path = "/tmp/gemma-4-E4B-it",
) -> SemanticInterpreter | None:
    mapping = agent_interpreters or {}
    default_interpreter = build_lazy_named_interpreter(default_interpreter_name, model_path=model_path)
    built_mapping = {
        agent_id: build_lazy_named_interpreter(name, model_path=model_path)
        for agent_id, name in mapping.items()
        if name
    }
    built_mapping = {
        agent_id: interpreter
        for agent_id, interpreter in built_mapping.items()
        if interpreter is not None
    }
    if default_interpreter is None and not built_mapping:
        return None
    return AgentInterpreterRouter(
        default_interpreter=default_interpreter,
        agent_interpreters=built_mapping,
    )
