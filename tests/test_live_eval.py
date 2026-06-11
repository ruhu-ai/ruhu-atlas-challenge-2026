"""
Phase 1 tests for the continuous (live) evaluation foundation.

Covers the four building blocks of ``ruhu.live_eval``:
  1. Sampling — deterministic + tier-aware
  2. GoalCompletionScorer — heuristic correctness on representative traces
  3. InMemoryLiveScoreStore — append + lookup
  4. LiveEvalWorker — submit / drain / status / lifecycle

The worker tests drive the loop synchronously via ``process_once()`` instead
of starting a daemon thread — gives deterministic asserts and no flaky
timing dependencies.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ruhu.live_eval import (
    DimensionRollup,
    GoalCompletionScorer,
    InMemoryLiveScoreStore,
    InstrumentedTraceStore,
    LiveEvalRuntime,
    LiveEvalWorker,
    LiveTurnScore,
    SamplingPolicy,
    QUALITY_DIMENSIONS,
    _score_bucket,
    rollup_by_dimension,
    should_sample,
)
from ruhu.runtime_config import RuntimeSettings
from ruhu.stores import InMemoryTraceStore
from ruhu.schemas import (
    ActionRecord,
    ToolCallRecord,
    TurnTrace,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_trace(
    *,
    trace_id: str = "trace-1",
    conversation_id: str = "conv-1",
    organization_id: str | None = "org-1",
    agent_id: str = "agent-1",
    step_before: str = "discovery",
    step_after: str = "discovery",
    error_kind: str = "none",
    tool_calls: list[ToolCallRecord] | None = None,
) -> TurnTrace:
    return TurnTrace(
        trace_id=trace_id,
        conversation_id=conversation_id,
        organization_id=organization_id,
        turn_id=f"turn-{trace_id}",
        agent_id=agent_id,
        step_before=step_before,
        step_after=step_after,
        error_kind=error_kind,  # type: ignore[arg-type]
        chosen_action=ActionRecord(type="reply", reason="test"),
        tool_calls=list(tool_calls or []),
    )


# ── Quality taxonomy ──────────────────────────────────────────────────────────

class TestQualityDimensions:
    def test_taxonomy_is_fixed_and_complete(self) -> None:
        """Adding a fifth dimension is a deliberate platform decision."""
        assert QUALITY_DIMENSIONS == (
            "correctness",
            "helpfulness",
            "safety",
            "goal_completion",
        )


# ── LiveTurnScore validation ──────────────────────────────────────────────────

class TestLiveTurnScore:
    def test_accepts_in_range_score(self) -> None:
        score = LiveTurnScore(
            trace_id="t-1",
            conversation_id="c-1",
            organization_id=None,
            agent_id="a-1",
            dimension="goal_completion",
            score=0.7,
            scorer_name="x",
            scorer_version="v1",
        )
        assert score.score == 0.7
        # scored_at is auto-populated when not provided
        assert isinstance(score.scored_at, datetime)
        assert score.scored_at.tzinfo is timezone.utc

    @pytest.mark.parametrize("invalid", [-0.01, 1.01, -1.0, 2.0])
    def test_rejects_out_of_range_score(self, invalid: float) -> None:
        """Scores outside [0, 1] are a contract violation, not a typo."""
        with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
            LiveTurnScore(
                trace_id="t-1",
                conversation_id="c-1",
                organization_id=None,
                agent_id="a-1",
                dimension="goal_completion",
                score=invalid,
                scorer_name="x",
                scorer_version="v1",
            )


# ── Sampling ──────────────────────────────────────────────────────────────────

class TestSamplingPolicy:
    def test_default_rate_used_when_tier_unknown(self) -> None:
        policy = SamplingPolicy(default_rate=0.5)
        assert policy.rate_for("nonexistent_tier") == 0.5
        assert policy.rate_for(None) == 0.5

    def test_per_tier_overrides_default(self) -> None:
        policy = SamplingPolicy(
            default_rate=0.01,
            per_tier_rate={"enterprise": 0.0, "free": 0.5},
        )
        assert policy.rate_for("enterprise") == 0.0
        assert policy.rate_for("free") == 0.5
        assert policy.rate_for("starter") == 0.01

    def test_invalid_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
            SamplingPolicy(default_rate=1.5)
        with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
            SamplingPolicy(per_tier_rate={"x": -0.1})


class TestShouldSample:
    def test_zero_rate_never_samples(self) -> None:
        policy = SamplingPolicy(default_rate=0.0)
        for i in range(50):
            assert should_sample(trace_id=f"trace-{i}", policy=policy) is False

    def test_full_rate_always_samples(self) -> None:
        policy = SamplingPolicy(default_rate=1.0)
        for i in range(50):
            assert should_sample(trace_id=f"trace-{i}", policy=policy) is True

    def test_decision_is_deterministic(self) -> None:
        """Same trace_id → same decision under the same policy.

        This invariant lets replicas independently reach identical sampling
        outcomes without coordination.
        """
        policy = SamplingPolicy(default_rate=0.3)
        trace_id = "stable-trace-id"
        first = should_sample(trace_id=trace_id, policy=policy)
        for _ in range(100):
            assert should_sample(trace_id=trace_id, policy=policy) is first

    def test_distribution_roughly_matches_rate(self) -> None:
        """Across many trace_ids the sampling rate should be close to target.

        We allow a wide tolerance — this is a sanity check that the hash
        bucketing is uniform-ish, not a strict statistical test.
        """
        policy = SamplingPolicy(default_rate=0.2)
        sampled = sum(
            1
            for i in range(2000)
            if should_sample(trace_id=f"trace-{i}", policy=policy)
        )
        # Expected ~400; allow ±150 (so tests stay green even if hash
        # distribution is slightly skewed).
        assert 250 <= sampled <= 550, f"sampled={sampled} far from expected ~400"

    def test_tier_specific_rate_used(self) -> None:
        policy = SamplingPolicy(
            default_rate=1.0,
            per_tier_rate={"enterprise": 0.0},
        )
        # Enterprise tier never sampled even though default is 1.0
        for i in range(20):
            assert should_sample(
                trace_id=f"trace-{i}", tier="enterprise", policy=policy
            ) is False


# ── GoalCompletionScorer ──────────────────────────────────────────────────────

class TestGoalCompletionScorer:
    def setup_method(self) -> None:
        self.scorer = GoalCompletionScorer()

    def test_error_kind_drives_score_to_zero(self) -> None:
        for kind in ("llm_error", "tool_timeout", "kernel_panic", "guard_rejected"):
            trace = _make_trace(error_kind=kind)
            score = self.scorer(trace)
            assert score.score == 0.0
            assert score.dimension == "goal_completion"
            assert kind in (score.notes or "")

    def test_step_advancement_scores_well(self) -> None:
        trace = _make_trace(step_before="discovery", step_after="qualification")
        score = self.scorer(trace)
        assert score.score == 0.7
        assert "discovery" in (score.notes or "")
        assert "qualification" in (score.notes or "")

    def test_no_progress_with_successful_tool_scores_partial(self) -> None:
        trace = _make_trace(
            step_before="discovery",
            step_after="discovery",
            tool_calls=[
                ToolCallRecord(tool_ref="lookup", status="success", reason="ok"),
            ],
        )
        score = self.scorer(trace)
        assert score.score == 0.5

    def test_stuck_with_no_tools_scores_low(self) -> None:
        trace = _make_trace(step_before="discovery", step_after="discovery")
        score = self.scorer(trace)
        assert score.score == 0.3

    def test_failed_tool_does_not_save_stuck_turn(self) -> None:
        """A failed tool isn't 'productive work' — same score as no tools."""
        trace = _make_trace(
            step_before="discovery",
            step_after="discovery",
            tool_calls=[
                ToolCallRecord(tool_ref="lookup", status="error", reason="boom"),
            ],
        )
        score = self.scorer(trace)
        assert score.score == 0.3

    def test_score_propagates_trace_identity(self) -> None:
        trace = _make_trace(
            trace_id="t-xyz",
            conversation_id="c-xyz",
            organization_id="org-xyz",
            agent_id="agent-xyz",
        )
        score = self.scorer(trace)
        assert score.trace_id == "t-xyz"
        assert score.conversation_id == "c-xyz"
        assert score.organization_id == "org-xyz"
        assert score.agent_id == "agent-xyz"
        assert score.scorer_name == "goal_completion_heuristic"
        assert score.scorer_version == "v1"


# ── InMemoryLiveScoreStore ────────────────────────────────────────────────────

class TestInMemoryLiveScoreStore:
    def test_append_and_lookup_by_trace(self) -> None:
        store = InMemoryLiveScoreStore()
        s1 = LiveTurnScore(
            trace_id="t-1", conversation_id="c-1", organization_id=None,
            agent_id="a-1", dimension="goal_completion", score=0.7,
            scorer_name="goal", scorer_version="v1",
        )
        s2 = LiveTurnScore(
            trace_id="t-2", conversation_id="c-1", organization_id=None,
            agent_id="a-1", dimension="goal_completion", score=0.3,
            scorer_name="goal", scorer_version="v1",
        )
        store.append(s1)
        store.append(s2)
        assert len(store) == 2
        assert store.list_for_trace("t-1") == [s1]
        assert store.list_for_conversation("c-1") == [s1, s2]
        assert store.list_for_trace("missing") == []


# ── LiveEvalWorker ────────────────────────────────────────────────────────────

class TestLiveEvalWorker:
    def setup_method(self) -> None:
        self.store = InMemoryLiveScoreStore()
        self.worker = LiveEvalWorker(
            scorers=[GoalCompletionScorer()],
            store=self.store,
            sampling_policy=SamplingPolicy(default_rate=1.0),  # sample everything
            tick_seconds=0.5,
            max_batch=10,
        )

    def test_submit_then_process_once_persists_score(self) -> None:
        trace = _make_trace(step_after="qualification")
        accepted = self.worker.submit(trace)
        assert accepted is True
        processed = self.worker.process_once()
        assert processed == 1
        scores = self.store.list_for_trace(trace.trace_id)
        assert len(scores) == 1
        assert scores[0].dimension == "goal_completion"
        assert scores[0].score == 0.7

    def test_sampler_can_skip_submission(self) -> None:
        worker = LiveEvalWorker(
            scorers=[GoalCompletionScorer()],
            store=InMemoryLiveScoreStore(),
            sampling_policy=SamplingPolicy(default_rate=0.0),  # never sample
        )
        trace = _make_trace()
        assert worker.submit(trace) is False
        assert worker.process_once() == 0

    def test_process_once_drains_up_to_max_batch(self) -> None:
        worker = LiveEvalWorker(
            scorers=[GoalCompletionScorer()],
            store=self.store,
            sampling_policy=SamplingPolicy(default_rate=1.0),
            max_batch=3,
        )
        for i in range(7):
            worker.submit(_make_trace(trace_id=f"t-{i}"))
        # First drain: 3 traces × 1 scorer = 3 scores
        assert worker.process_once() == 3
        # Second drain: 3 more
        assert worker.process_once() == 3
        # Third drain: 1 remaining
        assert worker.process_once() == 1
        # Fourth drain: empty
        assert worker.process_once() == 0

    def test_scorer_exception_is_isolated(self) -> None:
        """A failing scorer must not stall other scorers or future turns."""
        class BoomScorer:
            name = "boom"
            version = "v1"
            dimension = "correctness"

            def __call__(self, trace: TurnTrace) -> LiveTurnScore:
                raise RuntimeError("scorer is broken")

        worker = LiveEvalWorker(
            scorers=[BoomScorer(), GoalCompletionScorer()],
            store=self.store,
            sampling_policy=SamplingPolicy(default_rate=1.0),
        )
        worker.submit(_make_trace(step_after="qualification"))
        # process_once returns the number of *successful* scores.
        # BoomScorer fails, GoalCompletion succeeds → 1 score persisted.
        assert worker.process_once() == 1
        scores = self.store.list_for_trace("trace-1")
        assert len(scores) == 1
        assert scores[0].scorer_name == "goal_completion_heuristic"

    def test_status_reflects_last_run(self) -> None:
        before = self.worker.status()
        assert before.last_run_at is None
        assert before.last_processed_count == 0

        self.worker.submit(_make_trace())
        self.worker.process_once()

        after = self.worker.status()
        assert after.last_run_at is not None
        assert after.last_processed_count == 1
        assert after.last_error is None

    def test_tier_resolver_applied(self) -> None:
        seen_orgs: list[str | None] = []

        def _resolver(org_id: str | None) -> str | None:
            seen_orgs.append(org_id)
            return "enterprise"

        worker = LiveEvalWorker(
            scorers=[GoalCompletionScorer()],
            store=InMemoryLiveScoreStore(),
            sampling_policy=SamplingPolicy(
                default_rate=1.0,
                per_tier_rate={"enterprise": 0.0},  # opt-out
            ),
            tier_resolver=_resolver,
        )
        accepted = worker.submit(_make_trace(organization_id="org-x"))
        # Resolver was consulted, returned "enterprise", which has rate=0
        assert accepted is False
        assert seen_orgs == ["org-x"]


# ── _score_bucket helper ──────────────────────────────────────────────────────

class TestScoreBucket:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.0, "very_low"),
            (0.19, "very_low"),
            (0.2, "low"),
            (0.39, "low"),
            (0.4, "medium"),
            (0.59, "medium"),
            (0.6, "high"),
            (0.79, "high"),
            (0.8, "very_high"),
            (1.0, "very_high"),
        ],
    )
    def test_bucket_boundaries(self, score: float, expected: str) -> None:
        assert _score_bucket(score) == expected


# ── InstrumentedTraceStore ───────────────────────────────────────────────────

class TestInstrumentedTraceStore:
    """The wrapper is a drop-in replacement for ruhu.stores.TraceStore.

    Goal: every kernel ``trace_store.append(trace)`` call also feeds the
    live-eval worker, but eval failures NEVER affect the kernel's view of
    the trace write.
    """

    def test_append_persists_to_inner_and_submits_to_worker(self) -> None:
        inner = InMemoryTraceStore()
        worker_store = InMemoryLiveScoreStore()
        worker = LiveEvalWorker(
            scorers=[GoalCompletionScorer()],
            store=worker_store,
            sampling_policy=SamplingPolicy(default_rate=1.0),
        )
        wrapped = InstrumentedTraceStore(inner=inner, worker=worker)
        trace = _make_trace(step_after="qualification")

        wrapped.append(trace)

        # Inner store received the trace
        assert inner.all() == [trace]
        # Worker received the trace via submit
        worker.process_once()
        assert len(worker_store.list_for_trace(trace.trace_id)) == 1

    def test_append_failure_in_inner_propagates(self) -> None:
        """If persistence fails, the kernel must see the error.

        Live eval is downstream — it must never mask a real DB failure.
        """
        class _BrokenInner:
            def append(self, trace, *, session=None):
                raise RuntimeError("DB unavailable")
            def all(self, *, organization_id=None): return []
            def by_conversation(self, conversation_id, *, organization_id=None): return []

        submissions: list[TurnTrace] = []
        wrapped = InstrumentedTraceStore(
            inner=_BrokenInner(),
            submit_fn=lambda t: submissions.append(t) or True,
        )

        with pytest.raises(RuntimeError, match="DB unavailable"):
            wrapped.append(_make_trace())

        # Submission must NOT have happened — persistence is the prerequisite.
        assert submissions == []

    def test_submit_failure_does_not_affect_persistence(self) -> None:
        """If the worker is broken, the kernel still sees a successful append."""
        inner = InMemoryTraceStore()

        def _boom_submit(trace: TurnTrace) -> bool:
            raise RuntimeError("worker inbox is wedged")

        wrapped = InstrumentedTraceStore(inner=inner, submit_fn=_boom_submit)
        trace = _make_trace()

        # Must not raise — eval is best-effort
        wrapped.append(trace)
        # Inner persistence still happened
        assert inner.all() == [trace]

    def test_requires_worker_or_submit_fn(self) -> None:
        with pytest.raises(ValueError, match="worker= or submit_fn="):
            InstrumentedTraceStore(inner=InMemoryTraceStore())

    def test_delegates_read_methods_to_inner(self) -> None:
        inner = InMemoryTraceStore()
        wrapped = InstrumentedTraceStore(
            inner=inner,
            submit_fn=lambda _t: True,
        )
        trace_a = _make_trace(trace_id="t-a", conversation_id="c-1")
        trace_b = _make_trace(trace_id="t-b", conversation_id="c-2")
        wrapped.append(trace_a)
        wrapped.append(trace_b)

        # all() returns everything
        assert {t.trace_id for t in wrapped.all()} == {"t-a", "t-b"}
        # by_conversation() filters
        assert [t.trace_id for t in wrapped.by_conversation("c-1")] == ["t-a"]
        assert [t.trace_id for t in wrapped.by_conversation("c-2")] == ["t-b"]

    def test_sampler_decision_propagates_through_wrapper(self) -> None:
        """When the worker rejects a trace via sampling, the wrapper still
        completes the append cleanly (no exception, inner persistence ok)."""
        inner = InMemoryTraceStore()
        worker = LiveEvalWorker(
            scorers=[GoalCompletionScorer()],
            store=InMemoryLiveScoreStore(),
            sampling_policy=SamplingPolicy(default_rate=0.0),  # never sample
        )
        wrapped = InstrumentedTraceStore(inner=inner, worker=worker)
        wrapped.append(_make_trace())

        assert len(inner.all()) == 1
        worker.process_once()
        # Worker has nothing to process because nothing was sampled
        assert worker.status().last_processed_count == 0


# ── SQLAlchemyLiveScoreStore (Postgres-backed) ────────────────────────────────

class TestSQLAlchemyLiveScoreStore:
    """Postgres-backed store with UPSERT semantics + RLS scoping.

    Uses the project's ``postgres_database_url_factory`` fixture so each
    test run gets a clean schema.
    """

    def _make_score(self, **overrides) -> LiveTurnScore:
        defaults: dict = {
            "trace_id": "trace-sql-1",
            "conversation_id": "conv-sql-1",
            "organization_id": "org-sql-1",
            "agent_id": "agent-sql-1",
            "dimension": "goal_completion",
            "score": 0.7,
            "scorer_name": "goal",
            "scorer_version": "v1",
            "notes": "test",
        }
        defaults.update(overrides)
        return LiveTurnScore(**defaults)

    def test_append_and_lookup(self, postgres_database_url_factory) -> None:
        from ruhu.db import build_session_factory
        from ruhu.live_eval import SQLAlchemyLiveScoreStore

        session_factory = build_session_factory(postgres_database_url_factory())
        store = SQLAlchemyLiveScoreStore(session_factory)

        original = self._make_score()
        store.append(original)

        loaded = store.list_for_trace(original.trace_id)
        assert len(loaded) == 1
        assert loaded[0].score == 0.7
        assert loaded[0].notes == "test"
        assert loaded[0].dimension == "goal_completion"
        assert loaded[0].organization_id == "org-sql-1"

    def test_upsert_on_replay(self, postgres_database_url_factory) -> None:
        """Re-running the same scorer at the same version overwrites — no PK
        conflict, no duplicate row. This makes worker replays idempotent."""
        from ruhu.db import build_session_factory
        from ruhu.live_eval import SQLAlchemyLiveScoreStore

        session_factory = build_session_factory(postgres_database_url_factory())
        store = SQLAlchemyLiveScoreStore(session_factory)

        first = self._make_score(score=0.3, notes="initial")
        store.append(first)
        second = self._make_score(score=0.9, notes="rescored")
        store.append(second)

        rows = store.list_for_trace(first.trace_id)
        assert len(rows) == 1
        assert rows[0].score == 0.9
        assert rows[0].notes == "rescored"

    def test_different_versions_coexist(self, postgres_database_url_factory) -> None:
        """Bumping scorer_version creates a new row (A/B history)."""
        from ruhu.db import build_session_factory
        from ruhu.live_eval import SQLAlchemyLiveScoreStore

        session_factory = build_session_factory(postgres_database_url_factory())
        store = SQLAlchemyLiveScoreStore(session_factory)

        v1 = self._make_score(scorer_version="v1", score=0.4)
        v2 = self._make_score(scorer_version="v2", score=0.8)
        store.append(v1)
        store.append(v2)

        rows = store.list_for_trace(v1.trace_id)
        assert len(rows) == 2
        versions = {r.scorer_version for r in rows}
        assert versions == {"v1", "v2"}

    def test_list_for_conversation_orders_by_scored_at(
        self, postgres_database_url_factory
    ) -> None:
        from datetime import datetime, timedelta, timezone

        from ruhu.db import build_session_factory
        from ruhu.live_eval import SQLAlchemyLiveScoreStore

        session_factory = build_session_factory(postgres_database_url_factory())
        store = SQLAlchemyLiveScoreStore(session_factory)

        base = datetime.now(timezone.utc)
        store.append(self._make_score(
            trace_id="t-late", scored_at=base + timedelta(seconds=30),
        ))
        store.append(self._make_score(
            trace_id="t-early", scored_at=base + timedelta(seconds=10),
        ))
        store.append(self._make_score(
            trace_id="t-mid", scored_at=base + timedelta(seconds=20),
        ))

        rows = store.list_for_conversation("conv-sql-1")
        assert [r.trace_id for r in rows] == ["t-early", "t-mid", "t-late"]

    def test_missing_organization_id_is_rejected(
        self, postgres_database_url_factory
    ) -> None:
        """A score without org_id is unsafe — RLS policies need a tenant
        anchor. We refuse the write rather than insert a tenant-orphan row."""
        from ruhu.db import build_session_factory
        from ruhu.live_eval import SQLAlchemyLiveScoreStore

        session_factory = build_session_factory(postgres_database_url_factory())
        store = SQLAlchemyLiveScoreStore(session_factory)

        # The dataclass itself permits None (matches the in-memory store
        # contract for non-tenant-scoped dev/test usage). The SQL store
        # rejects it because the column is NOT NULL.
        score = self._make_score(organization_id=None)
        with pytest.raises(ValueError, match="organization_id"):
            store.append(score)


# ── RLS policy installation ───────────────────────────────────────────────────

class TestLiveTurnScoresRLS:
    def test_table_appears_in_runtime_rls_list(self) -> None:
        """The auto-discovered RLS table list must include live_turn_scores
        because the model carries an organization_id column.

        This is the integration-level guard: if someone deletes the model's
        organization_id field, the table silently drops out of RLS coverage
        and customer data cross-tenant risk reappears. The startup
        ``assert_rls_policies_healthy`` audit catches it; this test catches
        it in CI before deploy."""
        from ruhu.db import _compute_runtime_tenant_rls_tables

        tables = _compute_runtime_tenant_rls_tables()
        assert "live_turn_scores" in tables

    def test_tenant_scope_policy_is_installed(
        self, postgres_database_url_factory
    ) -> None:
        from sqlalchemy import text

        from ruhu.db import build_session_factory

        session_factory = build_session_factory(postgres_database_url_factory())
        with session_factory.begin() as session:
            rows = session.execute(text(
                """
                SELECT policyname
                FROM pg_policies
                WHERE schemaname = current_schema()
                  AND tablename = 'live_turn_scores'
                """
            )).all()
        names = {r.policyname for r in rows}
        assert "tenant_scope_live_turn_scores" in names


# ── rollup_by_dimension ───────────────────────────────────────────────────────

class TestRollupByDimension:
    """Aggregating scores per dimension is pure computation — exhaustively
    test the math here so the API endpoint can trust its output."""

    def _score(self, dimension, value: float, trace_id: str = "t-1") -> LiveTurnScore:
        return LiveTurnScore(
            trace_id=trace_id,
            conversation_id="c-1",
            organization_id="org-1",
            agent_id="a-1",
            dimension=dimension,
            score=value,
            scorer_name="x",
            scorer_version="v1",
        )

    def test_empty_input_returns_empty(self) -> None:
        assert rollup_by_dimension([]) == {}

    def test_single_dimension_single_score(self) -> None:
        rollups = rollup_by_dimension([self._score("goal_completion", 0.7)])
        assert set(rollups.keys()) == {"goal_completion"}
        rollup = rollups["goal_completion"]
        assert rollup.count == 1
        assert rollup.mean == 0.7
        assert rollup.min == 0.7
        assert rollup.max == 0.7

    def test_multiple_scores_one_dimension(self) -> None:
        scores = [
            self._score("goal_completion", 0.3, trace_id="t-1"),
            self._score("goal_completion", 0.5, trace_id="t-2"),
            self._score("goal_completion", 0.7, trace_id="t-3"),
        ]
        rollup = rollup_by_dimension(scores)["goal_completion"]
        assert rollup.count == 3
        assert rollup.mean == pytest.approx(0.5)
        assert rollup.min == 0.3
        assert rollup.max == 0.7

    def test_multiple_dimensions_independent(self) -> None:
        scores = [
            self._score("goal_completion", 0.7, trace_id="t-1"),
            self._score("goal_completion", 0.9, trace_id="t-2"),
            self._score("safety", 1.0, trace_id="t-3"),
            self._score("safety", 0.4, trace_id="t-4"),
        ]
        rollups = rollup_by_dimension(scores)
        assert rollups["goal_completion"].count == 2
        assert rollups["goal_completion"].mean == pytest.approx(0.8)
        assert rollups["safety"].count == 2
        assert rollups["safety"].mean == pytest.approx(0.7)
        # Empty dimensions are absent — callers shouldn't synthesise.
        assert "correctness" not in rollups
        assert "helpfulness" not in rollups

    def test_returned_object_is_dimension_rollup(self) -> None:
        rollup = rollup_by_dimension([self._score("safety", 0.5)])["safety"]
        assert isinstance(rollup, DimensionRollup)


# ── LiveEvalRuntime ──────────────────────────────────────────────────────────

class TestLiveEvalRuntime:
    def test_default_scorer_is_goal_completion(self) -> None:
        """Constructing without explicit scorers gets the deterministic
        goal-completion scorer — gives Phase 1 something to score even
        before the LLM scorers (Phase 2C+) land."""
        runtime = LiveEvalRuntime(store=InMemoryLiveScoreStore())
        assert len(runtime._scorers) == 1
        assert runtime._scorers[0].name == "goal_completion_heuristic"

    def test_submit_routes_through_worker(self) -> None:
        store = InMemoryLiveScoreStore()
        runtime = LiveEvalRuntime(
            store=store,
            sampling_policy=SamplingPolicy(default_rate=1.0),
        )
        accepted = runtime.submit(_make_trace(step_after="qualified"))
        assert accepted is True
        runtime.worker.process_once()
        assert len(store) == 1

    def test_stop_drains_pending_traces_before_shutting_down(self) -> None:
        """An immediate stop() should not lose already-sampled work — the
        runtime drains the inbox before joining the worker thread."""
        store = InMemoryLiveScoreStore()
        runtime = LiveEvalRuntime(
            store=store,
            sampling_policy=SamplingPolicy(default_rate=1.0),
        )
        runtime.submit(_make_trace())
        # Don't call start() — stay synchronous. stop() should still drain.
        runtime.stop()
        assert len(store) == 1

    def test_lifecycle_is_idempotent(self) -> None:
        runtime = LiveEvalRuntime(store=InMemoryLiveScoreStore())
        runtime.start()
        runtime.start()  # second start is a no-op
        runtime.stop()
        runtime.stop()  # second stop is a no-op


# ── HTTP API router ───────────────────────────────────────────────────────────

def test_build_default_app_installs_live_eval_runtime_when_enabled(
    postgres_database_url_factory,
) -> None:
    from ruhu.api import build_default_app

    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    app = build_default_app(
        agent_root=agent_root_path,
        database_url=postgres_database_url_factory(),
        runtime_settings=RuntimeSettings(
            live_eval_enabled=True,
            live_eval_sample_rate=1.0,
        ),
    )

    assert isinstance(app.state.live_eval_runtime, LiveEvalRuntime)
    assert any(getattr(route, "path", "") == "/live-eval/scores/trace/{trace_id}" for route in app.routes)


class TestLiveEvalAPI:
    """Smoke tests for the read-only /live-eval/* endpoints.

    Uses the FastAPI ``TestClient`` directly against a minimally-wired app
    — we don't need the full ``build_default_app`` machinery here because
    the endpoints only need a runtime + a request-scoped principal.
    """

    def _make_app_with_runtime(
        self,
        runtime: LiveEvalRuntime,
        *,
        principal_org_id: str = "org-1",
    ):
        from fastapi import FastAPI
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.testclient import TestClient

        from ruhu.live_eval_api import install_live_eval_router

        app = FastAPI()

        # Inject a minimal auth_context so require_authenticated_context
        # can pull a principal off request.state. The shape mirrors what
        # AuthContextMiddleware sets up in production.
        from unittest.mock import MagicMock
        principal = MagicMock()
        principal.organization.organization_id = principal_org_id
        principal.user.user_id = "u-1"
        principal.user.is_superuser = False
        ctx = MagicMock()
        ctx.principal = principal

        class _AuthInjector(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.auth_context = ctx
                return await call_next(request)

        app.add_middleware(_AuthInjector)
        install_live_eval_router(app, runtime=runtime)
        return TestClient(app), principal

    def _seed(self, store: InMemoryLiveScoreStore) -> None:
        store.append(LiveTurnScore(
            trace_id="t-1", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="goal_completion", score=0.7,
            scorer_name="goal", scorer_version="v1",
        ))
        store.append(LiveTurnScore(
            trace_id="t-1", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="safety", score=0.9,
            scorer_name="safety_v1", scorer_version="v1",
        ))
        # Seed one row from a different org — the API must hide it.
        store.append(LiveTurnScore(
            trace_id="t-other", conversation_id="c-1", organization_id="org-other",
            agent_id="a-1", dimension="goal_completion", score=0.4,
            scorer_name="goal", scorer_version="v1",
        ))

    def test_list_scores_for_trace_filters_by_org(self) -> None:
        store = InMemoryLiveScoreStore()
        self._seed(store)
        runtime = LiveEvalRuntime(store=store)
        client, _ = self._make_app_with_runtime(runtime)

        resp = client.get("/live-eval/scores/trace/t-1")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        # All rows belong to the principal's org.
        assert all(r["organization_id"] == "org-1" for r in body)
        # Score for the foreign org is invisible.
        resp_other = client.get("/live-eval/scores/trace/t-other")
        assert resp_other.json() == []

    def test_list_scores_for_conversation_returns_all_dimensions(self) -> None:
        store = InMemoryLiveScoreStore()
        self._seed(store)
        runtime = LiveEvalRuntime(store=store)
        client, _ = self._make_app_with_runtime(runtime)

        resp = client.get("/live-eval/scores/conversation/c-1")
        assert resp.status_code == 200
        dims = {r["dimension"] for r in resp.json()}
        assert dims == {"goal_completion", "safety"}

    def test_summary_aggregates_across_dimensions(self) -> None:
        store = InMemoryLiveScoreStore()
        # Seed multiple goal_completion scores so we can verify mean math.
        store.append(LiveTurnScore(
            trace_id="t-1", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="goal_completion", score=0.4,
            scorer_name="goal", scorer_version="v1",
        ))
        store.append(LiveTurnScore(
            trace_id="t-2", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="goal_completion", score=0.8,
            scorer_name="goal", scorer_version="v1",
        ))
        store.append(LiveTurnScore(
            trace_id="t-3", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="safety", score=1.0,
            scorer_name="safety_v1", scorer_version="v1",
        ))
        runtime = LiveEvalRuntime(store=store)
        client, _ = self._make_app_with_runtime(runtime)

        resp = client.get("/live-eval/conversations/c-1/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"] == "c-1"
        assert body["organization_id"] == "org-1"
        assert body["total_score_count"] == 3

        by_dim = {d["dimension"]: d for d in body["dimensions"]}
        assert by_dim["goal_completion"]["count"] == 2
        assert by_dim["goal_completion"]["mean"] == pytest.approx(0.6)
        assert by_dim["goal_completion"]["min"] == 0.4
        assert by_dim["goal_completion"]["max"] == 0.8
        assert by_dim["safety"]["count"] == 1
        assert by_dim["safety"]["mean"] == 1.0
        # correctness/helpfulness are absent — no scores recorded.
        assert "correctness" not in by_dim
        assert "helpfulness" not in by_dim

    def test_summary_excludes_other_org_data(self) -> None:
        """Cross-tenant rollup leak guard."""
        store = InMemoryLiveScoreStore()
        # One row in org-1, one in org-other, both for conversation c-1.
        store.append(LiveTurnScore(
            trace_id="t-mine", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="goal_completion", score=0.5,
            scorer_name="goal", scorer_version="v1",
        ))
        store.append(LiveTurnScore(
            trace_id="t-other", conversation_id="c-1", organization_id="org-other",
            agent_id="a-1", dimension="goal_completion", score=0.9,
            scorer_name="goal", scorer_version="v1",
        ))
        runtime = LiveEvalRuntime(store=store)
        client, _ = self._make_app_with_runtime(runtime, principal_org_id="org-1")

        resp = client.get("/live-eval/conversations/c-1/summary")
        body = resp.json()
        # Only the org-1 row was counted — mean is its own value, not the
        # mix of both rows.
        assert body["total_score_count"] == 1
        gc = next(d for d in body["dimensions"] if d["dimension"] == "goal_completion")
        assert gc["mean"] == 0.5  # NOT 0.7 (the cross-tenant average)
        assert gc["count"] == 1

    def test_unknown_conversation_returns_empty_summary(self) -> None:
        runtime = LiveEvalRuntime(store=InMemoryLiveScoreStore())
        client, _ = self._make_app_with_runtime(runtime)
        resp = client.get("/live-eval/conversations/missing/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_score_count"] == 0
        assert body["dimensions"] == []

    def test_summary_respects_since_filter(self) -> None:
        """``?since=...`` excludes scores before the cutoff."""
        from datetime import datetime, timedelta, timezone

        store = InMemoryLiveScoreStore()
        base = datetime.now(timezone.utc)
        # Score at base - 10 minutes (should be excluded by since=base)
        store.append(LiveTurnScore(
            trace_id="t-old", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="goal_completion", score=0.2,
            scorer_name="goal", scorer_version="v1",
            scored_at=base - timedelta(minutes=10),
        ))
        # Score at base + 1 second (should be included)
        store.append(LiveTurnScore(
            trace_id="t-new", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="goal_completion", score=0.9,
            scorer_name="goal", scorer_version="v1",
            scored_at=base + timedelta(seconds=1),
        ))
        runtime = LiveEvalRuntime(store=store)
        client, _ = self._make_app_with_runtime(runtime)

        # Without filter: both rows
        full = client.get("/live-eval/conversations/c-1/summary").json()
        assert full["total_score_count"] == 2

        # With since=base: only the newer row.  Use params= dict so
        # httpx/urllib correctly URL-encodes the timezone offset (the
        # `+` in `+00:00` would otherwise be parsed server-side as a
        # space, producing a 422).
        narrow = client.get(
            "/live-eval/conversations/c-1/summary",
            params={"since": base.isoformat()},
        ).json()
        assert narrow["total_score_count"] == 1
        gc = next(d for d in narrow["dimensions"] if d["dimension"] == "goal_completion")
        # Mean is now just the newer score's value
        assert gc["mean"] == 0.9

    def test_summary_respects_until_filter(self) -> None:
        from datetime import datetime, timedelta, timezone

        store = InMemoryLiveScoreStore()
        base = datetime.now(timezone.utc)
        store.append(LiveTurnScore(
            trace_id="t-1", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="goal_completion", score=0.4,
            scorer_name="goal", scorer_version="v1",
            scored_at=base - timedelta(minutes=5),
        ))
        store.append(LiveTurnScore(
            trace_id="t-2", conversation_id="c-1", organization_id="org-1",
            agent_id="a-1", dimension="goal_completion", score=0.8,
            scorer_name="goal", scorer_version="v1",
            scored_at=base + timedelta(minutes=5),
        ))
        runtime = LiveEvalRuntime(store=store)
        client, _ = self._make_app_with_runtime(runtime)

        # until=base (exclusive) → only the older row counts
        narrow = client.get(
            "/live-eval/conversations/c-1/summary",
            params={"until": base.isoformat()},
        ).json()
        assert narrow["total_score_count"] == 1
        gc = next(d for d in narrow["dimensions"] if d["dimension"] == "goal_completion")
        assert gc["mean"] == 0.4

    def test_summary_rejects_inverted_window(self) -> None:
        runtime = LiveEvalRuntime(store=InMemoryLiveScoreStore())
        client, _ = self._make_app_with_runtime(runtime)
        # since > until is a configuration error — return 422 so the
        # caller sees the bug rather than silently returning empty.
        resp = client.get(
            "/live-eval/conversations/c-1/summary"
            "?since=2026-05-02T00:00:00Z&until=2026-05-01T00:00:00Z"
        )
        assert resp.status_code == 422
        assert "earlier than" in resp.json()["detail"]
