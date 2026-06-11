/**
 * Agent Integrations Tab
 *
 * Category-based integrations layout:
 * - Brand-colored icon boxes per provider
 * - CRM / Calendar / Ticketing sections
 * - Connected provider card (border-white/10 bg-card/50)
 * - Available provider buttons (border-white/10 bg-card/30 hover:bg-card/50)
 * - Radix Dialog connect card with capabilities preview + security note + brand-color button
 */

import { useState, useMemo, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  AlertCircle,
  Loader2,
  ExternalLink,
  X,
  Plus,
  Key,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Globe,
} from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { Card, CardContent } from '@/components/atoms/card'
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
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/atoms/alert-dialog'
import {
  toolService,
  apiConnectionService,
  buildCustomToolMetadata,
  getCustomToolAciDraftWarnings,
  getCustomToolAciStatus,
  type ToolDefinition,
  type ProviderTemplate,
} from '@/api/services/tools.service'
import { apiClient } from '@/api/client'
import { toast } from 'sonner'

// ── Provider brand colors ──────────────────────────────────────────────────

const PROVIDER_COLOR: Record<string, string> = {
  hubspot: '#FF7A59',
  salesforce: '#00A1E0',
  zoho: '#E42527',
  pipedrive: '#25292C',
  google_calendar: '#4285F4',
  microsoft_calendar: '#0078D4',
  zendesk: '#03363D',
  freshdesk: '#25C16F',
  jira: '#0052CC',
  generic_oauth: '#6366F1',
}

function getProviderColor(slug: string): string {
  return PROVIDER_COLOR[slug] ?? '#6366F1'
}

// ── Types ──────────────────────────────────────────────────────────────────

interface APIConnectionSimple {
  connection_id: string
  organization_id: string
  display_name: string
  provider: string
  auth_type: string
  base_url: string | null
  status: string
  error_message: string | null
  has_credentials: boolean
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

interface AgentIntegrationsTabProps {
  agentId: string
  agentName: string
}

type CategoryType = 'crm' | 'calendar' | 'ticketing'

const CATEGORY_META: Record<CategoryType, { title: string; description: string }> = {
  crm: {
    title: 'CRM',
    description: 'Look up customers and log activities',
  },
  calendar: {
    title: 'Calendar',
    description: 'Check availability and book appointments',
  },
  ticketing: {
    title: 'Ticketing',
    description: 'Create and update support tickets',
  },
}

// ── Status Badge ───────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  if (status === 'active') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-500/10 text-emerald-400 text-xs">
        <Check className="h-3 w-3" />
        Connected
      </span>
    )
  }
  if (status === 'error') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-amber-500/10 text-amber-400 text-xs">
        <AlertCircle className="h-3 w-3" />
        Needs attention
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-gray-500/10 text-gray-400 text-xs">
      <X className="h-3 w-3" />
      Disconnected
    </span>
  )
}

// ── Connected Provider Card ────────────────────────────────────────────────

function ConnectedProviderCard({
  connection,
  template,
  tools,
  onDisconnect,
}: {
  connection: APIConnectionSimple
  template: ProviderTemplate | undefined
  tools: ToolDefinition[]
  onDisconnect: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [isTesting, setIsTesting] = useState(false)

  const color = getProviderColor(connection.provider)

  const handleTest = async () => {
    setIsTesting(true)
    try {
      const result = await apiConnectionService.healthCheck(connection.connection_id)
      if (result.healthy) {
        toast.success('Connection is healthy!')
      } else {
        toast.error(result.error_message || 'Connection test failed')
      }
    } catch {
      toast.error('Failed to test connection')
    } finally {
      setIsTesting(false)
    }
  }

  const toolNames = tools.map((t) => t.function_name || t.tool_ref).filter(Boolean)

  return (
    <Card className="border-white/10 bg-card/50">
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div
              className="flex h-10 w-10 items-center justify-center rounded-lg text-xl"
              style={{ backgroundColor: `${color}20` }}
            >
              {template?.icon || '🔌'}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h4 className="font-medium">{connection.display_name}</h4>
                <StatusBadge status={connection.status} />
              </div>
              {toolNames.length > 0 && (
                <p className="text-xs text-muted-foreground mt-0.5">
                  Tools: {toolNames.join(' · ')}
                </p>
              )}
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setExpanded(!expanded)}>
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </div>

        {expanded && (
          <div className="mt-4 pt-4 border-t border-white/10 space-y-4">
            {template?.capabilities && template.capabilities.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground mb-2">Your agent can:</p>
                <div className="grid grid-cols-2 gap-2">
                  {template.capabilities.map((cap) => (
                    <div key={cap} className="flex items-center gap-2 text-sm">
                      <Check className="h-3 w-3 text-emerald-400" />
                      {cap}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={handleTest} disabled={isTesting}>
                {isTesting ? (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                ) : (
                  <RefreshCw className="h-3 w-3 mr-1" />
                )}
                Test
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onDisconnect}
                className="text-destructive hover:text-destructive"
              >
                <X className="h-3 w-3 mr-1" />
                Disconnect
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Available Provider Button ──────────────────────────────────────────────

function ProviderButton({
  template,
  isConnecting,
  onClick,
}: {
  template: ProviderTemplate
  isConnecting: boolean
  onClick: () => void
}) {
  const color = getProviderColor(template.slug)
  return (
    <button
      onClick={onClick}
      disabled={isConnecting}
      className="flex flex-col items-center gap-2 p-4 rounded-lg border border-white/10 bg-card/30 hover:bg-card/50 hover:border-white/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <div
        className="flex h-12 w-12 items-center justify-center rounded-lg text-2xl"
        style={{ backgroundColor: `${color}20` }}
      >
        {template.icon}
      </div>
      <span className="text-sm font-medium">{template.display_name}</span>
      {isConnecting ? (
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      ) : (
        <span className="text-xs text-muted-foreground">Connect</span>
      )}
    </button>
  )
}

// ── Integration Category Section ───────────────────────────────────────────

function IntegrationCategory({
  category,
  templates,
  connections,
  toolsByConnection,
  connectingSlug,
  onConnect,
  onDisconnect,
}: {
  category: CategoryType
  templates: ProviderTemplate[]
  connections: APIConnectionSimple[]
  toolsByConnection: Record<string, ToolDefinition[]>
  connectingSlug: string | null
  onConnect: (template: ProviderTemplate) => void
  onDisconnect: (conn: APIConnectionSimple) => void
}) {
  const { title, description } = CATEGORY_META[category]

  const connectedSlugs = new Set(connections.map((c) => c.provider))
  const connectedConn = connections.find((c) => templates.some((t) => t.slug === c.provider))
  const connectedTemplate = connectedConn
    ? templates.find((t) => t.slug === connectedConn.provider)
    : undefined

  const availableTemplates = templates.filter((t) => !connectedSlugs.has(t.slug))

  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-medium">{title}</h3>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>

      {connectedConn ? (
        <>
          <ConnectedProviderCard
            connection={connectedConn}
            template={connectedTemplate}
            tools={toolsByConnection[connectedConn.connection_id] ?? []}
            onDisconnect={() => onDisconnect(connectedConn)}
          />
          {availableTemplates.length > 0 && (
            <div className="pl-4 border-l-2 border-white/10">
              <p className="text-xs text-muted-foreground mb-2">Switch to another provider:</p>
              <div className="flex flex-wrap gap-2">
                {availableTemplates.slice(0, 3).map((tmpl) => (
                  <button
                    key={tmpl.slug}
                    onClick={() => onConnect(tmpl)}
                    disabled={connectingSlug !== null}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-white/10 text-xs hover:bg-white/5 disabled:opacity-50"
                  >
                    <span>{tmpl.icon}</span>
                    {tmpl.display_name}
                  </button>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <Card className="border-dashed border-white/10 bg-transparent">
          <CardContent className="p-4">
            <p className="text-sm text-muted-foreground mb-4">
              Connect {title.toLowerCase()} to let your agent {description.toLowerCase()}
            </p>
            <div className="flex flex-wrap gap-3">
              {templates.map((tmpl) => (
                <ProviderButton
                  key={tmpl.slug}
                  template={tmpl}
                  isConnecting={connectingSlug === tmpl.slug}
                  onClick={() => onConnect(tmpl)}
                />
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

// ── Custom API Hosts Section ───────────────────────────────────────────────
//
// Hosts ≠ Endpoints. This section manages only the *credentials* layer
// for authoring custom APIs:
//
//   - display_name + base_url + auth_type + token
//   - ``api_connection`` row with ``provider='custom'``
//
// The endpoint definitions (HTTP method, path, ACI metadata) that
// reference these hosts live in the Library tab (kind=custom_api
// ``tool_definition`` rows). Splitting them this way keeps Connections
// purely auth and Tools purely callable definitions — and lets a single host
// back many endpoints without
// re-entering credentials each time.

function CustomAPIHostsSection({
  hosts,
  isLoading,
  onDisconnect,
}: {
  hosts: APIConnectionSimple[]
  isLoading: boolean
  onDisconnect: (connection: APIConnectionSimple) => void
}) {
  const queryClient = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    name: '',
    base_url: '',
    auth_type: 'none',
    auth_token: '',
  })

  const resetForm = () => {
    setForm({
      name: '',
      base_url: '',
      auth_type: 'none',
      auth_token: '',
    })
    setShowForm(false)
  }

  const handleSave = async () => {
    if (!form.name.trim() || !form.base_url.trim()) {
      toast.error('Name and Base URL are required')
      return
    }
    try {
      const connPayload: Record<string, unknown> = {
        display_name: form.name,
        provider: 'custom',
        auth_type: form.auth_type,
        base_url: form.base_url,
      }
      if (form.auth_type === 'bearer' && form.auth_token) {
        connPayload.credentials = { token: form.auth_token }
      } else if (form.auth_type === 'api_key' && form.auth_token) {
        connPayload.credentials = { api_key: form.auth_token }
      }
      await apiClient.post('/api/tools/connections', connPayload)
      queryClient.invalidateQueries({ queryKey: ['tools', 'connections'] })
      toast.success(`Custom API host "${form.name}" added`)
      resetForm()
    } catch {
      toast.error('Failed to add custom API host')
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-medium">Custom API hosts</h3>
          <p className="text-sm text-muted-foreground">
            Register an external API host (base URL + credentials). Author the
            individual endpoints — method, path, schema — in the Library tab.
          </p>
        </div>
        {!showForm && (
          <Button variant="outline" size="sm" onClick={() => setShowForm(true)}>
            <Plus className="h-4 w-4 mr-1" />
            Add host
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading hosts...
        </div>
      ) : hosts.length === 0 && !showForm ? (
        <div className="rounded-lg border border-dashed border-white/10 bg-transparent px-4 py-6 text-center">
          <Globe className="mx-auto h-7 w-7 text-muted-foreground/40 mb-2" />
          <p className="text-sm text-muted-foreground">No custom API hosts added yet.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {hosts.map((host) => (
            <Card key={host.connection_id} className="border-white/10 bg-card/40">
              <CardContent className="p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium truncate">
                        {host.display_name || host.provider}
                      </span>
                      <span className="text-[10px] uppercase tracking-wide text-emerald-400">
                        {host.status === 'active' ? 'connected' : host.status}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <span className="text-xs text-muted-foreground font-mono truncate">
                        {host.base_url || '—'}
                      </span>
                      {host.auth_type && (
                        <code className="text-[10px] px-1.5 py-0.5 bg-muted rounded font-mono">
                          {host.auth_type}
                        </code>
                      )}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-xs text-muted-foreground hover:text-destructive"
                    onClick={() => onDisconnect(host)}
                  >
                    <X className="h-3 w-3 mr-1" />
                    Remove
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showForm && (
        <Card className="border-border">
          <CardContent className="p-4 space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs font-medium text-muted-foreground">Name *</Label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Banking API"
                  className="h-9 text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs font-medium text-muted-foreground">Base URL *</Label>
                <Input
                  value={form.base_url}
                  onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  placeholder="https://api.example.com/v1"
                  className="h-9 text-sm font-mono"
                />
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs font-medium text-muted-foreground">Authentication</Label>
                <Select value={form.auth_type} onValueChange={(v) => setForm({ ...form, auth_type: v })}>
                  <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None</SelectItem>
                    <SelectItem value="bearer">Bearer Token</SelectItem>
                    <SelectItem value="api_key">API Key</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.auth_type !== 'none' && (
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium text-muted-foreground">
                    <Key className="inline h-3 w-3 mr-1" />
                    {form.auth_type === 'bearer' ? 'Bearer Token' : 'API Key'}
                  </Label>
                  <Input
                    type="password"
                    value={form.auth_token}
                    onChange={(e) => setForm({ ...form, auth_token: e.target.value })}
                    placeholder="Enter credential"
                    className="h-9 text-sm"
                  />
                </div>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              After saving the host, switch to the Library tab and use{' '}
              <strong>+ New API</strong> to author method + path + schema for
              specific endpoints on this host.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={resetForm}>Cancel</Button>
              <Button size="sm" onClick={handleSave} disabled={!form.name.trim() || !form.base_url.trim()}>
                <Check className="h-4 w-4 mr-1" />
                Save host
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────

export function AgentIntegrationsTab({ agentId, agentName: _agentName }: AgentIntegrationsTabProps) {
  const queryClient = useQueryClient()
  const [connectingSlug, setConnectingSlug] = useState<string | null>(null)
  // Simple OAuth providers — show capabilities dialog first
  const [selectedForConfirm, setSelectedForConfirm] = useState<ProviderTemplate | null>(null)
  // Providers that need config (subdomain, instance, custom URLs)
  const [configTemplate, setConfigTemplate] = useState<ProviderTemplate | null>(null)
  const [configValues, setConfigValues] = useState<Record<string, string>>({})
  const [customUrls, setCustomUrls] = useState({ display_name: '', auth_url: '', token_url: '', base_url: '' })
  const [disconnectTarget, setDisconnectTarget] = useState<APIConnectionSimple | null>(null)
  // OAuth popup window reference (popup+postMessage flow)
  const oauthPopupRef = useRef<Window | null>(null)
  const oauthDoneRef = useRef(false)

  // Fetch real connections
  const { data: connectionsData, isLoading: loadingConnections } = useQuery({
    queryKey: ['tools', 'connections'],
    queryFn: async () => {
      const response = await apiClient.get<{ items: APIConnectionSimple[] }>('/api/tools/connections')
      return response.items ?? []
    },
    enabled: !!agentId && agentId !== 'new',
  })

  // Fetch integration tools
  const { data: integrationTools } = useQuery({
    queryKey: ['tools', 'definitions', 'integration'],
    queryFn: () => toolService.listDefinitions({ kind: 'integration' }),
    enabled: !!agentId && agentId !== 'new',
  })

  // Fetch provider templates
  const { data: templates } = useQuery({
    queryKey: ['tools', 'provider-templates'],
    queryFn: () => toolService.listProviderTemplates(),
  })

  const connections = connectionsData ?? []
  const integrationConnections = connections.filter((c) => c.provider !== 'custom')
  const customHosts = connections.filter((c) => c.provider === 'custom')

  // Group tools by connection_id
  const toolsByConnection = useMemo(() => {
    const map: Record<string, ToolDefinition[]> = {}
    for (const tool of integrationTools ?? []) {
      const connId = tool.connection_id ?? '__none__'
      if (!map[connId]) map[connId] = []
      map[connId].push(tool)
    }
    return map
  }, [integrationTools])

  // Group templates by category
  const templatesByCategory = useMemo(() => {
    const map: Partial<Record<CategoryType, ProviderTemplate[]>> = {}
    for (const tmpl of templates ?? []) {
      const cat = tmpl.category as CategoryType
      if (!map[cat]) map[cat] = []
      map[cat]!.push(tmpl)
    }
    return map
  }, [templates])

  // Setup provider mutation
  const setupMutation = useMutation({
    mutationFn: (args: { slug: string; data?: Record<string, unknown> }) =>
      toolService.setupProvider(args.slug, args.data),
    onSuccess: (result) => {
      setConnectingSlug(null)
      setSelectedForConfirm(null)
      setConfigTemplate(null)
      if (result.oauth_start_url) {
        // Open OAuth consent screen in a popup — callback page sends code back via postMessage
        const width = 600
        const height = 700
        const left = Math.round(window.screenX + (window.outerWidth - width) / 2)
        const top = Math.round(window.screenY + (window.outerHeight - height) / 2)
        oauthDoneRef.current = false
        oauthPopupRef.current = window.open(
          result.oauth_start_url,
          'ruhu_oauth_popup',
          `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`,
        )
        if (!oauthPopupRef.current) {
          toast.error('Popup was blocked — please allow popups for this site and try again.')
        }
      } else {
        queryClient.invalidateQueries({ queryKey: ['tools'] })
        toast.success(`${result.tools_created ?? 0} tools created`)
      }
    },
    onError: (err: unknown) => {
      setConnectingSlug(null)
      toast.error(err instanceof Error ? err.message : 'Failed to connect provider')
    },
  })

  // Listen for postMessage from the OAuth popup callback page
  useEffect(() => {
    const handleMessage = async (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return
      if (event.data?.type !== 'oauth_callback') return
      if (oauthDoneRef.current) return
      oauthDoneRef.current = true

      oauthPopupRef.current = null

      if (event.data.error) {
        toast.error(event.data.error_description || 'Authorization was cancelled or failed.')
        return
      }

      try {
        await apiClient.post('/api/tools/oauth/exchange', {
          code: event.data.code,
          state: event.data.state,
        })
        queryClient.invalidateQueries({ queryKey: ['tools', 'connections'] })
        queryClient.invalidateQueries({ queryKey: ['tools', 'definitions'] })
        toast.success('Integration connected!')
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to complete authorization')
      }
    }

    window.addEventListener('message', handleMessage)

    // Detect if the user closed the popup without completing OAuth.
    // We listen for focus returning to the parent window instead of polling
    // popup.closed, which triggers Cross-Origin-Opener-Policy warnings in
    // Chrome when the popup has visited a COOP-protected page (e.g. Google).
    const handleFocus = () => {
      if (!oauthPopupRef.current || oauthDoneRef.current) return
      // Brief delay so any in-flight postMessage can arrive before we give up.
      setTimeout(() => {
        if (oauthPopupRef.current && !oauthDoneRef.current) {
          oauthDoneRef.current = true
          oauthPopupRef.current = null
          setConnectingSlug(null)
        }
      }, 400)
    }
    window.addEventListener('focus', handleFocus)

    return () => {
      window.removeEventListener('message', handleMessage)
      window.removeEventListener('focus', handleFocus)
    }
  }, [queryClient])

  // Cascade disconnect: delete tool definitions first, then the connection
  const disconnectMutation = useMutation({
    mutationFn: async (connectionId: string) => {
      const defs = await apiClient.get<{ items: Array<{ tool_definition_id: string }> }>(
        `/api/tools/definitions?connection_id=${encodeURIComponent(connectionId)}&enabled_only=false`
      )
      const items = defs.items ?? []
      await Promise.all(
        items.map((d) => apiClient.delete(`/api/tools/definitions/${d.tool_definition_id}`))
      )
      await apiClient.delete(`/api/tools/connections/${connectionId}`)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tools', 'connections'] })
      queryClient.invalidateQueries({ queryKey: ['tools', 'definitions'] })
      toast.success('Integration disconnected')
      setDisconnectTarget(null)
    },
    onError: () => {
      toast.error('Failed to disconnect integration.')
    },
  })

  const handleConnect = (template: ProviderTemplate) => {
    const urlPlaceholders = [...(template.base_url ?? '').matchAll(/\{([^}]+)\}/g)].map((m) => m[1])
    const effectiveRequiredConfig =
      (template.required_config ?? []).length > 0 ? template.required_config : urlPlaceholders

    if (!template.requires_custom_urls && effectiveRequiredConfig.length === 0) {
      // No config needed — show capabilities confirm dialog
      setSelectedForConfirm(template)
    } else {
      // Config needed — show config dialog
      setConfigValues({})
      setCustomUrls({ display_name: template.display_name, auth_url: '', token_url: '', base_url: template.base_url || '' })
      setConfigTemplate({ ...template, required_config: effectiveRequiredConfig })
    }
  }

  const handleConfirmConnect = () => {
    if (!selectedForConfirm) return
    setConnectingSlug(selectedForConfirm.slug)
    setupMutation.mutate({ slug: selectedForConfirm.slug })
  }

  const handleConfigSubmit = () => {
    if (!configTemplate) return
    if (configTemplate.requires_custom_urls) {
      setupMutation.mutate({
        slug: configTemplate.slug,
        data: {
          display_name: customUrls.display_name || undefined,
          base_url: customUrls.base_url || undefined,
          auth_url_override: customUrls.auth_url || undefined,
          token_url_override: customUrls.token_url || undefined,
        },
      })
    } else {
      setupMutation.mutate({ slug: configTemplate.slug, data: { template_config: configValues } })
    }
  }

  const configSubmitDisabled =
    setupMutation.isPending ||
    (configTemplate?.requires_custom_urls
      ? !customUrls.auth_url || !customUrls.token_url || !customUrls.base_url
      : (configTemplate?.required_config ?? []).some((k) => !configValues[k]))

  if (loadingConnections) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-xl font-semibold">Connect Your Tools</h2>
        <p className="text-muted-foreground mt-1">
          Let your agent access customer data, book appointments, and create tickets
        </p>
      </div>

      {/* Category sections */}
      <div className="space-y-8">
        {(['crm', 'calendar', 'ticketing'] as CategoryType[]).map((cat) => {
          const catTemplates = templatesByCategory[cat]
          if (!catTemplates || catTemplates.length === 0) return null
          const catConnections = integrationConnections.filter((c) =>
            catTemplates.some((t) => t.slug === c.provider)
          )
          return (
            <IntegrationCategory
              key={cat}
              category={cat}
              templates={catTemplates}
              connections={catConnections}
              toolsByConnection={toolsByConnection}
              connectingSlug={connectingSlug}
              onConnect={handleConnect}
              onDisconnect={(conn) => setDisconnectTarget(conn)}
            />
          )
        })}
      </div>

      {/* Custom API hosts — credentials only. Endpoint authoring lives
           in the Library tab as kind=custom_api tool definitions. */}
      <CustomAPIHostsSection
        hosts={customHosts}
        isLoading={loadingConnections}
        onDisconnect={(conn) => setDisconnectTarget(conn)}
      />

      {/* Footer */}
      <div className="pt-4 border-t border-white/10">
        <p className="text-sm text-muted-foreground">
          Manage tools and API connections for your organization on the{' '}
          <a href="/tools" className="text-primary hover:underline inline-flex items-center gap-1">
            Tools &amp; APIs page
            <ExternalLink className="h-3 w-3" />
          </a>
        </p>
      </div>

      {/* ── Simple OAuth Confirm Dialog ── */}
      <Dialog
        open={!!selectedForConfirm}
        onOpenChange={(open) => { if (!open) setSelectedForConfirm(null) }}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <span className="text-2xl">{selectedForConfirm?.icon}</span>
              Connect to {selectedForConfirm?.display_name}
            </DialogTitle>
            <DialogDescription>
              You'll be redirected to {selectedForConfirm?.display_name} to sign in. We'll
              automatically set up everything your agent needs.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            {selectedForConfirm?.capabilities && selectedForConfirm.capabilities.length > 0 && (
              <div className="rounded-lg bg-muted/50 p-4">
                <p className="text-sm font-medium mb-2">Your agent will be able to:</p>
                <ul className="space-y-2">
                  {selectedForConfirm.capabilities.map((cap) => (
                    <li key={cap} className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Check className="h-4 w-4 text-emerald-400 shrink-0" />
                      {cap}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500/20">
                <Check className="h-2.5 w-2.5 text-emerald-400" />
              </span>
              Your credentials are never stored on our servers
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setSelectedForConfirm(null)}>
              Cancel
            </Button>
            <Button
              onClick={handleConfirmConnect}
              disabled={setupMutation.isPending}
              style={{ backgroundColor: selectedForConfirm ? getProviderColor(selectedForConfirm.slug) : undefined }}
              className="text-white"
            >
              {setupMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Connecting…
                </>
              ) : (
                <>Continue with {selectedForConfirm?.display_name}</>
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Config Dialog (subdomain / instance / custom URLs) ── */}
      <Dialog
        open={!!configTemplate}
        onOpenChange={(open) => { if (!open) setConfigTemplate(null) }}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <span className="text-2xl">{configTemplate?.icon}</span>
              Connect to {configTemplate?.display_name}
            </DialogTitle>
            <DialogDescription>
              {configTemplate?.requires_custom_urls
                ? 'Enter your OAuth endpoints and API base URL.'
                : `Enter your ${configTemplate?.required_config.join(', ')} to continue.`}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {/* Config fields */}
            {configTemplate?.requires_custom_urls ? (
              <div className="space-y-3">
                {[
                  { label: 'Connection name', key: 'display_name', type: 'text', placeholder: 'My Custom Provider' },
                  { label: 'Authorization URL', key: 'auth_url', type: 'url', placeholder: 'https://auth.example.com/oauth/authorize' },
                  { label: 'Token URL', key: 'token_url', type: 'url', placeholder: 'https://auth.example.com/oauth/token' },
                  { label: 'API base URL', key: 'base_url', type: 'url', placeholder: 'https://api.example.com/v1' },
                ].map(({ label, key, type, placeholder }) => (
                  <div key={key}>
                    <label className="text-sm font-medium">{label}</label>
                    <input
                      type={type}
                      value={customUrls[key as keyof typeof customUrls]}
                      onChange={(e) => setCustomUrls({ ...customUrls, [key]: e.target.value })}
                      placeholder={placeholder}
                      className="mt-1.5 w-full rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/30"
                    />
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                {(configTemplate?.required_config ?? []).map((key) => (
                  <div key={key}>
                    <label className="text-sm font-medium capitalize">{key}</label>
                    <input
                      type="text"
                      value={configValues[key] || ''}
                      onChange={(e) => setConfigValues({ ...configValues, [key]: e.target.value })}
                      placeholder={
                        key === 'subdomain'
                          ? 'e.g. acme (for acme.zendesk.com)'
                          : key === 'instance'
                            ? 'e.g. mycompany (for mycompany.salesforce.com)'
                            : key
                      }
                      className="mt-1.5 w-full rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                      autoFocus
                    />
                    {key === 'subdomain' && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Just the subdomain — not the full URL.
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Capabilities */}
            {configTemplate?.capabilities && configTemplate.capabilities.length > 0 && (
              <div className="rounded-lg bg-muted/50 p-4">
                <p className="text-sm font-medium mb-2">Your agent will be able to:</p>
                <ul className="space-y-2">
                  {configTemplate.capabilities.map((cap) => (
                    <li key={cap} className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Check className="h-4 w-4 text-emerald-400 shrink-0" />
                      {cap}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Security note */}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500/20">
                <Check className="h-2.5 w-2.5 text-emerald-400" />
              </span>
              Your credentials are never stored on our servers
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setConfigTemplate(null)}>
              Cancel
            </Button>
            <Button
              onClick={handleConfigSubmit}
              disabled={configSubmitDisabled}
              style={{ backgroundColor: configTemplate ? getProviderColor(configTemplate.slug) : undefined }}
              className="text-white"
            >
              {setupMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Connecting…
                </>
              ) : (
                <>Continue with {configTemplate?.display_name}</>
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Disconnect confirmation */}
      <AlertDialog open={!!disconnectTarget} onOpenChange={() => setDisconnectTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect {disconnectTarget?.display_name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Your agent will no longer have access to this integration's tools. You can reconnect
              at any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                disconnectTarget && disconnectMutation.mutate(disconnectTarget.connection_id)
              }
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {disconnectMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                'Disconnect'
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

export default AgentIntegrationsTab
