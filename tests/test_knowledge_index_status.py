"""Tests for KnowledgeService.compute_document_index_status.

Three states only:
  - ready:    all chunks indexed
  - indexing: work in progress or partial coverage
  - error:    not searchable (no embeddings or at least one failed)
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from ruhu.knowledge import (
    InMemoryKnowledgeStore,
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeService,
)


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _make_service() -> tuple[KnowledgeService, InMemoryKnowledgeStore]:
    store = InMemoryKnowledgeStore()
    service = KnowledgeService(store)
    return service, store


def _save_doc_with_chunks(store: InMemoryKnowledgeStore, *, doc_id: str = "kdoc_1") -> KnowledgeDocument:
    doc = KnowledgeDocument(
        document_id=doc_id,
        organization_id="org_test",
        title="Test Doc",
        content="Chunk one content. Chunk two content.",
        status="published",
    )
    store.save_document(doc)
    chunks = [
        KnowledgeChunk(
            chunk_id="c1",
            document_id=doc_id,
            organization_id="org_test",
            position=0,
            content="Chunk one content.",
            search_text="chunk one content",
            token_count=3,
        ),
        KnowledgeChunk(
            chunk_id="c2",
            document_id=doc_id,
            organization_id="org_test",
            position=1,
            content="Chunk two content.",
            search_text="chunk two content",
            token_count=3,
        ),
    ]
    store.replace_chunks(doc_id, chunks)
    return doc


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class TestIndexStatusComputation:
    def test_no_chunks_returns_error(self):
        service, store = _make_service()
        doc = KnowledgeDocument(
            document_id="kdoc_empty",
            organization_id="org_test",
            title="Empty",
            content="",
            status="published",
        )
        store.save_document(doc)
        result = service.compute_document_index_status(document=doc)
        assert result.index_status == "error"

    def test_chunks_without_embeddings_returns_error(self):
        service, store = _make_service()
        doc = _save_doc_with_chunks(store)
        result = service.compute_document_index_status(document=doc)
        assert result.index_status == "error"

    def test_all_embeddings_indexed_returns_ready(self):
        service, store = _make_service()
        doc = _save_doc_with_chunks(store)
        model_key = service._embedding_provider.model_key
        for chunk in store.list_chunks(doc.document_id):
            store.save_chunk_embedding(
                KnowledgeChunkEmbedding(
                    chunk_id=chunk.chunk_id,
                    document_id=doc.document_id,
                    organization_id="org_test",
                    model_key=model_key,
                    dimensions=3,
                    vector=[0.1, 0.2, 0.3],
                    content_hash=_hash(chunk.content),
                    sync_status="indexed",
                    indexed_at=_utc(),
                )
            )
        result = service.compute_document_index_status(document=doc)
        assert result.index_status == "ready"
        assert result.last_index_error is None

    def test_any_pending_returns_indexing(self):
        service, store = _make_service()
        doc = _save_doc_with_chunks(store)
        model_key = service._embedding_provider.model_key
        chunks = list(store.list_chunks(doc.document_id))
        store.save_chunk_embedding(
            KnowledgeChunkEmbedding(
                chunk_id=chunks[0].chunk_id,
                document_id=doc.document_id,
                organization_id="org_test",
                model_key=model_key,
                dimensions=3,
                vector=[0.0, 0.0, 0.0],
                content_hash=_hash(chunks[0].content),
                sync_status="pending",
            )
        )
        result = service.compute_document_index_status(document=doc)
        assert result.index_status == "indexing"

    def test_failed_returns_error_with_message(self):
        service, store = _make_service()
        doc = _save_doc_with_chunks(store)
        model_key = service._embedding_provider.model_key
        chunks = list(store.list_chunks(doc.document_id))
        store.save_chunk_embedding(
            KnowledgeChunkEmbedding(
                chunk_id=chunks[0].chunk_id,
                document_id=doc.document_id,
                organization_id="org_test",
                model_key=model_key,
                dimensions=3,
                vector=[0.0, 0.0, 0.0],
                content_hash=_hash(chunks[0].content),
                sync_status="failed",
                last_error="embedding model unavailable",
            )
        )
        result = service.compute_document_index_status(document=doc)
        assert result.index_status == "error"
        assert result.last_index_error == "embedding model unavailable"

    def test_partial_coverage_returns_indexing(self):
        service, store = _make_service()
        doc = _save_doc_with_chunks(store)
        model_key = service._embedding_provider.model_key
        chunks = list(store.list_chunks(doc.document_id))
        # Only one of two chunks has an embedding
        store.save_chunk_embedding(
            KnowledgeChunkEmbedding(
                chunk_id=chunks[0].chunk_id,
                document_id=doc.document_id,
                organization_id="org_test",
                model_key=model_key,
                dimensions=3,
                vector=[0.1, 0.2, 0.3],
                content_hash=_hash(chunks[0].content),
                sync_status="indexed",
                indexed_at=_utc(),
            )
        )
        result = service.compute_document_index_status(document=doc)
        assert result.index_status == "indexing"

    def test_result_preserves_base_document_fields(self):
        service, store = _make_service()
        doc = _save_doc_with_chunks(store)
        result = service.compute_document_index_status(document=doc)
        assert result.document_id == doc.document_id
        assert result.organization_id == doc.organization_id
        assert result.title == doc.title
        assert result.status == doc.status
        assert hasattr(result, "index_status")
        assert hasattr(result, "last_index_error")
