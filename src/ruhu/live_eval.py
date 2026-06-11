"""
Continuous evaluation loop — Phase 1 foundation.

### What this module is for

Production conversational AI quality erodes silently. A scenario passes its
offline simulation fixtures the day it's authored, but six months later
nobody knows whether the agent that customers are actually talking to is
still answering correctly. This module is the data plane that closes that
loop: it samples a configurable percentage of live turns, scores them on a
fixed quality taxonomy, and exposes the results as Prometheus metrics +
queryable score records.

### Design at a glance

- **Sampler** — deterministic per-trace sampling (`hash(trace_id)`) so
  adjacent processes/replicas score the same set without coordination.
  Configurable rate per organization tier; defaults to 1% for all tiers.
- **Quality taxonomy** — fixed set of four scoring dimensions:
  ``correctness``, ``helpfulness``, ``safety``, ``goal_completion``.
  Phase 1 implements only ``goal_completion`` (deterministic, free,
  always-on); the other three are LLM-based and arrive in Phase 2.
- **Scorer interface** — pure functions of ``TurnTrace`` returning a
  ``LiveTurnScore``. Each scorer declares its own version string so
  ``score_correctness:v1`` vs ``v2`` are tracked separately and can run
  side-by-side during A/B comparisons.
- **Worker** — modelled on
  :mod:`ruhu.sentiment_worker` (daemon thread, configurable interval,
  bounded batch). Phase 1 ships an in-memory store; Phase 2 swaps in a
  SQLAlchemy-backed store.

### What's NOT here yet

- Kernel hook for trace-write callbacks (Phase 2 — requires kernel.py
  edits, which is the other track's territory)
- LLM-based scorers (Phase 2 — needs a `LLMJudge` abstraction and prompt
  templates)
- Aggregation (mean/p50/p99 per agent/conversation — Phase 2)
- Score history persistence (Phase 2 — requires a migration)

The code here is intentionally a single-file module. As scorers grow,
break it apart along the same boundary the sentiment system uses
(``sentiment_worker.py`` + ``sentiment.py``).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Literal, Protocol

from .schemas import TurnTrace

logger = logging.getLogger(__name__)


# ── Quality taxonomy ───────────────────────────────────────────────────────────
# The four dimensions are intentionally fixed at the module level — a scorer
# pipeline is only useful if every agent/org reports against the same axes.
# Adding a fifth dimension is a deliberate platform decision, not a per-team
# config option.

QualityDimension = Literal[
    "correctness",
    "helpfulness",
    "safety",
    "goal_completion",
]

QUALITY_DIMENSIONS: tuple[QualityDimension, ...] = (
    "correctness",
    "helpfulness",
    "safety",
    "goal_completion",
)


# ── Score record ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiveTurnScore:
    """A single (trace_id, dimension) score emitted by a scorer.

    Frozen so individual scores can't be mutated after creation. Persisted
    as one row per (trace_id, dimension) — composite primary key in the
    Phase 2 SQLAlchemy model.
    """
    trace_id: str
    conversation_id: str
    organization_id: str | None
    agent_id: str
    dimension: QualityDimension
    score: float
    """Value in [0.0, 1.0]. 1.0 = perfect, 0.0 = unacceptable."""
    scorer_name: str
    """Stable scorer identifier — used to filter or A/B compare."""
    scorer_version: str
    """Version string; changing the scoring logic must bump this."""
    notes: str | None = None
    """Optional human-readable rationale (kept short for storage cost)."""
    scored_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self):  # type: ignore[no-untyped-def]
        # Frozen dataclass post-init must use object.__setattr__.
        if self.scored_at is None:
            object.__setattr__(self, "scored_at", datetime.now(timezone.utc))
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(
                f"score must be in [0.0, 1.0]; got {self.score!r} for "
                f"{self.scorer_name}@{self.dimension}"
            )


# ── Sampler ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SamplingPolicy:
    """How aggressively to sample turns for live scoring.

    Rates are floats in [0.0, 1.0]. Use 0.0 to disable sampling for a tier
    (e.g. enterprise customers may opt out for compliance reasons), 1.0
    for "score every turn" (only useful in dev/staging).

    Per-tier rates default to 1% — enough volume to spot regressions in
    busy orgs, low enough that scorer cost is bounded.
    """
    default_rate: float = 0.01
    per_tier_rate: dict[str, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.per_tier_rate is None:
            object.__setattr__(self, "per_tier_rate", {})
        for rate in (self.default_rate, *self.per_tier_rate.values()):
            if not 0.0 <= rate <= 1.0:
                raise ValueError(f"rate must be in [0.0, 1.0]; got {rate!r}")

    def rate_for(self, tier: str | None) -> float:
        if tier and tier in self.per_tier_rate:
            return self.per_tier_rate[tier]
        return self.default_rate


def should_sample(
    *,
    trace_id: str,
    tier: str | None = None,
    policy: SamplingPolicy,
) -> bool:
    """Decide whether ``trace_id`` should be live-scored under ``policy``.

    The decision is **deterministic** (same trace → same answer), so:
    - Replicas independently arrive at identical sampling decisions
      without any coordination
    - Re-running scoring on a historical trace is cheap and consistent
    - Bug investigations can manually check "would this trace have been
      sampled?" without consulting external state

    We hash trace_id to a uniform [0, 1) float and compare against the
    policy's rate. Hash is SHA-256 truncated — overkill for sampling but
    keeps the function dependency-free of any RNG seed state.
    """
    rate = policy.rate_for(tier)
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    digest = hashlib.sha256(trace_id.encode("utf-8")).digest()
    # First 8 bytes interpreted as big-endian unsigned int → uniform float.
    bucket = int.from_bytes(digest[:8], "big") / (1 << 64)
    return bucket < rate


# ── Scorer protocol ───────────────────────────────────────────────────────────

class TurnScorer(Protocol):
    """A scorer maps a TurnTrace to one ``LiveTurnScore``.

    Scorers are pure functions of the trace. They must NOT mutate the
    trace, the trace store, or any global state. They MAY call out to
    LLMs or other tools, but the worker enforces a per-scorer timeout
    so a slow scorer can't stall the eval pipeline.
    """

    name: str
    version: str
    dimension: QualityDimension

    def __call__(self, trace: TurnTrace) -> LiveTurnScore: ...


# ── Goal completion scorer (deterministic, Phase 1) ───────────────────────────

class GoalCompletionScorer:
    """Score a turn on whether it advanced the conversation toward resolution.

    Phase 1 heuristic — deterministic, no LLM call, no network:

    - ``error_kind != "none"`` → 0.0 (the turn failed mechanically)
    - ``step_after != step_before`` → 0.7 (forward progress; not perfect
      because we can't tell yet whether the destination is a successful
      step or an error/escape step without the agent definition)
    - ``step_after == step_before`` AND tool_calls succeeded → 0.5
      (no advancement but the turn did useful work)
    - ``step_after == step_before`` AND no tool calls → 0.3
      (the agent stayed put without doing anything productive)

    Phase 2 will replace this with a richer model that:
    - Looks at the agent definition to know which steps are terminal/success
    - Tracks goal_completion across a conversation, not per-turn
    - Distinguishes "stuck in clarification loop" from "user is exploring"
    """

    name = "goal_completion_heuristic"
    version = "v1"
    dimension: QualityDimension = "goal_completion"

    def __call__(self, trace: TurnTrace) -> LiveTurnScore:
        score, notes = self._compute(trace)
        return LiveTurnScore(
            trace_id=trace.trace_id,
            conversation_id=trace.conversation_id,
            organization_id=trace.organization_id,
            agent_id=trace.agent_id,
            dimension=self.dimension,
            score=score,
            scorer_name=self.name,
            scorer_version=self.version,
            notes=notes,
        )

    @staticmethod
    def _compute(trace: TurnTrace) -> tuple[float, str]:
        if trace.error_kind != "none":
            return 0.0, f"turn errored ({trace.error_kind})"
        if trace.step_after != trace.step_before:
            return 0.7, f"advanced {trace.step_before}→{trace.step_after}"
        succeeded_tools = [tc for tc in trace.tool_calls if str(tc.status).lower() == "success"]
        if succeeded_tools:
            return 0.5, f"no step change, {len(succeeded_tools)} tool(s) succeeded"
        return 0.3, "no step change, no tool calls"


# ── Score store (Phase 1 in-memory) ───────────────────────────────────────────

class LiveScoreStore(Protocol):
    """Persistence boundary for ``LiveTurnScore`` records.

    Phase 1 ships an in-memory implementation suitable for tests + small
    self-hosted deploys. Phase 2 will add a SQLAlchemy-backed store with a
    real migration. Keeping the protocol narrow now means swapping is a
    one-class change.
    """

    def append(self, score: LiveTurnScore) -> None: ...

    def list_for_trace(self, trace_id: str) -> list[LiveTurnScore]: ...

    def list_for_conversation(self, conversation_id: str) -> list[LiveTurnScore]: ...


class InMemoryLiveScoreStore:
    def __init__(self) -> None:
        self._scores: list[LiveTurnScore] = []
        self._lock = threading.Lock()  # cheap; protects the list under
        # concurrent worker writes + test reads

    def append(self, score: LiveTurnScore) -> None:
        with self._lock:
            self._scores.append(score)

    def list_for_trace(self, trace_id: str) -> list[LiveTurnScore]:
        with self._lock:
            return [s for s in self._scores if s.trace_id == trace_id]

    def list_for_conversation(self, conversation_id: str) -> list[LiveTurnScore]:
        with self._lock:
            return [s for s in self._scores if s.conversation_id == conversation_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._scores)


class SQLAlchemyLiveScoreStore:
    """Postgres-backed score store.

    UPSERT semantics — re-scoring the same (trace_id, scorer_name,
    scorer_version) triple updates the existing row rather than failing on
    PK conflict. This keeps the scorer worker idempotent: replays after a
    crash don't fight the constraint.

    The store does NOT manage tenant context — RLS is driven by the
    enclosing request/worker scope via ``tenant_db_context``. Every read
    and write here goes through a normal session, so org_a queries can
    only see org_a rows, etc.

    Note: ``organization_id`` is required on writes (see model). If a
    score arrives without one, the write raises — the worker should drop
    that score rather than insert a tenant-orphaned row.
    """

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def append(self, score: LiveTurnScore) -> None:
        # Local imports keep the in-memory store path importable without
        # SQLAlchemy in dev/test runtimes that haven't set up the DB.
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from .live_eval_sqlalchemy_models import LiveTurnScoreRecord

        if not score.organization_id:
            raise ValueError(
                "live score requires organization_id for tenant scoping; "
                f"got {score.organization_id!r} for trace {score.trace_id}"
            )

        values = {
            "trace_id": score.trace_id,
            "scorer_name": score.scorer_name,
            "scorer_version": score.scorer_version,
            "organization_id": score.organization_id,
            "conversation_id": score.conversation_id,
            "agent_id": score.agent_id,
            "dimension": score.dimension,
            "score": score.score,
            "notes": score.notes,
            "scored_at": score.scored_at,
        }

        with self._session_factory() as session:
            stmt = pg_insert(LiveTurnScoreRecord).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    "trace_id", "scorer_name", "scorer_version",
                ],
                set_={
                    # All non-PK columns. ``organization_id`` should never
                    # actually change on a re-score, but updating defensively
                    # rather than asserting equality avoids surprising
                    # failures on schema-evolution edge cases.
                    "organization_id": values["organization_id"],
                    "conversation_id": values["conversation_id"],
                    "agent_id": values["agent_id"],
                    "dimension": values["dimension"],
                    "score": values["score"],
                    "notes": values["notes"],
                    "scored_at": values["scored_at"],
                },
            )
            session.execute(stmt)
            session.commit()

    def list_for_trace(self, trace_id: str) -> list[LiveTurnScore]:
        from sqlalchemy import select

        from .live_eval_sqlalchemy_models import LiveTurnScoreRecord

        with self._session_factory() as session:
            rows = session.execute(
                select(LiveTurnScoreRecord).where(
                    LiveTurnScoreRecord.trace_id == trace_id
                )
            ).scalars().all()
        return [_record_to_score(row) for row in rows]

    def list_for_conversation(self, conversation_id: str) -> list[LiveTurnScore]:
        from sqlalchemy import select

        from .live_eval_sqlalchemy_models import LiveTurnScoreRecord

        with self._session_factory() as session:
            rows = session.execute(
                select(LiveTurnScoreRecord)
                .where(LiveTurnScoreRecord.conversation_id == conversation_id)
                .order_by(LiveTurnScoreRecord.scored_at)
            ).scalars().all()
        return [_record_to_score(row) for row in rows]


def _record_to_score(record) -> LiveTurnScore:
    """Convert a ``LiveTurnScoreRecord`` row back into the domain dataclass."""
    return LiveTurnScore(
        trace_id=record.trace_id,
        conversation_id=record.conversation_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        dimension=record.dimension,  # type: ignore[arg-type]
        score=float(record.score),
        scorer_name=record.scorer_name,
        scorer_version=record.scorer_version,
        notes=record.notes,
        scored_at=record.scored_at,
    )


# ── Conversation-level rollups ────────────────────────────────────────────────

@dataclass(frozen=True)
class DimensionRollup:
    """Aggregate stats for one dimension across a set of scores.

    ``count == 0`` means the dimension had no scores in the input set —
    callers should treat ``mean``/``min``/``max`` as undefined in that
    case (we set them to 0.0 to avoid Optional fields, but the contract
    is "look at count first").
    """
    dimension: QualityDimension
    count: int
    mean: float
    min: float
    max: float


def rollup_by_dimension(
    scores: Iterable[LiveTurnScore],
) -> dict[QualityDimension, DimensionRollup]:
    """Aggregate scores per dimension.

    Pure function — no I/O, no side effects. Suitable for both real-time
    API responses and offline batch reporting. Empty dimensions are
    omitted from the result rather than included with count=0; callers
    that need "all 4 dimensions always present" can intersect against
    ``QUALITY_DIMENSIONS``.
    """
    buckets: dict[QualityDimension, list[float]] = {}
    for score in scores:
        buckets.setdefault(score.dimension, []).append(score.score)
    rollups: dict[QualityDimension, DimensionRollup] = {}
    for dimension, values in buckets.items():
        rollups[dimension] = DimensionRollup(
            dimension=dimension,
            count=len(values),
            mean=sum(values) / len(values),
            min=min(values),
            max=max(values),
        )
    return rollups


# ── Billing-store tier resolver ───────────────────────────────────────────────

def make_billing_tier_resolver(billing_store) -> Callable[[str | None], str | None]:
    """Build a ``tier_resolver`` callable backed by the billing store.

    Resolves an ``organization_id`` to its plan slug (e.g. "free",
    "starter", "professional", "enterprise") via two lookups:
    ``get_active_subscription`` then ``get_plan``. The resolver swallows
    all exceptions and returns ``None`` on miss — sampling decisions
    must NEVER be blocked by a billing-store outage.

    The resolver is intentionally NOT cached at this layer; ``rate_limit
    ._TierCache`` already provides aggressive caching for the hot rate-
    limit path. Live eval is sampled (1% by default), so adding another
    cache layer here is overkill until profiling proves it necessary.
    """
    def _resolve(organization_id: str | None) -> str | None:
        if not organization_id:
            return None
        try:
            sub = billing_store.get_active_subscription(organization_id)
            if sub is None:
                return None
            plan = billing_store.get_plan(sub.plan_id)
            if plan is None:
                return None
            return getattr(plan, "slug", None)
        except Exception:  # noqa: BLE001 — sampling decisions must not raise
            logger.warning(
                "live_eval_tier_resolver_failed",
                extra={"organization_id": organization_id},
                exc_info=True,
            )
            return None
    return _resolve


# ── Runtime bundle ────────────────────────────────────────────────────────────

class LiveEvalRuntime:
    """Bundles the score store + worker for app-level wiring.

    Exists so ``build_default_app`` can talk to one object instead of
    juggling ``store``, ``worker``, ``policy``, and ``scorers`` separately.
    Mirrors the shape of ``KnowledgeRuntime`` and ``KPIRuntime`` —
    constructor params include enough to fully instantiate; lifecycle is
    explicit via ``start()`` / ``stop()`` so the FastAPI lifespan context
    can manage the worker thread cleanly.

    For tests, callers can construct a runtime around an in-memory store
    and skip ``start()`` — the worker only runs the daemon thread when
    started, otherwise ``submit()`` queues without scoring.
    """

    def __init__(
        self,
        *,
        store: LiveScoreStore,
        scorers: Iterable[TurnScorer] | None = None,
        sampling_policy: SamplingPolicy | None = None,
        tier_resolver: Callable[[str | None], str | None] | None = None,
        tick_seconds: float = 5.0,
        max_batch: int = 100,
    ) -> None:
        self.store = store
        self._scorers: tuple[TurnScorer, ...] = tuple(
            scorers if scorers is not None else (GoalCompletionScorer(),)
        )
        self._policy = sampling_policy or SamplingPolicy()
        self.worker = LiveEvalWorker(
            scorers=self._scorers,
            store=self.store,
            sampling_policy=self._policy,
            tier_resolver=tier_resolver,
            tick_seconds=tick_seconds,
            max_batch=max_batch,
        )

    def submit(self, trace: TurnTrace) -> bool:
        """Forwarded to the worker — primary integration point for callers."""
        return self.worker.submit(trace)

    def start(self) -> None:
        self.worker.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        # Drain any queued work first so a sudden shutdown doesn't lose
        # already-sampled traces. Best-effort — bounded by max_batch *
        # tick_seconds at worst case.
        try:
            self.worker.process_once()
        except Exception:  # noqa: BLE001 — never block shutdown
            logger.warning("live_eval_runtime_stop_drain_failed", exc_info=True)
        self.worker.stop(timeout=timeout)

    @classmethod
    def from_settings(
        cls,
        *,
        session_factory,
        sample_rate: float,
        scorers: Iterable[TurnScorer] | None = None,
        llm_judge=None,  # ruhu.live_eval_judges.LLMJudge | None
        billing_store=None,  # BillingStore | None — for tier-aware sampling
        per_tier_rate: dict[str, float] | None = None,
    ) -> "LiveEvalRuntime":
        """Build a production-shaped runtime from settings.

        Convenience for ``create_app`` — wires the SQLAlchemy-backed store
        + a 1- or 4-scorer pipeline + a sampling policy with the given
        default rate. Returns an unstarted runtime; the caller is
        responsible for ``start()`` (typically inside a FastAPI lifespan
        startup hook).

        ``scorers``: explicit override. When set, that list is used as-is
        and ``llm_judge`` is ignored. Useful in tests that want a single
        deterministic scorer.

        ``llm_judge``: when set (and ``scorers`` is None), the runtime
        scores all 4 quality dimensions:
            * goal_completion (deterministic, free, always-on)
            * correctness, helpfulness, safety (LLM-judged)
        When None, only goal_completion is scored — the LLM dimensions
        will simply have no rows in ``live_turn_scores`` until a judge is
        wired. This is the right default for operators who enable live
        eval but haven't yet selected/funded an LLM judge.
        """
        if scorers is None:
            scorer_list: list[TurnScorer] = [GoalCompletionScorer()]
            if llm_judge is not None:
                # Lazy import — keeps this module's surface narrow when
                # the judges aren't needed.
                from .live_eval_judges import make_all_llm_scorers
                scorer_list.extend(make_all_llm_scorers(llm_judge))
            scorers = scorer_list

        policy = SamplingPolicy(
            default_rate=sample_rate,
            per_tier_rate=dict(per_tier_rate) if per_tier_rate else {},
        )
        tier_resolver = (
            make_billing_tier_resolver(billing_store)
            if billing_store is not None
            else None
        )
        return cls(
            store=SQLAlchemyLiveScoreStore(session_factory),
            scorers=scorers,
            sampling_policy=policy,
            tier_resolver=tier_resolver,
        )


# ── Worker ────────────────────────────────────────────────────────────────────

@dataclass
class LiveEvalWorkerStatus:
    """Snapshot of the worker's last run — exposed via ``LiveEvalWorker.status()``.

    Mirrors :class:`ruhu.sentiment_worker.WorkerStatus` so dashboards can
    consume both with the same shape.
    """
    started: bool = False
    last_run_at: datetime | None = None
    last_processed_count: int = 0
    last_skipped_count: int = 0
    last_error: str | None = None


class LiveEvalWorker:
    """Daemon-thread worker that scores newly-arrived sampled traces.

    Phase 1 design assumes a **pull-based** flow: callers feed traces
    into ``submit()`` (typically from a kernel hook in Phase 2), the
    worker drains them on its tick interval and runs every registered
    scorer. We don't share the worker across multiple processes — single-
    process is fine for the volumes Phase 1 needs to validate.

    The deliberate non-features:

    - **No durable queue.** Submitted traces live in an in-memory list.
      A crash loses pending work. That's acceptable for sampled scoring
      (we lose <1% of turns from <1% of crashes); not acceptable for
      authoritative state. Phase 2 swaps this for a DB-backed inbox.
    - **No retry on scorer failure.** If a scorer raises, we log it and
      drop the trace. Continuous eval is informational, not load-bearing.
    """

    def __init__(
        self,
        *,
        scorers: Iterable[TurnScorer],
        store: LiveScoreStore,
        sampling_policy: SamplingPolicy,
        tier_resolver: Callable[[str | None], str | None] | None = None,
        tick_seconds: float = 5.0,
        max_batch: int = 100,
    ) -> None:
        self._scorers: tuple[TurnScorer, ...] = tuple(scorers)
        self._store = store
        self._policy = sampling_policy
        self._tier_resolver = tier_resolver or (lambda _org_id: None)
        self._tick_seconds = max(0.5, tick_seconds)
        self._max_batch = max(1, max_batch)
        self._inbox: list[TurnTrace] = []
        self._inbox_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._status = LiveEvalWorkerStatus()

    # ── Submission ─────────────────────────────────────────────────────────

    def submit(self, trace: TurnTrace) -> bool:
        """Record a finished turn for sampled scoring.

        Returns True iff the trace was sampled (and queued); False if the
        sampler rejected it. Useful for callers that want to emit a
        "skipped due to sampling" metric.
        """
        tier = self._tier_resolver(trace.organization_id)
        if not should_sample(
            trace_id=trace.trace_id,
            tier=tier,
            policy=self._policy,
        ):
            return False
        with self._inbox_lock:
            self._inbox.append(trace)
        return True

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the daemon thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ruhu-live-eval-worker",
            daemon=True,
        )
        self._thread.start()
        with self._status_lock:
            self._status.started = True

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        with self._status_lock:
            self._status.started = False

    def status(self) -> LiveEvalWorkerStatus:
        with self._status_lock:
            # Defensive copy — callers shouldn't see worker state mutate
            # underneath them.
            return LiveEvalWorkerStatus(
                started=self._status.started,
                last_run_at=self._status.last_run_at,
                last_processed_count=self._status.last_processed_count,
                last_skipped_count=self._status.last_skipped_count,
                last_error=self._status.last_error,
            )

    # ── Run loop ──────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.process_once()
            except Exception:  # noqa: BLE001 — never let the loop die
                logger.exception("live_eval_worker loop iteration failed")
                with self._status_lock:
                    self._status.last_error = "loop_iteration_failed"
            self._stop_event.wait(self._tick_seconds)

    def process_once(self) -> int:
        """Drain up to ``max_batch`` traces and score each. Returns count.

        Public for testability — callers can drive the worker without
        starting the thread.
        """
        batch = self._drain_batch()
        if not batch:
            with self._status_lock:
                self._status.last_run_at = datetime.now(timezone.utc)
                self._status.last_processed_count = 0
                self._status.last_skipped_count = 0
                self._status.last_error = None
            return 0

        # Lazy metric import — keeps this module importable in tests
        # that don't want to wire the prometheus registry.
        try:
            from .observability.metrics import (
                live_eval_scores_total,
                live_eval_scorer_duration_seconds,
                live_eval_scorer_errors_total,
                live_eval_turns_processed_total,
                safe_observe,
            )
        except ImportError:
            live_eval_scores_total = None  # type: ignore[assignment]
            live_eval_scorer_duration_seconds = None  # type: ignore[assignment]
            live_eval_scorer_errors_total = None  # type: ignore[assignment]
            live_eval_turns_processed_total = None  # type: ignore[assignment]
            safe_observe = lambda *_args, **_kwargs: None  # noqa: E731

        processed = 0
        skipped = 0
        for trace in batch:
            for scorer in self._scorers:
                start = time.monotonic()
                try:
                    score = scorer(trace)
                    self._store.append(score)
                    if live_eval_scores_total is not None:
                        bucket = _score_bucket(score.score)
                        safe_observe(
                            "live_eval_scores_total",
                            live_eval_scores_total.labels(
                                dimension=scorer.dimension,
                                scorer=scorer.name,
                                bucket=bucket,
                            ).inc,
                        )
                    processed += 1
                except Exception as exc:  # noqa: BLE001 — informational
                    logger.warning(
                        "live_eval_scorer_failed",
                        extra={
                            "scorer": scorer.name,
                            "trace_id": trace.trace_id,
                            "error_class": type(exc).__name__,
                        },
                    )
                    skipped += 1
                    if live_eval_scorer_errors_total is not None:
                        safe_observe(
                            "live_eval_scorer_errors_total",
                            live_eval_scorer_errors_total.labels(
                                scorer=scorer.name,
                                error_class=type(exc).__name__,
                            ).inc,
                        )
                finally:
                    if live_eval_scorer_duration_seconds is not None:
                        safe_observe(
                            "live_eval_scorer_duration_seconds",
                            live_eval_scorer_duration_seconds.labels(
                                scorer=scorer.name,
                            ).observe,
                            time.monotonic() - start,
                        )
            if live_eval_turns_processed_total is not None:
                safe_observe(
                    "live_eval_turns_processed_total",
                    live_eval_turns_processed_total.inc,
                )

        with self._status_lock:
            self._status.last_run_at = datetime.now(timezone.utc)
            self._status.last_processed_count = processed
            self._status.last_skipped_count = skipped
            self._status.last_error = None
        return processed

    def _drain_batch(self) -> list[TurnTrace]:
        with self._inbox_lock:
            if not self._inbox:
                return []
            batch = self._inbox[: self._max_batch]
            del self._inbox[: self._max_batch]
            return batch


def _score_bucket(score: float) -> str:
    """Map a [0,1] score to a coarse Prometheus label bucket.

    Bounded cardinality (5 values) → safe to use as a label.
    """
    if score < 0.2:
        return "very_low"
    if score < 0.4:
        return "low"
    if score < 0.6:
        return "medium"
    if score < 0.8:
        return "high"
    return "very_high"


# ── Trace-store integration ───────────────────────────────────────────────────

class InstrumentedTraceStore:
    """Decorator over any :class:`ruhu.stores.TraceStore` that auto-submits.

    Wrapping the store at app-construction time is the **non-invasive** way
    to hook live eval into the kernel: the kernel only knows about the
    Protocol interface, so wrapping the inner store transparently fans out
    every ``append()`` to the eval worker without any kernel.py changes.

    The wrapper deliberately **never raises** from the eval submission path.
    Live eval is informational; if the worker is down or the inbox is
    saturated, the kernel must still complete its trace write. The original
    store's exceptions propagate normally.

    Usage::

        worker = LiveEvalWorker(
            scorers=[GoalCompletionScorer()],
            store=score_store,
            sampling_policy=SamplingPolicy(default_rate=0.01),
        )
        worker.start()
        kernel.trace_store = InstrumentedTraceStore(
            inner=kernel.trace_store,
            worker=worker,
        )

    For tests, callers can pass ``submit_fn=worker.submit`` directly to
    avoid spinning the worker thread.
    """

    def __init__(
        self,
        *,
        inner,  # TraceStore — typed loosely to dodge a circular import with stores.py
        worker: "LiveEvalWorker | None" = None,
        submit_fn: Callable[[TurnTrace], bool] | None = None,
    ) -> None:
        if worker is None and submit_fn is None:
            raise ValueError("InstrumentedTraceStore needs either worker= or submit_fn=")
        self._inner = inner
        self._submit: Callable[[TurnTrace], bool] = (
            submit_fn if submit_fn is not None else worker.submit  # type: ignore[union-attr]
        )

    # Delegating methods — must mirror ruhu.stores.TraceStore exactly so the
    # wrapper is a drop-in replacement.

    def append(self, trace: TurnTrace, *, session=None) -> None:
        # Persist FIRST, submit SECOND. If persistence fails the kernel
        # needs to know; live eval is downstream and never the cause of
        # turn-write failures.
        self._inner.append(trace, session=session)
        try:
            self._submit(trace)
        except Exception:  # noqa: BLE001 — eval is best-effort
            logger.warning(
                "live_eval_submit_failed",
                extra={"trace_id": trace.trace_id},
                exc_info=True,
            )

    def all(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
    ):
        return self._inner.all(
            organization_id=organization_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
        )

    def by_conversation(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ):
        return self._inner.by_conversation(
            conversation_id,
            organization_id=organization_id,
            limit=limit,
            offset=offset,
        )
