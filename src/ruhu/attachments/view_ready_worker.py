"""Attachment view-ready worker.

Synthesizes ``system_event`` turns when an attachment view transitions to
``ready`` and the conversation's current state has a matching ``view_ready``
transition.

Canonical spec:
  docs/realtime-system/Attachment-Only-Turn-Kernel-Behavior.md
    §"View-ready follow-up turns"
  docs/realtime-system/Attachment-System-First-Principles-And-Rebuild-Spec.md
    §"Storage Impact" — Option B

Algorithm
---------

Each sweep queries ``attachment_views`` (status='ready') LEFT JOINed against
``attachment_view_deliveries`` to find view-ready events that have not yet
been actioned.  For each candidate:

1. **Subscription-time scoping** — load the conversation and agent version
   at the time the worker fires.  If the conversation is gone, ended, or the
   agent version can no longer be loaded, record a ``skipped_*`` result and
   move on.

2. **Dispatch-time revalidation** — check whether the *current* state of the
   conversation has a ``view_ready`` transition whose ``view_kind`` matches
   the view's kind.  If the conversation advanced to a different state
   between subscription and dispatch (e.g. the user replied), this check
   fails and the delivery is recorded as ``skipped_stale``.

3. **Optimistic claim (dedup gate)** — INSERT a delivery record before
   calling the kernel.  The unique constraint on
   ``(conversation_id, attachment_id, view_kind)`` ensures that exactly one
   worker can claim each candidate.  A race-losing worker receives
   IntegrityError and exits early.

4. **Kernel dispatch** — call ``kernel.process_turn`` with a synthetic
   ``RuntimeTurn(event_type='system_event', metadata={'system_event_kind':
   'view_ready', 'view_kind': <kind>})`` that carries the attachment ref
   (with ``inline_text`` populated for text views).

5. **Result recording** — on kernel success the delivery record stays as
   ``dispatched``; on kernel failure it is updated to ``failed`` with an
   error_detail excerpt.

Scheduling: this runs as a recurring tick on the unified jobs runtime
(``view_ready.tick``, registered in ``ruhu.worker``) — it is not a thread in
the API process. Opt-in per-deployment via
``RUHU_ATTACHMENTS_VIEW_READY_WORKER_ENABLED``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import AttachmentRef
from .sqlalchemy_models import (
    AttachmentRecord,
    AttachmentViewDeliveryRecord,
    AttachmentViewRecord,
)
from ..db_models import ConversationRecord
from ..schemas import RuntimeTurn

if TYPE_CHECKING:
    from ..kernel import ConversationKernel
    from ..registry import SQLAlchemyAgentRegistry

logger = logging.getLogger(__name__)

VIEW_READY_JOB_TYPE = "view_ready.tick"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_delivery_id() -> str:
    return f"avd_{uuid4().hex}"


def _new_turn_id() -> str:
    return f"turn_{uuid4().hex}"


@dataclass(slots=True)
class ViewReadyRunSummary:
    dispatched_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    error: str | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "dispatched_count": self.dispatched_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "error": self.error,
        }


class AttachmentViewReadyWorker:
    """Dispatches view-ready system_event turns to the conversation kernel.

    Parameters
    ----------
    session_factory:
        SQLAlchemy session factory.
    kernel:
        ``ConversationKernel`` instance — the target for turn dispatch.
    agent_registry:
        ``SQLAlchemyAgentRegistry`` — used to deserialize agent version
        objects for revalidation.
    batch_size:
        Maximum candidates processed per sweep.  Limits latency spikes when
        a large backlog accumulates.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        kernel: "ConversationKernel",
        agent_registry: "SQLAlchemyAgentRegistry",
        batch_size: int = 50,
    ) -> None:
        self._session_factory = session_factory
        self._kernel = kernel
        self._agent_registry = agent_registry
        self._batch_size = max(1, int(batch_size))

    # ── Single sweep pass (the view_ready.tick handler) ───────────────────────

    def process_once(self) -> ViewReadyRunSummary:
        summary = ViewReadyRunSummary()
        try:
            candidates = self._find_candidates()
        except Exception as exc:
            summary.error = f"find_candidates: {exc}"
            logger.exception("view-ready worker: failed to find candidates: %s", exc)
            return summary

        for candidate in candidates:
            try:
                result = self._process_candidate(candidate)
                if result == "dispatched":
                    summary.dispatched_count += 1
                elif result == "failed":
                    summary.failed_count += 1
                else:
                    summary.skipped_count += 1
            except Exception as exc:
                summary.failed_count += 1
                logger.exception(
                    "view-ready worker: unhandled error for view=%s attachment=%s: %s",
                    candidate.view_id,
                    candidate.attachment_id,
                    exc,
                )

        if summary.dispatched_count or summary.failed_count:
            logger.info(
                "view-ready worker: dispatched=%d skipped=%d failed=%d",
                summary.dispatched_count,
                summary.skipped_count,
                summary.failed_count,
            )
        return summary

    # ── Core implementation ────────────────────────────────────────────────────

    def _find_candidates(self) -> list[AttachmentViewRecord]:
        """Return ready views that have no delivery record.

        LEFT JOIN against ``attachment_view_deliveries`` on the
        (conversation_id, attachment_id, view_kind) tuple so we pick up
        only candidates that have never been actioned (regardless of
        result value).
        """
        stmt = (
            select(AttachmentViewRecord)
            .outerjoin(
                AttachmentViewDeliveryRecord,
                (AttachmentViewDeliveryRecord.conversation_id == AttachmentViewRecord.conversation_id)
                & (AttachmentViewDeliveryRecord.attachment_id == AttachmentViewRecord.attachment_id)
                & (AttachmentViewDeliveryRecord.view_kind == AttachmentViewRecord.kind),
            )
            .where(
                AttachmentViewRecord.status == "ready",
                AttachmentViewDeliveryRecord.delivery_id.is_(None),
            )
            .order_by(AttachmentViewRecord.updated_at.asc())
            .limit(self._batch_size)
        )
        with self._session_factory() as session:
            return list(session.execute(stmt).scalars().all())

    def _process_candidate(self, view_record: AttachmentViewRecord) -> str:
        """Process one candidate.

        Returns one of the canonical result values:
        ``dispatched`` | ``skipped_no_match`` | ``skipped_stale`` |
        ``skipped_attachment_gone`` | ``skipped_agent_version_missing`` | ``failed``.
        """
        now = _utcnow()

        # ── 1. Subscription-time scoping ──────────────────────────────────────
        with self._session_factory() as session:
            conversation_record = session.get(ConversationRecord, view_record.conversation_id)

        if conversation_record is None or conversation_record.status == "ended":
            return self._record_skip(view_record, "skipped_attachment_gone", now=now)

        try:
            snapshot = self._agent_registry.get_version_snapshot(
                conversation_record.agent_version_id,
                organization_id=conversation_record.organization_id,
            )
        except Exception as exc:
            logger.warning(
                "view-ready worker: cannot load agent version %s for conversation %s: %s",
                conversation_record.agent_version_id,
                view_record.conversation_id,
                exc,
            )
            return self._record_skip(view_record, "skipped_agent_version_missing", now=now)

        agent_document = snapshot.agent_document
        if agent_document is None:
            return self._record_skip(view_record, "skipped_stale", now=now)

        # ── 2. Dispatch-time revalidation ─────────────────────────────────────
        try:
            current_step = agent_document.step_by_id(conversation_record.step_id)
        except KeyError:
            # Step ID recorded on the conversation is no longer in the agent
            # version — the agent was likely republished between upload and now.
            return self._record_skip(view_record, "skipped_stale", now=now)

        matching = next(
            (
                t
                for t in current_step.transitions
                if t.when.kind == "view_ready" and t.when.view_kind == view_record.kind
            ),
            None,
        )
        if matching is None:
            return self._record_skip(view_record, "skipped_no_match", now=now)

        # ── 3. Load attachment for ref materialization ─────────────────────────
        with self._session_factory() as session:
            attachment_record = session.get(AttachmentRecord, view_record.attachment_id)

        if attachment_record is None or attachment_record.deleted_at is not None:
            return self._record_skip(view_record, "skipped_attachment_gone", now=now)

        # ── 4. Claim the delivery slot (optimistic dedup gate) ────────────────
        delivery_id = _new_delivery_id()
        try:
            with self._session_factory.begin() as session:
                session.add(
                    AttachmentViewDeliveryRecord(
                        delivery_id=delivery_id,
                        organization_id=view_record.organization_id,
                        conversation_id=view_record.conversation_id,
                        attachment_id=view_record.attachment_id,
                        view_kind=view_record.kind,
                        result="dispatched",
                        error_detail=None,
                        source_event_id=None,
                        delivered_at=now,
                    )
                )
        except IntegrityError:
            # Another worker instance already claimed this candidate.
            logger.debug(
                "view-ready worker: delivery slot already claimed "
                "(conv=%s, att=%s, kind=%s) — race loss, skipping",
                view_record.conversation_id,
                view_record.attachment_id,
                view_record.kind,
            )
            return "skipped_no_match"

        # ── 5. Kernel dispatch ────────────────────────────────────────────────
        turn = _build_view_ready_turn(
            attachment_record=attachment_record,
            view_record=view_record,
            conversation_record=conversation_record,
        )
        try:
            self._kernel.process_turn(
                conversation_record.conversation_id,
                turn,
                agent_document=agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                organization_id=conversation_record.organization_id,
            )
            logger.debug(
                "view-ready worker: dispatched turn %s for conv=%s kind=%s",
                turn.turn_id,
                view_record.conversation_id,
                view_record.kind,
            )
            return "dispatched"
        except Exception as exc:
            error_msg = str(exc)
            logger.exception(
                "view-ready worker: kernel dispatch failed "
                "(conv=%s, att=%s, kind=%s): %s",
                view_record.conversation_id,
                view_record.attachment_id,
                view_record.kind,
                exc,
            )
            # Update the delivery record we already inserted so the slot
            # reflects the actual outcome.
            try:
                with self._session_factory.begin() as session:
                    session.execute(
                        update(AttachmentViewDeliveryRecord)
                        .where(AttachmentViewDeliveryRecord.delivery_id == delivery_id)
                        .values(result="failed", error_detail=error_msg[:1000])
                    )
            except Exception:
                pass  # Best-effort; dispatch error is already logged above
            return "failed"

    def _record_skip(
        self,
        view_record: AttachmentViewRecord,
        result: str,
        *,
        now: datetime,
    ) -> str:
        """Insert a skip delivery record, silently ignoring duplicates."""
        try:
            with self._session_factory.begin() as session:
                session.add(
                    AttachmentViewDeliveryRecord(
                        delivery_id=_new_delivery_id(),
                        organization_id=view_record.organization_id,
                        conversation_id=view_record.conversation_id,
                        attachment_id=view_record.attachment_id,
                        view_kind=view_record.kind,
                        result=result,
                        error_detail=None,
                        source_event_id=None,
                        delivered_at=now,
                    )
                )
        except IntegrityError:
            pass
        return result


# ── Turn builder ──────────────────────────────────────────────────────────────

def _build_view_ready_turn(
    *,
    attachment_record: AttachmentRecord,
    view_record: AttachmentViewRecord,
    conversation_record: ConversationRecord,
) -> RuntimeTurn:
    """Build the synthetic ``system_event`` turn for view-ready dispatch.

    The turn carries:
    - ``event_type='system_event'``
    - ``metadata['system_event_kind'] = 'view_ready'``
    - ``metadata['view_kind']`` = the view kind that just became ready
    - An ``AttachmentRef`` with ``inline_text`` populated for text views,
      so the kernel can immediately use the content without a second DB read.
    """
    inline_text: str | None = None
    if view_record.kind == "text" and view_record.content_text:
        inline_text = view_record.content_text

    ref = AttachmentRef(
        attachment_id=attachment_record.attachment_id,
        kind=attachment_record.kind,  # type: ignore[arg-type]
        source=attachment_record.source,
        filename=attachment_record.filename,
        content_type=attachment_record.content_type,
        trust_tier=attachment_record.trust_tier,  # type: ignore[arg-type]
        available_views=[view_record.kind],  # type: ignore[list-item]
        inline_text=inline_text,
        size_bytes=attachment_record.size_bytes,
    )
    # Preserve the conversation's original channel so trace records are
    # attributed to the right surface.
    channel = conversation_record.channel or "web_widget"
    return RuntimeTurn(
        turn_id=_new_turn_id(),
        # Dedupe key is stable per view_id so re-runs during outage recovery
        # are idempotent at the kernel level (kernel tracks dedupe_keys).
        dedupe_key=f"view_ready:{view_record.view_id}",
        channel=channel,  # type: ignore[arg-type]
        modality="event",
        event_type="system_event",
        text=None,
        attachments=[ref],
        metadata={
            "system_event_kind": "view_ready",
            "view_kind": view_record.kind,
            "attachment_id": attachment_record.attachment_id,
            "source_view_id": view_record.view_id,
        },
        received_at=_utcnow(),
    )
