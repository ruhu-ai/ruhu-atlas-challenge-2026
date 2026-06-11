from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


KnowledgeDocumentStatus = Literal["draft", "published", "archived"]
KnowledgeDocumentIndexStatus = Literal[
    "ready",      # all chunks have indexed embeddings — searchable
    "indexing",   # indexing in progress (chunks pending or partial coverage)
    "error",      # not searchable (no embeddings, failed, or unknown)
]
KnowledgeSourceKind = Literal["manual", "seed", "file", "import"]
KnowledgeFileKind = Literal["text", "markdown", "json", "yaml", "csv", "html", "xml", "docx", "pdf", "binary"]
EmbeddingSyncStatus = Literal["pending", "indexed", "failed"]
KnowledgeRetrievalMode = Literal["lexical", "semantic", "hybrid"]
KnowledgeLookupMode = Literal["standard", "deep"]
KnowledgeLookupEvaluationGrade = Literal["pass", "weak", "fail"]
KnowledgeIndexJobScope = Literal["document", "organization"]
KnowledgeIndexJobStatus = Literal["queued", "running", "completed", "failed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    value = uuid4().hex
    return f"{prefix}{value}" if prefix else value


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    document_id: str = Field(default_factory=lambda: new_id("kdoc_"))
    organization_id: str
    title: str
    content: str
    summary: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: KnowledgeDocumentStatus = "draft"
    source_kind: KnowledgeSourceKind = "manual"
    source_ref: str | None = None
    source_url: str | None = None
    media_type: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    published_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _ensure_published_at(self) -> "KnowledgeDocument":
        if self.status == "published" and self.published_at is None:
            self.published_at = self.updated_at
        return self


class KnowledgeDocumentWithIndexStatus(KnowledgeDocument):
    """Document with computed index readiness, suitable for list/detail responses.

    Only two fields beyond the base document: the overall status and an
    optional error detail string for tooltips/diagnostics.  If callers need
    chunk-level telemetry, they can hit the /chunks and /embeddings endpoints.
    """

    index_status: KnowledgeDocumentIndexStatus = "error"
    last_index_error: str | None = None


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    chunk_id: str = Field(default_factory=lambda: new_id("kchunk_"))
    document_id: str
    organization_id: str
    position: int
    content: str
    search_text: str
    token_count: int
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class KnowledgeSearchCandidate(BaseModel):
    document_id: str
    organization_id: str
    title: str
    summary: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: KnowledgeDocumentStatus = "draft"
    updated_at: datetime
    chunk_id: str
    position: int
    chunk_content: str
    search_text: str


class KnowledgeSearchHit(BaseModel):
    document_id: str
    title: str
    summary: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    chunk_id: str
    snippet: str
    score: float
    retrieval_mode: KnowledgeRetrievalMode = "lexical"
    lexical_score: float | None = None
    semantic_score: float | None = None
    index_score: float | None = None


class KnowledgeLookupSource(BaseModel):
    document_id: str
    title: str
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    score: float


class KnowledgeLookupStep(BaseModel):
    query: str
    mode: KnowledgeLookupMode = "standard"
    hit_count: int = 0


class KnowledgeLookupEvaluation(BaseModel):
    grade: KnowledgeLookupEvaluationGrade = "fail"
    comment: str | None = None
    gaps: list[str] = Field(default_factory=list)
    follow_up_queries: list[str] = Field(default_factory=list)


class KnowledgeLookupResult(BaseModel):
    query: str
    message: str
    lookup_mode: KnowledgeLookupMode = "standard"
    context_block: str | None = None
    retrieval_queries: list[str] = Field(default_factory=list)
    retrieval_steps: list[KnowledgeLookupStep] = Field(default_factory=list)
    evaluation: KnowledgeLookupEvaluation | None = None
    hits: list[KnowledgeSearchHit] = Field(default_factory=list)
    sources: list[KnowledgeLookupSource] = Field(default_factory=list)


class ExtractedKnowledgeDocument(BaseModel):
    title: str
    content: str
    summary: str | None = None
    file_kind: KnowledgeFileKind
    media_type: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class SeedKnowledgeDocument(BaseModel):
    external_id: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    summary: str | None = None


class KnowledgeChunkEmbedding(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    chunk_id: str
    document_id: str
    organization_id: str
    model_key: str
    dimensions: int
    vector: list[float]
    content_hash: str
    sync_status: EmbeddingSyncStatus = "pending"
    index_ref: str | None = None
    indexed_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class IndexedKnowledgeChunk(BaseModel):
    chunk_id: str
    document_id: str
    organization_id: str
    model_key: str
    title: str
    summary: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    content: str
    search_text: str
    vector: list[float]


class KnowledgeOrganizationStats(BaseModel):
    organization_id: str
    document_count: int = 0
    published_document_count: int = 0
    chunk_count: int = 0
    embedding_count: int = 0
    indexed_embedding_count: int = 0
    pending_embedding_count: int = 0
    failed_embedding_count: int = 0


class KnowledgeGuardrails(BaseModel):
    max_file_bytes: int
    max_chunks_per_document: int
    chunk_max_words: int
    chunk_overlap_words: int


class KnowledgeIndexHealth(BaseModel):
    organization_id: str
    model_key: str
    chunk_count: int = 0
    indexed_chunk_count: int = 0
    missing_chunk_count: int = 0
    pending_chunk_count: int = 0
    failed_chunk_count: int = 0
    lagging_chunk_count: int = 0
    last_successful_indexed_at: datetime | None = None
    index_lag_seconds: float | None = None


class KnowledgeVectorIndexDiagnostics(BaseModel):
    index_name: str | None = None
    endpoint: str | None = None
    collection_name: str | None = None
    last_operation: str | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    last_successful_write_at: datetime | None = None
    last_successful_read_at: datetime | None = None


class KnowledgeIndexJob(BaseModel):
    job_id: str = Field(default_factory=lambda: new_id("kjob_"))
    organization_id: str
    scope: KnowledgeIndexJobScope
    document_id: str | None = None
    force: bool = False
    status: KnowledgeIndexJobStatus = "queued"
    indexed_embeddings: int = 0
    error: str | None = None
    submitted_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class KnowledgeRuntimeStatus(BaseModel):
    default_organization_id: str
    embedding_model_key: str
    embedding_provider: str
    vector_index: str | None = None
    vector_index_available: bool = False
    guardrails: KnowledgeGuardrails
    index_health: KnowledgeIndexHealth
    vector_diagnostics: KnowledgeVectorIndexDiagnostics | None = None
    queued_jobs: int = 0
    running_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    last_error: str | None = None
    organization: KnowledgeOrganizationStats
    recent_jobs: list[KnowledgeIndexJob] = Field(default_factory=list)
