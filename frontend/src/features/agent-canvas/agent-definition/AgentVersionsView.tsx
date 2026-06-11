import { useMemo } from 'react'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table'
import { GitCompare, Loader2, RefreshCw, RotateCcw } from 'lucide-react'
import type { AgentVersionDiff, AgentVersionSummary } from '@/types/agent-definition'

interface AgentVersionsViewProps {
  versions: AgentVersionSummary[]
  selectedVersionId?: string | null
  againstVersionId?: string | null
  diff?: AgentVersionDiff | null
  loadingVersions: boolean
  loadingDiff: boolean
  creatingDraft: boolean
  onSelectVersion: (versionId: string) => void
  onSelectAgainstVersion: (versionId: string | null) => void
  onRefresh: () => void
  onCreateDraft: (versionId: string) => void
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'n/a'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'n/a'
  return date.toLocaleString()
}

function versionStatusClass(version: AgentVersionSummary): string {
  if (version.is_current_published) return 'border-blue-500/30 text-blue-300'
  if (version.is_current_draft) return 'border-amber-500/30 text-amber-300'
  if (version.status === 'published') return 'border-emerald-500/30 text-emerald-300'
  return 'border-border text-muted-foreground'
}

export function AgentVersionsView({
  versions,
  selectedVersionId,
  againstVersionId,
  diff,
  loadingVersions,
  loadingDiff,
  creatingDraft,
  onSelectVersion,
  onSelectAgainstVersion,
  onRefresh,
  onCreateDraft,
}: AgentVersionsViewProps) {
  const selectedVersion = versions.find((version) => version.version_id === selectedVersionId) || null
  const compareOptions = useMemo(
    () => versions.filter((version) => version.version_id !== selectedVersionId),
    [selectedVersionId, versions],
  )

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Agent Versions</CardTitle>
          <CardDescription>
            Compare agent definitions and create a new draft from any prior version.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 md:grid-cols-3">
            <Select
              value={selectedVersionId || 'none'}
              onValueChange={(value) => {
                if (value !== 'none') onSelectVersion(value)
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select agent version" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none" disabled>
                  Select agent version
                </SelectItem>
                {versions.map((version) => (
                  <SelectItem key={version.version_id} value={version.version_id}>
                    v{version.version_number} · {version.status}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={againstVersionId || 'auto'}
              onValueChange={(value) => onSelectAgainstVersion(value === 'auto' ? null : value)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Compare against" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto (published or previous)</SelectItem>
                {compareOptions.map((version) => (
                  <SelectItem key={version.version_id} value={version.version_id}>
                    v{version.version_number} · {version.status}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Button variant="outline" onClick={onRefresh}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              disabled={!selectedVersion || selectedVersion.is_current_draft || creatingDraft}
              onClick={() => {
                if (selectedVersion) onCreateDraft(selectedVersion.version_id)
              }}
            >
              {creatingDraft ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RotateCcw className="mr-2 h-4 w-4" />
              )}
              Create Draft From Selected Version
            </Button>
            {selectedVersion?.is_current_draft && (
              <span className="text-xs text-muted-foreground">
                This version is already the active draft.
              </span>
            )}
          </div>

          {selectedVersion && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Selected:</span>
              <span className="font-medium">v{selectedVersion.version_number}</span>
              <Badge variant="outline" className={versionStatusClass(selectedVersion)}>
                {selectedVersion.status}
              </Badge>
              {selectedVersion.is_current_draft && <Badge variant="outline">current draft</Badge>}
              {selectedVersion.is_current_published && <Badge variant="outline">live</Badge>}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Version Inventory</CardTitle>
          <CardDescription>
            Every stored definition snapshot for this agent.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadingVersions ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading versions...
            </div>
          ) : versions.length === 0 ? (
            <p className="text-sm text-muted-foreground">No agent versions are available yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Version</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Published</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {versions.map((version) => (
                  <TableRow
                    key={version.version_id}
                    className={version.version_id === selectedVersionId ? 'bg-muted/30' : undefined}
                  >
                    <TableCell className="font-medium">v{version.version_number}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-2">
                        <Badge variant="outline" className={versionStatusClass(version)}>
                          {version.status}
                        </Badge>
                        {version.is_current_draft && <Badge variant="outline">current draft</Badge>}
                        {version.is_current_published && <Badge variant="outline">live</Badge>}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDateTime(version.created_at)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDateTime(version.published_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Version Diff</CardTitle>
          <CardDescription>
            State, fact, transition, and tool-policy changes between two agent versions.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadingDiff ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Computing diff...
            </div>
          ) : !diff ? (
            <p className="text-sm text-muted-foreground">
              Select a source version to inspect changes against the current live or previous version.
            </p>
          ) : (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm">
                <GitCompare className="h-4 w-4 text-muted-foreground" />
                <span>
                  v{versions.find((version) => version.version_id === diff.source_version_id)?.version_number || '?'}
                  {' '}vs{' '}
                  v{versions.find((version) => version.version_id === diff.against_version_id)?.version_number || '?'}
                </span>
              </div>

              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">+{diff.summary.added_steps} states</Badge>
                <Badge variant="outline">-{diff.summary.removed_steps} states</Badge>
                <Badge variant="outline">~{diff.summary.changed_steps} states</Badge>
                <Badge variant="outline">+{diff.summary.added_facts} facts</Badge>
                <Badge variant="outline">~{diff.summary.changed_facts} facts</Badge>
                <Badge variant="outline">~{diff.summary.changed_transitions} transitions</Badge>
                <Badge variant="outline">~{diff.summary.changed_tool_bindings} tool bindings</Badge>
              </div>

              {diff.metadata_changes.length > 0 && (
                <div className="space-y-2">
                  <p className="text-sm font-medium">Metadata changes</p>
                  {diff.metadata_changes.map((change) => (
                    <div key={change.field} className="rounded-md border border-border p-3 text-sm">
                      <p className="font-medium">{change.field}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {JSON.stringify(change.before)} → {JSON.stringify(change.after)}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {diff.step_changes.length > 0 && (
                <div className="space-y-2">
                  <p className="text-sm font-medium">Changed states</p>
                  {diff.step_changes.slice(0, 8).map((change) => (
                    <div key={change.step_id} className="rounded-md border border-border p-3 text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <p className="font-medium">{change.after?.name || change.before?.name || change.step_id}</p>
                        <Badge variant="outline">{change.status}</Badge>
                      </div>
                      {change.changed_fields.length > 0 && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          Fields: {change.changed_fields.join(', ')}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
