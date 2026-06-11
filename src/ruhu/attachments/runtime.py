from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..runtime_config import RuntimeSettings
from .models import AttachmentRuntimeStatus
from .service import AttachmentService
from .store import SQLAlchemyAttachmentStore

logger = logging.getLogger(__name__)


def build_realtime_attachment_event_emitter(control_plane: object):
    """Adapt attachment-service events onto the realtime control plane."""

    def _emit(
        *,
        conversation_id: str,
        organization_id: str | None,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        family = "artifact" if name.startswith("artifact.") else "attachment"
        event_name = name.split(".", 1)[1] if family == "artifact" and "." in name else name
        try:
            control_plane.events.append(
                conversation_id=conversation_id,
                organization_id=organization_id,
                family=family,
                name=event_name,
                payload=dict(payload),
                actor_type="system",
                visibility="surface",
                outbox_topic="conversation_projection",
            )
        except Exception:
            logger.warning("attachment realtime emitter failed", exc_info=True)

    return _emit


@dataclass(slots=True)
class AttachmentRuntime:
    service: AttachmentService
    max_workers: int = 2
    _lock: Lock = field(init=False, repr=False)
    _executor: ThreadPoolExecutor = field(init=False, repr=False)
    _futures: dict[str, Future[Any]] = field(init=False, repr=False, default_factory=dict)
    _completed_jobs: int = field(init=False, repr=False, default=0)
    _failed_jobs: int = field(init=False, repr=False, default=0)
    _last_error: str | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, self.max_workers), thread_name_prefix="ruhu-att")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def schedule_processing(
        self,
        *,
        attachment_id: str,
        organization_id: str | None = None,
    ) -> None:
        with self._lock:
            future = self._futures.get(attachment_id)
            if future is not None and not future.done():
                return
            future = self._executor.submit(self._run_job, attachment_id, organization_id)
            self._futures[attachment_id] = future

    def status(self) -> AttachmentRuntimeStatus:
        with self._lock:
            queued_jobs = sum(1 for future in self._futures.values() if not future.running() and not future.done())
            running_jobs = sum(1 for future in self._futures.values() if future.running())
            return AttachmentRuntimeStatus(
                queued_jobs=queued_jobs,
                running_jobs=running_jobs,
                completed_jobs=self._completed_jobs,
                failed_jobs=self._failed_jobs,
                last_error=self._last_error,
            )

    def _run_job(self, attachment_id: str, organization_id: str | None) -> None:
        try:
            self.service.process_attachment(attachment_id=attachment_id, organization_id=organization_id)
            with self._lock:
                self._completed_jobs += 1
        except Exception as exc:
            with self._lock:
                self._failed_jobs += 1
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._futures.pop(attachment_id, None)


def build_attachment_runtime(
    *,
    session_factory: sessionmaker[Session],
    runtime_settings: RuntimeSettings,
    gemini_api_key: str | None = None,
) -> AttachmentRuntime:
    store = SQLAlchemyAttachmentStore(session_factory)
    file_uploader = None
    vision_producer = None
    if runtime_settings.attachments_vision_enabled and gemini_api_key:
        from .producers import GeminiFileUploader, GeminiVisionProducer
        file_uploader = GeminiFileUploader(api_key=gemini_api_key)
        vision_producer = GeminiVisionProducer(api_key=gemini_api_key)
    return AttachmentRuntime(
        service=AttachmentService(
            store=store,
            max_file_bytes=runtime_settings.attachments_max_file_bytes,
            file_uploader=file_uploader,
            vision_producer=vision_producer,
        ),
        max_workers=runtime_settings.attachments_workers,
    )
