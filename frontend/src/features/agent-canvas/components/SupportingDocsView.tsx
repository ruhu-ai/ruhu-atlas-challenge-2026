/**
 * Supporting Documents View — select which knowledge base articles
 * this agent can access for RAG context.
 *
 * One level of linking: checked = agent can use it. That's it.
 * Semantic search handles relevance at query time — no per-scenario
 * document filtering needed.
 */

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Badge } from '@/components/atoms/badge'
import { Checkbox } from '@/components/atoms/checkbox'
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  FileText,
  Loader2,
  Plus,
  Save,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type {
  KnowledgeDocument,
  KnowledgeDocumentIndexStatus,
} from '@/api/services/knowledge-base.service'

function IndexStatusIndicator({ doc }: { doc: KnowledgeDocument }) {
  const status: KnowledgeDocumentIndexStatus | undefined = doc.index_status
  if (!status) return null
  switch (status) {
    case 'ready':
      return (
        <Badge
          variant="outline"
          className="text-emerald-600 border-emerald-300 dark:text-emerald-400 dark:border-emerald-500/30 gap-1 text-xs"
        >
          <CheckCircle2 className="h-3 w-3" />
          Ready
        </Badge>
      )
    case 'indexing':
      return (
        <Badge
          variant="outline"
          className="text-blue-600 border-blue-300 dark:text-blue-400 dark:border-blue-500/30 gap-1 text-xs"
        >
          <Loader2 className="h-3 w-3 animate-spin" />
          Indexing…
        </Badge>
      )
    case 'error':
      return (
        <Badge
          variant="outline"
          className="text-red-600 border-red-300 dark:text-red-400 dark:border-red-500/30 gap-1 text-xs"
          title={doc.last_index_error || 'Not searchable — document has no embeddings'}
        >
          <AlertCircle className="h-3 w-3" />
          Not searchable
        </Badge>
      )
    default:
      return null
  }
}

interface SupportingDocsViewProps {
  dataSources: KnowledgeDocument[]
  loading: boolean
  agentId?: string
  selectedIds: string[]
  onSelectionChange: (ids: string[]) => void
  onSave: () => void
  saving?: boolean
}

export function SupportingDocsView({
  dataSources,
  loading,
  agentId,
  selectedIds,
  onSelectionChange,
  onSave,
  saving,
}: SupportingDocsViewProps) {
  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const isNewAgent = !agentId

  const toggleDocument = (articleId: string) => {
    if (isNewAgent) return
    if (selectedIds.includes(articleId)) {
      onSelectionChange(selectedIds.filter((id) => id !== articleId))
    } else {
      onSelectionChange([...selectedIds, articleId])
    }
  }

  // Count selected docs that aren't yet searchable so we can warn the user.
  const selectedButNotSearchable = dataSources.filter(
    (doc) => selectedIds.includes(doc.document_id) && doc.index_status === 'error',
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Knowledge Base</h2>
          <p className="text-sm text-muted-foreground">
            Select documents your agent can reference during conversations
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!isNewAgent && (
            <Button onClick={onSave} disabled={saving} size="sm">
              {saving ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Save className="h-4 w-4 mr-2" />
              )}
              Save
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => window.open('/knowledge-base', '_blank')}>
            <Plus className="h-4 w-4 mr-2" />
            Add Article
          </Button>
        </div>
      </div>

      {selectedButNotSearchable.length > 0 && (
        <div className="rounded-lg border border-amber-300 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-950/30 p-3 flex items-start gap-3">
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
          <div className="text-sm flex-1">
            <p className="font-medium text-amber-900 dark:text-amber-200">
              {selectedButNotSearchable.length === 1
                ? '1 linked document is not searchable'
                : `${selectedButNotSearchable.length} linked documents are not searchable`}
            </p>
            <p className="text-amber-800 dark:text-amber-300/80 mt-0.5">
              Your agent will not be able to find information in these documents until they are indexed.
              Open the Knowledge Base page to re-index.
            </p>
          </div>
        </div>
      )}

      {dataSources.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-16">
            <FileText className="h-12 w-12 text-muted-foreground mb-4" />
            <h3 className="font-medium mb-2">No Documents Yet</h3>
            <p className="text-sm text-muted-foreground text-center max-w-md mb-4">
              Add documents to your knowledge base, then select them here for your agent to reference.
            </p>
            <Button onClick={() => window.open('/knowledge-base', '_blank')}>
              <Plus className="h-4 w-4 mr-2" />
              Add First Document
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          {isNewAgent && (
            <p className="text-sm text-muted-foreground">
              Save the agent first to assign data sources.
            </p>
          )}
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {dataSources.map((doc) => {
              const isSelected = selectedIds.includes(doc.document_id)
              return (
                <Card
                  key={doc.document_id}
                  className={cn(
                    'cursor-pointer transition-colors',
                    isSelected
                      ? 'border-cyan-500 bg-cyan-500/5'
                      : 'opacity-60 hover:opacity-100 hover:border-border',
                  )}
                  onClick={() => toggleDocument(doc.document_id)}
                >
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      {!isNewAgent && (
                        <Checkbox
                          checked={isSelected}
                          onCheckedChange={() => toggleDocument(doc.document_id)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      )}
                      <CardTitle className="text-base line-clamp-1 flex-1">
                        {doc.title}
                      </CardTitle>
                    </div>
                    {doc.summary && (
                      <CardDescription className="line-clamp-2">{doc.summary}</CardDescription>
                    )}
                  </CardHeader>
                  <CardContent>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground flex-wrap">
                      {doc.category && (
                        <Badge variant="secondary" className="text-xs">
                          {doc.category}
                        </Badge>
                      )}
                      <IndexStatusIndicator doc={doc} />
                    </div>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        </>
      )}

      <div className="pt-4 border-t">
        <Button variant="outline" onClick={() => window.open('/knowledge-base', '_blank')}>
          <ExternalLink className="h-4 w-4 mr-2" />
          Manage Knowledge Base
        </Button>
      </div>
    </div>
  )
}
