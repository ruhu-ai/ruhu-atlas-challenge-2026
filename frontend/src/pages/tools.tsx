/**
 * Custom APIs Page — Org-level custom API management
 *
 * Shows only `kind=custom_api` tools. Integration tools are managed
 * via Agent Canvas → Integrations tab. System tools are built-in.
 */

import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Textarea } from '@/components/atoms/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
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
  Wrench,
  MoreVertical,
  Trash2,
  Loader2,
  Search,
  X,
  CheckCircle2,
  XCircle,
  ExternalLink,
  Key,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  toolService,
  buildCustomToolMetadata,
  getCustomToolAciDraftWarnings,
  getCustomToolAciStatus,
  type ToolDefinition,
} from '@/api/services/tools.service'
import { apiClient } from '@/api/client'

// ── Helpers ────────────────────────────────────────────────────────────────

function MethodBadge({ method }: { method: string }) {
  const colors: Record<string, string> = {
    GET: 'text-emerald-400 border-emerald-500/30',
    POST: 'text-blue-400 border-blue-500/30',
    PUT: 'text-amber-400 border-amber-500/30',
    PATCH: 'text-orange-400 border-orange-500/30',
    DELETE: 'text-red-400 border-red-500/30',
  }
  return (
    <Badge variant="outline" className={`text-[10px] font-mono ${colors[method] || ''}`}>
      {method}
    </Badge>
  )
}

// ── Create/Edit Form ───────────────────────────────────────────────────────

interface ToolFormData {
  display_name: string
  tool_ref: string
  function_name: string
  description: string
  purpose: string
  use_when: string
  avoid_when: string
  base_url: string
  endpoint_path: string
  http_method: string
  auth_type: string
  auth_token: string
  read_only: boolean
}

const EMPTY_FORM: ToolFormData = {
  display_name: '',
  tool_ref: '',
  function_name: '',
  description: '',
  purpose: '',
  use_when: '',
  avoid_when: '',
  base_url: '',
  endpoint_path: '/',
  http_method: 'GET',
  auth_type: 'none',
  auth_token: '',
  read_only: false,
}

function deriveRef(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '')
}

// ── Main Component ─────────────────────────────────────────────────────────

export default function ToolsPage() {
  const queryClient = useQueryClient()
  const [searchQuery, setSearchQuery] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<ToolFormData>(EMPTY_FORM)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null)

  const { data: tools = [], isLoading } = useQuery({
    queryKey: ['tool-definitions', 'api'],
    queryFn: () => toolService.listDefinitions({ kind: 'api' }),
  })

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['tool-definitions'] })
  }, [queryClient])

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/tools/definitions/${id}`)
    },
    onSuccess: () => {
      invalidate()
      setIsDeleteDialogOpen(false)
      setDeleteTarget(null)
      toast.success('API deleted')
    },
    onError: () => toast.error('Failed to delete'),
  })

  const createMutation = useMutation({
    mutationFn: async (data: ToolFormData) => {
      // Create connection
      const connPayload: Record<string, unknown> = {
        display_name: data.display_name,
        provider: 'custom',
        auth_type: data.auth_type === 'none' ? 'none' : data.auth_type,
        base_url: data.base_url,
      }
      if (data.auth_type === 'bearer' && data.auth_token) {
        connPayload.credentials = { token: data.auth_token }
      } else if (data.auth_type === 'api_key' && data.auth_token) {
        connPayload.credentials = { api_key: data.auth_token }
      }
      const conn = await apiClient.post<{ connection_id: string }>('/api/tools/connections', connPayload)

      // Create tool definition
      const ref = data.tool_ref || deriveRef(data.display_name)
      await apiClient.post('/api/tools/definitions', {
        connection_id: conn.connection_id,
        kind: 'api',
        tool_ref: ref,
        function_name: data.function_name || ref,
        display_name: data.display_name,
        description: data.description || `Custom API: ${data.display_name}`,
        endpoint_path: data.endpoint_path || '/',
        http_method: data.http_method,
        read_only: data.read_only,
        metadata: buildCustomToolMetadata({
          display_name: data.display_name,
          description: data.description || `Custom API: ${data.display_name}`,
          http_method: data.http_method,
          endpoint_path: data.endpoint_path || '/',
          read_only: data.read_only,
          purpose: data.purpose,
          use_when: data.use_when,
          avoid_when: data.avoid_when,
        }),
      })
    },
    onSuccess: () => {
      invalidate()
      setShowForm(false)
      setForm(EMPTY_FORM)
      toast.success('Custom API created')
    },
    onError: () => toast.error('Failed to create API'),
  })

  const filteredTools = searchQuery
    ? tools.filter((t) =>
        t.display_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.tool_ref.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (t.function_name ?? '').toLowerCase().includes(searchQuery.toLowerCase())
      )
    : tools

  const aciWarnings = getCustomToolAciDraftWarnings({
    display_name: form.display_name,
    description: form.description,
    http_method: form.http_method,
    endpoint_path: form.endpoint_path,
    read_only: form.read_only,
    purpose: form.purpose,
    use_when: form.use_when,
    avoid_when: form.avoid_when,
  })

  const handleSave = () => {
    if (!form.display_name.trim()) {
      toast.error('Name is required')
      return
    }
    if (!form.base_url.trim()) {
      toast.error('Base URL is required')
      return
    }
    createMutation.mutate(form)
  }

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Custom APIs</h1>
            <p className="text-muted-foreground">
              Your organization's backend APIs. Available to all agents in the code editor's APIs tab.
            </p>
          </div>
          <Button onClick={() => { setForm(EMPTY_FORM); setShowForm(true) }}>
            <Plus className="mr-2 h-4 w-4" />
            Add Custom API
          </Button>
        </div>

        {/* Search */}
        {tools.length > 5 && (
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Filter APIs..."
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

        {/* Tool List */}
        <Card>
          <CardContent className="p-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : tools.length === 0 && !showForm ? (
              <div className="flex flex-col items-center justify-center py-16">
                <Wrench className="h-12 w-12 text-muted-foreground mb-4" />
                <h3 className="font-medium mb-1">No custom APIs yet</h3>
                <p className="text-sm text-muted-foreground mb-4 text-center max-w-md">
                  Add your backend APIs so agents can call them from action state code.
                  Each API creates a callable function like <code className="text-xs bg-muted px-1 rounded">await verify_identity(customer_id)</code>.
                </p>
                <Button onClick={() => { setForm(EMPTY_FORM); setShowForm(true) }}>
                  <Plus className="mr-2 h-4 w-4" />
                  Add Custom API
                </Button>
              </div>
            ) : (
              <div className="divide-y divide-border">
                {filteredTools.map((tool) => (
                  <div
                    key={tool.tool_definition_id}
                    className="flex items-center gap-4 px-6 py-4 hover:bg-muted/50 transition-colors"
                  >
                    <div className="shrink-0 w-9 h-9 rounded-lg bg-muted flex items-center justify-center">
                      <Wrench className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <h3 className="font-medium truncate">{tool.display_name}</h3>
                        <MethodBadge method={tool.http_method} />
                        {tool.enabled ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                        ) : (
                          <XCircle className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                        )}
                        {tool.read_only && (
                          <Badge variant="outline" className="text-[10px]">read-only</Badge>
                        )}
                        <Badge
                          variant="outline"
                          className={`text-[10px] ${
                            getCustomToolAciStatus(tool.metadata).variant === 'authored'
                              ? 'text-emerald-400 border-emerald-500/30'
                              : 'text-amber-400 border-amber-500/30'
                          }`}
                        >
                          {getCustomToolAciStatus(tool.metadata).label}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span className="font-mono">{tool.function_name || tool.tool_ref}</span>
                        {tool.endpoint_path && (
                          <>
                            <span>&middot;</span>
                            <span className="truncate">{tool.endpoint_path}</span>
                          </>
                        )}
                      </div>
                    </div>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-8 w-8 p-0">
                          <MoreVertical className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() => {
                            setDeleteTarget({ id: tool.tool_definition_id, name: tool.display_name })
                            setIsDeleteDialogOpen(true)
                          }}
                          className="text-destructive focus:text-destructive"
                        >
                          <Trash2 className="h-4 w-4 mr-2" />
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                ))}

                {filteredTools.length === 0 && searchQuery && (
                  <div className="flex flex-col items-center py-12 text-muted-foreground">
                    <p className="text-sm">No APIs match &ldquo;{searchQuery}&rdquo;</p>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Tip */}
        <p className="text-xs text-muted-foreground">
          Custom APIs are available org-wide. Manage per-agent integrations via{' '}
          <a href="/agents" className="text-primary hover:underline inline-flex items-center gap-1">
            Agent Canvas &rarr; Integrations
            <ExternalLink className="h-3 w-3" />
          </a>
        </p>
      </div>

      {/* Create Form Dialog */}
      <Dialog open={showForm} onOpenChange={setShowForm}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Add Custom API</DialogTitle>
            <DialogDescription>
              Configure an HTTP endpoint your agents can call from action state code.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Display Name</Label>
                <Input
                  value={form.display_name}
                  onChange={(e) => setForm({ ...form, display_name: e.target.value, tool_ref: deriveRef(e.target.value), function_name: deriveRef(e.target.value) })}
                  placeholder="e.g. Billing API"
                  className="h-8 text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Function Name</Label>
                <Input
                  value={form.function_name}
                  onChange={(e) => setForm({ ...form, function_name: e.target.value })}
                  placeholder="e.g. verify_identity"
                  className="h-8 text-sm font-mono"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Base URL</Label>
              <Input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.example.com"
                className="h-8 text-sm"
              />
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Method</Label>
                <Select value={form.http_method} onValueChange={(v) => setForm({ ...form, http_method: v, read_only: v === 'GET' })}>
                  <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="GET">GET</SelectItem>
                    <SelectItem value="POST">POST</SelectItem>
                    <SelectItem value="PUT">PUT</SelectItem>
                    <SelectItem value="PATCH">PATCH</SelectItem>
                    <SelectItem value="DELETE">DELETE</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Endpoint Path</Label>
                <Input
                  value={form.endpoint_path}
                  onChange={(e) => setForm({ ...form, endpoint_path: e.target.value })}
                  placeholder="/v2/verify"
                  className="h-8 text-sm font-mono"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Auth</Label>
                <Select value={form.auth_type} onValueChange={(v) => setForm({ ...form, auth_type: v })}>
                  <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None</SelectItem>
                    <SelectItem value="bearer">Bearer Token</SelectItem>
                    <SelectItem value="api_key">API Key</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            {form.auth_type !== 'none' && (
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">
                  <Key className="inline h-3 w-3 mr-1" />
                  {form.auth_type === 'bearer' ? 'Bearer Token' : 'API Key'}
                </Label>
                <Input
                  type="password"
                  value={form.auth_token}
                  onChange={(e) => setForm({ ...form, auth_token: e.target.value })}
                  placeholder="Enter token..."
                  className="h-8 text-sm"
                />
              </div>
            )}
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Description</Label>
              <Input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="What this API does (min 20 characters)"
                className="h-8 text-sm"
              />
            </div>
            <div className="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-3">
              <div>
                <Label className="text-xs text-muted-foreground">Model guidance</Label>
                <p className="text-xs text-muted-foreground mt-1">
                  Optional. Leave these blank and Ruhu will scaffold guidance for the model from the API shape.
                </p>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Purpose</Label>
                <Textarea
                  value={form.purpose}
                  onChange={(e) => setForm({ ...form, purpose: e.target.value })}
                  placeholder="What should this tool help the agent accomplish?"
                  className="min-h-[72px] text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Use when</Label>
                <Textarea
                  value={form.use_when}
                  onChange={(e) => setForm({ ...form, use_when: e.target.value })}
                  placeholder="When should the model prefer this API?"
                  className="min-h-[72px] text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Avoid when</Label>
                <Textarea
                  value={form.avoid_when}
                  onChange={(e) => setForm({ ...form, avoid_when: e.target.value })}
                  placeholder="Optional warning about when not to use this API."
                  className="min-h-[72px] text-sm"
                />
              </div>
            </div>
            {aciWarnings.length > 0 && (
              <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
                <p className="text-xs font-medium text-amber-300">ACI guidance</p>
                <ul className="mt-2 space-y-1 text-xs text-amber-100/90">
                  {aciWarnings.map((warning) => (
                    <li key={warning.code}>- {warning.message}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowForm(false)}>Cancel</Button>
            <Button onClick={handleSave} disabled={createMutation.isPending}>
              {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Create API
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Custom API</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete &ldquo;{deleteTarget?.name}&rdquo;?
              Agents using this API in their action states will be affected.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setIsDeleteDialogOpen(false); setDeleteTarget(null) }}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
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
