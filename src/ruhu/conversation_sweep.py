"""Stale conversation sweep.

Widget sessions and other channels never send an explicit close event — the user
just closes the browser tab. Conversations that were started but never reached a
terminal state therefore remain status='active' indefinitely. This means:

  - Resolution badges in the ticket dashboard always show "--" (outcome is None).
  - The outcome-derived sentiment proxy never fires (same root cause).
  - Active conversation counts are inflated.

The sweep finds conversations whose ``updated_at`` is older than
``idle_timeout_seconds`` and that are still in status='active' with no outcome,
and marks them:

    status  = 'ended'
    outcome = 'abandoned'
    ended_at = <sweep time>
    updated_at = <sweep time>

The change is a direct bulk UPDATE — no per-row Python round-trip — so the sweep
stays cheap even with large conversation tables.

Scheduling: this runs as a recurring tick on the unified jobs runtime
(``conversation_sweep.tick``, registered in ``ruhu.worker``) — it is not a
thread in the API process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from .db_models import ConversationRecord

logger = logging.getLogger(__name__)

_SweepOutcome = Literal["abandoned"]
_SWEEP_OUTCOME: _SweepOutcome = "abandoned"

SWEEP_JOB_TYPE = "conversation_sweep.tick"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ConversationSweepRunSummary:
    abandoned_count: int = 0
    error: str | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "abandoned_count": self.abandoned_count,
            "error": self.error,
        }


class ConversationSweep:
    """Marks idle active conversations as abandoned.

    Parameters
    ----------
    session_factory:
        SQLAlchemy session factory connected to the runtime database.
    idle_timeout_seconds:
        How long a conversation must be silent (no ``updated_at`` change) before
        it is considered abandoned. Default: 30 minutes.
    batch_size:
        Maximum number of conversations abandoned in a single sweep pass.
        Keeping this bounded prevents long-running transactions on large tables.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        idle_timeout_seconds: float = 1800.0,
        batch_size: int = 100,
    ) -> None:
        self._session_factory = session_factory
        self._idle_timeout = timedelta(seconds=max(60.0, float(idle_timeout_seconds)))
        self._batch_size = max(1, int(batch_size))

    def process_once(self) -> ConversationSweepRunSummary:
        """Find stale active conversations and mark them abandoned."""
        now = _utcnow()
        cutoff = now - self._idle_timeout
        summary = ConversationSweepRunSummary()
        try:
            with self._session_factory.begin() as session:
                # Identify stale conversation_ids first (bounded by batch_size)
                # so we can issue a targeted UPDATE rather than a full-table scan
                # with a LIMIT on the UPDATE itself (not portable across DBs).
                stale_ids = list(
                    session.scalars(
                        select(ConversationRecord.conversation_id)
                        .where(
                            ConversationRecord.status == "active",
                            ConversationRecord.outcome.is_(None),
                            ConversationRecord.updated_at < cutoff,
                        )
                        .order_by(ConversationRecord.updated_at.asc())
                        .limit(self._batch_size)
                    ).all()
                )
                if not stale_ids:
                    return summary

                result = session.execute(
                    update(ConversationRecord)
                    .where(ConversationRecord.conversation_id.in_(stale_ids))
                    .values(
                        status="ended",
                        outcome=_SWEEP_OUTCOME,
                        ended_at=now,
                        updated_at=now,
                    )
                )
                summary.abandoned_count = result.rowcount
        except Exception as exc:
            summary.error = str(exc)
            logger.exception("conversation sweep failed: %s", exc)

        if summary.abandoned_count:
            logger.info(
                "conversation sweep: abandoned %d stale conversations (idle > %ds)",
                summary.abandoned_count,
                int(self._idle_timeout.total_seconds()),
            )
        return summary
