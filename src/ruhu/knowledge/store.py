from __future__ import annotations

from copy import deepcopy
from typing import Protocol, Sequence

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..db_models import Base
from .models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeIndexHealth,
    KnowledgeOrganizationStats,
    KnowledgeSearchCandidate,
    utc_now,
)
from .sqlalchemy_models import KnowledgeChunkEmbeddingRecord, KnowledgeChunkRecord, KnowledgeDocumentRecord


class KnowledgeStore(Protocol):
    def save_document(self, document: KnowledgeDocument) -> KnowledgeDocument: ...

    def get_document(self, document_id: str, *, organization_id: str | None = None) -> KnowledgeDocument | None: ...

    def get_document_by_source(
        self,
        *,
        organization_id: str,
        source_kind: str,
        source_ref: str,
    ) -> KnowledgeDocument | None: ...

    def list_documents(
        self,
        organization_id: str,
        *,
        status: KnowledgeDocumentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KnowledgeDocument]: ...

    def delete_document(self, document_id: str, *, organization_id: str | None = None) -> None: ...

    def replace_chunks(self, document_id: str, chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]: ...

    def list_chunks(self, document_id: str, *, organization_id: str | None = None) -> list[KnowledgeChunk]: ...

    def save_chunk_embedding(self, embedding: KnowledgeChunkEmbedding) -> KnowledgeChunkEmbedding: ...

    def get_chunk_embedding(self, chunk_id: str, model_key: str) -> KnowledgeChunkEmbedding | None: ...

    def list_chunk_embeddings_for_document(
        self,
        document_id: str,
        *,
        model_key: str | None = None,
    ) -> list[KnowledgeChunkEmbedding]: ...

    def get_organization_stats(self, organization_id: str) -> KnowledgeOrganizationStats: ...

    def get_index_health(
        self,
        organization_id: str,
        *,
        model_key: str,
        status: KnowledgeDocumentStatus | None = "published",
    ) -> KnowledgeIndexHealth: ...

    def search_chunks(
        self,
        *,
        organization_id: str,
        query_tokens: Sequence[str],
        document_ids: Sequence[str] | None = None,
        status: KnowledgeDocumentStatus | None = "published",
        limit: int = 50,
    ) -> list[KnowledgeSearchCandidate]: ...


class InMemoryKnowledgeStore:
    def __init__(self) -> None:
        self._documents: dict[str, KnowledgeDocument] = {}
        self._documents_by_source: dict[tuple[str, str, str], str] = {}
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._chunk_ids_by_document: dict[str, list[str]] = {}
        self._chunk_embeddings: dict[tuple[str, str], KnowledgeChunkEmbedding] = {}

    def save_document(self, document: KnowledgeDocument) -> KnowledgeDocument:
        stored = document.model_copy(deep=True)
        self._documents[stored.document_id] = stored
        if stored.source_ref:
            self._documents_by_source[(stored.organization_id, stored.source_kind, stored.source_ref)] = stored.document_id
        return stored.model_copy(deep=True)

    def get_document(self, document_id: str, *, organization_id: str | None = None) -> KnowledgeDocument | None:
        document = self._documents.get(document_id)
        if document is None:
            return None
        if organization_id is not None and document.organization_id != organization_id:
            return None
        return document.model_copy(deep=True)

    def get_document_by_source(
        self,
        *,
        organization_id: str,
        source_kind: str,
        source_ref: str,
    ) -> KnowledgeDocument | None:
        document_id = self._documents_by_source.get((organization_id, source_kind, source_ref))
        return None if document_id is None else self.get_document(document_id)

    def list_documents(
        self,
        organization_id: str,
        *,
        status: KnowledgeDocumentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KnowledgeDocument]:
        items = [doc for doc in self._documents.values() if doc.organization_id == organization_id]
        if status is not None:
            items = [doc for doc in items if doc.status == status]
        items.sort(key=lambda item: (item.updated_at, item.document_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[offset : offset + limit]]

    def delete_document(self, document_id: str, *, organization_id: str | None = None) -> None:
        document = self._documents.get(document_id)
        if document is None:
            return
        if organization_id is not None and document.organization_id != organization_id:
            return
        if document.source_ref:
            self._documents_by_source.pop((document.organization_id, document.source_kind, document.source_ref), None)
        self._documents.pop(document_id, None)
        for chunk_id in self._chunk_ids_by_document.pop(document_id, []):
            self._chunks.pop(chunk_id, None)
            self._delete_embeddings_for_chunk(chunk_id)

    def replace_chunks(self, document_id: str, chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]:
        for chunk_id in self._chunk_ids_by_document.pop(document_id, []):
            self._chunks.pop(chunk_id, None)
            self._delete_embeddings_for_chunk(chunk_id)
        stored_ids: list[str] = []
        stored_chunks: list[KnowledgeChunk] = []
        for chunk in chunks:
            stored = chunk.model_copy(deep=True)
            self._chunks[stored.chunk_id] = stored
            stored_ids.append(stored.chunk_id)
            stored_chunks.append(stored.model_copy(deep=True))
        self._chunk_ids_by_document[document_id] = stored_ids
        return stored_chunks

    def list_chunks(self, document_id: str, *, organization_id: str | None = None) -> list[KnowledgeChunk]:
        chunk_ids = self._chunk_ids_by_document.get(document_id, [])
        result: list[KnowledgeChunk] = []
        for chunk_id in chunk_ids:
            chunk = self._chunks[chunk_id]
            if organization_id is not None and chunk.organization_id != organization_id:
                continue
            result.append(chunk.model_copy(deep=True))
        result.sort(key=lambda item: (item.position, item.chunk_id))
        return result

    def save_chunk_embedding(self, embedding: KnowledgeChunkEmbedding) -> KnowledgeChunkEmbedding:
        stored = embedding.model_copy(deep=True)
        self._chunk_embeddings[(stored.chunk_id, stored.model_key)] = stored
        return stored.model_copy(deep=True)

    def get_chunk_embedding(self, chunk_id: str, model_key: str) -> KnowledgeChunkEmbedding | None:
        item = self._chunk_embeddings.get((chunk_id, model_key))
        return None if item is None else item.model_copy(deep=True)

    def list_chunk_embeddings_for_document(
        self,
        document_id: str,
        *,
        model_key: str | None = None,
    ) -> list[KnowledgeChunkEmbedding]:
        result = [
            item.model_copy(deep=True)
            for item in self._chunk_embeddings.values()
            if item.document_id == document_id and (model_key is None or item.model_key == model_key)
        ]
        result.sort(key=lambda item: (item.created_at, item.chunk_id, item.model_key))
        return result

    def get_organization_stats(self, organization_id: str) -> KnowledgeOrganizationStats:
        documents = [doc for doc in self._documents.values() if doc.organization_id == organization_id]
        chunk_ids = {
            chunk.chunk_id
            for chunk in self._chunks.values()
            if chunk.organization_id == organization_id
        }
        embeddings = [
            item
            for item in self._chunk_embeddings.values()
            if item.organization_id == organization_id
        ]
        return KnowledgeOrganizationStats(
            organization_id=organization_id,
            document_count=len(documents),
            published_document_count=sum(1 for doc in documents if doc.status == "published"),
            chunk_count=len(chunk_ids),
            embedding_count=len(embeddings),
            indexed_embedding_count=sum(1 for item in embeddings if item.sync_status == "indexed"),
            pending_embedding_count=sum(1 for item in embeddings if item.sync_status == "pending"),
            failed_embedding_count=sum(1 for item in embeddings if item.sync_status == "failed"),
        )

    def get_index_health(
        self,
        organization_id: str,
        *,
        model_key: str,
        status: KnowledgeDocumentStatus | None = "published",
    ) -> KnowledgeIndexHealth:
        documents = [
            doc
            for doc in self._documents.values()
            if doc.organization_id == organization_id and (status is None or doc.status == status)
        ]
        document_ids = {doc.document_id for doc in documents}
        chunks = [
            chunk
            for chunk in self._chunks.values()
            if chunk.organization_id == organization_id and chunk.document_id in document_ids
        ]
        indexed_chunk_count = 0
        pending_chunk_count = 0
        failed_chunk_count = 0
        lag_timestamps = []
        last_successful_indexed_at = None
        for chunk in chunks:
            embedding = self._chunk_embeddings.get((chunk.chunk_id, model_key))
            if embedding is None:
                lag_timestamps.append(chunk.created_at)
                continue
            if embedding.sync_status == "indexed":
                indexed_chunk_count += 1
                if embedding.indexed_at is not None and (
                    last_successful_indexed_at is None or embedding.indexed_at > last_successful_indexed_at
                ):
                    last_successful_indexed_at = embedding.indexed_at
            elif embedding.sync_status == "pending":
                pending_chunk_count += 1
                lag_timestamps.append(embedding.updated_at)
            elif embedding.sync_status == "failed":
                failed_chunk_count += 1
                lag_timestamps.append(embedding.updated_at)
        chunk_count = len(chunks)
        missing_chunk_count = max(chunk_count - indexed_chunk_count - pending_chunk_count - failed_chunk_count, 0)
        lagging_chunk_count = missing_chunk_count + pending_chunk_count + failed_chunk_count
        index_lag_seconds = None
        if lag_timestamps:
            oldest = min(lag_timestamps)
            index_lag_seconds = round((utc_now() - oldest).total_seconds(), 3)
        elif chunk_count:
            index_lag_seconds = 0.0
        return KnowledgeIndexHealth(
            organization_id=organization_id,
            model_key=model_key,
            chunk_count=chunk_count,
            indexed_chunk_count=indexed_chunk_count,
            missing_chunk_count=missing_chunk_count,
            pending_chunk_count=pending_chunk_count,
            failed_chunk_count=failed_chunk_count,
            lagging_chunk_count=lagging_chunk_count,
            last_successful_indexed_at=last_successful_indexed_at,
            index_lag_seconds=index_lag_seconds,
        )

    def search_chunks(
        self,
        *,
        organization_id: str,
        query_tokens: Sequence[str],
        document_ids: Sequence[str] | None = None,
        status: KnowledgeDocumentStatus | None = "published",
        limit: int = 50,
    ) -> list[KnowledgeSearchCandidate]:
        document_scope = None if document_ids is None else set(document_ids)
        token_scope = {token.lower() for token in query_tokens if token}
        results: list[KnowledgeSearchCandidate] = []
        for document in self._documents.values():
            if document.organization_id != organization_id:
                continue
            if status is not None and document.status != status:
                continue
            if document_scope is not None and document.document_id not in document_scope:
                continue
            for chunk in self.list_chunks(document.document_id, organization_id=organization_id):
                if token_scope and not any(token in chunk.search_text for token in token_scope):
                    continue
                results.append(
                    KnowledgeSearchCandidate(
                        document_id=document.document_id,
                        organization_id=document.organization_id,
                        title=document.title,
                        summary=document.summary,
                        category=document.category,
                        tags=list(document.tags),
                        status=document.status,
                        updated_at=document.updated_at,
                        chunk_id=chunk.chunk_id,
                        position=chunk.position,
                        chunk_content=chunk.content,
                        search_text=chunk.search_text,
                    )
                )
        results.sort(key=lambda item: (item.updated_at, -item.position, item.chunk_id), reverse=True)
        return [item.model_copy(deep=True) for item in results[:limit]]

    def _delete_embeddings_for_chunk(self, chunk_id: str) -> None:
        keys = [key for key in self._chunk_embeddings if key[0] == chunk_id]
        for key in keys:
            self._chunk_embeddings.pop(key, None)


class SQLAlchemyKnowledgeStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        bind = self._session_factory.kw.get("bind")
        if bind is None:
            with self._session_factory() as session:
                bind = session.get_bind()
        Base.metadata.create_all(
            bind=bind,
            tables=[
                KnowledgeDocumentRecord.__table__,
                KnowledgeChunkRecord.__table__,
                KnowledgeChunkEmbeddingRecord.__table__,
            ],
        )

    def save_document(self, document: KnowledgeDocument) -> KnowledgeDocument:
        with self._session_factory() as session:
            record = session.get(KnowledgeDocumentRecord, document.document_id)
            if record is None:
                record = KnowledgeDocumentRecord(document_id=document.document_id)
                session.add(record)
            _apply_document(record, document)
            session.commit()
        return self.get_document(document.document_id) or document.model_copy(deep=True)

    def get_document(self, document_id: str, *, organization_id: str | None = None) -> KnowledgeDocument | None:
        statement = select(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.document_id == document_id)
        if organization_id is not None:
            statement = statement.where(KnowledgeDocumentRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
        return None if record is None else _record_to_document(record)

    def get_document_by_source(
        self,
        *,
        organization_id: str,
        source_kind: str,
        source_ref: str,
    ) -> KnowledgeDocument | None:
        statement = select(KnowledgeDocumentRecord).where(
            KnowledgeDocumentRecord.organization_id == organization_id,
            KnowledgeDocumentRecord.source_kind == source_kind,
            KnowledgeDocumentRecord.source_ref == source_ref,
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
        return None if record is None else _record_to_document(record)

    def list_documents(
        self,
        organization_id: str,
        *,
        status: KnowledgeDocumentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KnowledgeDocument]:
        statement = select(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.organization_id == organization_id)
        if status is not None:
            statement = statement.where(KnowledgeDocumentRecord.status == status)
        statement = statement.order_by(KnowledgeDocumentRecord.updated_at.desc(), KnowledgeDocumentRecord.document_id.desc())
        statement = statement.limit(limit).offset(offset)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_document(record) for record in records]

    def delete_document(self, document_id: str, *, organization_id: str | None = None) -> None:
        with self._session_factory.begin() as session:
            statement = delete(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.document_id == document_id)
            if organization_id is not None:
                statement = statement.where(KnowledgeDocumentRecord.organization_id == organization_id)
            session.execute(delete(KnowledgeChunkRecord).where(KnowledgeChunkRecord.document_id == document_id))
            session.execute(statement)

    def replace_chunks(self, document_id: str, chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]:
        with self._session_factory.begin() as session:
            session.execute(
                delete(KnowledgeChunkEmbeddingRecord).where(
                    KnowledgeChunkEmbeddingRecord.document_id == document_id
                )
            )
            session.execute(delete(KnowledgeChunkRecord).where(KnowledgeChunkRecord.document_id == document_id))
            for chunk in chunks:
                record = KnowledgeChunkRecord(chunk_id=chunk.chunk_id)
                _apply_chunk(record, chunk)
                session.add(record)
        return self.list_chunks(document_id)

    def list_chunks(self, document_id: str, *, organization_id: str | None = None) -> list[KnowledgeChunk]:
        statement = select(KnowledgeChunkRecord).where(KnowledgeChunkRecord.document_id == document_id)
        if organization_id is not None:
            statement = statement.where(KnowledgeChunkRecord.organization_id == organization_id)
        statement = statement.order_by(KnowledgeChunkRecord.position.asc(), KnowledgeChunkRecord.chunk_id.asc())
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_chunk(record) for record in records]

    def save_chunk_embedding(self, embedding: KnowledgeChunkEmbedding) -> KnowledgeChunkEmbedding:
        with self._session_factory() as session:
            record = session.get(
                KnowledgeChunkEmbeddingRecord,
                {"chunk_id": embedding.chunk_id, "model_key": embedding.model_key},
            )
            if record is None:
                record = KnowledgeChunkEmbeddingRecord(chunk_id=embedding.chunk_id, model_key=embedding.model_key)
                session.add(record)
            _apply_chunk_embedding(record, embedding)
            session.commit()
        return self.get_chunk_embedding(embedding.chunk_id, embedding.model_key) or embedding.model_copy(deep=True)

    def get_chunk_embedding(self, chunk_id: str, model_key: str) -> KnowledgeChunkEmbedding | None:
        with self._session_factory() as session:
            record = session.get(
                KnowledgeChunkEmbeddingRecord,
                {"chunk_id": chunk_id, "model_key": model_key},
            )
        return None if record is None else _record_to_chunk_embedding(record)

    def list_chunk_embeddings_for_document(
        self,
        document_id: str,
        *,
        model_key: str | None = None,
    ) -> list[KnowledgeChunkEmbedding]:
        statement = select(KnowledgeChunkEmbeddingRecord).where(
            KnowledgeChunkEmbeddingRecord.document_id == document_id
        )
        if model_key is not None:
            statement = statement.where(KnowledgeChunkEmbeddingRecord.model_key == model_key)
        statement = statement.order_by(
            KnowledgeChunkEmbeddingRecord.created_at.asc(),
            KnowledgeChunkEmbeddingRecord.chunk_id.asc(),
        )
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_chunk_embedding(record) for record in records]

    def get_organization_stats(self, organization_id: str) -> KnowledgeOrganizationStats:
        with self._session_factory() as session:
            document_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocumentRecord)
                    .where(KnowledgeDocumentRecord.organization_id == organization_id)
                )
                or 0
            )
            published_document_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocumentRecord)
                    .where(
                        KnowledgeDocumentRecord.organization_id == organization_id,
                        KnowledgeDocumentRecord.status == "published",
                    )
                )
                or 0
            )
            chunk_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunkRecord)
                    .where(KnowledgeChunkRecord.organization_id == organization_id)
                )
                or 0
            )
            embedding_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunkEmbeddingRecord)
                    .where(KnowledgeChunkEmbeddingRecord.organization_id == organization_id)
                )
                or 0
            )
            grouped_status = session.execute(
                select(
                    KnowledgeChunkEmbeddingRecord.sync_status,
                    func.count(),
                )
                .where(KnowledgeChunkEmbeddingRecord.organization_id == organization_id)
                .group_by(KnowledgeChunkEmbeddingRecord.sync_status)
            ).all()
        status_counts = {str(status): int(count) for status, count in grouped_status}
        return KnowledgeOrganizationStats(
            organization_id=organization_id,
            document_count=document_count,
            published_document_count=published_document_count,
            chunk_count=chunk_count,
            embedding_count=embedding_count,
            indexed_embedding_count=status_counts.get("indexed", 0),
            pending_embedding_count=status_counts.get("pending", 0),
            failed_embedding_count=status_counts.get("failed", 0),
        )

    def get_index_health(
        self,
        organization_id: str,
        *,
        model_key: str,
        status: KnowledgeDocumentStatus | None = "published",
    ) -> KnowledgeIndexHealth:
        document_filters = [KnowledgeDocumentRecord.organization_id == organization_id]
        if status is not None:
            document_filters.append(KnowledgeDocumentRecord.status == status)
        chunk_embedding_join = and_(
            KnowledgeChunkEmbeddingRecord.chunk_id == KnowledgeChunkRecord.chunk_id,
            KnowledgeChunkEmbeddingRecord.model_key == model_key,
        )
        with self._session_factory() as session:
            chunk_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunkRecord)
                    .join(
                        KnowledgeDocumentRecord,
                        KnowledgeDocumentRecord.document_id == KnowledgeChunkRecord.document_id,
                    )
                    .where(*document_filters)
                )
                or 0
            )
            grouped_status = session.execute(
                select(
                    KnowledgeChunkEmbeddingRecord.sync_status,
                    func.count(),
                )
                .select_from(KnowledgeChunkEmbeddingRecord)
                .join(
                    KnowledgeDocumentRecord,
                    KnowledgeDocumentRecord.document_id == KnowledgeChunkEmbeddingRecord.document_id,
                )
                .where(*document_filters, KnowledgeChunkEmbeddingRecord.model_key == model_key)
                .group_by(KnowledgeChunkEmbeddingRecord.sync_status)
            ).all()
            last_successful_indexed_at = session.scalar(
                select(func.max(KnowledgeChunkEmbeddingRecord.indexed_at))
                .select_from(KnowledgeChunkEmbeddingRecord)
                .join(
                    KnowledgeDocumentRecord,
                    KnowledgeDocumentRecord.document_id == KnowledgeChunkEmbeddingRecord.document_id,
                )
                .where(
                    *document_filters,
                    KnowledgeChunkEmbeddingRecord.model_key == model_key,
                    KnowledgeChunkEmbeddingRecord.sync_status == "indexed",
                )
            )
            oldest_missing_chunk_at = session.scalar(
                select(func.min(KnowledgeChunkRecord.created_at))
                .select_from(KnowledgeChunkRecord)
                .join(
                    KnowledgeDocumentRecord,
                    KnowledgeDocumentRecord.document_id == KnowledgeChunkRecord.document_id,
                )
                .outerjoin(KnowledgeChunkEmbeddingRecord, chunk_embedding_join)
                .where(*document_filters, KnowledgeChunkEmbeddingRecord.chunk_id.is_(None))
            )
            oldest_pending_or_failed_at = session.scalar(
                select(func.min(KnowledgeChunkEmbeddingRecord.updated_at))
                .select_from(KnowledgeChunkEmbeddingRecord)
                .join(
                    KnowledgeDocumentRecord,
                    KnowledgeDocumentRecord.document_id == KnowledgeChunkEmbeddingRecord.document_id,
                )
                .where(
                    *document_filters,
                    KnowledgeChunkEmbeddingRecord.model_key == model_key,
                    KnowledgeChunkEmbeddingRecord.sync_status.in_(["pending", "failed"]),
                )
            )
        status_counts = {str(sync_status): int(count) for sync_status, count in grouped_status}
        indexed_chunk_count = status_counts.get("indexed", 0)
        pending_chunk_count = status_counts.get("pending", 0)
        failed_chunk_count = status_counts.get("failed", 0)
        missing_chunk_count = max(chunk_count - indexed_chunk_count - pending_chunk_count - failed_chunk_count, 0)
        lagging_chunk_count = missing_chunk_count + pending_chunk_count + failed_chunk_count
        lag_sources = [value for value in [oldest_missing_chunk_at, oldest_pending_or_failed_at] if value is not None]
        index_lag_seconds = None
        if lag_sources:
            index_lag_seconds = round((utc_now() - min(lag_sources)).total_seconds(), 3)
        elif chunk_count:
            index_lag_seconds = 0.0
        return KnowledgeIndexHealth(
            organization_id=organization_id,
            model_key=model_key,
            chunk_count=chunk_count,
            indexed_chunk_count=indexed_chunk_count,
            missing_chunk_count=missing_chunk_count,
            pending_chunk_count=pending_chunk_count,
            failed_chunk_count=failed_chunk_count,
            lagging_chunk_count=lagging_chunk_count,
            last_successful_indexed_at=last_successful_indexed_at,
            index_lag_seconds=index_lag_seconds,
        )

    def search_chunks(
        self,
        *,
        organization_id: str,
        query_tokens: Sequence[str],
        document_ids: Sequence[str] | None = None,
        status: KnowledgeDocumentStatus | None = "published",
        limit: int = 50,
    ) -> list[KnowledgeSearchCandidate]:
        statement = (
            select(KnowledgeChunkRecord, KnowledgeDocumentRecord)
            .join(KnowledgeDocumentRecord, KnowledgeDocumentRecord.document_id == KnowledgeChunkRecord.document_id)
            .where(KnowledgeDocumentRecord.organization_id == organization_id)
        )
        if status is not None:
            statement = statement.where(KnowledgeDocumentRecord.status == status)
        if document_ids is not None:
            document_id_list = list(document_ids)
            if not document_id_list:
                return []
            statement = statement.where(KnowledgeDocumentRecord.document_id.in_(document_id_list))
        normalized_tokens = [token.lower() for token in query_tokens if token]
        if normalized_tokens:
            statement = statement.where(
                or_(*[KnowledgeChunkRecord.search_text.ilike(f"%{token}%") for token in normalized_tokens[:8]])
            )
        statement = statement.order_by(KnowledgeDocumentRecord.updated_at.desc(), KnowledgeChunkRecord.position.asc())
        statement = statement.limit(limit)
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return [
            KnowledgeSearchCandidate(
                document_id=document.document_id,
                organization_id=document.organization_id,
                title=document.title,
                summary=document.summary,
                category=document.category,
                tags=list(document.tags_json or []),
                status=document.status,  # type: ignore[arg-type]
                updated_at=document.updated_at,
                chunk_id=chunk.chunk_id,
                position=chunk.position,
                chunk_content=chunk.content,
                search_text=chunk.search_text,
            )
            for chunk, document in rows
        ]


def _apply_document(record: KnowledgeDocumentRecord, document: KnowledgeDocument) -> None:
    record.organization_id = document.organization_id
    record.title = document.title
    record.content = document.content
    record.summary = document.summary
    record.category = document.category
    record.tags_json = list(document.tags)
    record.status = document.status
    record.source_kind = document.source_kind
    record.source_ref = document.source_ref
    record.source_url = document.source_url
    record.media_type = document.media_type
    record.metadata_json = deepcopy(document.metadata)
    record.published_at = document.published_at
    record.created_at = document.created_at
    record.updated_at = document.updated_at


def _record_to_document(record: KnowledgeDocumentRecord) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=record.document_id,
        organization_id=record.organization_id,
        title=record.title,
        content=record.content,
        summary=record.summary,
        category=record.category,
        tags=[str(tag) for tag in (record.tags_json or [])],
        status=record.status,  # type: ignore[arg-type]
        source_kind=record.source_kind,  # type: ignore[arg-type]
        source_ref=record.source_ref,
        source_url=record.source_url,
        media_type=record.media_type,
        metadata=deepcopy(record.metadata_json or {}),
        published_at=record.published_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _apply_chunk(record: KnowledgeChunkRecord, chunk: KnowledgeChunk) -> None:
    record.document_id = chunk.document_id
    record.organization_id = chunk.organization_id
    record.position = chunk.position
    record.content = chunk.content
    record.search_text = chunk.search_text
    record.token_count = chunk.token_count
    record.metadata_json = deepcopy(chunk.metadata)
    record.created_at = chunk.created_at


def _record_to_chunk(record: KnowledgeChunkRecord) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=record.chunk_id,
        document_id=record.document_id,
        organization_id=record.organization_id,
        position=record.position,
        content=record.content,
        search_text=record.search_text,
        token_count=record.token_count,
        metadata=deepcopy(record.metadata_json or {}),
        created_at=record.created_at,
    )


def _apply_chunk_embedding(record: KnowledgeChunkEmbeddingRecord, embedding: KnowledgeChunkEmbedding) -> None:
    record.document_id = embedding.document_id
    record.organization_id = embedding.organization_id
    record.dimensions = embedding.dimensions
    record.vector_json = list(embedding.vector)
    record.content_hash = embedding.content_hash
    record.sync_status = embedding.sync_status
    record.index_ref = embedding.index_ref
    record.indexed_at = embedding.indexed_at
    record.last_error = embedding.last_error
    record.created_at = embedding.created_at
    record.updated_at = embedding.updated_at


def _record_to_chunk_embedding(record: KnowledgeChunkEmbeddingRecord) -> KnowledgeChunkEmbedding:
    return KnowledgeChunkEmbedding(
        chunk_id=record.chunk_id,
        document_id=record.document_id,
        organization_id=record.organization_id,
        model_key=record.model_key,
        dimensions=record.dimensions,
        vector=[float(value) for value in (record.vector_json or [])],
        content_hash=record.content_hash,
        sync_status=record.sync_status,  # type: ignore[arg-type]
        index_ref=record.index_ref,
        indexed_at=record.indexed_at,
        last_error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
