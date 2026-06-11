"""Attachment retention sweep.

Implements the retention half of the attachment-system rebuild
(canonical spec §"Immediate backend changes" item 9 in
docs/realtime-system/Attachment-System-First-Principles-And-Rebuild-Spec.md).

Two-phase lifecycle:

  1. ``retention_expires_at`` is set at upload time (or later, by policy).
     The soft-delete pass finds attachments whose ``retention_expires_at``
     is in the past and whose ``deleted_at`` is still NULL, and sets
     ``deleted_at = now()``.
  2. After a grace period (default 30 days) past the ``deleted_at``
     timestamp, the hard-delete pass removes the row and its cascading
     blob/view rows entirely.

Design choices:

  - No per-row Python round-trip: both passes are bulk UPDATE/DELETE with
    a pre-selected batch of IDs, matching ``ConversationSweep``.
  - Idempotent: re-running either pass on the same rows is a no-op.
  - RLS-safe: operations go through SQLAlchemy session factories and
    respect the same ``OptionalTenantScopeMixin`` policies as the rest of
    the attachment layer.
  - Hard-delete cascades to ``attachment_blobs`` and ``attachment_views``
    via ``ON DELETE CASCADE`` foreign keys declared in the migration.

Scheduling: runs as a recurring tick on the unified jobs runtime
(``attachment_retention.tick``, registered in ``ruhu.worker``); opt-in via
``RuntimeSettings.attachments_retention_sweep_enabled``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from .sqlalchemy_models import AttachmentRecord

logger = logging.getLogger(__name__)

RETENTION_JOB_TYPE = "attachment_retention.tick"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class AttachmentRetentionRunSummary:
    soft_deleted_count: int = 0
    hard_deleted_count: int = 0
    error: str | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "soft_deleted_count": self.soft_deleted_count,
            "hard_deleted_count": self.hard_deleted_count,
            "error": self.error,
        }


class AttachmentRetention:
    """Two-phase retention sweep for attachments.

    Parameters
    ----------
    session_factory:
        SQLAlchemy session factory connected to the runtime database.
    batch_size:
        Maximum attachments processed per pass (both soft and hard delete
        are bounded by this).
    hard_delete_grace_seconds:
        How long a soft-deleted attachment remains before the blob + views
        are purged.  Default: 30 days.  Set to a smaller value for dev/test
        environments where reclaiming storage matters more than undo
        windows.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        batch_size: int = 100,
        hard_delete_grace_seconds: float = 30 * 24 * 3600.0,
    ) -> None:
        self._session_factory = session_factory
        self._batch_size = max(1, int(batch_size))
        self._hard_delete_grace = timedelta(seconds=max(0.0, float(hard_delete_grace_seconds)))

    def process_once(self) -> AttachmentRetentionRunSummary:
        """Run both soft-delete and hard-delete passes once."""
        summary = AttachmentRetentionRunSummary()
        now = _utcnow()
        try:
            summary.soft_deleted_count = self._soft_delete_expired(now)
        except Exception as exc:
            summary.error = f"soft_delete: {exc}"
            logger.exception("attachment retention soft-delete failed: %s", exc)
            return summary
        try:
            summary.hard_deleted_count = self._hard_delete_past_grace(now)
        except Exception as exc:
            # Record the error but keep the soft-delete count — it actually
            # happened regardless of whether hard-delete succeeded.
            summary.error = f"hard_delete: {exc}"
            logger.exception("attachment retention hard-delete failed: %s", exc)

        if summary.soft_deleted_count or summary.hard_deleted_count:
            logger.info(
                "attachment retention: soft-deleted %d, hard-deleted %d",
                summary.soft_deleted_count,
                summary.hard_deleted_count,
            )
        return summary

    def _soft_delete_expired(self, now: datetime) -> int:
        """Find attachments past retention and mark ``deleted_at = now``.

        Idempotent: attachments that already have ``deleted_at != NULL`` are
        excluded, so re-runs on the same set are a no-op.
        """
        with self._session_factory.begin() as session:
            stale_ids = list(
                session.scalars(
                    select(AttachmentRecord.attachment_id)
                    .where(
                        AttachmentRecord.retention_expires_at.isnot(None),
                        AttachmentRecord.retention_expires_at < now,
                        AttachmentRecord.deleted_at.is_(None),
                    )
                    .order_by(AttachmentRecord.retention_expires_at.asc())
                    .limit(self._batch_size)
                ).all()
            )
            if not stale_ids:
                return 0
            result = session.execute(
                update(AttachmentRecord)
                .where(AttachmentRecord.attachment_id.in_(stale_ids))
                .values(deleted_at=now, updated_at=now)
            )
            return result.rowcount or 0

    def _hard_delete_past_grace(self, now: datetime) -> int:
        """Remove attachments whose soft-delete is older than the grace
        period.  ON DELETE CASCADE on the blob + views FKs handles the
        associated rows.
        """
        grace_cutoff = now - self._hard_delete_grace
        with self._session_factory.begin() as session:
            stale_ids = list(
                session.scalars(
                    select(AttachmentRecord.attachment_id)
                    .where(
                        AttachmentRecord.deleted_at.isnot(None),
                        AttachmentRecord.deleted_at < grace_cutoff,
                    )
                    .order_by(AttachmentRecord.deleted_at.asc())
                    .limit(self._batch_size)
                ).all()
            )
            if not stale_ids:
                return 0
            result = session.execute(
                delete(AttachmentRecord).where(AttachmentRecord.attachment_id.in_(stale_ids))
            )
            return result.rowcount or 0
