/**
 * Knowledge Base — Document Manager
 *
 * Simple document manager for adding/removing content the AI agent references.
 * Not a CMS — the agent consumes these via vector search, so we focus on
 * getting documents in (text or file) and letting users see what exists.
 */

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/atoms/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/atoms/dropdown-menu'
import {
  Plus,
  FileText,
  Upload,
  File,
  Loader2,
  MoreVertical,
  Trash2,
  Edit,
  CheckCircle2,
  Search,
  X,
  RefreshCw,
  AlertCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import { knowledgeBaseService } from '@/api/services/knowledge-base.service'
import type {
  KnowledgeDocument,
  CreateKnowledgeDocumentBody,
  UpdateKnowledgeDocumentBody,
  KnowledgeDocumentStatus,
} from '@/api/services/knowledge-base.service'

// ==================== Types ====================

interface DocumentFormData {
  title: string
  content: string
  summary: string
  category: string
  source_url: string
  status: KnowledgeDocumentStatus
}

const EMPTY_FORM: DocumentFormData = {
  title: '',
  content: '',
  summary: '',
  category: '',
  source_url: '',
  status: 'published',
}

// ==================== Helpers ====================

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function getDocumentType(doc: KnowledgeDocument): string {
  const fileKind = doc.metadata?.file_kind
  if (typeof fileKind === 'string') return fileKind.toUpperCase()
  if (doc.source_kind === 'file') return 'File'
  return 'Text'
}

function getStatusBadge(doc: KnowledgeDocument) {
  if (doc.status === 'draft') {
    return (
      <Badge variant="outline" className="text-muted-foreground text-xs">
        Draft
      </Badge>
    )
  }
  if (doc.status === 'archived') {
    return (
      <Badge variant="outline" className="text-muted-foreground text-xs">
        Archived
      </Badge>
    )
  }
  // published
  return (
    <Badge variant="outline" className="text-emerald-600 border-emerald-300 dark:text-emerald-400 dark:border-emerald-500/30 gap-1">
      <CheckCircle2 className="h-3 w-3" />
      Published
    </Badge>
  )
}

function getIndexBadge(doc: KnowledgeDocument, isIndexing: boolean) {
  // Optimistic state when the user just clicked re-index.
  if (isIndexing) {
    return (
      <Badge variant="outline" className="text-blue-600 border-blue-300 dark:text-blue-400 dark:border-blue-500/30 gap-1">
        <Loader2 className="h-3 w-3 animate-spin" />
        Indexing
      </Badge>
    )
  }
  const status = doc.index_status
  if (!status) return null
  switch (status) {
    case 'ready':
      return (
        <Badge
          variant="outline"
          className="text-emerald-600 border-emerald-300 dark:text-emerald-400 dark:border-emerald-500/30 gap-1"
        >
          <CheckCircle2 className="h-3 w-3" />
          Ready
        </Badge>
      )
    case 'indexing':
      return (
        <Badge variant="outline" className="text-blue-600 border-blue-300 dark:text-blue-400 dark:border-blue-500/30 gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          Indexing…
        </Badge>
      )
    case 'error':
      return (
        <Badge
          variant="outline"
          className="text-red-600 border-red-300 dark:text-red-400 dark:border-red-500/30 gap-1"
          title={doc.last_index_error || "Not searchable — document has no embeddings"}
        >
          <AlertCircle className="h-3 w-3" />
          Not searchable
        </Badge>
      )
    default:
      return null
  }
}

// ==================== Main Component ====================

export default function KnowledgeBasePage() {
  const queryClient = useQueryClient()

  // State
  const [isDocModalOpen, setIsDocModalOpen] = useState(false)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [selectedDoc, setSelectedDoc] = useState<KnowledgeDocument | null>(null)
  const [editingDoc, setEditingDoc] = useState<KnowledgeDocument | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [indexingDocId, setIndexingDocId] = useState<string | null>(null)

  const [formData, setFormData] = useState<DocumentFormData>(EMPTY_FORM)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [isDragActive, setIsDragActive] = useState(false)
  const [inputMode, setInputMode] = useState<'text' | 'file'>('text')

  // ---- Queries ----

  const { data: documents = [], isLoading } = useQuery({
    queryKey: ['knowledge-documents'],
    queryFn: () => knowledgeBaseService.listDocuments({ limit: 100, offset: 0 }),
    refetchInterval: 15000,
  })

  // ---- Mutations ----

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['knowledge-documents'] })

  const createMutation = useMutation({
    mutationFn: (body: CreateKnowledgeDocumentBody) => knowledgeBaseService.createDocument(body),
    onSuccess: () => { invalidate(); closeDocModal(); toast.success('Document added') },
    onError: (e: Error) => toast.error(`Failed to create: ${e.message}`),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateKnowledgeDocumentBody }) =>
      knowledgeBaseService.updateDocument(id, body),
    onSuccess: () => { invalidate(); closeDocModal(); toast.success('Document updated') },
    onError: (e: Error) => toast.error(`Failed to update: ${e.message}`),
  })

  const deleteMutation = useMutation({
    mutationFn: (document_id: string) => knowledgeBaseService.deleteDocument(document_id),
    onSuccess: () => {
      invalidate()
      setIsDeleteDialogOpen(false)
      setSelectedDoc(null)
      toast.success('Document deleted')
    },
    onError: (e: Error) => toast.error(`Failed to delete: ${e.message}`),
  })

  const uploadMutation = useMutation({
    mutationFn: ({ file, opts }: {
      file: File
      opts: { title?: string; category?: string; status?: KnowledgeDocumentStatus }
    }) => knowledgeBaseService.uploadDocument(file, opts),
    onSuccess: () => { invalidate(); closeDocModal(); toast.success('Document uploaded') },
    onError: (e: Error) => toast.error(`Upload failed: ${e.message}`),
  })

  const indexOneMutation = useMutation({
    mutationFn: (document_id: string) => {
      setIndexingDocId(document_id)
      return knowledgeBaseService.indexDocument(document_id)
    },
    onSuccess: (result) => {
      invalidate()
      setIndexingDocId(null)
      toast.success(`Indexed ${result.indexed_chunks} chunk(s)${result.failed_chunks ? `, ${result.failed_chunks} failed` : ''}`)
    },
    onError: (e: Error) => { setIndexingDocId(null); toast.error(`Indexing failed: ${e.message}`) },
  })

  const indexAllMutation = useMutation({
    mutationFn: () => knowledgeBaseService.indexAll({ status: 'published' }),
    onSuccess: (result) => {
      invalidate()
      toast.success(`Indexed ${result.indexed_chunks} chunk(s)${result.failed_chunks ? `, ${result.failed_chunks} failed` : ''}`)
    },
    onError: (e: Error) => toast.error(`Indexing failed: ${e.message}`),
  })

  // ---- Handlers ----

  const closeDocModal = () => {
    setIsDocModalOpen(false)
    setEditingDoc(null)
    setFormData(EMPTY_FORM)
    setSelectedFile(null)
    setInputMode('text')
    setIsDragActive(false)
  }

  const openCreateModal = () => {
    setEditingDoc(null)
    setFormData(EMPTY_FORM)
    setSelectedFile(null)
    setInputMode('text')
    setIsDocModalOpen(true)
  }

  const openEditModal = (doc: KnowledgeDocument) => {
    setEditingDoc(doc)
    setFormData({
      title: doc.title,
      content: doc.content,
      summary: doc.summary ?? '',
      category: doc.category ?? '',
      source_url: doc.source_url ?? '',
      status: doc.status,
    })
    setSelectedFile(null)
    setInputMode('text')
    setIsDocModalOpen(true)
  }

  const openDeleteDialog = (doc: KnowledgeDocument) => {
    setSelectedDoc(doc)
    setIsDeleteDialogOpen(true)
  }

  const handleSave = () => {
    if (editingDoc) {
      const body: UpdateKnowledgeDocumentBody = {
        title: formData.title,
        content: formData.content,
        summary: formData.summary || undefined,
        category: formData.category || undefined,
        source_url: formData.source_url || undefined,
        status: formData.status,
      }
      updateMutation.mutate({ id: editingDoc.document_id, body })
      return
    }

    if (inputMode === 'file' && selectedFile) {
      uploadMutation.mutate({
        file: selectedFile,
        opts: {
          title: formData.title || undefined,
          category: formData.category || undefined,
          status: formData.status,
        },
      })
      return
    }

    createMutation.mutate({
      title: formData.title,
      content: formData.content,
      summary: formData.summary || undefined,
      category: formData.category || undefined,
      source_url: formData.source_url || undefined,
      status: formData.status,
    })
  }

  const validateAndSetFile = (file: File) => {
    const allowed = ['.pdf', '.docx', '.txt', '.md', '.json', '.yaml', '.yml', '.csv', '.html']
    const ext = '.' + (file.name.split('.').pop()?.toLowerCase() ?? '')
    if (!allowed.includes(ext)) {
      toast.warning(`Unsupported file type. Allowed: ${allowed.join(', ')}`)
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      toast.warning('File too large (max 5 MB)')
      return
    }
    setSelectedFile(file)
    if (!formData.title) {
      setFormData((prev) => ({ ...prev, title: file.name.replace(/\.[^/.]+$/, '') }))
    }
  }

  const canSave = editingDoc
    ? !!formData.title && !!formData.content
    : inputMode === 'file'
      ? !!selectedFile
      : !!formData.title && !!formData.content

  const isSaving = createMutation.isPending || updateMutation.isPending || uploadMutation.isPending

  // ---- Filtered list ----

  const filtered = searchQuery
    ? documents.filter(
        (d) =>
          d.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
          (d.category ?? '').toLowerCase().includes(searchQuery.toLowerCase()),
      )
    : documents

  // ==================== Render ====================

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Knowledge Base</h1>
            <p className="text-muted-foreground">
              Manage documents with AI-powered semantic search
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => indexAllMutation.mutate()}
              disabled={indexAllMutation.isPending}
            >
              <RefreshCw className={`mr-2 h-4 w-4 ${indexAllMutation.isPending ? 'animate-spin' : ''}`} />
              {indexAllMutation.isPending ? 'Indexing...' : 'Index All'}
            </Button>
            <Button onClick={openCreateModal}>
              <Plus className="mr-2 h-4 w-4" />
              Add Document
            </Button>
          </div>
        </div>

        {/* Search */}
        {documents.length > 5 && (
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Filter documents..."
              className="pl-10"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        )}

        {/* Document List */}
        <Card>
          <CardContent className="p-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : documents.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16">
                <FileText className="h-12 w-12 text-muted-foreground mb-4" />
                <h3 className="font-medium mb-1">No documents yet</h3>
                <p className="text-sm text-muted-foreground mb-4">
                  Add documents for your AI agent to reference during conversations.
                </p>
                <Button onClick={openCreateModal}>
                  <Plus className="mr-2 h-4 w-4" />
                  Add First Document
                </Button>
              </div>
            ) : (
              <div className="divide-y divide-border">
                {filtered.map((doc) => {
                  const fileSizeBytes = typeof doc.metadata?.file_size_bytes === 'number'
                    ? doc.metadata.file_size_bytes
                    : null
                  const isIndexing = indexingDocId === doc.document_id

                  return (
                    <div
                      key={doc.document_id}
                      className="flex items-center gap-4 px-6 py-4 hover:bg-muted/50 transition-colors"
                    >
                      {/* Icon */}
                      <div className="shrink-0 w-9 h-9 rounded-lg bg-muted flex items-center justify-center">
                        {doc.source_kind === 'file' ? (
                          <File className="h-4 w-4 text-muted-foreground" />
                        ) : (
                          <FileText className="h-4 w-4 text-muted-foreground" />
                        )}
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <h3 className="font-medium truncate">{doc.title}</h3>
                          {getStatusBadge(doc)}
                          {getIndexBadge(doc, isIndexing)}
                        </div>
                        <div className="flex items-center gap-3 text-xs text-muted-foreground">
                          <span>{getDocumentType(doc)}</span>
                          {fileSizeBytes !== null && (
                            <>
                              <span>&middot;</span>
                              <span>{formatFileSize(fileSizeBytes)}</span>
                            </>
                          )}
                          {doc.category && (
                            <>
                              <span>&middot;</span>
                              <span>{doc.category}</span>
                            </>
                          )}
                          <span>&middot;</span>
                          <span>{formatDate(doc.created_at)}</span>
                        </div>
                      </div>

                      {/* Actions */}
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm" className="h-8 w-8 p-0">
                            <MoreVertical className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openEditModal(doc)}>
                            <Edit className="h-4 w-4 mr-2" />
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => indexOneMutation.mutate(doc.document_id)}
                            disabled={isIndexing}
                          >
                            <RefreshCw className={`h-4 w-4 mr-2 ${isIndexing ? 'animate-spin' : ''}`} />
                            Re-index
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => openDeleteDialog(doc)}
                            className="text-destructive focus:text-destructive"
                          >
                            <Trash2 className="h-4 w-4 mr-2" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  )
                })}

                {filtered.length === 0 && searchQuery && (
                  <div className="flex flex-col items-center py-12 text-muted-foreground">
                    <p className="text-sm">No documents match "{searchQuery}"</p>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ==================== Add / Edit Modal ==================== */}
      <Dialog open={isDocModalOpen} onOpenChange={(open) => !open && closeDocModal()}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingDoc ? 'Edit Document' : 'Add Document'}</DialogTitle>
            <DialogDescription>
              {editingDoc
                ? 'Update document details. Content changes will re-chunk for AI search.'
                : 'Add text content or upload a file (PDF, DOCX, TXT, MD, JSON, YAML, CSV, HTML).'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Input mode toggle (create only) */}
            {!editingDoc && (
              <div className="flex gap-2 p-1 bg-muted rounded-lg w-fit">
                <button
                  type="button"
                  onClick={() => setInputMode('text')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                    inputMode === 'text'
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <FileText className="h-4 w-4" />
                  Write Text
                </button>
                <button
                  type="button"
                  onClick={() => setInputMode('file')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                    inputMode === 'file'
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Upload className="h-4 w-4" />
                  Upload File
                </button>
              </div>
            )}

            {/* File drop zone */}
            {!editingDoc && inputMode === 'file' && (
              <div
                className={`border-2 border-dashed rounded-lg p-8 transition-colors ${
                  isDragActive ? 'border-primary bg-primary/5' : 'border-border hover:border-muted-foreground/50'
                }`}
                onDragOver={(e) => { e.preventDefault(); setIsDragActive(true) }}
                onDragLeave={(e) => { e.preventDefault(); setIsDragActive(false) }}
                onDrop={(e) => {
                  e.preventDefault()
                  setIsDragActive(false)
                  const file = e.dataTransfer.files?.[0]
                  if (file) validateAndSetFile(file)
                }}
              >
                <div className="flex flex-col items-center gap-3">
                  {selectedFile ? (
                    <>
                      <CheckCircle2 className="h-10 w-10 text-emerald-500" />
                      <div className="text-center">
                        <p className="font-medium">{selectedFile.name}</p>
                        <p className="text-sm text-muted-foreground">{formatFileSize(selectedFile.size)}</p>
                      </div>
                      <Button variant="outline" size="sm" onClick={() => setSelectedFile(null)}>
                        Remove
                      </Button>
                    </>
                  ) : (
                    <>
                      <Upload className="h-10 w-10 text-muted-foreground" />
                      <div className="text-center">
                        <p className="font-medium">Drop a file here or click to browse</p>
                        <p className="text-sm text-muted-foreground">
                          PDF, DOCX, TXT, MD, JSON, YAML, CSV, HTML (max 5 MB)
                        </p>
                      </div>
                      <input
                        type="file"
                        id="kb-file-upload"
                        className="hidden"
                        accept=".pdf,.docx,.txt,.md,.json,.yaml,.yml,.csv,.html"
                        onChange={(e) => {
                          const file = e.target.files?.[0]
                          if (file) validateAndSetFile(file)
                        }}
                      />
                      <Button
                        variant="outline"
                        onClick={() => document.getElementById('kb-file-upload')?.click()}
                      >
                        <File className="mr-2 h-4 w-4" />
                        Browse Files
                      </Button>
                    </>
                  )}
                </div>
              </div>
            )}

            {/* Title */}
            <div>
              <Label htmlFor="doc-title">
                Title {inputMode === 'file' && !editingDoc ? '(optional)' : '*'}
              </Label>
              <Input
                id="doc-title"
                value={formData.title}
                onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                placeholder="e.g., Password Reset Guide"
              />
            </div>

            {/* Content (text mode or editing) */}
            {(inputMode === 'text' || editingDoc) && (
              <div>
                <Label htmlFor="doc-content">Content *</Label>
                <textarea
                  id="doc-content"
                  className="w-full min-h-[200px] p-3 rounded-md border border-input bg-transparent text-sm resize-y focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={formData.content}
                  onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                  placeholder="Write your document content here..."
                />
              </div>
            )}

            {/* Category */}
            <div>
              <Label htmlFor="doc-category">Category (optional)</Label>
              <Input
                id="doc-category"
                value={formData.category}
                onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                placeholder="e.g., FAQ, Policy, Product"
              />
            </div>

            {/* Summary */}
            <div>
              <Label htmlFor="doc-summary">Summary (optional)</Label>
              <Input
                id="doc-summary"
                value={formData.summary}
                onChange={(e) => setFormData({ ...formData, summary: e.target.value })}
                placeholder="Auto-generated if left blank"
              />
            </div>

            {/* Source URL */}
            <div>
              <Label htmlFor="doc-source-url">Source URL (optional)</Label>
              <Input
                id="doc-source-url"
                value={formData.source_url}
                onChange={(e) => setFormData({ ...formData, source_url: e.target.value })}
                placeholder="https://example.com/article"
              />
            </div>

            {/* Status */}
            <div>
              <Label htmlFor="doc-status">Status</Label>
              <div className="flex gap-2 mt-1">
                {(['draft', 'published', 'archived'] as KnowledgeDocumentStatus[]).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setFormData({ ...formData, status: s })}
                    className={`px-3 py-1.5 rounded-md text-sm font-medium border transition-colors capitalize ${
                      formData.status === s
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'border-input text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Only <span className="font-medium">published</span> documents are searched by agents.
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={closeDocModal}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={!canSave || isSaving}>
              {isSaving ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  {editingDoc ? 'Saving...' : inputMode === 'file' ? 'Uploading...' : 'Creating...'}
                </>
              ) : editingDoc ? (
                'Save Changes'
              ) : inputMode === 'file' ? (
                'Upload Document'
              ) : (
                'Add Document'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ==================== Delete Confirmation ==================== */}
      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Document</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{selectedDoc?.title}"? This will also remove it
              from the vector index. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => { setIsDeleteDialogOpen(false); setSelectedDoc(null) }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => selectedDoc && deleteMutation.mutate(selectedDoc.document_id)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DashboardLayout>
  )
}
