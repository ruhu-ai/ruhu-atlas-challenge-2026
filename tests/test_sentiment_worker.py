"""Tests for ConversationSentimentWorker (Issue 2 — LLM sentiment pipeline)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from ruhu.db import build_session_factory
from ruhu.db_models import ConversationRecord, TurnTraceRecord
from ruhu.sentiment_worker import (
    ConversationSentimentWorker,
    _METADATA_ANALYZED_AT,
    _METADATA_ATTEMPTS,
    _METADATA_ERROR,
    _METADATA_NEXT_RETRY,
    _METADATA_SCORE,
    _METADATA_STATUS,
    _STATUS_COMPLETE,
    _STATUS_EXHAUSTED,
    _STATUS_FAILED,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_worker(session_factory, *, max_attempts: int = 3, backoff_base: float = 60.0) -> ConversationSentimentWorker:
    return ConversationSentimentWorker(
        session_factory=session_factory,
        llm_base_url="https://api.example.test/v1",
        llm_api_key="test-key",
        model="gpt-test",
        batch_size=20,
        max_attempts=max_attempts,
        backoff_base_seconds=backoff_base,
    )


def _insert_ended_conversation(
    session_factory,
    *,
    metadata: dict | None = None,
    conversation_id: str | None = None,
    ended_at: datetime | None = None,
) -> str:
    cid = conversation_id or f"conv-{uuid4().hex[:8]}"
    now = _utcnow()
    with session_factory.begin() as session:
        session.add(
            ConversationRecord(
                conversation_id=cid,
                agent_id="test_agent",
                agent_version_id="v1",
                step_id="end",
                status="ended",
                outcome="resolved",
                started_at=now - timedelta(hours=1),
                ended_at=ended_at or (now - timedelta(minutes=5)),
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(minutes=5),
                metadata_json=metadata or {},
                facts_json={},
            )
        )
    return cid


def _insert_trace(session_factory, conversation_id: str, *, messages: list[dict]) -> None:
    now = _utcnow()
    with session_factory.begin() as session:
        session.add(
            TurnTraceRecord(
                trace_id=f"trace-{uuid4().hex[:8]}",
                conversation_id=conversation_id,
                turn_id=f"turn-{uuid4().hex[:8]}",
                agent_id="test_agent",
                step_before="start",
                step_after="end",
                emitted_messages_json=messages,
                semantic_events_json=[],
                fact_updates_json=[],
                chosen_action_json={},
                tool_calls_json=[],
                rules_json={},
                latency_breakdown_ms_json={},
                recorded_at=now - timedelta(minutes=10),
            )
        )


def _load_metadata(session_factory, conversation_id: str) -> dict:
    with session_factory() as session:
        record = session.get(ConversationRecord, conversation_id)
        return dict(record.metadata_json or {})


def _fake_llm_response(score: float) -> bytes:
    body = {
        "choices": [{
            "message": {
                "content": json.dumps({"score": score})
            }
        }]
    }
    return json.dumps(body).encode()


# ── _call_llm: parsing ────────────────────────────────────────────────────────

def test_call_llm_parses_score_from_openai_response() -> None:
    worker = _make_worker(MagicMock())  # session_factory unused in this test

    mock_response = MagicMock()
    mock_response.read.return_value = _fake_llm_response(0.75)
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        score = worker._call_llm("user: hello\nassistant: hi")

    assert score == 0.75


def test_call_llm_rejects_out_of_range_score() -> None:
    worker = _make_worker(MagicMock())

    mock_response = MagicMock()
    mock_response.read.return_value = _fake_llm_response(1.5)
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        import pytest
        with pytest.raises(ValueError, match="out of range"):
            worker._call_llm("user: hello")


def test_call_llm_rejects_non_numeric_score() -> None:
    worker = _make_worker(MagicMock())

    body = json.dumps({"choices": [{"message": {"content": '{"score": "positive"}'}}]}).encode()
    mock_response = MagicMock()
    mock_response.read.return_value = body
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        import pytest
        with pytest.raises(ValueError, match="non-numeric"):
            worker._call_llm("user: hello")


# ── _build_transcript ────────────────────────────────────────────────────────

def test_build_transcript_extracts_emitted_messages(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf)
    _insert_trace(sf, cid, messages=[
        {"role": "user", "text": "Hello"},
        {"role": "assistant", "text": "Hi there, how can I help?"},
    ])

    transcript = worker._build_transcript(cid)
    assert "user: Hello" in transcript
    assert "assistant: Hi there" in transcript


def test_build_transcript_raises_skip_when_no_traces(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf)
    # No traces inserted

    from ruhu.sentiment_worker import _SkipConversation
    import pytest
    with pytest.raises(_SkipConversation):
        worker._build_transcript(cid)


def test_build_transcript_raises_skip_when_all_messages_empty(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf)
    _insert_trace(sf, cid, messages=[
        {"role": "assistant", "text": ""},
        {"role": "user", "text": "   "},
    ])

    from ruhu.sentiment_worker import _SkipConversation
    import pytest
    with pytest.raises(_SkipConversation, match="empty"):
        worker._build_transcript(cid)


def test_build_transcript_trims_to_max_lines(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf)
    # Insert 40 messages — more than _MAX_TRANSCRIPT_LINES=30
    messages = [{"role": "user", "text": f"message {i}"} for i in range(40)]
    _insert_trace(sf, cid, messages=messages)

    transcript = worker._build_transcript(cid)
    lines = transcript.strip().split("\n")
    assert len(lines) == 30  # trimmed to _MAX_TRANSCRIPT_LINES


# ── _write_success ─────────────────────────────────────────────────────────────

def test_write_success_stores_score_and_complete_status(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf)
    now = _utcnow()
    worker._write_success(cid, score=0.6, now=now)

    meta = _load_metadata(sf, cid)
    assert meta[_METADATA_SCORE] == 0.6
    assert meta[_METADATA_STATUS] == _STATUS_COMPLETE
    assert meta[_METADATA_ANALYZED_AT] is not None


def test_write_success_clears_prior_retry_tracking(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf, metadata={
        _METADATA_STATUS: _STATUS_FAILED,
        _METADATA_ERROR: "previous error",
        _METADATA_ATTEMPTS: 1,
        _METADATA_NEXT_RETRY: (_utcnow() + timedelta(seconds=30)).isoformat(),
    })
    worker._write_success(cid, score=-0.2, now=_utcnow())

    meta = _load_metadata(sf, cid)
    assert meta[_METADATA_SCORE] == -0.2
    assert meta[_METADATA_STATUS] == _STATUS_COMPLETE
    assert _METADATA_ERROR not in meta
    assert _METADATA_ATTEMPTS not in meta
    assert _METADATA_NEXT_RETRY not in meta


# ── _write_failure ────────────────────────────────────────────────────────────

def test_write_failure_never_writes_a_score(postgres_database_url_factory) -> None:
    """Core invariant: failures must not pollute the sentiment_score field."""
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf, max_attempts=3)

    cid = _insert_ended_conversation(sf)
    worker._write_failure(cid, error="LLM timeout", now=_utcnow())

    meta = _load_metadata(sf, cid)
    assert _METADATA_SCORE not in meta


def test_write_failure_sets_failed_status_and_next_retry(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf, max_attempts=3, backoff_base=60.0)

    cid = _insert_ended_conversation(sf)
    now = _utcnow()
    worker._write_failure(cid, error="connection refused", now=now)

    meta = _load_metadata(sf, cid)
    assert meta[_METADATA_STATUS] == _STATUS_FAILED
    assert meta[_METADATA_ATTEMPTS] == 1
    assert meta[_METADATA_ERROR] == "connection refused"

    # next_retry_at = now + base * 2^0 = now + 60s
    next_retry = datetime.fromisoformat(meta[_METADATA_NEXT_RETRY])
    expected_delay = timedelta(seconds=60.0)
    assert abs((next_retry - now) - expected_delay) < timedelta(seconds=2)


def test_write_failure_uses_exponential_backoff(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf, max_attempts=5, backoff_base=10.0)

    cid = _insert_ended_conversation(sf, metadata={
        _METADATA_STATUS: _STATUS_FAILED,
        _METADATA_ATTEMPTS: 2,  # already had 2 attempts
        _METADATA_ERROR: "prev error",
    })
    now = _utcnow()
    worker._write_failure(cid, error="new error", now=now)

    meta = _load_metadata(sf, cid)
    assert meta[_METADATA_ATTEMPTS] == 3  # incremented

    # delay = base * 2^(attempt-1) = 10 * 2^2 = 40s
    next_retry = datetime.fromisoformat(meta[_METADATA_NEXT_RETRY])
    expected_delay = timedelta(seconds=40.0)
    assert abs((next_retry - now) - expected_delay) < timedelta(seconds=2)


def test_write_failure_sets_exhausted_after_max_attempts(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf, max_attempts=3)

    # Already at attempt 2 — next failure exhausts
    cid = _insert_ended_conversation(sf, metadata={
        _METADATA_STATUS: _STATUS_FAILED,
        _METADATA_ATTEMPTS: 2,
    })
    worker._write_failure(cid, error="final failure", now=_utcnow())

    meta = _load_metadata(sf, cid)
    assert meta[_METADATA_STATUS] == _STATUS_EXHAUSTED
    assert _METADATA_NEXT_RETRY not in meta
    assert _METADATA_SCORE not in meta  # never written


# ── _fetch_candidates ─────────────────────────────────────────────────────────

def test_fetch_candidates_skips_already_complete(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    _insert_ended_conversation(sf, metadata={
        _METADATA_SCORE: 0.5,
        _METADATA_STATUS: _STATUS_COMPLETE,
    })

    candidates = worker._fetch_candidates(_utcnow())
    assert candidates == []


def test_fetch_candidates_skips_exhausted(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    _insert_ended_conversation(sf, metadata={_METADATA_STATUS: _STATUS_EXHAUSTED})

    candidates = worker._fetch_candidates(_utcnow())
    assert candidates == []


def test_fetch_candidates_skips_failed_with_future_retry(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    future = (_utcnow() + timedelta(hours=1)).isoformat()
    _insert_ended_conversation(sf, metadata={
        _METADATA_STATUS: _STATUS_FAILED,
        _METADATA_NEXT_RETRY: future,
    })

    candidates = worker._fetch_candidates(_utcnow())
    assert candidates == []


def test_fetch_candidates_includes_failed_with_past_retry(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    past = (_utcnow() - timedelta(hours=1)).isoformat()
    cid = _insert_ended_conversation(sf, metadata={
        _METADATA_STATUS: _STATUS_FAILED,
        _METADATA_NEXT_RETRY: past,
    })

    candidates = worker._fetch_candidates(_utcnow())
    assert any(r.conversation_id == cid for r in candidates)


def test_fetch_candidates_includes_new_conversation(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf)  # no metadata at all

    candidates = worker._fetch_candidates(_utcnow())
    assert any(r.conversation_id == cid for r in candidates)


def test_fetch_candidates_skips_conversation_with_numeric_score(postgres_database_url_factory) -> None:
    """A sentiment_score field already in metadata means no re-analysis needed."""
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    _insert_ended_conversation(sf, metadata={_METADATA_SCORE: 0.0})

    candidates = worker._fetch_candidates(_utcnow())
    assert candidates == []


# ── process_once: end-to-end with mocked LLM ─────────────────────────────────

def test_process_once_analyses_conversation_and_writes_score(
    postgres_database_url_factory,
) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf)
    _insert_trace(sf, cid, messages=[
        {"role": "user", "text": "I love this product!"},
        {"role": "assistant", "text": "Glad to hear it!"},
    ])

    mock_resp = MagicMock()
    mock_resp.read.return_value = _fake_llm_response(0.9)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        summary = worker.process_once()

    assert summary.analysed_count == 1
    assert summary.failed_count == 0
    assert summary.skipped_count == 0

    meta = _load_metadata(sf, cid)
    assert meta[_METADATA_SCORE] == 0.9
    assert meta[_METADATA_STATUS] == _STATUS_COMPLETE


def test_process_once_records_failure_without_score_on_llm_error(
    postgres_database_url_factory,
) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf, max_attempts=3)

    cid = _insert_ended_conversation(sf)
    _insert_trace(sf, cid, messages=[{"role": "user", "text": "hello"}])

    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        summary = worker.process_once()

    assert summary.failed_count == 1
    assert summary.analysed_count == 0

    meta = _load_metadata(sf, cid)
    assert _METADATA_SCORE not in meta
    assert meta[_METADATA_STATUS] == _STATUS_FAILED


def test_process_once_skips_empty_transcript_without_recording_failure(
    postgres_database_url_factory,
) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = _make_worker(sf)

    cid = _insert_ended_conversation(sf)
    # No traces — transcript will be empty

    summary = worker.process_once()

    assert summary.skipped_count == 1
    assert summary.failed_count == 0

    meta = _load_metadata(sf, cid)
    assert _METADATA_SCORE not in meta
    assert _METADATA_STATUS not in meta  # no failure recorded for skips


# ── jobs-runtime integration (RP-3.2 step 4) ─────────────────────────────────

def test_sentiment_runs_as_recurring_tick_on_job_runtime(
    postgres_database_url_factory,
) -> None:
    """End-to-end: schedule -> tick job -> sentiment handler -> score written."""
    from ruhu.jobs import JobRuntime, SQLAlchemyJobStore
    from ruhu.runtime_config import RuntimeSettings
    from ruhu.worker import build_handler_registry

    database_url = postgres_database_url_factory()
    sf = build_session_factory(database_url)
    cid = _insert_ended_conversation(sf)
    _insert_trace(sf, cid, messages=[
        {"role": "user", "text": "I love this product!"},
    ])

    settings = RuntimeSettings(
        sentiment_worker_enabled=True,
        sentiment_worker_llm_base_url="https://api.example.test/v1",
        sentiment_worker_llm_api_key="test-key",
        sentiment_worker_interval_seconds=60.0,
        journey_runtime_worker_enabled=False,
        tool_integration_worker_enabled=False,
    )
    registry, schedules = build_handler_registry(
        session_factory=sf, settings=settings, database_url=database_url
    )
    assert [s.job_type for s in schedules] == ["sentiment.tick"]

    store = SQLAlchemyJobStore(sf)
    runtime = JobRuntime(store, registry, worker_id="w-test", schedules=schedules)

    mock_resp = MagicMock()
    mock_resp.read.return_value = _fake_llm_response(0.7)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        runtime.run_once()

    meta = _load_metadata(sf, cid)
    assert meta[_METADATA_SCORE] == 0.7
    assert meta[_METADATA_STATUS] == _STATUS_COMPLETE


def test_sentiment_tick_status_visible_from_jobs_table(
    postgres_database_url_factory,
) -> None:
    from ruhu.jobs import JobRuntime, SQLAlchemyJobStore, recurring_tick_status
    from ruhu.runtime_config import RuntimeSettings
    from ruhu.sentiment_worker import SENTIMENT_JOB_TYPE
    from ruhu.worker import build_handler_registry

    database_url = postgres_database_url_factory()
    sf = build_session_factory(database_url)
    store = SQLAlchemyJobStore(sf)
    settings = RuntimeSettings(
        sentiment_worker_enabled=True,
        sentiment_worker_llm_base_url="https://api.example.test/v1",
        sentiment_worker_llm_api_key="test-key",
        journey_runtime_worker_enabled=False,
        tool_integration_worker_enabled=False,
    )
    registry, schedules = build_handler_registry(
        session_factory=sf, settings=settings, database_url=database_url
    )

    before = recurring_tick_status(store, SENTIMENT_JOB_TYPE)
    assert before.scheduled is False and before.last_tick_at is None

    # No candidates — the tick succeeds without an LLM call.
    JobRuntime(store, registry, worker_id="w-test", schedules=schedules).run_once()

    after = recurring_tick_status(store, SENTIMENT_JOB_TYPE)
    assert after.last_tick_status == "succeeded"
    assert after.last_tick_at is not None
    assert after.last_error is None
