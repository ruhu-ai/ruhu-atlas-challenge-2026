"""Tests for src/ruhu/classifier/dispatcher.py — WI-4.3."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ruhu.agent_document import AgentDocument, Scenario, Step, StepCompletion, StepTransition
from ruhu.classifier.dispatcher import ClassifierDispatcher
from ruhu.classifier.prompt import (
    build_classifier_prefix,
    build_classifier_suffix,
    reset_prefix_cache,
)
from ruhu.classifier.protocol import (
    ClassificationRequest,
    ClassificationResult,
)
from ruhu.classifier.registry import promote_to_production, register_candidate
from ruhu.db_models import AgentRecord, Base, ClassifierLoraRecord
from ruhu.schemas import OutcomeCondition


@pytest.fixture(autouse=True)
def _clear_prefix_cache() -> None:
    reset_prefix_cache()
    yield
    reset_prefix_cache()


@dataclass(slots=True)
class _FakeClassifier:
    """Records the request seen and returns a fixed result."""

    result: ClassificationResult = ClassificationResult(
        chosen_label="transfer_status",
        confidence=0.9,
        backend="vllm",
    )
    last_request: ClassificationRequest | None = None
    call_count: int = 0

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        self.last_request = request
        self.call_count += 1
        return self.result


def _step(**overrides) -> Step:
    """Build a step whose outcome catalog includes ``transfer_status``.

    Edge-owned-outcomes contract: the catalog is sourced from
    ``OutcomeCondition`` transitions, never from ``event_hints``.
    """
    base_transitions = [
        StepTransition(
            id="t_transfer",
            when=OutcomeCondition(
                event="transfer_status",
                description="User asks about a transfer.",
            ),
            to_step_id="entry",
        ),
    ]
    base = dict(
        id="entry",
        name="Entry",
        description="Triage the user.",
        transitions=base_transitions,
    )
    base.update(overrides)
    return Step(**base)


def _doc(step: Step | None = None, version: str = "v1") -> AgentDocument:
    step = step or _step(completion=StepCompletion(disposition="resolved"))
    return AgentDocument(
        version=version,
        start_scenario_id="main",
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )


# ── prepares request before dispatch ───────────────────────────────────────


def test_dispatcher_populates_prefix_and_suffix() -> None:
    classifier = _FakeClassifier()
    step = _step(completion=StepCompletion(disposition="resolved"))
    document = _doc(step)
    dispatcher = ClassifierDispatcher(classifier=classifier)

    dispatcher.classify(
        agent_document=document,
        step=step,
        agent_id="agent_a",
        user_text="where is my money",
    )

    request = classifier.last_request
    assert request is not None
    assert request.prefix == build_classifier_prefix(document, step)
    assert request.suffix == build_classifier_suffix("where is my money")


def test_dispatcher_threads_facts_into_suffix_when_step_opts_in() -> None:
    classifier = _FakeClassifier()
    step = _step(
        completion=StepCompletion(disposition="resolved"),
        classifier_uses_facts=["account_status"],
    )
    document = _doc(step)
    dispatcher = ClassifierDispatcher(classifier=classifier)

    dispatcher.classify(
        agent_document=document,
        step=step,
        agent_id="agent_a",
        user_text="hi",
        facts={"account_status": "frozen"},
    )

    request = classifier.last_request
    assert request is not None
    assert "frozen" not in request.prefix  # facts never enter the cached prefix
    assert "account_status=frozen" in request.suffix


def test_dispatcher_passes_through_agent_metadata() -> None:
    classifier = _FakeClassifier()
    step = _step(completion=StepCompletion(disposition="resolved"))
    document = _doc(step, version="v2")
    dispatcher = ClassifierDispatcher(classifier=classifier)

    dispatcher.classify(
        agent_document=document,
        step=step,
        agent_id="agent_xyz",
        user_text="hi",
    )

    request = classifier.last_request
    assert request is not None
    assert request.agent_id == "agent_xyz"
    assert request.agent_version_id == "v2"
    assert request.step_id == "entry"
    assert request.step_name == "Entry"
    assert "transfer_status" in request.candidate_labels


def test_dispatcher_falls_back_to_step_name_when_no_description() -> None:
    """step_summary defaults to step.name when description is missing/empty."""
    step = Step(
        id="entry",
        name="Entry",
        description=None,
        transitions=[
            StepTransition(
                id="t_x",
                when=OutcomeCondition(
                    event="some_outcome",
                    description="An authored outcome description.",
                ),
                to_step_id="entry",
            ),
        ],
        completion=StepCompletion(disposition="resolved"),
    )
    document = _doc(step)
    classifier = _FakeClassifier()
    dispatcher = ClassifierDispatcher(classifier=classifier)
    dispatcher.classify(
        agent_document=document, step=step, agent_id="a", user_text="hi"
    )
    assert classifier.last_request is not None
    assert classifier.last_request.step_summary == "Entry"


def test_dispatcher_returns_classifier_result_unchanged() -> None:
    classifier = _FakeClassifier(
        result=ClassificationResult(
            chosen_label="kyc_help",
            confidence=0.42,
            backend="vllm",
            elapsed_ms=87,
        )
    )
    step = _step(completion=StepCompletion(disposition="resolved"))
    dispatcher = ClassifierDispatcher(classifier=classifier)
    result = dispatcher.classify(
        agent_document=_doc(step), step=step, agent_id="a", user_text="hi"
    )
    assert result.chosen_label == "kyc_help"
    assert result.confidence == 0.42
    assert result.elapsed_ms == 87


# ── LoRA resolution via registry ──────────────────────────────────────────


def _registry_session() -> Session:
    """In-memory SQLite session limited to the LoRA-registry tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[AgentRecord.__table__, ClassifierLoraRecord.__table__],
    )
    return Session(engine)


def _seed_agent(session: Session, agent_id: str) -> None:
    now = datetime.now(timezone.utc)
    session.add(
        AgentRecord(
            agent_id=agent_id,
            name=agent_id,
            settings_json={},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


def test_dispatcher_no_session_factory_means_no_lora() -> None:
    classifier = _FakeClassifier()
    step = _step(completion=StepCompletion(disposition="resolved"))
    dispatcher = ClassifierDispatcher(classifier=classifier)
    dispatcher.classify(
        agent_document=_doc(step), step=step, agent_id="a", user_text="hi"
    )
    assert classifier.last_request is not None
    assert classifier.last_request.lora_name is None


def test_dispatcher_resolves_lora_via_registry() -> None:
    session = _registry_session()
    _seed_agent(session, "agent_a")
    record = register_candidate(
        session,
        agent_id="agent_a",
        lora_name="agent-a-prod-v3",
        model_uri="gs://bucket/agent-a-v3.safetensors",
        version="v3",
    )
    promote_to_production(session, lora_id=record.lora_id)

    classifier = _FakeClassifier()
    step = _step(completion=StepCompletion(disposition="resolved"))
    dispatcher = ClassifierDispatcher(
        classifier=classifier,
        registry_session_factory=lambda: session,
    )
    dispatcher.classify(
        agent_document=_doc(step),
        step=step,
        agent_id="agent_a",
        user_text="hi",
    )
    assert classifier.last_request is not None
    assert classifier.last_request.lora_name == "agent-a-prod-v3"


def test_dispatcher_resolves_per_step_lora_when_present() -> None:
    """Per-step LoRA wins over per-agent LoRA at the dispatcher boundary."""
    session = _registry_session()
    _seed_agent(session, "agent_a")
    agent_wide = register_candidate(
        session,
        agent_id="agent_a",
        lora_name="agent-a-wide",
        model_uri="gs://bucket/wide.safetensors",
        version="v1",
    )
    promote_to_production(session, lora_id=agent_wide.lora_id)
    step_specific = register_candidate(
        session,
        agent_id="agent_a",
        step_id="entry",
        lora_name="agent-a-entry-step",
        model_uri="gs://bucket/entry.safetensors",
        version="v1",
    )
    promote_to_production(session, lora_id=step_specific.lora_id)

    classifier = _FakeClassifier()
    step = _step(completion=StepCompletion(disposition="resolved"))
    dispatcher = ClassifierDispatcher(
        classifier=classifier,
        registry_session_factory=lambda: session,
    )
    dispatcher.classify(
        agent_document=_doc(step),
        step=step,
        agent_id="agent_a",
        user_text="hi",
    )
    assert classifier.last_request is not None
    assert classifier.last_request.lora_name == "agent-a-entry-step"


def test_dispatcher_scopes_lora_by_organization_id() -> None:
    session = _registry_session()
    _seed_agent(session, "shared_agent")
    org_a = register_candidate(
        session,
        agent_id="shared_agent",
        organization_id="org-a",
        lora_name="org-a-lora",
        model_uri="gs://bucket/org-a.safetensors",
        version="v1",
    )
    promote_to_production(session, lora_id=org_a.lora_id)

    classifier = _FakeClassifier()
    step = _step(completion=StepCompletion(disposition="resolved"))
    dispatcher = ClassifierDispatcher(
        classifier=classifier,
        registry_session_factory=lambda: session,
    )

    dispatcher.classify(
        agent_document=_doc(step),
        step=step,
        agent_id="shared_agent",
        user_text="hi",
        organization_id="org-a",
    )
    assert classifier.last_request is not None
    assert classifier.last_request.lora_name == "org-a-lora"

    # Different org sees no LoRA
    classifier.last_request = None
    dispatcher.classify(
        agent_document=_doc(step),
        step=step,
        agent_id="shared_agent",
        user_text="hi",
        organization_id="org-b",
    )
    assert classifier.last_request is not None
    assert classifier.last_request.lora_name is None


def test_dispatcher_session_is_closed_after_use() -> None:
    """Dispatcher must close the session it pulls from the factory."""

    class _TrackedSession:
        def __init__(self) -> None:
            self.closed = False
            self._real = _registry_session()

        def execute(self, *args, **kwargs):
            return self._real.execute(*args, **kwargs)

        def close(self) -> None:
            self.closed = True
            self._real.close()

    tracked = _TrackedSession()
    classifier = _FakeClassifier()
    step = _step(completion=StepCompletion(disposition="resolved"))
    dispatcher = ClassifierDispatcher(
        classifier=classifier,
        registry_session_factory=lambda: tracked,
    )
    dispatcher.classify(
        agent_document=_doc(step), step=step, agent_id="a", user_text="hi"
    )
    assert tracked.closed is True
