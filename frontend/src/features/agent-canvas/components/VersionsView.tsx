import { useMemo, useState } from 'react'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
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
import { Loader2, RefreshCw, GitCompare, RotateCcw, ChevronDown, ChevronRight } from 'lucide-react'
import type { CanvasVersion, CanvasVersionDiffResponse } from '@/types/canvas'

interface VersionsViewProps {
  versions: CanvasVersion[]
  selectedVersionId?: string | null
  againstVersionId?: string | null
  diff?: CanvasVersionDiffResponse | null
  loadingVersions: boolean
  loadingDiff: boolean
  reverting: boolean
  onSelectVersion: (versionId: string) => void
  onSelectAgainstVersion: (versionId: string | null) => void
  onRefresh: () => void
  onRevert: (versionId: string, reason?: string) => void
}

function statusClass(status: string): string {
  if (status === 'published' || status === 'active') return 'border-blue-500/30 text-blue-300'
  if (status === 'draft') return 'border-gray-500/30 text-gray-400'
  if (status === 'archived') return 'border-slate-500/30 text-slate-300'
  return 'border-border text-muted-foreground'
}

function CollapsibleSection({
  title,
  defaultOpen = false,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-md border border-border">
      <button
        type="button"
        className="flex w-full items-center gap-2 p-2 text-left text-sm font-medium hover:bg-muted/50"
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        {title}
      </button>
      {open && <div className="border-t border-border p-2">{children}</div>}
    </div>
  )
}

export function VersionsView({
  versions,
  selectedVersionId,
  againstVersionId,
  diff,
  loadingVersions,
  loadingDiff,
  reverting,
  onSelectVersion,
  onSelectAgainstVersion,
  onRefresh,
  onRevert,
}: VersionsViewProps) {
  const [reason, setReason] = useState('')
  const selectedVersion = versions.find((version) => version.id === selectedVersionId) || null
  const hasAutoPrevious = !!selectedVersion && versions.some(
    (version) => version.version_number < selectedVersion.version_number
  )
  const compareOptions = useMemo(
    () => versions.filter((version) => version.id !== selectedVersionId),
    [versions, selectedVersionId]
  )

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Version operations</CardTitle>
          <CardDescription>
            Compare versions and create draft reverts.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-2 md:grid-cols-3">
            <Select
              value={selectedVersionId || 'none'}
              onValueChange={(value) => {
                if (value !== 'none') onSelectVersion(value)
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select source version" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none" disabled>
                  Select source version
                </SelectItem>
                {versions.map((version) => (
                  <SelectItem key={version.id} value={version.id}>
                    v{version.version_number} - {version.name}
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
                <SelectItem value="auto">Auto (previous version)</SelectItem>
                {compareOptions.map((version) => (
                  <SelectItem key={version.id} value={version.id}>
                    v{version.version_number} - {version.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Button variant="outline" onClick={onRefresh}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
          {selectedVersion && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Selected:</span>
              <span className="font-medium">
                v{selectedVersion.version_number} - {selectedVersion.name}
              </span>
              <Badge variant="outline" className={statusClass(selectedVersion.status)}>
                {selectedVersion.status}
              </Badge>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Version diff</CardTitle>
          <CardDescription>
            Metadata and workflow changes.
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
              {selectedVersion
                ? !hasAutoPrevious
                  ? "No previous version found. Choose an explicit 'Compare against' version."
                  : 'Select a source version to view diff.'
                : 'Select a source version to view diff.'}
            </p>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <GitCompare className="h-4 w-4 text-muted-foreground" />
                <p className="text-sm">
                  v{versions.find((v) => v.id === diff.source_canvas_version_id)?.version_number || '?'}
                  {' '}vs{' '}
                  v{versions.find((v) => v.id === diff.against_canvas_version_id)?.version_number || '?'}
                </p>
                {diff.cache_hit && (
                  <Badge variant="outline" className="border-blue-500/30 text-blue-300">
                    cached
                  </Badge>
                )}
              </div>
              {/* Summary badges */}
              <div className="flex flex-wrap items-center gap-2">
                {(diff.summary.added_nodes ?? 0) > 0 && (
                  <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
                    +{diff.summary.added_nodes} node{diff.summary.added_nodes !== 1 ? 's' : ''}
                  </Badge>
                )}
                {(diff.summary.removed_nodes ?? 0) > 0 && (
                  <Badge variant="outline" className="border-red-500/30 bg-red-500/10 text-red-400">
                    -{diff.summary.removed_nodes} node{diff.summary.removed_nodes !== 1 ? 's' : ''}
                  </Badge>
                )}
                {(diff.summary.changed_nodes ?? 0) > 0 && (
                  <Badge variant="outline" className="border-yellow-500/30 bg-yellow-500/10 text-yellow-300">
                    ~{diff.summary.changed_nodes} changed node{diff.summary.changed_nodes !== 1 ? 's' : ''}
                  </Badge>
                )}
                {(diff.summary.added_edges ?? 0) > 0 && (
                  <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
                    +{diff.summary.added_edges} edge{diff.summary.added_edges !== 1 ? 's' : ''}
                  </Badge>
                )}
                {(diff.summary.removed_edges ?? 0) > 0 && (
                  <Badge variant="outline" className="border-red-500/30 bg-red-500/10 text-red-400">
                    -{diff.summary.removed_edges} edge{diff.summary.removed_edges !== 1 ? 's' : ''}
                  </Badge>
                )}
                {(diff.summary.changed_edges ?? 0) > 0 && (
                  <Badge variant="outline" className="border-yellow-500/30 bg-yellow-500/10 text-yellow-300">
                    ~{diff.summary.changed_edges} changed edge{diff.summary.changed_edges !== 1 ? 's' : ''}
                  </Badge>
                )}
                {(diff.canvas_data_diff?.changed_keys?.length ?? 0) > 0 && (
                  <Badge variant="outline" className="border-slate-500/30 text-slate-300">
                    {(diff.canvas_data_diff.changed_keys?.length ?? 0)} canvas key{(diff.canvas_data_diff.changed_keys?.length ?? 0) !== 1 ? 's' : ''} changed
                  </Badge>
                )}
              </div>

              {/* Metadata diff */}
              {diff.metadata_diff?.changed_keys && diff.metadata_diff.changed_keys.length > 0 && (
                <CollapsibleSection title={`Metadata changes (${diff.metadata_diff.changed_keys.length})`} defaultOpen>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-xs">Field</TableHead>
                        <TableHead className="text-xs">From</TableHead>
                        <TableHead className="text-xs">To</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {diff.metadata_diff.changed_keys.map((entry: any, idx: number) => {
                        const key = typeof entry === 'object' ? Object.keys(entry)[0] : String(entry)
                        const change = typeof entry === 'object' ? entry[key] : null
                        return (
                          <TableRow key={idx}>
                            <TableCell className="font-mono text-xs">{key}</TableCell>
                            <TableCell className="text-xs text-red-400">
                              {change ? JSON.stringify(change.from) : 'n/a'}
                            </TableCell>
                            <TableCell className="text-xs text-emerald-400">
                              {change ? JSON.stringify(change.to) : 'n/a'}
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </CollapsibleSection>
              )}

              {/* Workflow diff - Changed Nodes */}
              {diff.workflow_diff?.changed_nodes && diff.workflow_diff.changed_nodes.length > 0 && (
                <CollapsibleSection title={`Changed nodes (${diff.workflow_diff.changed_nodes.length})`}>
                  <div className="space-y-2">
                    {diff.workflow_diff.changed_nodes.map((node: any, idx: number) => (
                      <CollapsibleSection key={idx} title={`Node ${node.id || idx}`}>
                        {node.changes && typeof node.changes === 'object' ? (
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="text-xs">Field</TableHead>
                                <TableHead className="text-xs">From</TableHead>
                                <TableHead className="text-xs">To</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {Object.entries(node.changes).map(([field, change]: [string, any]) => (
                                <TableRow key={field}>
                                  <TableCell className="font-mono text-xs">{field}</TableCell>
                                  <TableCell className="max-w-[200px] truncate text-xs text-red-400">
                                    {JSON.stringify(change?.from)}
                                  </TableCell>
                                  <TableCell className="max-w-[200px] truncate text-xs text-emerald-400">
                                    {JSON.stringify(change?.to)}
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        ) : (
                          <p className="text-xs text-muted-foreground">No field-level details available.</p>
                        )}
                      </CollapsibleSection>
                    ))}
                  </div>
                </CollapsibleSection>
              )}

              {/* Workflow diff - Added Nodes */}
              {diff.workflow_diff?.added_nodes && diff.workflow_diff.added_nodes.length > 0 && (
                <CollapsibleSection title={`Added nodes (${diff.workflow_diff.added_nodes.length})`}>
                  <ul className="space-y-1">
                    {diff.workflow_diff.added_nodes.map((node: any, idx: number) => (
                      <li key={idx} className="flex items-center gap-2 text-xs">
                        <Badge variant="outline" className="border-emerald-500/30 text-emerald-400">+</Badge>
                        <span className="font-mono">{node.node_type || 'unknown'}</span>
                        {node.label && <span className="text-muted-foreground">- {node.label}</span>}
                        {node.id && <span className="text-muted-foreground">({node.id})</span>}
                      </li>
                    ))}
                  </ul>
                </CollapsibleSection>
              )}

              {/* Workflow diff - Removed Nodes */}
              {diff.workflow_diff?.removed_nodes && diff.workflow_diff.removed_nodes.length > 0 && (
                <CollapsibleSection title={`Removed nodes (${diff.workflow_diff.removed_nodes.length})`}>
                  <ul className="space-y-1">
                    {diff.workflow_diff.removed_nodes.map((node: any, idx: number) => (
                      <li key={idx} className="flex items-center gap-2 text-xs">
                        <Badge variant="outline" className="border-red-500/30 text-red-400">-</Badge>
                        <span className="font-mono">{node.node_type || 'unknown'}</span>
                        {node.label && <span className="text-muted-foreground">- {node.label}</span>}
                        {node.id && <span className="text-muted-foreground">({node.id})</span>}
                      </li>
                    ))}
                  </ul>
                </CollapsibleSection>
              )}

              {/* Workflow diff - Added Edges */}
              {diff.workflow_diff?.added_edges && diff.workflow_diff.added_edges.length > 0 && (
                <CollapsibleSection title={`Added edges (${diff.workflow_diff.added_edges.length})`}>
                  <ul className="space-y-1">
                    {diff.workflow_diff.added_edges.map((edge: any, idx: number) => (
                      <li key={idx} className="flex items-center gap-2 text-xs">
                        <Badge variant="outline" className="border-emerald-500/30 text-emerald-400">+</Badge>
                        <span className="font-mono">{edge.source || '?'} &rarr; {edge.target || '?'}</span>
                        {edge.label && <span className="text-muted-foreground">({edge.label})</span>}
                      </li>
                    ))}
                  </ul>
                </CollapsibleSection>
              )}

              {/* Workflow diff - Removed Edges */}
              {diff.workflow_diff?.removed_edges && diff.workflow_diff.removed_edges.length > 0 && (
                <CollapsibleSection title={`Removed edges (${diff.workflow_diff.removed_edges.length})`}>
                  <ul className="space-y-1">
                    {diff.workflow_diff.removed_edges.map((edge: any, idx: number) => (
                      <li key={idx} className="flex items-center gap-2 text-xs">
                        <Badge variant="outline" className="border-red-500/30 text-red-400">-</Badge>
                        <span className="font-mono">{edge.source || '?'} &rarr; {edge.target || '?'}</span>
                        {edge.label && <span className="text-muted-foreground">({edge.label})</span>}
                      </li>
                    ))}
                  </ul>
                </CollapsibleSection>
              )}

              {/* Workflow diff - Changed Edges */}
              {diff.workflow_diff?.changed_edges && diff.workflow_diff.changed_edges.length > 0 && (
                <CollapsibleSection title={`Changed edges (${diff.workflow_diff.changed_edges.length})`}>
                  <div className="space-y-2">
                    {diff.workflow_diff.changed_edges.map((edge: any, idx: number) => (
                      <CollapsibleSection key={idx} title={`Edge ${edge.id || idx}`}>
                        {edge.changes && typeof edge.changes === 'object' ? (
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="text-xs">Field</TableHead>
                                <TableHead className="text-xs">From</TableHead>
                                <TableHead className="text-xs">To</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {Object.entries(edge.changes).map(([field, change]: [string, any]) => (
                                <TableRow key={field}>
                                  <TableCell className="font-mono text-xs">{field}</TableCell>
                                  <TableCell className="max-w-[200px] truncate text-xs text-red-400">
                                    {JSON.stringify(change?.from)}
                                  </TableCell>
                                  <TableCell className="max-w-[200px] truncate text-xs text-emerald-400">
                                    {JSON.stringify(change?.to)}
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        ) : (
                          <p className="text-xs text-muted-foreground">No field-level details available.</p>
                        )}
                      </CollapsibleSection>
                    ))}
                  </div>
                </CollapsibleSection>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Revert to draft</CardTitle>
          <CardDescription>
            Create a new draft version from the selected source version.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="revert-reason">Reason (optional)</Label>
            <Input
              id="revert-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Regression in canary rollout"
            />
          </div>
          <Button
            onClick={() => selectedVersionId && onRevert(selectedVersionId, reason || undefined)}
            disabled={!selectedVersionId || reverting}
          >
            {reverting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RotateCcw className="mr-2 h-4 w-4" />
            )}
            Create revert draft
          </Button>
        </CardContent>
      </Card>

      {loadingVersions && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading versions...
        </div>
      )}
    </div>
  )
}
