"""Dedicated background-worker process (RP-2.3).

Run with ``python -m ruhu.worker``. Consumes the unified jobs table
(``ruhu.jobs``); the API process enqueues and never executes background work.
Scale by running more worker processes — claims are ``FOR UPDATE SKIP
LOCKED``, so workers never collide, and recurring ticks are slot-deduped so N
workers still produce one tick per interval.

Handlers register in :func:`build_handler_registry`; RP-2.2 migrates each
legacy thread-based worker into a handler here.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import uuid

from sqlalchemy.orm import Session, sessionmaker

import asyncio

from .attachments import RETENTION_JOB_TYPE, AttachmentRetention
from .audit.router import AuditEventRouter
from .audit.store import SQLAlchemyAuditStore
from .capture import CAPTURE_AUDIT_JOB_TYPE, CaptureAudit
from .capture.audit import SqlAuditWriter
from .composition import resolve_gemini_api_key
from .conversation_sweep import SWEEP_JOB_TYPE, ConversationSweep
from .db import build_session_factory, resolve_database_url
from .analytics_tagging.runtime import build_intent_tags_runtime
from .analytics_tagging.webhooks import WEBHOOK_DISPATCH_JOB_TYPE, SemanticSummaryWebhookDispatcher
from .jobs import JobHandlerRegistry, JobRuntime, RecurringSchedule, SQLAlchemyJobStore
from .realtime import (
    RealtimeControlPlane,
    SQLAlchemyRealtimeEventStore,
    SQLAlchemyRealtimeIdempotencyStore,
    SQLAlchemyRealtimeOutboxStore,
    SQLAlchemyRealtimeSessionStore,
)
from .runtime_config import RuntimeSettings

logger = logging.getLogger("ruhu.worker")


def build_handler_registry(
    *,
    session_factory: sessionmaker[Session],
    settings: RuntimeSettings,
    database_url: str | None = None,
) -> tuple[JobHandlerRegistry, list[RecurringSchedule]]:
    """All job handlers and recurring schedules the worker process serves.

    RP-2.2 migration point: each legacy background worker becomes a
    ``registry.register(...)`` (plus a ``RecurringSchedule`` when periodic)
    here.
    """
    registry = JobHandlerRegistry()
    schedules: list[RecurringSchedule] = []

    # One audit router for every handler in this process.  Security-class
    # events (security./auth./admin.) write synchronously to Postgres;
    # operational events (e.g. ``credential.decrypted``) land on the async
    # queue, which has no flush loop in the worker process — they surface as
    # metrics only.  Both the OAuth refresher and browser tasks emit only
    # security-class events, so nothing they produce accumulates here.
    audit_router = AuditEventRouter(
        store=SQLAlchemyAuditStore(session_factory),
        queue=asyncio.Queue(maxsize=1000),
    )

    if settings.conversation_sweep_worker_enabled:
        sweep = ConversationSweep(
            session_factory=session_factory,
            idle_timeout_seconds=settings.conversation_sweep_idle_timeout_seconds,
            batch_size=settings.conversation_sweep_batch_size,
        )

        def _sweep_tick(job: object) -> None:
            summary = sweep.process_once()
            if summary.error:
                # Surface the failure on the job row (ticks run with
                # max_attempts=1, so this dead-letters for visibility; the
                # next slot's tick proceeds regardless).
                raise RuntimeError(summary.error)

        registry.register(SWEEP_JOB_TYPE, _sweep_tick)
        schedules.append(
            RecurringSchedule(
                job_type=SWEEP_JOB_TYPE,
                interval_seconds=settings.conversation_sweep_interval_seconds,
            )
        )

    if settings.attachments_retention_sweep_enabled:
        retention = AttachmentRetention(
            session_factory=session_factory,
            batch_size=settings.attachments_retention_sweep_batch_size,
            hard_delete_grace_seconds=settings.attachments_retention_hard_delete_grace_seconds,
        )

        def _retention_tick(job: object) -> None:
            summary = retention.process_once()
            if summary.error:
                raise RuntimeError(summary.error)

        registry.register(RETENTION_JOB_TYPE, _retention_tick)
        schedules.append(
            RecurringSchedule(
                job_type=RETENTION_JOB_TYPE,
                interval_seconds=settings.attachments_retention_sweep_interval_seconds,
            )
        )

    if settings.capture_audit_worker_enabled:
        capture_audit = CaptureAudit(
            session_factory=session_factory,
            audit_writer=SqlAuditWriter(session_factory),
            outbox_batch_size=settings.capture_audit_outbox_batch_size,
            retention_days=settings.capture_audit_retention_days,
            retention_batch_size=settings.capture_audit_retention_sweep_batch_size,
        )

        def _capture_audit_tick(job: object) -> None:
            summary = capture_audit.process_once()
            if summary.error:
                raise RuntimeError(summary.error)

        registry.register(CAPTURE_AUDIT_JOB_TYPE, _capture_audit_tick)
        schedules.append(
            RecurringSchedule(
                job_type=CAPTURE_AUDIT_JOB_TYPE,
                interval_seconds=settings.capture_audit_worker_interval_seconds,
            )
        )

    if settings.semantic_summary_webhook_worker_enabled:
        control_plane = RealtimeControlPlane(
            sessions=SQLAlchemyRealtimeSessionStore(session_factory),
            events=SQLAlchemyRealtimeEventStore(
                session_factory,
                enable_pg_notify=bool(os.environ.get("RUHU_PG_DIRECT_URL", "")),
            ),
            idempotency=SQLAlchemyRealtimeIdempotencyStore(session_factory),
            outbox=SQLAlchemyRealtimeOutboxStore(session_factory),
        )
        intent_tags_runtime = build_intent_tags_runtime(
            session_factory=session_factory,
            default_adapter_name=(
                "hosted" if settings.intent_tags_classifier_base_url else "ruhu-general"
            ),
        )
        webhook_dispatcher = SemanticSummaryWebhookDispatcher(
            control_plane=control_plane,
            webhook_service=intent_tags_runtime.webhook_service,
        )

        def _webhook_dispatch_tick(job: object) -> None:
            # Per-delivery retry lives in the dispatcher (realtime outbox);
            # an unexpected exception here dead-letters the tick for
            # visibility and the next slot proceeds regardless.
            webhook_dispatcher.run_pending(
                limit=settings.semantic_summary_webhook_batch_size,
                mode="both",
            )

        registry.register(WEBHOOK_DISPATCH_JOB_TYPE, _webhook_dispatch_tick)
        schedules.append(
            RecurringSchedule(
                job_type=WEBHOOK_DISPATCH_JOB_TYPE,
                interval_seconds=settings.semantic_summary_webhook_interval_seconds,
            )
        )

    if settings.tool_oauth_redirect_base_url:
        from .tools.cipher import FernetCipher
        from .tools.oauth import OAUTH_REFRESH_JOB_TYPE, OAuthTokenRefresher
        from .tools.oauth_providers import get_client_credentials

        # Same key ring as the API's connection store (FernetCipher.from_env);
        # without keys configured the refresher skips the encrypted dual-write,
        # matching its optional-cipher contract.
        try:
            blob_cipher = FernetCipher.from_env()
        except ValueError:
            blob_cipher = None
            logger.warning("oauth refresh: no credential cipher configured; dual-write disabled")
        refresher = OAuthTokenRefresher(
            session_factory,
            get_credentials=lambda provider: get_client_credentials(provider, settings),
            blob_cipher=blob_cipher,
            audit_router=audit_router,
        )

        def _oauth_refresh_tick(job: object) -> None:
            asyncio.run(refresher.refresh_expiring_once())

        registry.register(OAUTH_REFRESH_JOB_TYPE, _oauth_refresh_tick)
        schedules.append(
            RecurringSchedule(job_type=OAUTH_REFRESH_JOB_TYPE, interval_seconds=60.0)
        )

    if settings.browser_task_worker_enabled:
        from .attachments import build_attachment_runtime
        from .browser_tasks import (
            BROWSER_TASKS_JOB_TYPE,
            APIConnectionBrowserCredentialValidator,
            AttachmentRuntimeBrowserArtifactPublisher,
            BrowserTaskPackAccessPolicy,
            BrowserTaskService,
            SQLAlchemyBrowserTaskStore,
            build_browser_task_runtime,
            load_browser_task_pack_registry,
        )
        from .tools.cipher import FernetCipher
        from .tools.management import APIConnectionStore, CredentialCipher

        # Same composition as the API's shared connection store: AEAD key
        # ring from the environment; APIConnectionStore requires a cipher,
        # so mirror api.py's dev fallback (process-local generated key) when
        # none is configured.
        try:
            browser_blob_cipher = FernetCipher.from_env()
        except ValueError:
            from cryptography.fernet import Fernet

            logger.warning(
                "browser tasks: no credential cipher configured; using a process-local dev key"
            )
            browser_blob_cipher = FernetCipher(primary=Fernet.generate_key().decode())
        browser_connection_store = APIConnectionStore(
            session_factory,
            blob_cipher=browser_blob_cipher,
            legacy_cipher=(
                CredentialCipher(settings.tool_credentials_encryption_key)
                if settings.tool_credentials_encryption_key
                else None
            ),
            audit_router=audit_router,
        )
        browser_task_service = BrowserTaskService(
            SQLAlchemyBrowserTaskStore(session_factory),
            task_pack_registry=load_browser_task_pack_registry(settings.browser_task_pack_path),
            credential_validator=APIConnectionBrowserCredentialValidator(browser_connection_store),
            task_pack_access_policy=(
                BrowserTaskPackAccessPolicy(
                    allowed_pack_ids=set(settings.browser_task_allowed_packs)
                )
                if settings.browser_task_allowed_packs
                else None
            ),
            audit_router=audit_router,
        )
        attachment_runtime = build_attachment_runtime(
            session_factory=session_factory,
            runtime_settings=settings,
            gemini_api_key=resolve_gemini_api_key(settings),
        )
        # Non-None here because the enabled flag is checked above; raises in
        # production with local isolation — failing fast is correct.
        browser_task_runtime = build_browser_task_runtime(
            service=browser_task_service,
            runtime_settings=settings,
            connection_store=browser_connection_store,
            artifact_publisher=AttachmentRuntimeBrowserArtifactPublisher(attachment_runtime),
            attachment_service=attachment_runtime.service,
        )

        def _browser_tasks_tick(job: object) -> None:
            browser_task_runtime.sweep_once()
            browser_task_runtime.process_available_tasks_once(
                max_tasks=settings.browser_task_worker_batch_size,
            )

        registry.register(BROWSER_TASKS_JOB_TYPE, _browser_tasks_tick)
        schedules.append(
            RecurringSchedule(
                job_type=BROWSER_TASKS_JOB_TYPE,
                interval_seconds=settings.browser_task_worker_poll_interval_seconds,
            )
        )

    if settings.journey_runtime_worker_enabled and database_url is not None:
        from .journey_worker import JOURNEYS_JOB_TYPE, build_journey_runtime

        # agent_root=None: the worker never seeds demo agents — it serves
        # whatever the database already holds.
        journey_runtime = build_journey_runtime(
            database_url=database_url,
            agent_root=None,
            run_scheduler=False,
        )

        def _journeys_tick(job: object) -> None:
            if settings.journey_abandonment_sweep_enabled:
                journey_runtime.run_abandonment_sweep_cycle()
            journey_runtime.process_available_jobs_once(max_jobs=10)

        registry.register(JOURNEYS_JOB_TYPE, _journeys_tick)
        schedules.append(
            RecurringSchedule(
                job_type=JOURNEYS_JOB_TYPE,
                interval_seconds=settings.journey_runtime_poll_interval_seconds,
            )
        )

    # ── Kernel-dependent workers (RP-3.2 step 4) ──────────────────────────────
    # These need a constructed kernel/agent-registry/tool-runtime, so they
    # share ONE composition root (`build_runtime`).  The worker never seeds
    # agents (agent_seed_root=None) and pre-wires the audit router built
    # above (H2).  Live eval is NOT hosted here: it samples at trace-write
    # time inside the API process and stays there pending the per-item jobs
    # redesign.
    view_ready_enabled = settings.attachments_view_ready_worker_enabled
    sentiment_enabled = bool(
        settings.sentiment_worker_enabled
        and settings.sentiment_worker_llm_base_url
        and settings.sentiment_worker_llm_api_key
    )
    tool_integration_enabled = settings.tool_integration_worker_enabled
    if database_url is not None and (
        view_ready_enabled or sentiment_enabled or tool_integration_enabled
    ):
        from .composition import build_runtime

        rt = build_runtime(
            settings=settings,
            database_url=database_url,
            agent_seed_root=None,
            audit_router=audit_router,
        )
        # Knowledge retrieval backends used by kernel tools need their
        # startup hook; once per worker process (H3 keeps it out of
        # build_runtime).
        rt.data.knowledge_runtime.startup()

        if view_ready_enabled:
            from .attachments.view_ready_worker import (
                VIEW_READY_JOB_TYPE,
                AttachmentViewReadyWorker,
            )

            view_ready = AttachmentViewReadyWorker(
                session_factory=rt.data.session_factory,
                kernel=rt.kernel,
                agent_registry=rt.data.agent_registry,
                batch_size=settings.attachments_view_ready_worker_batch_size,
            )

            def _view_ready_tick(job: object) -> None:
                summary = view_ready.process_once()
                if summary.error:
                    raise RuntimeError(summary.error)

            registry.register(VIEW_READY_JOB_TYPE, _view_ready_tick)
            schedules.append(
                RecurringSchedule(
                    job_type=VIEW_READY_JOB_TYPE,
                    interval_seconds=settings.attachments_view_ready_worker_interval_seconds,
                )
            )

        if sentiment_enabled:
            from .sentiment_worker import SENTIMENT_JOB_TYPE, ConversationSentimentWorker

            sentiment = ConversationSentimentWorker(
                session_factory=rt.data.session_factory,
                llm_base_url=settings.sentiment_worker_llm_base_url,
                llm_api_key=settings.sentiment_worker_llm_api_key,
                model=settings.sentiment_worker_model,
                batch_size=settings.sentiment_worker_batch_size,
                max_attempts=settings.sentiment_worker_max_attempts,
                backoff_base_seconds=settings.sentiment_worker_backoff_base_seconds,
                timeout_seconds=settings.sentiment_worker_timeout_seconds,
            )

            def _sentiment_tick(job: object) -> None:
                summary = sentiment.process_once()
                if summary.error:
                    raise RuntimeError(summary.error)

            registry.register(SENTIMENT_JOB_TYPE, _sentiment_tick)
            schedules.append(
                RecurringSchedule(
                    job_type=SENTIMENT_JOB_TYPE,
                    interval_seconds=settings.sentiment_worker_interval_seconds,
                )
            )

        if tool_integration_enabled and rt.tool_runtime.integration_runtime is not None:
            from .tools.integration_worker import (
                TOOL_INTEGRATION_JOB_TYPE,
                ToolIntegrationWorkerRuntime,
            )

            # embedded_worker_enabled=False: the tick below drains jobs; the
            # class's own thread loop never spawns in the worker process.
            tool_integration = ToolIntegrationWorkerRuntime(
                tool_runtime=rt.tool_runtime,
                integration_runtime=rt.tool_runtime.integration_runtime,
                embedded_worker_enabled=False,
            )

            def _tool_integration_tick(job: object) -> None:
                tool_integration.process_available_jobs_once(
                    max_jobs=settings.tool_integration_worker_batch_size,
                )
                tool_integration.sweep_stuck_jobs_once()

            registry.register(TOOL_INTEGRATION_JOB_TYPE, _tool_integration_tick)
            schedules.append(
                RecurringSchedule(
                    job_type=TOOL_INTEGRATION_JOB_TYPE,
                    interval_seconds=settings.tool_integration_worker_poll_interval_seconds,
                )
            )

    return registry, schedules


def build_worker_runtime(
    *,
    database_url: str | None = None,
    worker_id: str | None = None,
    settings: RuntimeSettings | None = None,
    poll_interval_seconds: float = 2.0,
) -> JobRuntime:
    effective_settings = settings or RuntimeSettings.from_env()
    resolved_url = resolve_database_url(database_url=database_url or os.environ.get("RUHU_DATABASE_URL", ""))
    session_factory = build_session_factory(resolved_url)
    registry, schedules = build_handler_registry(
        session_factory=session_factory,
        settings=effective_settings,
        database_url=resolved_url,
    )
    return JobRuntime(
        SQLAlchemyJobStore(session_factory),
        registry,
        worker_id=worker_id or f"worker-{uuid.uuid4().hex[:8]}",
        poll_interval_seconds=poll_interval_seconds,
        schedules=schedules,
    )


def main() -> None:
    logging.basicConfig(level=os.environ.get("RUHU_WORKER_LOG_LEVEL", "INFO"))
    stop_event = threading.Event()

    def _drain(signum: int, _frame: object) -> None:
        logger.info("received signal %s; draining current job and stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _drain)
    signal.signal(signal.SIGINT, _drain)

    runtime = build_worker_runtime()
    runtime.run_forever(stop_event)


if __name__ == "__main__":
    main()
