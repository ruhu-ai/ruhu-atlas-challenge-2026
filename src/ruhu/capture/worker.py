"""Capture audit outbox delivery and retention sweep.

Scheduling: runs as a recurring tick on the unified jobs runtime
(``capture_audit.tick``, registered in ``ruhu.worker``); opt-in via
``RuntimeSettings.capture_audit_worker_enabled``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ruhu.capture.audit import AuditWriter
from ruhu.capture.outbox import drain_capture_audit_outbox
from ruhu.capture.retention import sweep_capture_audit

logger = logging.getLogger(__name__)

CAPTURE_AUDIT_JOB_TYPE = "capture_audit.tick"


@dataclass(slots=True)
class CaptureAuditRunSummary:
    outbox_delivered_count: int = 0
    retention_deleted_count: int = 0
    error: str | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "outbox_delivered_count": self.outbox_delivered_count,
            "retention_deleted_count": self.retention_deleted_count,
            "error": self.error,
        }


class CaptureAudit:
    """Drains the capture audit outbox, then sweeps retention."""

    def __init__(
        self,
        *,
        session_factory,
        audit_writer: AuditWriter,
        outbox_batch_size: int = 100,
        retention_days: int = 90,
        retention_batch_size: int = 500,
    ) -> None:
        self._session_factory = session_factory
        self._audit_writer = audit_writer
        self._outbox_batch_size = max(1, int(outbox_batch_size))
        self._retention_days = max(1, int(retention_days))
        self._retention_batch_size = max(1, int(retention_batch_size))

    def process_once(self) -> CaptureAuditRunSummary:
        summary = CaptureAuditRunSummary()
        try:
            summary.outbox_delivered_count = drain_capture_audit_outbox(
                self._session_factory,
                audit_writer=self._audit_writer,
                batch_size=self._outbox_batch_size,
            )
        except Exception as exc:  # noqa: BLE001
            summary.error = f"outbox: {exc}"
            logger.exception("capture audit outbox drain failed: %s", exc)
            return summary
        try:
            engine = getattr(self._session_factory, "kw", {}).get("bind")
            if engine is None:
                raise RuntimeError("capture audit requires a sessionmaker bound to an engine")
            retention = sweep_capture_audit(
                engine,
                audit_window_days=self._retention_days,
                batch_size=self._retention_batch_size,
            )
            summary.retention_deleted_count = retention.rows_deleted
            if retention.errors:
                summary.error = "; ".join(retention.errors)
        except Exception as exc:  # noqa: BLE001
            summary.error = f"retention: {exc}"
            logger.exception("capture audit retention sweep failed: %s", exc)
        return summary
