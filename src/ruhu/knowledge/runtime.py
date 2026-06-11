from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
import time
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from ..runtime_config import RuntimeSettings
from .embeddings import HashingEmbeddingProvider, HostedEmbeddingProvider
from .models import KnowledgeIndexJob, KnowledgeRuntimeStatus, utc_now
from .service import KnowledgeService
from .store import SQLAlchemyKnowledgeStore
from .vector_index import InMemoryKnowledgeVectorIndex, WeaviateKnowledgeVectorIndex


@dataclass(slots=True)
class KnowledgeRuntime:
    service: KnowledgeService
    # Enterprise posture: no tenant-fallback sentinel.  Callers must pass
    # an explicit organization_id; `default_organization_id` remains as a
    # dev/single-tenant convenience for CLI tools only.
    default_organization_id: str | None = None
    seed_path: Path | None = None
    auto_seed: bool = True
    auto_reindex_on_startup: bool = True
    max_workers: int = 1
    service_factory: Callable[[], KnowledgeService] | None = None
    _lock: Lock = field(init=False, repr=False)
    _executor: ThreadPoolExecutor | None = field(init=False, repr=False, default=None)
    _jobs: dict[str, KnowledgeIndexJob] = field(init=False, repr=False, default_factory=dict)
    _job_futures: dict[str, Future[Any]] = field(init=False, repr=False, default_factory=dict)
    _last_error: str | None = field(init=False, repr=False, default=None)
    _started: bool = field(init=False, repr=False, default=False)
    _resources_closed: bool = field(init=False, repr=False, default=False)

    def __post_init__(self) -> None:
        self._lock = Lock()
        self._executor = self._build_executor()
        self._jobs: dict[str, KnowledgeIndexJob] = {}
        self._job_futures: dict[str, Future[Any]] = {}
        self._last_error: str | None = None

    def resolve_organization_id(self, organization_id: str | None) -> str:
        resolved = organization_id or self.default_organization_id
        if resolved is None:
            raise ValueError(
                "knowledge runtime requires an explicit organization_id — "
                "pass one or set RUHU_KNOWLEDGE_DEFAULT_ORGANIZATION_ID"
            )
        return resolved

    def startup(self) -> None:
        if self._started:
            return
        if self._resources_closed:
            if self.service_factory is None:
                raise RuntimeError("knowledge runtime cannot be restarted after shutdown without a service factory")
            self.service = self.service_factory()
            self._resources_closed = False
        if self._executor is None:
            self._executor = self._build_executor()
        self._started = True
        organization_id = self.default_organization_id
        if organization_id is None:
            # No dev default tenant configured → skip auto-seed/reindex.
            # Tests and production supply their own tenant at call time.
            return
        if self.auto_seed and self.seed_path is not None and self.seed_path.exists():
            seeded = self.service.list_documents(organization_id=organization_id, limit=1, offset=0)
            if not seeded:
                self.service.seed_documents(organization_id=organization_id, path=self.seed_path)
        if self.auto_reindex_on_startup:
            if self.service.list_documents(organization_id=organization_id, limit=1, offset=0):
                self.schedule_organization_reindex(organization_id=organization_id)

    def shutdown(self) -> None:
        if not self._started:
            return
        self._started = False
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if self.service_factory is not None:
            vector_index = self.service.vector_index
            if vector_index is not None:
                vector_index.close()
            self.service.close()
            self._resources_closed = True

    def schedule_document_reindex(
        self,
        *,
        organization_id: str | None = None,
        document_id: str,
        force: bool = False,
    ) -> KnowledgeIndexJob:
        effective_organization_id = self.resolve_organization_id(organization_id)
        return self._submit_job(
            KnowledgeIndexJob(
                organization_id=effective_organization_id,
                scope="document",
                document_id=document_id,
                force=force,
            ),
            lambda: self.service.index_document_embeddings(
                organization_id=effective_organization_id,
                document_id=document_id,
                force=force,
            ),
        )

    def schedule_organization_reindex(
        self,
        *,
        organization_id: str | None = None,
        force: bool = False,
    ) -> KnowledgeIndexJob:
        effective_organization_id = self.resolve_organization_id(organization_id)
        return self._submit_job(
            KnowledgeIndexJob(
                organization_id=effective_organization_id,
                scope="organization",
                force=force,
            ),
            lambda: self.service.index_organization_embeddings(
                organization_id=effective_organization_id,
                force=force,
            ),
        )

    def get_job(self, job_id: str) -> KnowledgeIndexJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else job.model_copy(deep=True)

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.05,
    ) -> KnowledgeIndexJob:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            job = self.get_job(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in {"completed", "failed"}:
                return job
            if time.monotonic() >= deadline:
                raise TimeoutError(f"knowledge job {job_id} did not finish within {timeout_seconds} seconds")
            self._sleep(poll_interval_seconds)

    def run_document_reindex(
        self,
        *,
        organization_id: str | None = None,
        document_id: str,
        force: bool = False,
        timeout_seconds: float = 300.0,
    ) -> KnowledgeIndexJob:
        job = self.schedule_document_reindex(
            organization_id=organization_id,
            document_id=document_id,
            force=force,
        )
        return self.wait_for_job(job.job_id, timeout_seconds=timeout_seconds)

    def run_organization_reindex(
        self,
        *,
        organization_id: str | None = None,
        force: bool = False,
        timeout_seconds: float = 300.0,
    ) -> KnowledgeIndexJob:
        job = self.schedule_organization_reindex(
            organization_id=organization_id,
            force=force,
        )
        return self.wait_for_job(job.job_id, timeout_seconds=timeout_seconds)

    def status(self, *, organization_id: str | None = None, recent_job_limit: int = 8) -> KnowledgeRuntimeStatus:
        effective_organization_id = self.resolve_organization_id(organization_id)
        vector_index = self.service.vector_index
        with self._lock:
            jobs = list(self._jobs.values())
            queued_jobs = sum(1 for job in jobs if job.status == "queued")
            running_jobs = sum(1 for job in jobs if job.status == "running")
            completed_jobs = sum(1 for job in jobs if job.status == "completed")
            failed_jobs = sum(1 for job in jobs if job.status == "failed")
            recent_jobs = sorted(
                (job.model_copy(deep=True) for job in jobs if job.organization_id == effective_organization_id),
                key=lambda item: (item.submitted_at, item.job_id),
                reverse=True,
            )[:recent_job_limit]
            last_error = self._last_error
        return KnowledgeRuntimeStatus(
            default_organization_id=self.default_organization_id,
            embedding_model_key=self.service.embedding_provider.model_key,
            embedding_provider=type(self.service.embedding_provider).__name__,
            vector_index=None if vector_index is None else type(vector_index).__name__,
            vector_index_available=False if vector_index is None else vector_index.is_available(),
            guardrails=self.service.guardrails,
            index_health=self.service.index_health(organization_id=effective_organization_id),
            vector_diagnostics=None if vector_index is None else vector_index.diagnostics(),
            queued_jobs=queued_jobs,
            running_jobs=running_jobs,
            completed_jobs=completed_jobs,
            failed_jobs=failed_jobs,
            last_error=last_error,
            organization=self.service.organization_stats(organization_id=effective_organization_id),
            recent_jobs=recent_jobs,
        )

    def _submit_job(self, job: KnowledgeIndexJob, work) -> KnowledgeIndexJob:
        if not self._started:
            raise RuntimeError("knowledge runtime must be started before scheduling jobs")
        executor = self._executor
        if executor is None:
            raise RuntimeError("knowledge runtime executor is not available")
        with self._lock:
            existing = self._live_job_for(job)
            if existing is not None:
                return existing.model_copy(deep=True)
            self._jobs[job.job_id] = job
        future = executor.submit(self._run_job, job.job_id, work)
        with self._lock:
            self._job_futures[job.job_id] = future
            return self._jobs[job.job_id].model_copy(deep=True)

    def _build_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=max(1, self.max_workers), thread_name_prefix="ruhu-kb")

    def _run_job(self, job_id: str, work) -> None:
        started_at = utc_now()
        with self._lock:
            job = self._jobs[job_id]
            self._jobs[job_id] = job.model_copy(update={"status": "running", "started_at": started_at})
        try:
            indexed = work()
            finished_at = utc_now()
            with self._lock:
                job = self._jobs[job_id]
                self._jobs[job_id] = job.model_copy(
                    update={
                        "status": "completed",
                        "finished_at": finished_at,
                        "indexed_embeddings": len(indexed),
                        "error": None,
                    }
                )
        except Exception as exc:
            finished_at = utc_now()
            with self._lock:
                job = self._jobs[job_id]
                self._jobs[job_id] = job.model_copy(
                    update={
                        "status": "failed",
                        "finished_at": finished_at,
                        "error": str(exc),
                    }
                )
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._job_futures.pop(job_id, None)

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _live_job_for(self, candidate: KnowledgeIndexJob) -> KnowledgeIndexJob | None:
        for job in self._jobs.values():
            if job.status not in {"queued", "running"}:
                continue
            if (
                job.organization_id == candidate.organization_id
                and job.scope == candidate.scope
                and job.document_id == candidate.document_id
                and job.force == candidate.force
            ):
                return job
        return None


def build_knowledge_runtime(
    *,
    session_factory: sessionmaker[Session],
    runtime_settings: RuntimeSettings,
    default_seed_path: Path | None = None,
) -> KnowledgeRuntime:
    def _build_service() -> KnowledgeService:
        return KnowledgeService(
            SQLAlchemyKnowledgeStore(session_factory),
            embedding_provider=_build_embedding_provider(runtime_settings),
            vector_index=_build_vector_index(runtime_settings),
            max_file_bytes=runtime_settings.knowledge_max_file_bytes,
            max_chunks_per_document=runtime_settings.knowledge_max_chunks_per_document,
            chunk_max_words=runtime_settings.knowledge_chunk_max_words,
            chunk_overlap_words=runtime_settings.knowledge_chunk_overlap_words,
        )

    seed_path = runtime_settings.knowledge_seed_path or default_seed_path
    return KnowledgeRuntime(
        service=_build_service(),
        default_organization_id=runtime_settings.knowledge_default_organization_id,
        seed_path=seed_path,
        auto_seed=runtime_settings.knowledge_auto_seed,
        auto_reindex_on_startup=runtime_settings.knowledge_auto_reindex_on_startup,
        max_workers=runtime_settings.knowledge_reindex_workers,
        service_factory=_build_service,
    )


def _build_embedding_provider(runtime_settings: RuntimeSettings):
    if runtime_settings.knowledge_embedding_base_url and runtime_settings.knowledge_embedding_model:
        return HostedEmbeddingProvider(
            base_url=runtime_settings.knowledge_embedding_base_url,
            model=runtime_settings.knowledge_embedding_model,
            api_key=runtime_settings.knowledge_embedding_api_key,
            dimensions=runtime_settings.knowledge_embedding_dimensions,
            timeout_seconds=runtime_settings.knowledge_embedding_timeout_seconds,
        )
    return HashingEmbeddingProvider()


def _build_vector_index(runtime_settings: RuntimeSettings):
    """Build the vector index.

    Returns WeaviateKnowledgeVectorIndex when Weaviate is enabled and
    configured.  Otherwise falls back to InMemoryKnowledgeVectorIndex so
    embeddings are actually stored and searchable.  Returning None here
    silently breaks indexing — embeddings get generated but never written,
    leaving documents permanently in "indexing" state.
    """
    if runtime_settings.knowledge_weaviate_enabled:
        return WeaviateKnowledgeVectorIndex(
            host=runtime_settings.knowledge_weaviate_host,
            port=runtime_settings.knowledge_weaviate_port,
            grpc_port=runtime_settings.knowledge_weaviate_grpc_port,
            collection_name=runtime_settings.knowledge_weaviate_collection,
            max_retries=runtime_settings.knowledge_weaviate_retry_attempts,
            base_backoff_seconds=runtime_settings.knowledge_weaviate_backoff_base_seconds,
            max_backoff_seconds=runtime_settings.knowledge_weaviate_backoff_max_seconds,
        )
    return InMemoryKnowledgeVectorIndex()
