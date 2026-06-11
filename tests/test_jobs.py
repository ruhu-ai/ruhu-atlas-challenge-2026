"""RP-1.3 / RP-2.1: unified jobs table, single retry policy, worker runtime.

Pins the contracts every migrated background worker relies on:

- enqueue is idempotent per (job_type, dedupe_key)
- transactional outbox: a job enqueued in a rolled-back session does not exist
- claim is exclusive under concurrency (SKIP LOCKED) and lease-expiry
  reclaims jobs from crashed workers
- one retry policy: exponential backoff, then dead-letter (never dropped)
- the runtime executes handlers, retries on failure, and respects
  ``retryable=False`` exceptions
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from ruhu.db import build_session_factory
from ruhu.jobs import (
    InMemoryJobStore,
    Job,
    JobHandlerRegistry,
    JobRuntime,
    RetryPolicy,
    SQLAlchemyJobStore,
    next_retry_at,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestRetryPolicy:
    def test_exponential_backoff_with_cap(self) -> None:
        now = _now()
        policy = RetryPolicy(max_attempts=10, base_delay_seconds=30, max_delay_seconds=900)
        delays = [
            (next_retry_at(attempt, now=now, policy=policy) - now).total_seconds()
            for attempt in range(1, 7)
        ]
        assert delays == [30, 60, 120, 240, 480, 900]

    def test_exhaustion_returns_none(self) -> None:
        policy = RetryPolicy(max_attempts=4)
        assert next_retry_at(4, now=_now(), policy=policy) is None
        assert next_retry_at(5, now=_now(), policy=policy) is None


class TestInMemoryJobStore:
    def test_enqueue_claim_complete(self) -> None:
        store = InMemoryJobStore()
        job = store.enqueue(Job(job_type="noop"))
        claimed = store.claim_next(worker_id="w1")
        assert claimed is not None and claimed.job_id == job.job_id
        assert claimed.status == "running" and claimed.attempt_count == 1
        completed = store.complete(job.job_id, worker_id="w1")
        assert completed is not None and completed.status == "succeeded"

    def test_dedupe_key_makes_enqueue_idempotent(self) -> None:
        store = InMemoryJobStore()
        first = store.enqueue(Job(job_type="noop", dedupe_key="k1"))
        second = store.enqueue(Job(job_type="noop", dedupe_key="k1"))
        assert second.job_id == first.job_id
        assert len(store.list_jobs(job_type="noop")) == 1

    def test_failure_requeues_with_backoff_then_dead_letters(self) -> None:
        store = InMemoryJobStore()
        job = store.enqueue(Job(job_type="flaky", max_attempts=2))
        policy = RetryPolicy(base_delay_seconds=30)

        claimed = store.claim_next(worker_id="w1")
        failed = store.fail(claimed.job_id, worker_id="w1", error="boom", policy=policy)
        assert failed.status == "queued"
        assert failed.run_at > _now()  # backed off into the future

        reclaimed = store.claim_next(worker_id="w1", now=failed.run_at)
        dead = store.fail(reclaimed.job_id, worker_id="w1", error="boom again", policy=policy)
        assert dead.status == "dead"
        assert dead.last_error == "boom again"

    def test_non_retryable_failure_dead_letters_immediately(self) -> None:
        store = InMemoryJobStore()
        store.enqueue(Job(job_type="fatal"))
        claimed = store.claim_next(worker_id="w1")
        dead = store.fail(claimed.job_id, worker_id="w1", error="bad input", retryable=False)
        assert dead.status == "dead"


class TestSQLAlchemyJobStore:
    def test_enqueue_claim_complete_roundtrip(self, postgres_database_url_factory) -> None:
        store = SQLAlchemyJobStore(build_session_factory(postgres_database_url_factory()))
        job = store.enqueue(Job(job_type="noop", payload={"x": 1}))
        claimed = store.claim_next(worker_id="w1")
        assert claimed is not None and claimed.job_id == job.job_id
        assert claimed.payload == {"x": 1}
        assert store.complete(job.job_id, worker_id="w1").status == "succeeded"

    def test_dedupe_key_is_db_enforced(self, postgres_database_url_factory) -> None:
        store = SQLAlchemyJobStore(build_session_factory(postgres_database_url_factory()))
        first = store.enqueue(Job(job_type="noop", dedupe_key="k1"))
        second = store.enqueue(Job(job_type="noop", dedupe_key="k1"))
        assert second.job_id == first.job_id
        assert len(store.list_jobs(job_type="noop")) == 1

    def test_completed_job_frees_its_dedupe_key(self, postgres_database_url_factory) -> None:
        """Recurring jobs re-enqueue under a stable key once the prior run finishes."""
        store = SQLAlchemyJobStore(build_session_factory(postgres_database_url_factory()))
        first = store.enqueue(Job(job_type="recurring", dedupe_key="tick"))
        claimed = store.claim_next(worker_id="w1")
        store.complete(claimed.job_id, worker_id="w1")
        second = store.enqueue(Job(job_type="recurring", dedupe_key="tick"))
        assert second.job_id != first.job_id
        assert len(store.list_jobs(job_type="recurring")) == 2

    def test_transactional_outbox_rollback_leaves_no_job(self, postgres_database_url_factory) -> None:
        session_factory = build_session_factory(postgres_database_url_factory())
        store = SQLAlchemyJobStore(session_factory)
        with session_factory() as session:
            store.enqueue(Job(job_type="outboxed"), session=session)
            session.rollback()
        assert store.list_jobs(job_type="outboxed") == []

    def test_transactional_outbox_commit_makes_job_claimable(self, postgres_database_url_factory) -> None:
        session_factory = build_session_factory(postgres_database_url_factory())
        store = SQLAlchemyJobStore(session_factory)
        with session_factory() as session:
            store.enqueue(Job(job_type="outboxed"), session=session)
            session.commit()
        claimed = store.claim_next(worker_id="w1")
        assert claimed is not None and claimed.job_type == "outboxed"

    def test_concurrent_claims_never_hand_out_the_same_job(self, postgres_database_url_factory) -> None:
        store = SQLAlchemyJobStore(build_session_factory(postgres_database_url_factory()))
        for _ in range(6):
            store.enqueue(Job(job_type="parallel"))

        def claim_all(worker_id: str) -> list[str]:
            claimed_ids: list[str] = []
            while True:
                job = store.claim_next(worker_id=worker_id)
                if job is None:
                    return claimed_ids
                claimed_ids.append(job.job_id)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(claim_all, ["w1", "w2", "w3"]))

        all_claimed = [job_id for chunk in results for job_id in chunk]
        assert len(all_claimed) == 6
        assert len(set(all_claimed)) == 6  # no double-claims

    def test_expired_lease_is_reclaimable(self, postgres_database_url_factory) -> None:
        store = SQLAlchemyJobStore(build_session_factory(postgres_database_url_factory()))
        store.enqueue(Job(job_type="leased"))
        first = store.claim_next(worker_id="w1", lease_seconds=0.0)
        assert first is not None
        # w1 crashed; once the lease expires another worker may take over.
        second = store.claim_next(
            worker_id="w2",
            now=_now() + timedelta(seconds=1),
        )
        assert second is not None and second.job_id == first.job_id
        assert second.worker_id == "w2"
        assert second.attempt_count == 2

    def test_heartbeat_extends_lease(self, postgres_database_url_factory) -> None:
        store = SQLAlchemyJobStore(build_session_factory(postgres_database_url_factory()))
        store.enqueue(Job(job_type="hb"))
        claimed = store.claim_next(worker_id="w1", lease_seconds=5.0)
        extended = store.heartbeat(claimed.job_id, worker_id="w1", lease_seconds=300.0)
        assert extended is not None
        assert extended.lease_expires_at > claimed.lease_expires_at
        # The wrong worker cannot heartbeat someone else's lease.
        assert store.heartbeat(claimed.job_id, worker_id="w2") is None


class TestJobRuntime:
    def test_runtime_executes_registered_handler(self) -> None:
        store = InMemoryJobStore()
        registry = JobHandlerRegistry()
        seen: list[str] = []
        registry.register("greet", lambda job: seen.append(job.payload["name"]))
        store.enqueue(Job(job_type="greet", payload={"name": "ada"}))

        runtime = JobRuntime(store, registry, worker_id="w1")
        assert runtime.run_once() == 1
        assert seen == ["ada"]
        assert store.list_jobs(job_type="greet")[0].status == "succeeded"

    def test_runtime_only_claims_registered_types(self) -> None:
        store = InMemoryJobStore()
        registry = JobHandlerRegistry()
        registry.register("known", lambda job: None)
        store.enqueue(Job(job_type="unknown"))

        runtime = JobRuntime(store, registry, worker_id="w1")
        assert runtime.run_once() == 0
        assert store.list_jobs(job_type="unknown")[0].status == "queued"

    def test_handler_failure_retries_then_dead_letters(self) -> None:
        store = InMemoryJobStore()
        registry = JobHandlerRegistry()
        attempts: list[int] = []

        def explode(job: Job) -> None:
            attempts.append(job.attempt_count)
            raise RuntimeError("downstream unavailable")

        registry.register("flaky", explode, policy=RetryPolicy(base_delay_seconds=0.0))
        store.enqueue(Job(job_type="flaky", max_attempts=3))

        runtime = JobRuntime(store, registry, worker_id="w1")
        for _ in range(3):
            runtime.run_once()
        assert attempts == [1, 2, 3]
        assert store.list_jobs(job_type="flaky")[0].status == "dead"

    def test_non_retryable_exception_dead_letters_immediately(self) -> None:
        store = InMemoryJobStore()
        registry = JobHandlerRegistry()

        class FatalError(RuntimeError):
            retryable = False

        def explode(job: Job) -> None:
            raise FatalError("malformed payload")

        registry.register("fatal", explode)
        store.enqueue(Job(job_type="fatal", max_attempts=4))

        runtime = JobRuntime(store, registry, worker_id="w1")
        runtime.run_once()
        job = store.list_jobs(job_type="fatal")[0]
        assert job.status == "dead"
        assert job.attempt_count == 1

    def test_run_forever_stops_on_event(self) -> None:
        store = InMemoryJobStore()
        registry = JobHandlerRegistry()
        registry.register("noop", lambda job: None)
        runtime = JobRuntime(
            store, registry, worker_id="w1", poll_interval_seconds=0.01
        )
        stop = threading.Event()
        thread = threading.Thread(target=runtime.run_forever, args=(stop,))
        thread.start()
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive()

    def test_duplicate_registration_rejected(self) -> None:
        registry = JobHandlerRegistry()
        registry.register("x", lambda job: None)
        with pytest.raises(ValueError):
            registry.register("x", lambda job: None)
