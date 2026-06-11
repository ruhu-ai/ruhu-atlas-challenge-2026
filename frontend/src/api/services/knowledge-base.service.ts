/**
 * Knowledge Base Service
 *
 * Wraps the /knowledge/* API endpoints.
 * Types match the backend KnowledgeDocument model exactly.
 */

import { apiClient } from '../client'

// ── Types ──────────────────────────────────────────────────────────────────

export type KnowledgeDocumentStatus = 'draft' | 'published' | 'archived'
export type KnowledgeSourceKind = 'manual' | 'seed' | 'file' | 'import'

/**
 * Index readiness — independent of the publication lifecycle.  A document
 * can be `published` but `error`, which means search will return nothing
 * for it until embeddings are generated.
 *
 * Three states: ready | indexing | error.  `last_index_error` carries
 * details for tooltips when status is `error`.
 */
export type KnowledgeDocumentIndexStatus = 'ready' | 'indexing' | 'error'

export interface KnowledgeDocument {
  document_id: string
  organization_id: string
  title: string
  content: string
  summary: string | null
  category: string | null
  tags: string[]
  status: KnowledgeDocumentStatus
  source_kind: KnowledgeSourceKind
  source_ref: string | null
  source_url: string | null
  media_type: string | null
  /** Free-form metadata. Includes file_kind, file_size_bytes for uploaded files. */
  metadata: Record<string, unknown>
  published_at: string | null
  created_at: string
  updated_at: string
  /** Populated when include_index_status=true (default). */
  index_status?: KnowledgeDocumentIndexStatus
  last_index_error?: string | null
}

export interface KnowledgeOrganizationStats {
  organization_id: string
  document_count: number
  published_document_count: number
  chunk_count: number
  embedding_count: number
  indexed_embedding_count: number
  pending_embedding_count: number
  failed_embedding_count: number
}

export interface KnowledgeIndexHealth {
  organization_id: string
  model_key: string
  chunk_count: number
  indexed_chunk_count: number
  missing_chunk_count: number
  pending_chunk_count: number
  failed_chunk_count: number
  /** missing + pending + failed */
  lagging_chunk_count: number
  last_successful_indexed_at: string | null
  index_lag_seconds: number | null
}

export interface KnowledgeStatus {
  default_organization_id: string
  embedding_model_key: string
  embedding_provider: string
  vector_index: string | null
  vector_index_available: boolean
  index_health: KnowledgeIndexHealth
  queued_jobs: number
  running_jobs: number
  completed_jobs: number
  failed_jobs: number
  organization: KnowledgeOrganizationStats
}

export interface KnowledgeSearchHit {
  document_id: string
  title: string
  summary: string | null
  category: string | null
  tags: string[]
  chunk_id: string
  snippet: string
  score: number
  retrieval_mode: 'lexical' | 'semantic' | 'hybrid'
  lexical_score: number | null
  semantic_score: number | null
}

export interface KnowledgeLookupResult {
  query: string
  message: string
  hits: KnowledgeSearchHit[]
  sources: Array<{
    document_id: string
    title: string
    category: string | null
    tags: string[]
    score: number
  }>
}

export interface KnowledgeIndexResult {
  document_id?: string
  total_chunks: number
  indexed_chunks: number
  failed_chunks: number
}

// ── Request bodies ─────────────────────────────────────────────────────────

export interface CreateKnowledgeDocumentBody {
  title: string
  content: string
  summary?: string
  category?: string
  tags?: string[]
  status?: KnowledgeDocumentStatus
  source_url?: string
  metadata?: Record<string, unknown>
}

export interface UpdateKnowledgeDocumentBody {
  title?: string
  content?: string
  summary?: string
  category?: string
  tags?: string[]
  status?: KnowledgeDocumentStatus
  source_url?: string
  metadata?: Record<string, unknown>
}

export interface ListKnowledgeDocumentsParams {
  status?: KnowledgeDocumentStatus
  limit?: number
  offset?: number
}

// ── Service ────────────────────────────────────────────────────────────────

class KnowledgeBaseService {
  private readonly base = '/knowledge'

  listDocuments(params?: ListKnowledgeDocumentsParams): Promise<KnowledgeDocument[]> {
    return apiClient.get<KnowledgeDocument[]>(`${this.base}/documents`, {
      params: params as Record<string, string | number | boolean | undefined | null>,
    })
  }

  getDocument(documentId: string): Promise<KnowledgeDocument> {
    return apiClient.get<KnowledgeDocument>(`${this.base}/documents/${documentId}`)
  }

  createDocument(body: CreateKnowledgeDocumentBody): Promise<KnowledgeDocument> {
    return apiClient.post<KnowledgeDocument>(`${this.base}/documents`, body)
  }

  updateDocument(documentId: string, body: UpdateKnowledgeDocumentBody): Promise<KnowledgeDocument> {
    return apiClient.patch<KnowledgeDocument>(`${this.base}/documents/${documentId}`, body)
  }

  deleteDocument(documentId: string): Promise<void> {
    return apiClient.delete<void>(`${this.base}/documents/${documentId}`)
  }

  /**
   * Upload a file (PDF, DOCX, TXT, MD, JSON, YAML, CSV, HTML).
   * Extra fields are sent as multipart Form fields alongside the file.
   */
  async uploadDocument(
    file: File,
    opts: {
      title?: string
      category?: string
      tags?: string[]
      status?: KnowledgeDocumentStatus
    } = {}
  ): Promise<KnowledgeDocument> {
    const formData = new FormData()
    formData.append('file', file)
    if (opts.title) formData.append('title', opts.title)
    if (opts.category) formData.append('category', opts.category)
    if (opts.tags && opts.tags.length > 0) formData.append('tags', JSON.stringify(opts.tags))
    if (opts.status) formData.append('status', opts.status)
    return apiClient.post<KnowledgeDocument>(`${this.base}/documents/upload`, formData)
  }

  /**
   * Trigger embedding generation for a single document.
   */
  indexDocument(documentId: string, force = false): Promise<KnowledgeIndexResult> {
    return apiClient.post<KnowledgeIndexResult>(
      `${this.base}/documents/${documentId}/index`,
      undefined,
      { params: force ? { force: 'true' } : undefined }
    )
  }

  /**
   * Trigger embedding generation for all documents with the given status.
   * Defaults to "published".
   */
  indexAll(opts: { status?: KnowledgeDocumentStatus; force?: boolean } = {}): Promise<KnowledgeIndexResult> {
    return apiClient.post<KnowledgeIndexResult>(`${this.base}/index`, undefined, {
      params: {
        ...(opts.status ? { status: opts.status } : {}),
        ...(opts.force ? { force: 'true' } : {}),
      },
    })
  }

  search(query: string, opts: { limit?: number; documentIds?: string[] } = {}): Promise<KnowledgeLookupResult> {
    const params: Record<string, string | number> = { query }
    if (opts.limit) params.limit = opts.limit
    // document_id is a multi-value query param; pass as separate keys
    return apiClient.get<KnowledgeLookupResult>(`${this.base}/search`, {
      params: params as Record<string, string | number | boolean | undefined | null>,
    })
  }

  getStatus(): Promise<KnowledgeStatus> {
    return apiClient.get<KnowledgeStatus>(`${this.base}/status`)
  }

  getStats(): Promise<KnowledgeOrganizationStats> {
    return apiClient.get<KnowledgeOrganizationStats>(`${this.base}/stats`)
  }
}

export const knowledgeBaseService = new KnowledgeBaseService()
export default knowledgeBaseService
