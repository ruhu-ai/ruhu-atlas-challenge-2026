"""Post-conversation LLM sentiment analysis worker.

After a conversation ends, this worker pulls its transcript and asks an
OpenAI-compatible LLM to score customer sentiment on a -1.0 → +1.0 scale.
The score is written to ``metadata_json["sentiment_score"]``, which the
``_sentiment_score()`` function in ``ticket_system.py`` already reads first
(before falling back to the outcome proxy).

Failure handling:
  - If the LLM call fails, the worker writes tracking fields to ``metadata_json``
    WITHOUT writing a sentiment score.  A synthetic 0.0/neutral is never written,
    because that would corrupt analytics by making genuinely unanalysed conversations
    look neutral.
  - Conversations that have failed analysis fewer than ``max_attempts`` times are
    retried on subsequent worker passes (with exponential backoff tracked in the
    metadata itself so no extra table is required).
  - After ``max_attempts`` exhaustion the conversation is left with
    ``sentiment_analysis_status = "exhausted"`` and the outcome-proxy fallback in
    ``ticket_system.py`` remains the display value.

Metadata keys written
---------------------
On success:
    sentiment_score                  float  [-1.0, 1.0]
    sentiment_analysis_status        "complete"
    sentiment_analyzed_at            ISO-8601 UTC

On failure / pending-retry:
    sentiment_analysis_status        "failed"   (or "exhausted" after max attempts)
    sentiment_analysis_error         str — last error message
    sentiment_analysis_attempt_count int
    sentiment_analysis_next_retry_at ISO-8601 UTC  (absent when exhausted)

Configuration
-------------
All settings are read from ``RuntimeSettings`` and sourced from environment
variables with the ``RUHU_SENTIMENT_`` prefix.  The worker is disabled by
default; set ``RUHU_SENTIMENT_WORKER_ENABLED=true`` to activate it.

Scheduling: this runs as a recurring tick on the unified jobs runtime
(``sentiment.tick``, registered in ``ruhu.worker``) — it is not a thread in
the API process.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import ConversationRecord, TurnTraceRecord

logger = logging.getLogger(__name__)

SENTIMENT_JOB_TYPE = "sentiment.tick"

_STATUS_COMPLETE = "complete"
_STATUS_FAILED = "failed"
_STATUS_EXHAUSTED = "exhausted"

_METADATA_SCORE = "sentiment_score"
_METADATA_STATUS = "sentiment_analysis_status"
_METADATA_ERROR = "sentiment_analysis_error"
_METADATA_ATTEMPTS = "sentiment_analysis_attempt_count"
_METADATA_ANALYZED_AT = "sentiment_analyzed_at"
_METADATA_NEXT_RETRY = "sentiment_analysis_next_retry_at"

# System prompt for the LLM.  Deliberately short so it fits in cheap models.
_SYSTEM_PROMPT = (
    "You are a sentiment analyser for customer service conversations. "
    "Given a transcript, return a single JSON object with one key: "
    '\"score\". The score is a float from -1.0 (very negative) to +1.0 '
    "(very positive), with 0.0 meaning neutral. "
    "Return ONLY the JSON object, no other text."
)

# How many transcript lines (user+assistant turns) to include in the prompt.
_MAX_TRANSCRIPT_LINES = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SentimentAnalysisRunSummary:
    analysed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    error: str | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "analysed_count": self.analysed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "error": self.error,
        }


class ConversationSentimentWorker:
    """Scores conversation sentiment using an LLM.

    Parameters
    ----------
    session_factory:
        SQLAlchemy sync session factory.
    llm_base_url:
        Base URL for an OpenAI-compatible completions API (e.g.
        ``https://api.openai.com/v1``).
    llm_api_key:
        Bearer token for the LLM API.
    model:
        Model name to pass to the API (e.g. ``"gpt-4o-mini"``).
    batch_size:
        Maximum conversations analysed per run.
    max_attempts:
        Give up after this many consecutive failures per conversation.
    backoff_base_seconds:
        Base for exponential backoff: next_retry = now + base * 2^(attempt-1).
    timeout_seconds:
        HTTP timeout for each LLM call.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        llm_base_url: str,
        llm_api_key: str,
        model: str = "gpt-4o-mini",
        batch_size: int = 20,
        max_attempts: int = 3,
        backoff_base_seconds: float = 60.0,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._session_factory = session_factory
        self._llm_base_url = llm_base_url.rstrip("/")
        self._llm_api_key = llm_api_key
        self._model = model
        self._batch_size = max(1, int(batch_size))
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base = max(1.0, float(backoff_base_seconds))
        self._timeout_seconds = max(5.0, float(timeout_seconds))

    # ── Single analysis pass (the sentiment.tick handler) ─────────────────────

    def process_once(self) -> SentimentAnalysisRunSummary:
        summary = SentimentAnalysisRunSummary()
        now = _utcnow()

        try:
            candidates = self._fetch_candidates(now)
        except Exception as exc:
            summary.error = str(exc)
            logger.exception("sentiment worker: failed to fetch candidates: %s", exc)
            return summary

        for record in candidates:
            try:
                transcript_text = self._build_transcript(record.conversation_id)
                score = self._call_llm(transcript_text)
                self._write_success(record.conversation_id, score=score, now=now)
                summary.analysed_count += 1
            except _SkipConversation:
                summary.skipped_count += 1
            except Exception as exc:
                logger.warning(
                    "sentiment worker: analysis failed for %s: %s",
                    record.conversation_id,
                    exc,
                )
                self._write_failure(record.conversation_id, error=str(exc), now=now)
                summary.failed_count += 1

        return summary

    # ── Candidate selection ───────────────────────────────────────────────────

    def _fetch_candidates(self, now: datetime) -> list[ConversationRecord]:
        """Return ended conversations that still need sentiment analysis.

        A conversation is eligible if:
          - status = 'ended'
          - metadata does NOT have sentiment_score already
          - metadata does NOT have sentiment_analysis_status = 'complete' or 'exhausted'
          - if sentiment_analysis_status = 'failed', next_retry_at <= now
        """
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(ConversationRecord)
                    .where(ConversationRecord.status == "ended")
                    .order_by(ConversationRecord.ended_at.asc())
                    .limit(self._batch_size * 5)  # over-fetch; filter in Python
                ).all()
            )

        candidates: list[ConversationRecord] = []
        for record in rows:
            if len(candidates) >= self._batch_size:
                break
            meta = dict(record.metadata_json or {})
            if isinstance(meta.get(_METADATA_SCORE), (int, float)):
                continue  # already has a real score
            status = meta.get(_METADATA_STATUS)
            if status in (_STATUS_COMPLETE, _STATUS_EXHAUSTED):
                continue
            if status == _STATUS_FAILED:
                next_retry_raw = meta.get(_METADATA_NEXT_RETRY)
                if isinstance(next_retry_raw, str):
                    try:
                        next_retry = datetime.fromisoformat(next_retry_raw)
                        if next_retry > now:
                            continue  # not yet due
                    except ValueError:
                        pass
            candidates.append(record)

        return candidates

    # ── Transcript builder ────────────────────────────────────────────────────

    def _build_transcript(self, conversation_id: str) -> str:
        with self._session_factory() as session:
            traces = list(
                session.scalars(
                    select(TurnTraceRecord)
                    .where(TurnTraceRecord.conversation_id == conversation_id)
                    .order_by(TurnTraceRecord.recorded_at.asc())
                ).all()
            )
        if not traces:
            raise _SkipConversation("no traces found")

        lines: list[str] = []
        for trace in traces:
            for msg in list(trace.emitted_messages_json or []):
                role = str(msg.get("role") or "assistant")
                text = str(msg.get("text") or msg.get("content") or "").strip()
                if text:
                    lines.append(f"{role}: {text}")

        if not lines:
            # Try reading tool calls for any user messages captured in the trace.
            for trace in traces:
                for event in list(trace.semantic_events_json or []):
                    text = str(event.get("text") or "").strip()
                    if text:
                        lines.append(f"user: {text}")

        if not lines:
            raise _SkipConversation("transcript is empty after extraction")

        # Trim to the last N lines so the prompt stays small.
        if len(lines) > _MAX_TRANSCRIPT_LINES:
            lines = lines[-_MAX_TRANSCRIPT_LINES:]

        return "\n".join(lines)

    # ── LLM call ──────────────────────────────────────────────────────────────

    def _call_llm(self, transcript: str) -> float:
        """POST to an OpenAI-compatible chat completions endpoint and parse the score."""
        import urllib.request

        url = f"{self._llm_base_url}/chat/completions"
        payload = json.dumps({
            "model": self._model,
            "temperature": 0,
            "max_tokens": 32,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Transcript:\n{transcript}"},
            ],
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._llm_api_key}",
            },
            method="POST",
        )
        # urllib has no native timeout per-request; use socket default via a thread trick.
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self._timeout_seconds)
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read().decode()
        finally:
            socket.setdefaulttimeout(old_timeout)

        response_json = json.loads(body)
        content = response_json["choices"][0]["message"]["content"].strip()

        # Parse the returned JSON object.
        parsed = json.loads(content)
        raw_score = parsed["score"]
        if not isinstance(raw_score, (int, float)):
            raise ValueError(f"LLM returned non-numeric score: {raw_score!r}")
        score = float(raw_score)
        if not (-1.0 <= score <= 1.0):
            raise ValueError(f"LLM score out of range: {score}")
        return round(score, 3)

    # ── Result writers ────────────────────────────────────────────────────────

    def _write_success(self, conversation_id: str, *, score: float, now: datetime) -> None:
        with self._session_factory.begin() as session:
            record = session.get(ConversationRecord, conversation_id)
            if record is None:
                return
            meta = dict(record.metadata_json or {})
            meta[_METADATA_SCORE] = score
            meta[_METADATA_STATUS] = _STATUS_COMPLETE
            meta[_METADATA_ANALYZED_AT] = now.isoformat()
            # Clear retry tracking on success.
            meta.pop(_METADATA_ERROR, None)
            meta.pop(_METADATA_ATTEMPTS, None)
            meta.pop(_METADATA_NEXT_RETRY, None)
            record.metadata_json = meta
            record.updated_at = now

    def _write_failure(self, conversation_id: str, *, error: str, now: datetime) -> None:
        """Record failure WITHOUT writing a synthetic score."""
        with self._session_factory.begin() as session:
            record = session.get(ConversationRecord, conversation_id)
            if record is None:
                return
            meta = dict(record.metadata_json or {})
            attempt_count = int(meta.get(_METADATA_ATTEMPTS, 0)) + 1
            meta[_METADATA_ATTEMPTS] = attempt_count
            meta[_METADATA_ERROR] = error[:500]  # cap to avoid bloating metadata column

            if attempt_count >= self._max_attempts:
                meta[_METADATA_STATUS] = _STATUS_EXHAUSTED
                meta.pop(_METADATA_NEXT_RETRY, None)
                logger.info(
                    "sentiment worker: exhausted retries for %s after %d attempts",
                    conversation_id,
                    attempt_count,
                )
            else:
                meta[_METADATA_STATUS] = _STATUS_FAILED
                # Exponential backoff: base * 2^(attempt-1)
                delay_seconds = self._backoff_base * (2 ** (attempt_count - 1))
                next_retry = now + timedelta(seconds=delay_seconds)
                meta[_METADATA_NEXT_RETRY] = next_retry.isoformat()

            # Important: never write a sentiment_score on failure.
            meta.pop(_METADATA_SCORE, None)

            record.metadata_json = meta
            record.updated_at = now


class _SkipConversation(Exception):
    """Raised internally to skip a conversation without recording a failure."""
