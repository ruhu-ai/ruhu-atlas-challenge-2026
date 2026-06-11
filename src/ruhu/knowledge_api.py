from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from .knowledge import (
    KnowledgeDocumentStatus,
    KnowledgeIngestError,
    KnowledgeRuntime,
    KnowledgeService,
)

OrganizationResolver = Callable[[Request, str | None], str]
logger = logging.getLogger(__name__)


class _CreateDocumentBody(BaseModel):
    title: str
    content: str
    summary: str | None = None
    category: str | None = None
    tags: list[str] = []
    status: KnowledgeDocumentStatus = "draft"
    source_url: str | None = None
    metadata: dict[str, object] = {}


class _UpdateDocumentBody(BaseModel):
    title: str | None = None
    content: str | None = None
    summary: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    status: KnowledgeDocumentStatus | None = None
    source_url: str | None = None
    metadata: dict[str, object] | None = None


def install_knowledge_router(
    app: FastAPI,
    *,
    runtime: KnowledgeRuntime | None,
    resolve_organization_id: OrganizationResolver,
    rate_limiter=None,
) -> None:
    router = APIRouter(
        tags=["knowledge"],
        dependencies=[rate_limiter] if rate_limiter else [],
    )
    multipart_available = _multipart_support_available()

    def _require_runtime() -> KnowledgeRuntime:
        if runtime is None:
            raise HTTPException(status_code=503, detail="knowledge runtime is not configured")
        return runtime

    def _service() -> KnowledgeService:
        return _require_runtime().service

    # ── Read ──────────────────────────────────────────────────────────────────

    @router.get("/knowledge/documents")
    def list_knowledge_documents(
        request: Request,
        organization_id: str | None = None,
        status: KnowledgeDocumentStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        include_index_status: Annotated[bool, Query()] = True,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        documents = service.list_documents(
            organization_id=effective_organization_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        if not include_index_status:
            return documents
        return [service.compute_document_index_status(document=doc) for doc in documents]

    @router.get("/knowledge/documents/{document_id}")
    def get_knowledge_document(
        document_id: str,
        request: Request,
        organization_id: str | None = None,
        include_index_status: Annotated[bool, Query()] = True,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        document = service.get_document(
            organization_id=effective_organization_id,
            document_id=document_id,
        )
        if document is None:
            raise HTTPException(status_code=404, detail="knowledge document not found")
        if not include_index_status:
            return document
        return service.compute_document_index_status(document=document)

    @router.get("/knowledge/documents/{document_id}/chunks")
    def list_knowledge_chunks(
        document_id: str,
        request: Request,
        organization_id: str | None = None,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        return service.list_chunks(
            organization_id=effective_organization_id,
            document_id=document_id,
        )

    @router.get("/knowledge/documents/{document_id}/embeddings")
    def list_knowledge_embeddings(
        document_id: str,
        request: Request,
        organization_id: str | None = None,
        model_key: str | None = None,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        return service.list_chunk_embeddings(
            organization_id=effective_organization_id,
            document_id=document_id,
            model_key=model_key,
        )

    @router.get("/knowledge/search")
    def search_knowledge(
        request: Request,
        query: str,
        organization_id: str | None = None,
        document_id: list[str] | None = Query(default=None),
        limit: Annotated[int, Query(ge=1, le=20)] = 5,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        return service.lookup(
            organization_id=effective_organization_id,
            query=query,
            document_ids=document_id,
            limit=limit,
        )

    @router.get("/knowledge/status")
    def knowledge_status(
        request: Request,
        organization_id: str | None = None,
    ):
        return _require_runtime().status(
            organization_id=resolve_organization_id(request, organization_id),
        )

    @router.get("/knowledge/stats")
    def knowledge_stats(
        request: Request,
        organization_id: str | None = None,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        return service.organization_stats(organization_id=effective_organization_id)

    # ── Write ─────────────────────────────────────────────────────────────────

    def _auto_index_document(organization_id: str, document_id: str) -> None:
        """Best-effort: schedule an async reindex for a document.  Silently
        no-ops if the runtime can't schedule (e.g., during tests)."""
        try:
            _require_runtime().schedule_document_reindex(
                organization_id=organization_id,
                document_id=document_id,
            )
        except Exception:
            logger.warning("auto_index_document failed for %s", document_id, exc_info=True)

    @router.post("/knowledge/documents", status_code=201)
    def create_knowledge_document(
        body: _CreateDocumentBody,
        request: Request,
        organization_id: str | None = None,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        try:
            document = service.upsert_document(
                organization_id=effective_organization_id,
                title=body.title,
                content=body.content,
                summary=body.summary,
                category=body.category,
                tags=body.tags,
                status=body.status,
                source_kind="manual",
                source_url=body.source_url,
                metadata=body.metadata,
            )
        except KnowledgeIngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Auto-schedule indexing so the document is searchable quickly.
        _auto_index_document(effective_organization_id, document.document_id)
        return service.compute_document_index_status(document=document)

    if multipart_available:

        @router.post("/knowledge/documents/upload", status_code=201)
        async def upload_knowledge_document(
            request: Request,
            file: UploadFile = File(...),
            organization_id: Annotated[str | None, Query()] = None,
            title: Annotated[str | None, Form()] = None,
            category: Annotated[str | None, Form()] = None,
            tags: Annotated[str | None, Form()] = None,
            source_url: Annotated[str | None, Form()] = None,
            status: Annotated[KnowledgeDocumentStatus, Form()] = "draft",
        ):
            """Upload a file (PDF, DOCX, TXT, MD, etc.) and ingest it as a knowledge document."""
            service = _service()
            effective_organization_id = resolve_organization_id(request, organization_id)
            file_bytes = await file.read()
            filename = file.filename or "upload"

            parsed_tags: list[str] = []
            if tags:
                try:
                    parsed_tags = json.loads(tags)
                except (json.JSONDecodeError, ValueError):
                    parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

            try:
                document = service.ingest_file(
                    organization_id=effective_organization_id,
                    filename=filename,
                    file_bytes=file_bytes,
                    title=title,
                    category=category,
                    tags=parsed_tags or None,
                    source_url=source_url,
                    status=status,
                )
            except KnowledgeIngestError as exc:
                status_code = 413 if exc.code == "file_too_large" else 422
                raise HTTPException(status_code=status_code, detail=str(exc)) from exc
            # Auto-schedule indexing after upload so embeddings are generated.
            _auto_index_document(effective_organization_id, document.document_id)
            return service.compute_document_index_status(document=document)

    else:
        logger.warning("knowledge document upload route is disabled because python-multipart is unavailable")

        @router.post("/knowledge/documents/upload", status_code=503)
        async def upload_knowledge_document_unavailable():
            raise HTTPException(
                status_code=503,
                detail="knowledge upload requires python-multipart to be installed",
            )

    @router.patch("/knowledge/documents/{document_id}")
    def update_knowledge_document(
        document_id: str,
        body: _UpdateDocumentBody,
        request: Request,
        organization_id: str | None = None,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        existing = service.get_document(
            organization_id=effective_organization_id,
            document_id=document_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="knowledge document not found")
        try:
            document = service.upsert_document(
                organization_id=effective_organization_id,
                document_id=document_id,
                title=body.title if body.title is not None else existing.title,
                content=body.content if body.content is not None else existing.content,
                summary=body.summary if body.summary is not None else existing.summary,
                category=body.category if body.category is not None else existing.category,
                tags=body.tags if body.tags is not None else existing.tags,
                status=body.status if body.status is not None else existing.status,
                source_kind=existing.source_kind,
                source_ref=existing.source_ref,
                source_url=body.source_url if body.source_url is not None else existing.source_url,
                media_type=existing.media_type,
                metadata={**existing.metadata, **(body.metadata or {})},
            )
        except KnowledgeIngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Re-index when content or status changed — embeddings may be stale.
        content_changed = body.content is not None and body.content != existing.content
        just_published = body.status == "published" and existing.status != "published"
        if content_changed or just_published:
            _auto_index_document(effective_organization_id, document.document_id)
        return service.compute_document_index_status(document=document)

    @router.delete("/knowledge/documents/{document_id}", status_code=204)
    def delete_knowledge_document(
        document_id: str,
        request: Request,
        organization_id: str | None = None,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        existing = service.get_document(
            organization_id=effective_organization_id,
            document_id=document_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="knowledge document not found")
        service.delete_document(
            organization_id=effective_organization_id,
            document_id=document_id,
        )

    @router.post("/knowledge/documents/{document_id}/index")
    def index_knowledge_document(
        document_id: str,
        request: Request,
        organization_id: str | None = None,
        force: bool = False,
    ):
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        existing = service.get_document(
            organization_id=effective_organization_id,
            document_id=document_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="knowledge document not found")
        embeddings = service.index_document_embeddings(
            organization_id=effective_organization_id,
            document_id=document_id,
            force=force,
        )
        indexed = sum(1 for e in embeddings if e.sync_status == "indexed")
        failed = sum(1 for e in embeddings if e.sync_status == "failed")
        return {
            "document_id": document_id,
            "total_chunks": len(embeddings),
            "indexed_chunks": indexed,
            "failed_chunks": failed,
        }

    @router.post("/knowledge/index")
    def index_all_knowledge_documents(
        request: Request,
        organization_id: str | None = None,
        status: KnowledgeDocumentStatus | None = Query(default="published"),
        force: bool = False,
    ):
        """Trigger embedding generation for all documents matching the given status."""
        service = _service()
        effective_organization_id = resolve_organization_id(request, organization_id)
        embeddings = service.index_organization_embeddings(
            organization_id=effective_organization_id,
            status=status,
            force=force,
        )
        indexed = sum(1 for e in embeddings if e.sync_status == "indexed")
        failed = sum(1 for e in embeddings if e.sync_status == "failed")
        return {
            "total_chunks": len(embeddings),
            "indexed_chunks": indexed,
            "failed_chunks": failed,
        }

    app.include_router(router)


def _multipart_support_available() -> bool:
    try:
        from python_multipart import __version__  # noqa: F401

        return True
    except ImportError:
        try:
            from multipart.multipart import parse_options_header  # type: ignore[import-untyped]

            return callable(parse_options_header)
        except ImportError:
            return False
