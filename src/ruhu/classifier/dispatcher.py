"""WI-4.3 — classifier dispatcher.

Single seam between callers (interpreter, training trace_export, dev
scripts) and the configured ``PrefillClassifier`` backend. The
dispatcher's job is the small but load-bearing layer of state most
callers don't want to recompute per turn:

1. **Prompt assembly** — call ``classifier.prompt.build_classifier_prompt``
   with the right step + facts, populate
   ``ClassificationRequest.prefix`` / ``suffix``. Without this the
   transformers backend raises ``missing_prefix_suffix`` (per
   WI-4.1's contract refactor) and the vLLM backend falls back to
   no prefix-cache hits.

2. **LoRA resolution** — call ``classifier.registry.resolve_lora`` so the
   request carries the right ``lora_name`` for the agent / step. Per
   spec resolution order: per-step → per-agent → ``None`` (base model).

Spec: ``docs/pre-fill-intent-classifier-design/02-architecture-spec.md``
§Architecture and ``07-work-items.md`` §WI-4.3.

Usage:

```python
dispatcher = ClassifierDispatcher(
    classifier=build_classifier_from_settings(settings, ...),
    registry_session_factory=lambda: Session(engine),
)
result = dispatcher.classify(
    agent_document=document,
    step=step,
    user_text=turn.text,
    facts=conversation_facts,
    organization_id=conversation.organization_id,
)
```

The dispatcher stays Protocol-friendly: ``registry_session_factory``
defaults to ``None`` so callers without a registry (dev / smoke / tests)
can still build a dispatcher and the LoRA layer cleanly degrades to
"no LoRA, use base model".
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from ..agent_document import AgentDocument, Step
from .prompt import build_classifier_prompt
from .protocol import ClassificationRequest, ClassificationResult, PrefillClassifier
from .registry import resolve_lora

SessionFactory = Callable[[], object]
"""Callable returning a SQLAlchemy ``Session`` (or any session-like).

Imported lazily so the dispatcher doesn't take a hard SQLAlchemy
dependency for callers that don't use the registry.
"""


@dataclass(slots=True)
class ClassifierDispatcher:
    """Stateless wrapper that prepares + dispatches one classifier turn."""

    classifier: PrefillClassifier
    registry_session_factory: SessionFactory | None = None

    def classify(
        self,
        *,
        agent_document: AgentDocument,
        step: Step,
        agent_id: str,
        user_text: str,
        facts: dict[str, object] | None = None,
        organization_id: str | None = None,
    ) -> ClassificationResult:
        """Build the request, resolve the LoRA, dispatch to the backend."""
        candidate_labels = _outcome_catalog_for_step(step)
        prefix, suffix = build_classifier_prompt(
            agent_document, step, user_text=user_text, facts=facts
        )
        lora_name = self._resolve_lora(
            agent_id=agent_id,
            step_id=step.id,
            organization_id=organization_id,
        )
        request = ClassificationRequest(
            agent_id=agent_id,
            agent_version_id=agent_document.version,
            step_id=step.id,
            step_name=step.name,
            step_summary=(step.description or "").strip() or step.name,
            user_text=user_text,
            candidate_labels=candidate_labels,
            prefix=prefix,
            suffix=suffix,
            lora_name=lora_name,
        )
        return self.classifier.classify(request)

    def _resolve_lora(
        self,
        *,
        agent_id: str,
        step_id: str,
        organization_id: str | None,
    ) -> str | None:
        if self.registry_session_factory is None:
            return None
        with _session_scope(self.registry_session_factory) as session:
            return resolve_lora(
                session,
                agent_id=agent_id,
                step_id=step_id,
                organization_id=organization_id,
            )


@contextmanager
def _session_scope(factory: SessionFactory) -> Iterator[object]:
    session = factory()
    try:
        yield session
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def _outcome_catalog_for_step(step: Step) -> dict[str, str]:
    """Mirror of ``classifier.prompt.outcome_catalog_for_step`` to keep
    the dispatcher's import surface small and avoid an import cycle.
    """
    from .prompt import outcome_catalog_for_step

    return outcome_catalog_for_step(step)


__all__ = [
    "ClassifierDispatcher",
    "SessionFactory",
]
