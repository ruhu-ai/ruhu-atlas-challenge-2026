"""RP-1.1 / RP-1.2: append-only turn log, DB-enforced dedupe, snapshot-as-fold.

The turn log's ``UNIQUE (conversation_id, dedupe_key)`` constraint is the
authoritative duplicate-turn guard — the kernel's in-memory
``processed_dedupe_keys`` check is only a fast path. These tests pin:

- seq assignment and ordering (in-memory and Postgres stores)
- DuplicateTurnError on dedupe collisions, with full rollback
- the concurrent-duplicate race: two simultaneous identical turns commit
  exactly one turn row and one state change
- fold(log) == snapshot: the conversation row is reconstructible from the
  turn log's per-turn state snapshots
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from ruhu import ConversationKernel
from ruhu.agent_document import AgentDocument, Scenario, Step, StepTransition
from ruhu.db import build_session_factory
from ruhu.heuristics import KeywordInterpreter
from ruhu.schemas import (
    ConversationState,
    OtherwiseCondition,
    RuntimeTurn,
    TurnLogEntry,
)
from ruhu.stores import (
    DuplicateTurnError,
    InMemoryTurnLogStore,
    SQLAlchemyConversationStore,
    SQLAlchemyTraceStore,
    SQLAlchemyTurnLogStore,
    rebuild_conversation_state,
)


def _document() -> AgentDocument:
    return AgentDocument(
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id="start",
                steps=[
                    Step(
                        id="start",
                        name="Start",
                        say="Hello.",
                        transitions=[
                            StepTransition(
                                id="t_stay",
                                when=OtherwiseCondition(),
                                to_step_id="start",
                            )
                        ],
                    )
                ],
            )
        ],
    )


def _turn(key: str, *, text: str = "hello") -> RuntimeTurn:
    return RuntimeTurn(
        turn_id=key,
        dedupe_key=key,
        channel="web_chat",
        modality="text",
        event_type="user_message",
        text=text,
        received_at=datetime.now(timezone.utc),
    )


def _entry(conversation_id: str, key: str) -> TurnLogEntry:
    return TurnLogEntry(
        conversation_id=conversation_id,
        turn_id=key,
        dedupe_key=key,
        step_before="start",
        step_after="start",
        state_after={},
    )


def _seed_conversation(store: SQLAlchemyConversationStore, conversation_id: str) -> None:
    store.save(
        ConversationState(
            conversation_id=conversation_id,
            agent_id="agent",
            agent_version_id="v1",
            step_id="start",
            updated_at=datetime.now(timezone.utc),
        )
    )


class TestInMemoryTurnLogStore:
    def test_assigns_monotonic_seq(self) -> None:
        store = InMemoryTurnLogStore()
        first = store.append(_entry("conv", "k1"))
        second = store.append(_entry("conv", "k2"))
        assert (first.seq, second.seq) == (1, 2)
        assert [item.dedupe_key for item in store.by_conversation("conv")] == ["k1", "k2"]

    def test_rejects_duplicate_dedupe_key(self) -> None:
        store = InMemoryTurnLogStore()
        store.append(_entry("conv", "k1"))
        with pytest.raises(DuplicateTurnError):
            store.append(_entry("conv", "k1"))
        assert len(store.by_conversation("conv")) == 1

    def test_same_key_in_different_conversations_is_allowed(self) -> None:
        store = InMemoryTurnLogStore()
        store.append(_entry("conv_a", "k1"))
        store.append(_entry("conv_b", "k1"))
        assert len(store.by_conversation("conv_a")) == 1
        assert len(store.by_conversation("conv_b")) == 1


class TestSQLAlchemyTurnLogStore:
    def test_appends_orders_and_increments_counter(self, postgres_database_url_factory) -> None:
        session_factory = build_session_factory(postgres_database_url_factory())
        conversations = SQLAlchemyConversationStore(session_factory)
        store = SQLAlchemyTurnLogStore(session_factory)
        _seed_conversation(conversations, "conv_seq")

        first = store.append(_entry("conv_seq", "k1"))
        second = store.append(_entry("conv_seq", "k2"))

        assert (first.seq, second.seq) == (1, 2)
        entries = store.by_conversation("conv_seq")
        assert [item.seq for item in entries] == [1, 2]
        assert [item.dedupe_key for item in entries] == ["k1", "k2"]

    def test_rejects_duplicate_and_rolls_back(self, postgres_database_url_factory) -> None:
        session_factory = build_session_factory(postgres_database_url_factory())
        conversations = SQLAlchemyConversationStore(session_factory)
        store = SQLAlchemyTurnLogStore(session_factory)
        _seed_conversation(conversations, "conv_dup")

        store.append(_entry("conv_dup", "k1"))
        with pytest.raises(DuplicateTurnError):
            store.append(_entry("conv_dup", "k1"))

        entries = store.by_conversation("conv_dup")
        assert len(entries) == 1
        # The rolled-back attempt must not burn a seq value.
        assert store.append(_entry("conv_dup", "k2")).seq == 2

    def test_unknown_conversation_raises(self, postgres_database_url_factory) -> None:
        session_factory = build_session_factory(postgres_database_url_factory())
        store = SQLAlchemyTurnLogStore(session_factory)
        with pytest.raises(KeyError):
            store.append(_entry("conv_missing", "k1"))


def _build_sql_kernel(database_url: str) -> ConversationKernel:
    session_factory = build_session_factory(database_url)
    return ConversationKernel(
        conversation_store=SQLAlchemyConversationStore(session_factory),
        trace_store=SQLAlchemyTraceStore(session_factory),
        turn_log_store=SQLAlchemyTurnLogStore(session_factory),
        interpreter=KeywordInterpreter(rules={}),
    )


class TestKernelTurnLog:
    def test_each_turn_appends_a_log_row(self, postgres_database_url_factory) -> None:
        kernel = _build_sql_kernel(postgres_database_url_factory())
        document = _document()
        kernel.start_conversation("conv_log", agent_document=document, agent_id="agent")

        kernel.process_turn("conv_log", _turn("t1"), agent_document=document)
        kernel.process_turn("conv_log", _turn("t2"), agent_document=document)

        entries = kernel.turn_log_store.by_conversation("conv_log")
        keys = [item.dedupe_key for item in entries]
        assert "t1" in keys and "t2" in keys
        seqs = [item.seq for item in entries]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    def test_sequential_duplicate_uses_fast_path(self, postgres_database_url_factory) -> None:
        kernel = _build_sql_kernel(postgres_database_url_factory())
        document = _document()
        kernel.start_conversation("conv_fast", agent_document=document, agent_id="agent")

        first = kernel.process_turn("conv_fast", _turn("t1"), agent_document=document)
        second = kernel.process_turn("conv_fast", _turn("t1"), agent_document=document)

        assert first.chosen_action.reason != "duplicate_dedupe_key"
        assert second.chosen_action.reason == "duplicate_dedupe_key"
        entries = kernel.turn_log_store.by_conversation("conv_fast")
        assert [item.dedupe_key for item in entries].count("t1") == 1

    def test_concurrent_duplicate_turns_commit_exactly_once(
        self, postgres_database_url_factory
    ) -> None:
        """Both requests pass the in-memory fast path; the DB constraint must
        let exactly one commit and report the other as a duplicate."""
        database_url = postgres_database_url_factory()
        session_factory = build_session_factory(database_url)
        rendezvous = threading.Barrier(2)

        class RendezvousInterpreter(KeywordInterpreter):
            """Holds both turns mid-processing (after the dedupe fast path,
            before commit) so the race is deterministic."""

            _local = threading.local()

            def interpret(self, *args, **kwargs):
                if not getattr(self._local, "met", False):
                    self._local.met = True
                    try:
                        rendezvous.wait(timeout=10)
                    except threading.BrokenBarrierError:
                        pass
                return super().interpret(*args, **kwargs)

        kernel = ConversationKernel(
            conversation_store=SQLAlchemyConversationStore(session_factory),
            trace_store=SQLAlchemyTraceStore(session_factory),
            turn_log_store=SQLAlchemyTurnLogStore(session_factory),
            interpreter=RendezvousInterpreter(rules={}),
        )
        document = _document()
        kernel.start_conversation("conv_race", agent_document=document, agent_id="agent")

        def run() -> str:
            result = kernel.process_turn("conv_race", _turn("t_race"), agent_document=document)
            return result.chosen_action.reason or ""

        with ThreadPoolExecutor(max_workers=2) as pool:
            reasons = sorted(pool.map(lambda _: run(), range(2)))

        assert reasons.count("duplicate_dedupe_key") == 1, reasons
        entries = kernel.turn_log_store.by_conversation("conv_race")
        assert [item.dedupe_key for item in entries].count("t_race") == 1

    def test_rebuild_conversation_state_matches_snapshot(
        self, postgres_database_url_factory
    ) -> None:
        """RP-1.2: fold(log) == snapshot for the load-bearing fields."""
        kernel = _build_sql_kernel(postgres_database_url_factory())
        document = _document()
        kernel.start_conversation("conv_fold", agent_document=document, agent_id="agent")
        kernel.process_turn("conv_fold", _turn("t1"), agent_document=document)
        kernel.process_turn("conv_fold", _turn("t2"), agent_document=document)
        kernel.process_turn("conv_fold", _turn("t3"), agent_document=document)

        rebuilt = rebuild_conversation_state(kernel.turn_log_store, "conv_fold")
        loaded = kernel.conversation_store.load("conv_fold")

        assert rebuilt is not None and loaded is not None
        assert rebuilt.conversation_id == loaded.conversation_id
        assert rebuilt.step_id == loaded.step_id
        assert rebuilt.facts == loaded.facts
        assert rebuilt.status == loaded.status
        assert rebuilt.outcome == loaded.outcome
        assert rebuilt.processed_dedupe_keys == loaded.processed_dedupe_keys
        assert rebuilt.control_state == loaded.control_state
