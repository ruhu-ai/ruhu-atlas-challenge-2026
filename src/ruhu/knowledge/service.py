from __future__ import annotations

from hashlib import sha256
import logging
import re
from pathlib import Path
from typing import Sequence

from .embeddings import EmbeddingProvider, HashingEmbeddingProvider
from .extractors import extract_knowledge_file
from .models import (
    IndexedKnowledgeChunk,
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeDocumentIndexStatus,
    KnowledgeDocumentStatus,
    KnowledgeDocumentWithIndexStatus,
    KnowledgeGuardrails,
    KnowledgeIndexHealth,
    KnowledgeLookupMode,
    KnowledgeLookupEvaluation,
    KnowledgeLookupResult,
    KnowledgeLookupSource,
    KnowledgeLookupStep,
    KnowledgeOrganizationStats,
    KnowledgeSearchCandidate,
    KnowledgeSearchHit,
    SeedKnowledgeDocument,
    utc_now,
)
from .seed import load_seed_documents
from .store import KnowledgeStore
from .vector_index import KnowledgeVectorIndex

_TOKEN_RE = re.compile(r"[a-z0-9]+")
logger = logging.getLogger(__name__)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "what",
    "when",
    "where",
    "with",
}


def normalize_tokens(text: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS]


def chunk_text(text: str, *, max_words: int = 120, overlap_words: int = 24) -> list[str]:
    words = text.split()
    if not words:
        return []
    effective_overlap = min(overlap_words, max(1, max_words // 4))
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(words):
            break
        start = max(end - effective_overlap, start + 1)
    return chunks


def summarize_text(text: str, *, max_chars: int = 220) -> str | None:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return None
    return compact if len(compact) <= max_chars else compact[: max_chars - 1].rstrip() + "…"


class KnowledgeIngestError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class KnowledgeService:
    def __init__(
        self,
        store: KnowledgeStore,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        vector_index: KnowledgeVectorIndex | None = None,
        max_file_bytes: int = 5 * 1024 * 1024,
        max_chunks_per_document: int = 128,
        chunk_max_words: int = 120,
        chunk_overlap_words: int = 24,
    ) -> None:
        self._store = store
        self._embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self._vector_index = vector_index
        self._max_file_bytes = max_file_bytes
        self._max_chunks_per_document = max_chunks_per_document
        self._chunk_max_words = chunk_max_words
        self._chunk_overlap_words = chunk_overlap_words

    @property
    def embedding_provider(self) -> EmbeddingProvider:
        return self._embedding_provider

    @property
    def vector_index(self) -> KnowledgeVectorIndex | None:
        return self._vector_index

    @property
    def guardrails(self) -> KnowledgeGuardrails:
        return KnowledgeGuardrails(
            max_file_bytes=self._max_file_bytes,
            max_chunks_per_document=self._max_chunks_per_document,
            chunk_max_words=self._chunk_max_words,
            chunk_overlap_words=self._chunk_overlap_words,
        )

    def upsert_document(
        self,
        *,
        organization_id: str,
        title: str,
        content: str,
        summary: str | None = None,
        category: str | None = None,
        tags: Sequence[str] | None = None,
        status: KnowledgeDocumentStatus = "draft",
        source_kind: str = "manual",
        source_ref: str | None = None,
        source_url: str | None = None,
        media_type: str | None = None,
        metadata: dict[str, object] | None = None,
        document_id: str | None = None,
    ) -> KnowledgeDocument:
        existing = None
        if document_id:
            existing = self._store.get_document(document_id, organization_id=organization_id)
        if existing is None and source_ref:
            existing = self._store.get_document_by_source(
                organization_id=organization_id,
                source_kind=source_kind,
                source_ref=source_ref,
            )

        now = utc_now()
        normalized_tags = [str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()]
        effective_summary = summary or summarize_text(content)
        if existing is None:
            document = KnowledgeDocument(
                organization_id=organization_id,
                title=title,
                content=content,
                summary=effective_summary,
                category=category,
                tags=normalized_tags,
                status=status,
                source_kind=source_kind,  # type: ignore[arg-type]
                source_ref=source_ref,
                source_url=source_url,
                media_type=media_type,
                metadata=dict(metadata or {}),
                created_at=now,
                updated_at=now,
                published_at=now if status == "published" else None,
            )
        else:
            published_at = existing.published_at
            if status == "published" and published_at is None:
                published_at = now
            document = existing.model_copy(
                update={
                    "title": title,
                    "content": content,
                    "summary": effective_summary,
                    "category": category,
                    "tags": normalized_tags,
                    "status": status,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "source_url": source_url,
                    "media_type": media_type,
                    "metadata": dict(metadata or {}),
                    "updated_at": now,
                    "published_at": published_at,
                }
            )

        saved = self._store.save_document(document)
        chunks = self._build_chunks(saved)
        self._store.replace_chunks(saved.document_id, chunks)
        return saved

    def ingest_file(
        self,
        *,
        organization_id: str,
        filename: str,
        file_bytes: bytes,
        title: str | None = None,
        category: str | None = None,
        tags: Sequence[str] | None = None,
        status: KnowledgeDocumentStatus = "draft",
        source_ref: str | None = None,
        source_url: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> KnowledgeDocument:
        file_size_bytes = len(file_bytes)
        if file_size_bytes > self._max_file_bytes:
            raise KnowledgeIngestError(
                (
                    f"knowledge file {filename} is too large: "
                    f"{file_size_bytes} bytes exceeds {self._max_file_bytes} bytes"
                ),
                code="file_too_large",
                details={
                    "filename": filename,
                    "file_size_bytes": file_size_bytes,
                    "max_file_bytes": self._max_file_bytes,
                },
            )
        try:
            extracted = extract_knowledge_file(filename=filename, file_bytes=file_bytes, title=title)
        except Exception as exc:
            raise KnowledgeIngestError(
                f"failed to ingest knowledge file {filename}: {exc}",
                code="extract_failed",
                details={
                    "filename": filename,
                    "file_size_bytes": file_size_bytes,
                    "error": str(exc),
                },
            ) from exc
        merged_metadata = {
            **dict(extracted.metadata),
            **dict(metadata or {}),
            "file_kind": extracted.file_kind,
            "file_size_bytes": file_size_bytes,
        }
        return self.upsert_document(
            organization_id=organization_id,
            title=extracted.title,
            content=extracted.content,
            summary=extracted.summary,
            category=category,
            tags=tags,
            status=status,
            source_kind="file",
            source_ref=source_ref or filename,
            source_url=source_url,
            media_type=extracted.media_type,
            metadata=merged_metadata,
        )

    def seed_documents(self, *, organization_id: str, path: str | Path) -> list[KnowledgeDocument]:
        seeded: list[KnowledgeDocument] = []
        for seed_document in load_seed_documents(path):
            seeded.append(self._upsert_seed_document(organization_id=organization_id, document=seed_document))
        seeded.sort(key=lambda item: item.title.lower())
        return seeded

    def get_document(self, *, organization_id: str, document_id: str) -> KnowledgeDocument | None:
        return self._store.get_document(document_id, organization_id=organization_id)

    def list_documents(
        self,
        *,
        organization_id: str,
        status: KnowledgeDocumentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KnowledgeDocument]:
        return self._store.list_documents(organization_id, status=status, limit=limit, offset=offset)

    def compute_document_index_status(
        self,
        *,
        document: KnowledgeDocument,
    ) -> KnowledgeDocumentWithIndexStatus:
        """Compute index readiness by aggregating embedding sync_status.

        Returns one of three states:
          - ready:    all chunks indexed — searchable
          - indexing: some chunks pending or partial coverage
          - error:    no embeddings at all, or at least one failed

        Content drift is caught at write time via PATCH reindex — this
        method does not compare content_hash.
        """
        try:
            chunks = self._store.list_chunks(document.document_id, organization_id=document.organization_id)
        except Exception:
            chunks = []
        try:
            embeddings = self._store.list_chunk_embeddings_for_document(
                document.document_id,
                model_key=self._embedding_provider.model_key,
            )
        except Exception:
            embeddings = []

        chunk_count = len(chunks)
        indexed = sum(1 for e in embeddings if e.sync_status == "indexed")
        pending = sum(1 for e in embeddings if e.sync_status == "pending")
        failed = sum(1 for e in embeddings if e.sync_status == "failed")
        last_error = next((e.last_error for e in embeddings if e.last_error), None)

        index_status: KnowledgeDocumentIndexStatus
        if failed > 0 or chunk_count == 0 or len(embeddings) == 0:
            index_status = "error"
        elif pending > 0 or indexed < chunk_count:
            index_status = "indexing"
        else:
            index_status = "ready"

        return KnowledgeDocumentWithIndexStatus(
            **document.model_dump(),
            index_status=index_status,
            last_index_error=last_error,
        )

    def organization_stats(self, *, organization_id: str) -> KnowledgeOrganizationStats:
        return self._store.get_organization_stats(organization_id)

    def index_health(
        self,
        *,
        organization_id: str,
        status: KnowledgeDocumentStatus | None = "published",
    ) -> KnowledgeIndexHealth:
        return self._store.get_index_health(
            organization_id,
            model_key=self._embedding_provider.model_key,
            status=status,
        )

    def list_chunks(self, *, organization_id: str, document_id: str) -> list[KnowledgeChunk]:
        return self._store.list_chunks(document_id, organization_id=organization_id)

    def list_chunk_embeddings(
        self,
        *,
        organization_id: str,
        document_id: str,
        model_key: str | None = None,
    ) -> list[KnowledgeChunkEmbedding]:
        document = self._require_document(organization_id=organization_id, document_id=document_id)
        return self._store.list_chunk_embeddings_for_document(document.document_id, model_key=model_key)

    def publish_document(self, *, organization_id: str, document_id: str) -> KnowledgeDocument:
        document = self._require_document(organization_id=organization_id, document_id=document_id)
        return self._store.save_document(
            document.model_copy(
                update={
                    "status": "published",
                    "updated_at": utc_now(),
                    "published_at": document.published_at or utc_now(),
                }
            )
        )

    def archive_document(self, *, organization_id: str, document_id: str) -> KnowledgeDocument:
        document = self._require_document(organization_id=organization_id, document_id=document_id)
        if self._vector_index is not None:
            self._vector_index.delete_document(organization_id=organization_id, document_id=document.document_id)
        return self._store.save_document(
            document.model_copy(update={"status": "archived", "updated_at": utc_now()})
        )

    def delete_document(self, *, organization_id: str, document_id: str) -> None:
        if self._vector_index is not None:
            self._vector_index.delete_document(organization_id=organization_id, document_id=document_id)
        self._store.delete_document(document_id, organization_id=organization_id)

    def index_document_embeddings(
        self,
        *,
        organization_id: str,
        document_id: str,
        force: bool = False,
    ) -> list[KnowledgeChunkEmbedding]:
        document = self._require_document(organization_id=organization_id, document_id=document_id)
        chunks = self.list_chunks(organization_id=organization_id, document_id=document_id)
        if not chunks:
            return []

        existing = {
            item.chunk_id: item
            for item in self._store.list_chunk_embeddings_for_document(
                document.document_id,
                model_key=self._embedding_provider.model_key,
            )
        }
        vectors = self._embedding_provider.embed_documents([chunk.search_text for chunk in chunks])
        saved_records: list[KnowledgeChunkEmbedding] = []
        now = utc_now()
        for chunk, vector in zip(chunks, vectors):
            content_hash = self._content_hash(chunk)
            current = existing.get(chunk.chunk_id)
            if (
                current is not None
                and not force
                and current.content_hash == content_hash
                and current.dimensions == len(vector)
                and len(current.vector) == len(vector)
            ):
                record = current.model_copy(update={"updated_at": now})
            else:
                record = KnowledgeChunkEmbedding(
                    chunk_id=chunk.chunk_id,
                    document_id=document.document_id,
                    organization_id=document.organization_id,
                    model_key=self._embedding_provider.model_key,
                    dimensions=len(vector),
                    vector=[float(value) for value in vector],
                    content_hash=content_hash,
                    sync_status="pending",
                    created_at=current.created_at if current is not None else now,
                    updated_at=now,
                )
            saved_records.append(self._store.save_chunk_embedding(record))

        if self._vector_index is None or not self._vector_index.is_available():
            return saved_records

        indexed_chunks = [
            IndexedKnowledgeChunk(
                chunk_id=chunk.chunk_id,
                document_id=document.document_id,
                organization_id=document.organization_id,
                model_key=self._embedding_provider.model_key,
                title=document.title,
                summary=document.summary,
                category=document.category,
                tags=list(document.tags),
                content=chunk.content,
                search_text=chunk.search_text,
                vector=record.vector,
            )
            for chunk, record in zip(chunks, saved_records)
        ]
        try:
            self._vector_index.delete_document(organization_id=organization_id, document_id=document_id)
            refs = self._vector_index.upsert_chunks(indexed_chunks)
            synced_at = utc_now()
            synchronized: list[KnowledgeChunkEmbedding] = []
            for record in saved_records:
                updated = record.model_copy(
                    update={
                        "sync_status": "indexed",
                        "index_ref": refs.get(record.chunk_id),
                        "indexed_at": synced_at,
                        "last_error": None,
                        "updated_at": synced_at,
                    }
                )
                synchronized.append(self._store.save_chunk_embedding(updated))
            return synchronized
        except Exception as exc:
            diagnostic = self._vector_sync_error_message(
                organization_id=organization_id,
                document_id=document_id,
                chunk_count=len(indexed_chunks),
                exc=exc,
            )
            logger.warning(
                "knowledge_vector_sync_failed",
                extra={
                    "organization_id": organization_id,
                    "document_id": document_id,
                    "chunk_count": len(indexed_chunks),
                },
            )
            failed_at = utc_now()
            failed_records: list[KnowledgeChunkEmbedding] = []
            for record in saved_records:
                failed = record.model_copy(
                    update={
                        "sync_status": "failed",
                        "last_error": diagnostic,
                        "updated_at": failed_at,
                    }
                )
                failed_records.append(self._store.save_chunk_embedding(failed))
            return failed_records

    def index_organization_embeddings(
        self,
        *,
        organization_id: str,
        status: KnowledgeDocumentStatus | None = "published",
        force: bool = False,
        limit: int = 100,
    ) -> list[KnowledgeChunkEmbedding]:
        documents = self.list_documents(
            organization_id=organization_id,
            status=status,
            limit=limit,
            offset=0,
        )
        indexed: list[KnowledgeChunkEmbedding] = []
        for document in documents:
            indexed.extend(
                self.index_document_embeddings(
                    organization_id=organization_id,
                    document_id=document.document_id,
                    force=force,
                )
            )
        return indexed

    def search(
        self,
        *,
        organization_id: str,
        query: str,
        document_ids: Sequence[str] | None = None,
        status: KnowledgeDocumentStatus | None = "published",
        limit: int = 5,
    ) -> list[KnowledgeSearchHit]:
        tokens = normalize_tokens(query)
        if not tokens:
            return []
        candidates = self._store.search_chunks(
            organization_id=organization_id,
            query_tokens=tokens,
            document_ids=document_ids,
            status=status,
            limit=max(limit * 8, 24),
        )
        query_phrase = query.strip().lower()
        best_by_document: dict[str, KnowledgeSearchHit] = {}
        for candidate in candidates:
            score = self._score_candidate(candidate, tokens=tokens, query_phrase=query_phrase)
            if score <= 0:
                continue
            hit = KnowledgeSearchHit(
                document_id=candidate.document_id,
                title=candidate.title,
                summary=candidate.summary,
                category=candidate.category,
                tags=list(candidate.tags),
                chunk_id=candidate.chunk_id,
                snippet=summarize_text(candidate.chunk_content, max_chars=800) or candidate.chunk_content,
                score=round(score, 4),
                retrieval_mode="lexical",
                lexical_score=round(score, 4),
            )
            existing = best_by_document.get(candidate.document_id)
            if existing is None or hit.score > existing.score:
                best_by_document[candidate.document_id] = hit
        hits = list(best_by_document.values())
        hits.sort(key=lambda item: (item.score, item.document_id), reverse=True)
        return hits[:limit]

    def semantic_search(
        self,
        *,
        organization_id: str,
        query: str,
        document_ids: Sequence[str] | None = None,
        limit: int = 5,
    ) -> list[KnowledgeSearchHit]:
        if self._vector_index is None or not self._vector_index.is_available():
            return []
        query_vector = self._embedding_provider.embed_query(query)
        hits = self._vector_index.vector_search(
            organization_id=organization_id,
            model_key=self._embedding_provider.model_key,
            query_vector=query_vector,
            limit=max(limit * 3, 12),
            document_ids=document_ids,
        )
        return self._dedupe_hits(hits, limit=limit)

    def hybrid_search(
        self,
        *,
        organization_id: str,
        query: str,
        document_ids: Sequence[str] | None = None,
        limit: int = 5,
        status: KnowledgeDocumentStatus | None = "published",
    ) -> list[KnowledgeSearchHit]:
        lexical_hits = self.search(
            organization_id=organization_id,
            query=query,
            document_ids=document_ids,
            status=status,
            limit=limit,
        )
        if self._vector_index is None or not self._vector_index.is_available():
            return lexical_hits
        query_vector = self._embedding_provider.embed_query(query)
        indexed_hits = self._vector_index.hybrid_search(
            organization_id=organization_id,
            model_key=self._embedding_provider.model_key,
            query=query,
            query_vector=query_vector,
            limit=max(limit * 3, 12),
            document_ids=document_ids,
        )
        if not indexed_hits:
            return lexical_hits

        merged_by_document: dict[str, KnowledgeSearchHit] = {}
        for hit in indexed_hits:
            merged_by_document[hit.document_id] = hit
        for hit in lexical_hits:
            existing = merged_by_document.get(hit.document_id)
            if existing is None:
                merged_by_document[hit.document_id] = hit
                continue
            if existing.lexical_score is None:
                merged_by_document[hit.document_id] = existing.model_copy(
                    update={
                        "lexical_score": hit.score,
                        "score": max(existing.score, hit.score),
                    }
                )
        merged = list(merged_by_document.values())
        merged.sort(key=lambda item: (item.score, item.document_id), reverse=True)
        return merged[:limit]

    def lookup(
        self,
        *,
        organization_id: str,
        query: str,
        document_ids: Sequence[str] | None = None,
        limit: int = 3,
        mode: KnowledgeLookupMode = "standard",
    ) -> KnowledgeLookupResult:
        if mode == "deep":
            hits, retrieval_queries, retrieval_steps, evaluation = self.deep_lookup(
                organization_id=organization_id,
                query=query,
                document_ids=document_ids,
                limit=limit,
            )
        else:
            hits = self.hybrid_search(
                organization_id=organization_id,
                query=query,
                document_ids=document_ids,
                limit=limit,
            )
            retrieval_queries = [query]
            retrieval_steps = [
                KnowledgeLookupStep(query=query, mode="standard", hit_count=len(hits))
            ]
            evaluation = self._evaluate_lookup_hits(
                query=query,
                hits=hits,
                attempted_queries=retrieval_queries,
            )
        if not hits:
            return KnowledgeLookupResult(
                query=query,
                lookup_mode=mode,
                message="I couldn't find a grounded answer in the configured knowledge base.",
                context_block=None,
                retrieval_queries=retrieval_queries,
                retrieval_steps=retrieval_steps,
                evaluation=evaluation,
                hits=[],
                sources=[],
            )
        best = hits[0]
        return KnowledgeLookupResult(
            query=query,
            lookup_mode=mode,
            message=self._lookup_message_from_hit(best),
            context_block=self._build_lookup_context_block(query=query, hits=hits),
            retrieval_queries=retrieval_queries,
            retrieval_steps=retrieval_steps,
            evaluation=evaluation,
            hits=hits,
            sources=[
                KnowledgeLookupSource(
                    document_id=hit.document_id,
                    title=hit.title,
                    category=hit.category,
                    tags=list(hit.tags),
                    score=hit.score,
                )
                for hit in hits
            ],
        )

    def deep_lookup(
        self,
        *,
        organization_id: str,
        query: str,
        document_ids: Sequence[str] | None = None,
        limit: int = 3,
        status: KnowledgeDocumentStatus | None = "published",
    ) -> tuple[list[KnowledgeSearchHit], list[str], list[KnowledgeLookupStep], KnowledgeLookupEvaluation]:
        query_variants = self._deep_lookup_queries(query)
        merged_by_chunk: dict[str, KnowledgeSearchHit] = {}
        attempted_queries: list[str] = []
        attempted_query_keys: set[str] = set()
        steps: list[KnowledgeLookupStep] = []
        pending_queries = list(query_variants)
        evaluation = KnowledgeLookupEvaluation(
            grade="fail",
            comment="No retrieval was attempted.",
        )

        for iteration in range(2):
            batch = [
                item
                for item in pending_queries
                if item.strip() and item.strip().lower() not in attempted_query_keys
            ][:4]
            if not batch:
                break
            for variant in batch:
                attempted_queries.append(variant)
                attempted_query_keys.add(variant.strip().lower())
                hits = self.hybrid_search(
                    organization_id=organization_id,
                    query=variant,
                    document_ids=document_ids,
                    limit=max(limit * 2, 6),
                    status=status,
                )
                step_mode: KnowledgeLookupMode = "standard" if not steps else "deep"
                steps.append(
                    KnowledgeLookupStep(
                        query=variant,
                        mode=step_mode,
                        hit_count=len(hits),
                    )
                )
                query_penalty = (len(attempted_queries) - 1) * 0.15
                for hit in hits:
                    existing = merged_by_chunk.get(hit.chunk_id)
                    adjusted = hit
                    if query_penalty > 0:
                        adjusted = hit.model_copy(
                            update={"score": round(hit.score - query_penalty, 4)}
                        )
                    if existing is None or adjusted.score > existing.score:
                        merged_by_chunk[hit.chunk_id] = adjusted

            merged = list(merged_by_chunk.values())
            merged.sort(key=lambda item: (item.score, item.document_id, item.chunk_id), reverse=True)
            evaluation = self._evaluate_lookup_hits(
                query=query,
                hits=merged[:limit],
                attempted_queries=attempted_queries,
            )
            if evaluation.grade == "pass" or not evaluation.follow_up_queries:
                break
            pending_queries = list(evaluation.follow_up_queries)

        merged = list(merged_by_chunk.values())
        merged.sort(key=lambda item: (item.score, item.document_id, item.chunk_id), reverse=True)
        return merged[:limit], attempted_queries, steps, evaluation

    @staticmethod
    def _deep_lookup_queries(query: str) -> list[str]:
        compact = re.sub(r"\s+", " ", query).strip()
        if not compact:
            return []
        variants: list[str] = [compact]
        token_list = normalize_tokens(compact)
        if token_list:
            noun_focused = " ".join(token_list[: min(len(token_list), 8)])
            if noun_focused and noun_focused.lower() != compact.lower():
                variants.append(noun_focused)

        split_candidates = re.split(r"\b(?:and|also|plus|versus|vs\.?|or)\b|[?;,]", compact, flags=re.IGNORECASE)
        for part in split_candidates:
            candidate = re.sub(r"\s+", " ", part).strip(" .,-")
            if len(normalize_tokens(candidate)) >= 3 and candidate.lower() not in {item.lower() for item in variants}:
                variants.append(candidate)

        deduped: list[str] = []
        seen: set[str] = set()
        for variant in variants:
            key = variant.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(variant)
        return deduped[:4]

    def _evaluate_lookup_hits(
        self,
        *,
        query: str,
        hits: Sequence[KnowledgeSearchHit],
        attempted_queries: Sequence[str],
    ) -> KnowledgeLookupEvaluation:
        query_tokens = normalize_tokens(query)
        if not hits:
            follow_up = self._follow_up_queries_for_weak_hits(
                query=query,
                hits=hits,
                attempted_queries=attempted_queries,
            )
            return KnowledgeLookupEvaluation(
                grade="fail",
                comment="No grounded knowledge hits matched the request.",
                gaps=["no_hits", "missing_grounding"],
                follow_up_queries=follow_up,
            )

        top_hit = hits[0]
        hit_tokens = self._hit_tokens(top_hit)
        matched_tokens = [token for token in query_tokens if token in hit_tokens]
        missing_tokens = [token for token in query_tokens if token not in hit_tokens]
        coverage = len(matched_tokens) / max(len(query_tokens), 1)
        distinct_documents = len({hit.document_id for hit in hits})
        gaps: list[str] = []

        if len(hits) < 2 and len(query_tokens) >= 4:
            gaps.append("low_hit_count")
        if missing_tokens:
            gaps.append("missing_query_terms")
        if top_hit.score < 3.0 and coverage < 0.5:
            gaps.append("low_relevance")

        grade: str = "pass"
        if "low_relevance" in gaps or (coverage < 0.34 and len(query_tokens) >= 3):
            grade = "weak"
        if distinct_documents == 0:
            grade = "fail"

        follow_up_queries: list[str] = []
        comment = (
            "Top hits appear grounded enough to answer the request."
            if grade == "pass"
            else "Top hits only partially cover the request; try a narrower follow-up search."
        )
        if grade != "pass":
            follow_up_queries = self._follow_up_queries_for_weak_hits(
                query=query,
                hits=hits,
                attempted_queries=attempted_queries,
            )

        return KnowledgeLookupEvaluation(
            grade=grade,  # type: ignore[arg-type]
            comment=comment,
            gaps=gaps,
            follow_up_queries=follow_up_queries,
        )

    @staticmethod
    def _hit_tokens(hit: KnowledgeSearchHit) -> set[str]:
        parts = [
            hit.title,
            hit.summary or "",
            hit.snippet,
            hit.category or "",
            " ".join(hit.tags),
        ]
        return set(normalize_tokens(" ".join(part for part in parts if part)))

    def _follow_up_queries_for_weak_hits(
        self,
        *,
        query: str,
        hits: Sequence[KnowledgeSearchHit],
        attempted_queries: Sequence[str],
    ) -> list[str]:
        attempted = {item.strip().lower() for item in attempted_queries if item.strip()}
        query_tokens = normalize_tokens(query)
        candidates: list[str] = []

        top_hit = hits[0] if hits else None
        if top_hit is not None:
            hit_tokens = self._hit_tokens(top_hit)
            missing_tokens = [token for token in query_tokens if token not in hit_tokens]
            if missing_tokens:
                candidates.append(" ".join(missing_tokens[: min(len(missing_tokens), 6)]))
                candidates.append(
                    f"{top_hit.title} {' '.join(missing_tokens[: min(len(missing_tokens), 4)])}".strip()
                )
        else:
            if len(query_tokens) >= 4:
                candidates.append(" ".join(query_tokens[:4]))
                candidates.append(" ".join(query_tokens[-4:]))

        clauses = re.split(r"\b(?:and|also|plus|versus|vs\.?|or)\b|[?;,]", query, flags=re.IGNORECASE)
        for clause in clauses:
            candidate = re.sub(r"\s+", " ", clause).strip(" .,-")
            if len(normalize_tokens(candidate)) >= 2:
                candidates.append(candidate)

        follow_up: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = re.sub(r"\s+", " ", candidate).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen or key in attempted:
                continue
            seen.add(key)
            follow_up.append(normalized)
        return follow_up[:3]

    @staticmethod
    def _lookup_message_from_hit(hit: KnowledgeSearchHit) -> str | None:
        for raw in (hit.summary, hit.snippet):
            text = KnowledgeService._clean_lookup_message_text(raw or "")
            if not text:
                continue
            for sentence in re.split(r"(?<=[.!?…])\s+", text):
                candidate = sentence.strip(" -")
                if KnowledgeService._is_user_facing_lookup_sentence(candidate):
                    return candidate
        return hit.snippet or hit.summary

    @staticmethod
    def _clean_lookup_message_text(raw_text: str) -> str:
        text = str(raw_text or "")
        if not text:
            return ""
        text = re.sub(r"(?m)^#{1,6}\s+", "", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)
        text = re.sub(r"\[[^\]]+\]\([^)]+\)", "", text)
        text = re.sub(r"(?:^|\s)[*_=-]{3,}(?:\s|$)", ". ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _is_user_facing_lookup_sentence(sentence: str) -> bool:
        text = re.sub(r"\s+", " ", str(sentence or "")).strip()
        if len(text) < 28 or text.count(" ") < 4:
            return False
        lowered = text.lower()
        if text.endswith("?") and re.match(r"^(what|who|how|why|where|when|which)\b", lowered):
            return False
        boilerplate_markers = (
            "knowledge base for sales agents",
            "sample knowledge document",
            "cover the common product and pricing questions",
            "edit freely",
            "placeholder",
            "before shipping",
        )
        return not any(marker in lowered for marker in boilerplate_markers)

    @staticmethod
    def _build_lookup_context_block(*, query: str, hits: Sequence[KnowledgeSearchHit]) -> str | None:
        if not hits:
            return None
        lines = [f"Question: {query}"]
        for index, hit in enumerate(hits[:3], start=1):
            detail = hit.summary or hit.snippet
            if not detail:
                continue
            lines.append(f"{index}. {hit.title}: {detail}")
        return "\n".join(lines) if len(lines) > 1 else None

    def _require_document(self, *, organization_id: str, document_id: str) -> KnowledgeDocument:
        document = self.get_document(organization_id=organization_id, document_id=document_id)
        if document is None:
            raise ValueError(f"knowledge document {document_id} not found")
        return document

    def _upsert_seed_document(self, *, organization_id: str, document: SeedKnowledgeDocument) -> KnowledgeDocument:
        return self.upsert_document(
            organization_id=organization_id,
            title=document.title,
            content=document.content,
            summary=document.summary,
            category=document.category,
            tags=document.tags,
            status="published",
            source_kind="seed",
            source_ref=document.external_id,
            metadata={"seed_external_id": document.external_id},
        )

    def _build_chunks(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        parts = chunk_text(
            document.content,
            max_words=self._chunk_max_words,
            overlap_words=self._chunk_overlap_words,
        )
        if not parts:
            parts = [document.content.strip()] if document.content.strip() else []
        if len(parts) > self._max_chunks_per_document:
            raise KnowledgeIngestError(
                (
                    f"knowledge document {document.title!r} expands to {len(parts)} chunks, "
                    f"which exceeds the limit of {self._max_chunks_per_document}"
                ),
                code="too_many_chunks",
                details={
                    "document_id": document.document_id,
                    "title": document.title,
                    "chunk_count": len(parts),
                    "max_chunks_per_document": self._max_chunks_per_document,
                    "chunk_max_words": self._chunk_max_words,
                },
            )
        created_at = utc_now()
        return [
            KnowledgeChunk(
                document_id=document.document_id,
                organization_id=document.organization_id,
                position=index,
                content=part,
                search_text=self._build_search_text(document=document, chunk_text=part),
                token_count=len(normalize_tokens(part)),
                metadata={"document_status": document.status},
                created_at=created_at,
            )
            for index, part in enumerate(parts)
        ]

    @staticmethod
    def _build_search_text(*, document: KnowledgeDocument, chunk_text: str) -> str:
        pieces = [document.title, document.summary or "", document.category or "", " ".join(document.tags), chunk_text]
        return " ".join(piece.lower() for piece in pieces if piece).strip()

    @staticmethod
    def _score_candidate(candidate: KnowledgeSearchCandidate, *, tokens: Sequence[str], query_phrase: str) -> float:
        token_set = set(tokens)
        title_tokens = set(normalize_tokens(candidate.title))
        summary_tokens = set(normalize_tokens(candidate.summary or ""))
        tag_tokens = {token for tag in candidate.tags for token in normalize_tokens(tag)}
        content_tokens = set(normalize_tokens(candidate.chunk_content))

        score = 0.0
        score += len(token_set & title_tokens) * 5.0
        score += len(token_set & summary_tokens) * 3.0
        score += len(token_set & tag_tokens) * 3.0
        score += len(token_set & content_tokens) * 1.0
        if query_phrase and query_phrase in candidate.title.lower():
            score += 4.0
        if query_phrase and query_phrase in candidate.chunk_content.lower():
            score += 2.0
        if candidate.position == 0:
            score += 0.25
        return score

    @staticmethod
    def _content_hash(chunk: KnowledgeChunk) -> str:
        return sha256(chunk.search_text.encode("utf-8")).hexdigest()

    def _vector_sync_error_message(
        self,
        *,
        organization_id: str,
        document_id: str,
        chunk_count: int,
        exc: Exception,
    ) -> str:
        diagnostics = None if self._vector_index is None else self._vector_index.diagnostics()
        context = [
            f"organization_id={organization_id}",
            f"document_id={document_id}",
            f"chunk_count={chunk_count}",
            f"model_key={self._embedding_provider.model_key}",
        ]
        if diagnostics is not None:
            if diagnostics.index_name:
                context.append(f"index={diagnostics.index_name}")
            if diagnostics.endpoint:
                context.append(f"endpoint={diagnostics.endpoint}")
            if diagnostics.collection_name:
                context.append(f"collection={diagnostics.collection_name}")
            if diagnostics.last_operation:
                context.append(f"operation={diagnostics.last_operation}")
            if diagnostics.last_error:
                return f"vector sync failed ({', '.join(context)}): {diagnostics.last_error}"
        return f"vector sync failed ({', '.join(context)}): {exc}"

    @staticmethod
    def _dedupe_hits(hits: Sequence[KnowledgeSearchHit], *, limit: int) -> list[KnowledgeSearchHit]:
        best_by_document: dict[str, KnowledgeSearchHit] = {}
        for hit in hits:
            existing = best_by_document.get(hit.document_id)
            if existing is None or hit.score > existing.score:
                best_by_document[hit.document_id] = hit
        result = list(best_by_document.values())
        result.sort(key=lambda item: (item.score, item.document_id), reverse=True)
        return result[:limit]

    def close(self) -> None:
        self._embedding_provider.close()
