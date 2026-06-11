/**
 * Library view — browse / filter / create surface for every callable any
 * step in any scenario can invoke.
 *
 * Decomposed in RP-4.4. This file keeps the list surface: category chips,
 * search, grouped sections, rows, and the creation flows ("+ New callable"
 * defaulting to code, the caret dropdown, the custom-host API picker).
 * The rest lives in:
 *   - library-view-helpers.ts — taxonomy, constants, pure projections
 *   - CallableDetailPanel.tsx — full-surface detail page
 *   - CallableDetailTabs.tsx + CompositeEditors.tsx — detail tab bodies
 *   - hooks/useCallableDetailEditor.ts — detail draft state + mutations
 */
import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertCircle,
  ChevronDown,
  Code,
  Globe,
  Loader2,
  Plus,
  Search,
  Workflow,
} from 'lucide-react'

import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardHeader } from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/atoms/dropdown-menu'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/atoms/popover'
import { Tabs, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import { apiClient } from '@/api/client'
import { toolService } from '@/api/services/tools.service'
import { cn } from '@/lib/utils'
import { useCallableUsageIndex } from '@/features/agent-canvas/hooks/useCallableUsageIndex'
import { ADVANCED_KINDS_ENABLED } from '@/utils/feature-flags'

import { CallableDetailPanel } from './CallableDetailPanel'
import {
  ALL_VIEW_GROUP_ORDER,
  CATEGORY_TO_KINDS,
  KIND_BADGE_VARIANT,
  KIND_ICON,
  KIND_LABEL,
  LIBRARY_CATEGORY_TABS,
  toolToCallable,
  type CallableEntry,
  type CallableKind,
  type ConnectionLite,
  type LibraryCategory,
} from './library-view-helpers'

// ``ADVANCED_KINDS_ENABLED`` is imported from ``@/utils/feature-flags``
// at the top of the file. When ``true``, the Library UI surfaces a
// "New composite (advanced)" entry next to "+ New callable". Existing
// composite rows keep working regardless of the flag; this only gates
// the *creation* affordance.

export function LibraryView() {
  const location = useLocation()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<LibraryCategory>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const toolsQuery = useQuery({
    queryKey: ['library-callables'],
    queryFn: () => toolService.listDefinitions({ enabled_only: false }),
    staleTime: 30_000,
  })

  const usage = useCallableUsageIndex()

  // Connection metadata for the "via {provider}" source badge. We pull all
  // connections (not just custom hosts) so provider-templated tools also
  // get a friendly label in the row footer.
  const connectionsQuery = useQuery({
    queryKey: ['tools', 'connections', 'all'],
    queryFn: async () => {
      const response = await apiClient.get<{ items: ConnectionLite[] }>(
        '/api/tools/connections',
      )
      return response.items ?? []
    },
    staleTime: 30_000,
  })

  const connectionsById = useMemo(() => {
    const map = new Map<string, ConnectionLite>()
    for (const conn of connectionsQuery.data ?? []) {
      map.set(conn.connection_id, conn)
    }
    return map
  }, [connectionsQuery.data])

  const callables: CallableEntry[] = useMemo(() => {
    const raw = toolsQuery.data
    if (!Array.isArray(raw)) return []
    return raw.map((tool) => {
      const usedByCount = (usage.index.get(tool.tool_ref) ?? []).length
      return toolToCallable(tool, connectionsById, usedByCount)
    })
  }, [toolsQuery.data, connectionsById, usage.index])

  // Map each LibraryCategory (other than 'all') to a Set of display kinds
  // for fast filter checks. Keeps the predicate body simple.
  const categoryKindLookup = useMemo(() => {
    const lookup = new Map<Exclude<LibraryCategory, 'all'>, Set<CallableKind>>()
    for (const [cat, kinds] of Object.entries(CATEGORY_TO_KINDS)) {
      lookup.set(cat as Exclude<LibraryCategory, 'all'>, new Set(kinds))
    }
    return lookup
  }, [])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return callables.filter((entry) => {
      if (category !== 'all') {
        const allowed = categoryKindLookup.get(category)
        if (!allowed || !allowed.has(entry.kind)) return false
      }
      if (q && !`${entry.displayName} ${entry.name} ${entry.description}`.toLowerCase().includes(q)) {
        return false
      }
      return true
    })
  }, [callables, category, categoryKindLookup, search])

  // Group filtered entries by category for the "All" view. Empty groups
  // are dropped so the list reads as a clean stack of sections instead of
  // empty headers. When a specific filter is active, ``groupedSections``
  // collapses to a single anonymous section so the renderer can use the
  // same code path.
  const groupedSections = useMemo(() => {
    if (category !== 'all') {
      return [{ category, entries: filtered }]
    }
    const buckets = new Map<Exclude<LibraryCategory, 'all'>, CallableEntry[]>()
    for (const entry of filtered) {
      for (const [cat, kinds] of categoryKindLookup) {
        if (kinds.has(entry.kind)) {
          const list = buckets.get(cat) ?? []
          list.push(entry)
          buckets.set(cat, list)
          break
        }
      }
    }
    return ALL_VIEW_GROUP_ORDER
      .filter((cat) => (buckets.get(cat)?.length ?? 0) > 0)
      .map((cat) => ({ category: cat as LibraryCategory, entries: buckets.get(cat) ?? [] }))
  }, [filtered, category, categoryKindLookup])

  // Deep-link support: ?callable_ref=<name> or ?callable_id=<id> from step cards.
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const refParam = params.get('callable_ref')
    const idParam = params.get('callable_id')
    if (!refParam && !idParam) return
    const match = callables.find((entry) => (idParam && entry.id === idParam) || (refParam && entry.name === refParam))
    if (match) {
      setSelectedId(match.id)
    }
  }, [callables, location.search])

  const selectedEntry = useMemo(
    () => filtered.find((entry) => entry.id === selectedId) ?? callables.find((entry) => entry.id === selectedId) ?? null,
    [callables, filtered, selectedId],
  )

  const handleSelect = (entry: CallableEntry) => {
    setSelectedId(entry.id)
  }

  const handleCloseDetail = () => {
    setSelectedId(null)
    // Strip deep-link query params so reselecting the view doesn't re-open.
    const params = new URLSearchParams(location.search)
    if (params.has('callable_ref') || params.has('callable_id')) {
      params.delete('callable_ref')
      params.delete('callable_id')
      const qs = params.toString()
      navigate({ pathname: location.pathname, search: qs ? `?${qs}` : '' }, { replace: true })
    }
  }

  const queryClient = useQueryClient()

  // Custom API hosts for the "+ New API" picker. Endpoints under
  // ``kind=custom_api`` need to point at an existing connection (see
  // backend ``ToolDefinitionRecord.api_connection_id``). We surface
  // only ``provider='custom'`` connections — provider-templated tools
  // (Slack, Salesforce, Calendar) are emitted by the integration
  // template path and aren't authored ad-hoc.
  const customHostsQuery = useQuery({
    queryKey: ['tools', 'connections'],
    queryFn: async () => {
      const response = await apiClient.get<{ items: Array<{ connection_id: string; display_name: string; provider: string; base_url?: string }> }>(
        '/api/tools/connections',
      )
      return (response.items ?? []).filter((c) => c.provider === 'custom')
    },
    staleTime: 30_000,
  })

  type CreateInput =
    | { kind: 'code' }
    | { kind: 'composite' }
    | { kind: 'api'; connection_id: string }

  const createMutation = useMutation({
    mutationFn: (input: CreateInput) => {
      const stamp = Date.now().toString(36)
      if (input.kind === 'api') {
        return toolService.createDefinition({
          kind: 'api',
          connection_id: input.connection_id,
          tool_ref: `api.untitled_${stamp}`,
          display_name: 'Untitled API endpoint',
          description:
            'Custom API endpoint — set the path, method, schema, and ACI metadata in the detail panel.',
          http_method: 'GET',
          endpoint_path: '/',
          timeout_ms: 5_000,
        })
      }
      const baseRef = input.kind === 'code' ? `code.untitled_${stamp}` : `composite.untitled_${stamp}`
      const baseName = input.kind === 'code' ? 'Untitled Code Callable' : 'Untitled Composite'
      const description = input.kind === 'code'
        ? 'Author-written Python sandboxed callable. Set vars["..."] inputs and assign result to return.'
        : 'Composite callable that chains other Library callables sequentially with arg mapping.'
      return toolService.createDefinition({
        kind: input.kind,
        tool_ref: baseRef,
        display_name: baseName,
        description,
        connection_id: null,
        timeout_ms: 30_000,
        metadata: input.kind === 'code' ? { code_body: '' } : { composite_steps: [] },
      })
    },
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ['library-callables'] })
      setSelectedId(created.tool_definition_id)
      toast.success('Callable created')
    },
    onError: (error: Error) => {
      toast.error(`Could not create callable: ${error.message}`)
    },
  })

  const [newApiOpen, setNewApiOpen] = useState(false)

  // Browse → focus: when a tool is selected, the detail page takes
  // the full surface — list and per-page header are hidden. This resolves
  // the cramped-side-panel complaint from the design review.
  if (selectedEntry) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-5xl flex-col gap-4 p-6">
          <CallableDetailPanel
            entry={selectedEntry}
            onClose={handleCloseDetail}
            usageRefs={usage.index.get(selectedEntry.name) ?? []}
            usageLoading={usage.isLoading}
            usageReady={usage.loadedCount > 0 && usage.agentCount > 0}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex min-h-full flex-col gap-6 p-6">
        <div className="mx-auto flex w-full max-w-5xl items-start justify-between gap-3">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold">Library</h1>
            <p className="text-sm text-muted-foreground">
              Every callable any step in any scenario can invoke. APIs, built-ins,
              MCP, code, and composites all live here. Connect provider hosts in the
              Connections tab; author Code, Composite, and Custom-API endpoints here.
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {/* Primary callable creation. Single + New callable defaults to
                Code. The caret reveals
                secondary actions: a custom-host API endpoint, and — only
                when ADVANCED_KINDS_ENABLED — a legacy composite. */}
            <div className="flex items-center">
              <Button
                size="sm"
                onClick={() => createMutation.mutate({ kind: 'code' })}
                disabled={createMutation.isPending}
                className="rounded-r-none border-r border-primary-foreground/20"
              >
                <Plus className="mr-2 h-3.5 w-3.5" />
                New callable
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    size="sm"
                    disabled={createMutation.isPending}
                    aria-label="More callable creation options"
                    className="rounded-l-none px-2"
                  >
                    <ChevronDown className="h-3.5 w-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-64">
                  <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
                    Create a callable
                  </DropdownMenuLabel>
                  <DropdownMenuItem
                    onSelect={() => createMutation.mutate({ kind: 'code' })}
                  >
                    <Code className="mr-2 h-3.5 w-3.5" />
                    <div className="flex flex-col items-start">
                      <span className="text-sm font-medium">New code callable</span>
                      <span className="text-[11px] text-muted-foreground">
                        Author Python in the sandbox; call APIs and other callables.
                      </span>
                    </div>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => setNewApiOpen(true)}>
                    <Globe className="mr-2 h-3.5 w-3.5" />
                    <div className="flex flex-col items-start">
                      <span className="text-sm font-medium">New API endpoint…</span>
                      <span className="text-[11px] text-muted-foreground">
                        Bind a request to a custom host configured in Connections.
                      </span>
                    </div>
                  </DropdownMenuItem>
                  {ADVANCED_KINDS_ENABLED && (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onSelect={() => createMutation.mutate({ kind: 'composite' })}
                      >
                        <Workflow className="mr-2 h-3.5 w-3.5" />
                        <div className="flex flex-col items-start">
                          <span className="text-sm font-medium">New composite (advanced)</span>
                          <span className="text-[11px] text-muted-foreground">
                            Legacy: chain other callables declaratively.
                          </span>
                        </div>
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {/* Custom-host picker for "New API endpoint…". Opens from the
                dropdown above; lives next to the primary button so the
                Popover can anchor cleanly. */}
            <Popover open={newApiOpen} onOpenChange={setNewApiOpen}>
              <PopoverTrigger asChild>
                <button type="button" className="sr-only" aria-hidden tabIndex={-1} />
              </PopoverTrigger>
              <PopoverContent className="w-80 p-3" align="end">
                <div className="space-y-3">
                  <div className="space-y-1">
                    <p className="text-sm font-medium">New custom API endpoint</p>
                    <p className="text-xs text-muted-foreground">
                      Pick a custom host. You'll author method, path, and
                      schema in the detail panel after creation.
                    </p>
                  </div>
                  {customHostsQuery.isLoading ? (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      Loading hosts…
                    </div>
                  ) : (customHostsQuery.data?.length ?? 0) === 0 ? (
                    <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                      No custom API hosts yet. Add one in the Connections
                      tab first.
                    </div>
                  ) : (
                    <ul className="space-y-1">
                      {(customHostsQuery.data ?? []).map((host) => (
                        <li key={host.connection_id}>
                          <button
                            type="button"
                            onClick={() => {
                              setNewApiOpen(false)
                              createMutation.mutate({
                                kind: 'api',
                                connection_id: host.connection_id,
                              })
                            }}
                            className="flex w-full flex-col items-start gap-0.5 rounded-md border border-border bg-card px-2 py-1.5 text-left hover:border-primary/40 hover:bg-muted/40"
                          >
                            <span className="text-xs font-medium">
                              {host.display_name || host.provider}
                            </span>
                            {host.base_url && (
                              <span className="truncate text-[10px] text-muted-foreground">
                                {host.base_url}
                              </span>
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </PopoverContent>
            </Popover>
          </div>
        </div>

        <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6">
          <Card className="flex-1">
            <CardHeader className="space-y-3 pb-3">
              <Tabs value={category} onValueChange={(value) => setCategory(value as LibraryCategory)}>
                <TabsList className="h-auto flex-wrap justify-start gap-1 bg-transparent p-0">
                  {LIBRARY_CATEGORY_TABS.map((tab) => (
                    <TabsTrigger
                      key={tab.value}
                      value={tab.value}
                      className="h-8 rounded-md px-3 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
                    >
                      {tab.label}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="library-search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search callables by name or description"
                  className="h-9 pl-8 text-sm"
                />
              </div>
            </CardHeader>
            <CardContent className="min-h-[420px]">
              {toolsQuery.isLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : toolsQuery.isError ? (
                <div className="flex h-40 flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
                  <AlertCircle className="h-5 w-5" />
                  <p>Could not load callables. {(toolsQuery.error as Error).message}</p>
                </div>
              ) : filtered.length === 0 ? (
                <EmptyState hasAny={callables.length > 0} search={search} />
              ) : (
                <div className="space-y-6">
                  {groupedSections.map((section) => {
                    const labelKey = section.category as Exclude<LibraryCategory, 'all'>
                    const label =
                      LIBRARY_CATEGORY_TABS.find((tab) => tab.value === labelKey)?.label
                      ?? labelKey
                    return (
                      <section key={labelKey}>
                        {category === 'all' && (
                          <header className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                            {label}
                            <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                              {section.entries.length}
                            </span>
                          </header>
                        )}
                        <ul className="divide-y divide-border">
                          {section.entries.map((entry) => (
                            <li key={entry.id}>
                              <CallableRow
                                entry={entry}
                                selected={entry.id === selectedId}
                                onSelect={() => handleSelect(entry)}
                              />
                            </li>
                          ))}
                        </ul>
                      </section>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function EmptyState({ hasAny, search }: { hasAny: boolean; search: string }) {
  if (hasAny && search.trim()) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
        No callables match &ldquo;{search.trim()}&rdquo;.
      </div>
    )
  }
  return (
    <div className="flex h-40 flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
      <p className="font-medium text-foreground">No callables yet</p>
      <p className="max-w-md text-center">
        Use <strong>+ New callable</strong> to author one here, or connect a
        provider in the Connections tab to expose its API tools.
      </p>
    </div>
  )
}

function CallableRow({
  entry,
  selected,
  onSelect,
}: {
  entry: CallableEntry
  selected: boolean
  onSelect: () => void
}) {
  const Icon = KIND_ICON[entry.kind]
  const totalInvocations = entry.invocationCount
  const reliability = entry.reliabilityScore > 0 ? Math.round(entry.reliabilityScore * 100) : null

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'flex w-full items-start justify-between gap-4 rounded-md px-2 py-3 text-left transition-colors',
        selected ? 'bg-primary/5 ring-1 ring-primary/30' : 'hover:bg-muted/40',
      )}
    >
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <span
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-muted/40 text-muted-foreground',
            entry.deprecated && 'opacity-50',
          )}
          aria-hidden
        >
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn('truncate font-medium', entry.deprecated && 'text-muted-foreground line-through')}>
              {entry.displayName}
            </span>
            <Badge variant={KIND_BADGE_VARIANT[entry.kind]} className="text-[10px]">
              {KIND_LABEL[entry.kind]}
            </Badge>
            {entry.kind === 'openapi' && (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">OpenAPI</Badge>
            )}
            {entry.deprecated && (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">Deprecated</Badge>
            )}
            {!entry.isActive && !entry.deprecated && (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">Inactive</Badge>
            )}
            <code className="rounded bg-muted px-1 py-0.5 text-[11px] text-muted-foreground">{entry.name}</code>
            {entry.usedByCount > 0 && (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">
                Used by {entry.usedByCount} step{entry.usedByCount === 1 ? '' : 's'}
              </Badge>
            )}
          </div>
          {entry.description && (
            <p className="truncate text-sm text-muted-foreground">{entry.description}</p>
          )}
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            {entry.connection && <span>via {entry.connection.name}</span>}
            {entry.category && <span>· {entry.category}</span>}
            <span>{entry.connection ? '·' : ''} v{entry.version}</span>
            {totalInvocations > 0 && (
              <>
                <span>· {totalInvocations.toLocaleString()} invocation{totalInvocations === 1 ? '' : 's'}</span>
                {reliability !== null && <span>· {reliability}% success</span>}
              </>
            )}
          </div>
        </div>
      </div>
    </button>
  )
}
