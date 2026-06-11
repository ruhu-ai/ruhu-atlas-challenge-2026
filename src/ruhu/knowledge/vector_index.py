from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5
import logging
import re
import time

from .embeddings import cosine_similarity
from .models import (
    IndexedKnowledgeChunk,
    KnowledgeSearchHit,
    KnowledgeVectorIndexDiagnostics,
    utc_now,
)

try:
    import weaviate
    from weaviate.classes.config import Configure, DataType, Property
    from weaviate.classes.query import Filter, MetadataQuery

    WEAVIATE_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    weaviate = None
    Configure = DataType = Property = Filter = MetadataQuery = None
    WEAVIATE_AVAILABLE = False

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class KnowledgeVectorIndex(Protocol):
    def is_available(self) -> bool: ...

    def close(self) -> None: ...

    def diagnostics(self) -> KnowledgeVectorIndexDiagnostics | None: ...

    def upsert_chunks(self, items: Sequence[IndexedKnowledgeChunk]) -> dict[str, str | None]: ...

    def delete_document(self, *, organization_id: str, document_id: str) -> int: ...

    def vector_search(
        self,
        *,
        organization_id: str,
        model_key: str,
        query_vector: Sequence[float],
        limit: int = 5,
        document_ids: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchHit]: ...

    def hybrid_search(
        self,
        *,
        organization_id: str,
        model_key: str,
        query: str,
        query_vector: Sequence[float],
        limit: int = 5,
        alpha: float = 0.7,
        document_ids: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchHit]: ...


def _snippet(text: str, *, max_chars: int = 320) -> str:
    compact = " ".join(text.split()).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _keyword_score(query: str, search_text: str) -> float:
    query_tokens = set(_TOKEN_RE.findall(query.lower()))
    if not query_tokens:
        return 0.0
    search_tokens = set(_TOKEN_RE.findall(search_text.lower()))
    overlap = len(query_tokens & search_tokens)
    if not overlap:
        return 0.0
    return overlap / max(len(query_tokens), 1)


class KnowledgeVectorIndexError(RuntimeError):
    pass


@dataclass(slots=True)
class InMemoryKnowledgeVectorIndex:
    _items: dict[tuple[str, str, str], IndexedKnowledgeChunk] = field(default_factory=dict)

    def is_available(self) -> bool:
        return True

    def close(self) -> None:
        return None

    def diagnostics(self) -> KnowledgeVectorIndexDiagnostics | None:
        return KnowledgeVectorIndexDiagnostics(
            index_name=type(self).__name__,
            endpoint="memory://knowledge-index",
            collection_name="in-memory",
            last_successful_write_at=None,
            last_successful_read_at=None,
        )

    def upsert_chunks(self, items: Sequence[IndexedKnowledgeChunk]) -> dict[str, str | None]:
        refs: dict[str, str | None] = {}
        for item in items:
            self._items[(item.organization_id, item.model_key, item.chunk_id)] = item.model_copy(deep=True)
            refs[item.chunk_id] = item.chunk_id
        return refs

    def delete_document(self, *, organization_id: str, document_id: str) -> int:
        keys = [
            key
            for key, item in self._items.items()
            if item.organization_id == organization_id and item.document_id == document_id
        ]
        for key in keys:
            self._items.pop(key, None)
        return len(keys)

    def vector_search(
        self,
        *,
        organization_id: str,
        model_key: str,
        query_vector: Sequence[float],
        limit: int = 5,
        document_ids: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchHit]:
        document_scope = None if document_ids is None else set(document_ids)
        scored: list[KnowledgeSearchHit] = []
        for item in self._items.values():
            if item.organization_id != organization_id or item.model_key != model_key:
                continue
            if document_scope is not None and item.document_id not in document_scope:
                continue
            score = cosine_similarity(query_vector, item.vector)
            if score <= 0:
                continue
            scored.append(
                KnowledgeSearchHit(
                    document_id=item.document_id,
                    title=item.title,
                    summary=item.summary,
                    category=item.category,
                    tags=list(item.tags),
                    chunk_id=item.chunk_id,
                    snippet=_snippet(item.content),
                    score=round(score, 4),
                    retrieval_mode="semantic",
                    semantic_score=round(score, 4),
                    index_score=round(score, 4),
                )
            )
        scored.sort(key=lambda hit: (hit.score, hit.document_id), reverse=True)
        return scored[:limit]

    def hybrid_search(
        self,
        *,
        organization_id: str,
        model_key: str,
        query: str,
        query_vector: Sequence[float],
        limit: int = 5,
        alpha: float = 0.7,
        document_ids: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchHit]:
        document_scope = None if document_ids is None else set(document_ids)
        scored: list[KnowledgeSearchHit] = []
        for item in self._items.values():
            if item.organization_id != organization_id or item.model_key != model_key:
                continue
            if document_scope is not None and item.document_id not in document_scope:
                continue
            semantic = cosine_similarity(query_vector, item.vector)
            lexical = _keyword_score(query, item.search_text)
            combined = (alpha * semantic) + ((1.0 - alpha) * lexical)
            if combined <= 0:
                continue
            scored.append(
                KnowledgeSearchHit(
                    document_id=item.document_id,
                    title=item.title,
                    summary=item.summary,
                    category=item.category,
                    tags=list(item.tags),
                    chunk_id=item.chunk_id,
                    snippet=_snippet(item.content),
                    score=round(combined, 4),
                    retrieval_mode="hybrid",
                    lexical_score=round(lexical, 4),
                    semantic_score=round(semantic, 4),
                    index_score=round(combined, 4),
                )
            )
        scored.sort(key=lambda hit: (hit.score, hit.document_id), reverse=True)
        return scored[:limit]


class WeaviateKnowledgeVectorIndex:
    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 8080,
        grpc_port: int = 50051,
        collection_name: str = "KnowledgeChunk",
        max_retries: int = 2,
        base_backoff_seconds: float = 0.1,
        max_backoff_seconds: float = 1.0,
        sleep_fn=time.sleep,
    ) -> None:
        self._host = host
        self._port = port
        self._grpc_port = grpc_port
        self._collection_name = collection_name
        self._max_retries = max(0, max_retries)
        self._base_backoff_seconds = max(0.0, base_backoff_seconds)
        self._max_backoff_seconds = max(self._base_backoff_seconds, max_backoff_seconds)
        self._sleep_fn = sleep_fn
        self._client: Any | None = None
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None
        self._last_operation: str | None = None
        self._last_successful_write_at: datetime | None = None
        self._last_successful_read_at: datetime | None = None

    def is_available(self) -> bool:
        if self._client is None:
            if not WEAVIATE_AVAILABLE:
                return False
            self._connect()
        return self._client is not None and self._client.is_ready()

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        finally:
            self._client = None

    def diagnostics(self) -> KnowledgeVectorIndexDiagnostics | None:
        return KnowledgeVectorIndexDiagnostics(
            index_name=type(self).__name__,
            endpoint=f"http://{self._host}:{self._port}",
            collection_name=self._collection_name,
            last_operation=self._last_operation,
            last_error=self._last_error,
            last_error_at=self._last_error_at,
            last_successful_write_at=self._last_successful_write_at,
            last_successful_read_at=self._last_successful_read_at,
        )

    def upsert_chunks(self, items: Sequence[IndexedKnowledgeChunk]) -> dict[str, str | None]:
        if not self.is_available() or not items:
            return {item.chunk_id: None for item in items}
        self._ensure_schema()
        refs: dict[str, str | None] = {}
        by_tenant: dict[str, list[IndexedKnowledgeChunk]] = {}
        for item in items:
            by_tenant.setdefault(item.organization_id, []).append(item)
        collection = self._client.collections.get(self._collection_name)
        for organization_id, tenant_items in by_tenant.items():
            tenant_collection = collection.with_tenant(organization_id)
            for item in tenant_items:
                object_uuid = str(uuid5(NAMESPACE_URL, f"{item.model_key}:{item.chunk_id}"))
                self._run_with_retry(
                    operation="upsert_chunks",
                    context={
                        "organization_id": organization_id,
                        "document_id": item.document_id,
                        "chunk_id": item.chunk_id,
                        "model_key": item.model_key,
                    },
                    fn=lambda tenant_collection=tenant_collection, item=item, object_uuid=object_uuid: tenant_collection.data.insert(
                        uuid=object_uuid,
                        properties={
                            "chunkId": item.chunk_id,
                            "documentId": item.document_id,
                            "modelKey": item.model_key,
                            "title": item.title,
                            "summary": item.summary or "",
                            "category": item.category or "",
                            "tags": list(item.tags),
                            "content": item.content,
                            "searchText": item.search_text,
                        },
                        vector=list(item.vector),
                    ),
                    successful_read=False,
                )
                refs[item.chunk_id] = object_uuid
        return refs

    def delete_document(self, *, organization_id: str, document_id: str) -> int:
        if not self.is_available():
            return 0
        collection = self._client.collections.get(self._collection_name).with_tenant(organization_id)
        try:
            result = self._run_with_retry(
                operation="delete_document",
                context={"organization_id": organization_id, "document_id": document_id},
                fn=lambda: collection.data.delete_many(
                    where=Filter.by_property("documentId").equal(document_id)
                ),
                successful_read=False,
            )
        except Exception:
            return 0
        return int(getattr(result, "successful", 0) or 0)

    def vector_search(
        self,
        *,
        organization_id: str,
        model_key: str,
        query_vector: Sequence[float],
        limit: int = 5,
        document_ids: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchHit]:
        if not self.is_available():
            return []
        return self._search(
            organization_id=organization_id,
            model_key=model_key,
            query_vector=query_vector,
            query=None,
            limit=limit,
            document_ids=document_ids,
            alpha=1.0,
            mode="semantic",
        )

    def hybrid_search(
        self,
        *,
        organization_id: str,
        model_key: str,
        query: str,
        query_vector: Sequence[float],
        limit: int = 5,
        alpha: float = 0.7,
        document_ids: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchHit]:
        if not self.is_available():
            return []
        return self._search(
            organization_id=organization_id,
            model_key=model_key,
            query_vector=query_vector,
            query=query,
            limit=limit,
            document_ids=document_ids,
            alpha=alpha,
            mode="hybrid",
        )

    def _connect(self) -> None:
        if not WEAVIATE_AVAILABLE:
            self._client = None
            return
        try:
            self._client = self._run_with_retry(
                operation="connect",
                context=None,
                fn=lambda: weaviate.connect_to_local(
                    host=self._host,
                    port=self._port,
                    grpc_port=self._grpc_port,
                ),
                successful_read=False,
            )
        except Exception:
            self._client = None

    def _ensure_schema(self) -> None:
        if self._client is None:
            return
        if self._run_with_retry(
            operation="ensure_schema.exists",
            context={"collection_name": self._collection_name},
            fn=lambda: self._client.collections.exists(self._collection_name),
        ):
            return
        self._run_with_retry(
            operation="ensure_schema.create",
            context={"collection_name": self._collection_name},
            fn=lambda: self._client.collections.create(
                name=self._collection_name,
                description="Knowledge chunks indexed for AI retrieval",
                properties=[
                    Property(name="chunkId", data_type=DataType.TEXT),
                    Property(name="documentId", data_type=DataType.TEXT),
                    Property(name="modelKey", data_type=DataType.TEXT),
                    Property(name="title", data_type=DataType.TEXT),
                    Property(name="summary", data_type=DataType.TEXT),
                    Property(name="category", data_type=DataType.TEXT),
                    Property(name="tags", data_type=DataType.TEXT_ARRAY),
                    Property(name="content", data_type=DataType.TEXT),
                    Property(name="searchText", data_type=DataType.TEXT),
                ],
                vectorizer_config=Configure.Vectorizer.none(),
                multi_tenancy_config=Configure.multi_tenancy(enabled=True, auto_tenant_creation=True),
            ),
            successful_read=False,
        )

    def _search(
        self,
        *,
        organization_id: str,
        model_key: str,
        query_vector: Sequence[float],
        query: str | None,
        limit: int,
        document_ids: Sequence[str] | None,
        alpha: float,
        mode: str,
    ) -> list[KnowledgeSearchHit]:
        collection = self._client.collections.get(self._collection_name).with_tenant(organization_id)
        filter_clause = Filter.by_property("modelKey").equal(model_key)
        request_limit = max(limit * 4, 16)
        try:
            if mode == "semantic":
                response = self._run_with_retry(
                    operation="search.semantic",
                    context={"organization_id": organization_id, "model_key": model_key, "limit": request_limit},
                    fn=lambda: collection.query.near_vector(
                        near_vector=list(query_vector),
                        filters=filter_clause,
                        limit=request_limit,
                        return_metadata=MetadataQuery(distance=True),
                    ),
                )
            else:
                response = self._run_with_retry(
                    operation="search.hybrid",
                    context={
                        "organization_id": organization_id,
                        "model_key": model_key,
                        "limit": request_limit,
                        "alpha": alpha,
                    },
                    fn=lambda: collection.query.hybrid(
                        query=query or "",
                        vector=list(query_vector),
                        alpha=alpha,
                        filters=filter_clause,
                        limit=request_limit,
                        return_metadata=MetadataQuery(score=True, distance=True),
                    ),
                )
        except Exception:
            return []

        document_scope = None if document_ids is None else set(document_ids)
        hits: list[KnowledgeSearchHit] = []
        for obj in getattr(response, "objects", []):
            properties = getattr(obj, "properties", {}) or {}
            if document_scope is not None and properties.get("documentId") not in document_scope:
                continue
            distance = getattr(getattr(obj, "metadata", None), "distance", None)
            score_value = getattr(getattr(obj, "metadata", None), "score", None)
            semantic_score = 0.0 if distance is None else max(0.0, 1.0 - float(distance))
            index_score = semantic_score if score_value is None else float(score_value)
            hits.append(
                KnowledgeSearchHit(
                    document_id=str(properties.get("documentId") or ""),
                    title=str(properties.get("title") or ""),
                    summary=None if not properties.get("summary") else str(properties.get("summary")),
                    category=None if not properties.get("category") else str(properties.get("category")),
                    tags=[str(tag) for tag in properties.get("tags", [])],
                    chunk_id=str(properties.get("chunkId") or ""),
                    snippet=_snippet(str(properties.get("content") or "")),
                    score=index_score,
                    retrieval_mode="semantic" if mode == "semantic" else "hybrid",
                    semantic_score=semantic_score,
                    index_score=index_score,
                )
            )
        hits.sort(key=lambda hit: (hit.score, hit.document_id), reverse=True)
        return hits[:limit]

    def _run_with_retry(
        self,
        *,
        operation: str,
        context: dict[str, object] | None,
        fn,
        successful_read: bool = True,
    ):
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            self._last_operation = operation
            try:
                result = fn()
                self._record_success(successful_read=successful_read)
                return result
            except Exception as exc:
                last_exc = exc
                self._record_failure(operation=operation, attempt=attempt, exc=exc, context=context)
                if attempt > self._max_retries:
                    break
                delay = min(
                    self._max_backoff_seconds,
                    self._base_backoff_seconds * math.pow(2, attempt - 1),
                )
                if delay > 0:
                    self._sleep_fn(delay)
        raise KnowledgeVectorIndexError(
            self._format_failure_message(
                operation=operation,
                attempt=self._max_retries + 1,
                exc=last_exc,
                context=context,
            )
        )

    def _record_success(self, *, successful_read: bool) -> None:
        if successful_read:
            self._last_successful_read_at = utc_now()
        else:
            self._last_successful_write_at = utc_now()
        self._last_error = None
        self._last_error_at = None

    def _record_failure(
        self,
        *,
        operation: str,
        attempt: int,
        exc: Exception,
        context: dict[str, object] | None,
    ) -> None:
        message = self._format_failure_message(
            operation=operation,
            attempt=attempt,
            exc=exc,
            context=context,
        )
        self._last_error = message
        self._last_error_at = utc_now()
        logger.warning(message)

    def _format_failure_message(
        self,
        *,
        operation: str,
        attempt: int,
        exc: Exception | None,
        context: dict[str, object] | None,
    ) -> str:
        pieces = [
            f"operation={operation}",
            f"attempt={attempt}",
            f"endpoint=http://{self._host}:{self._port}",
            f"collection={self._collection_name}",
        ]
        if context:
            pieces.extend(f"{key}={value}" for key, value in sorted(context.items()))
        return f"weaviate failure ({', '.join(pieces)}): {exc}"


def run_weaviate_smoke_check(
    *,
    index: KnowledgeVectorIndex,
    organization_id: str,
    model_key: str,
    query: str,
    vector: Sequence[float],
) -> dict[str, object]:
    if not index.is_available():
        diagnostics = index.diagnostics()
        return {
            "ok": False,
            "reason": "vector index unavailable",
            "diagnostics": None if diagnostics is None else diagnostics.model_dump(mode="json"),
        }
    smoke_document_id = "knowledge_smoke_document"
    smoke_chunk = IndexedKnowledgeChunk(
        chunk_id="knowledge_smoke_chunk",
        document_id=smoke_document_id,
        organization_id=organization_id,
        model_key=model_key,
        title="Knowledge smoke test",
        summary="Synthetic smoke document used to verify vector indexing.",
        category="ops",
        tags=["smoke", "vector"],
        content=query,
        search_text=query.lower(),
        vector=list(vector),
    )
    refs = index.upsert_chunks([smoke_chunk])
    hits = index.hybrid_search(
        organization_id=organization_id,
        model_key=model_key,
        query=query,
        query_vector=vector,
        limit=1,
    )
    deleted = index.delete_document(organization_id=organization_id, document_id=smoke_document_id)
    diagnostics = index.diagnostics()
    return {
        "ok": bool(hits),
        "ref": refs.get(smoke_chunk.chunk_id),
        "hit_count": len(hits),
        "deleted_count": deleted,
        "diagnostics": None if diagnostics is None else diagnostics.model_dump(mode="json"),
    }
