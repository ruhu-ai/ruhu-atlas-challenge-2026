/**
 * Tab bodies for the Library callable detail panel.
 *
 * Extracted from LibraryView.tsx (RP-4.4): the Callable functions / APIs
 * binding pickers (shared ``metadata.callable_refs`` backing field), the
 * read-only Connection / Built-in summaries, the Global Variables
 * cross-ref, and the Used-by backref list.
 */
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Loader2, Save } from 'lucide-react'

import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Checkbox } from '@/components/atoms/checkbox'
import { Label } from '@/components/atoms/label'
import { apiClient } from '@/api/client'
import { toolService } from '@/api/services/tools.service'
import type { ToolDefinition } from '@/api/services/tools.service'
import type { CallableUsageRef } from '@/features/agent-canvas/hooks/useCallableUsageIndex'
import { resolveCallableAliases } from '@/utils/callable-aliases'

import {
  CALLABLE_API_KINDS,
  CALLABLE_FN_KINDS,
  KIND_LABEL,
  type CallableEntry,
  type ConnectionLite,
  type FactReference,
} from './library-view-helpers'

export interface CallableBindingProps {
  ownRef: string
  selectedRefs: string[]
  explicitAliases: Record<string, string>
  onToggle: (ref: string) => void
  dirty: boolean
  saving: boolean
  onSave: () => void
}

function useLibraryCandidates(eligibleKinds: Set<string>, ownRef: string) {
  // Reuse the parent's library-callables cache; same query key, no extra
  // network roundtrip. Filter to the kinds the picker offers and exclude
  // the current callable so authors can't bind a row to itself
  // (recursion guard catches it at runtime, but the UI shouldn't even
  // tempt the misuse).
  return useQuery({
    queryKey: ['library-callables'],
    queryFn: () => toolService.listDefinitions({ enabled_only: false }),
    staleTime: 30_000,
    select: (data) =>
      (data ?? [])
        .filter((tool) => eligibleKinds.has(String(tool.kind)))
        .filter((tool) => tool.tool_ref !== ownRef),
  })
}

export function CallableFunctionsTab({
  ownRef,
  selectedRefs,
  explicitAliases,
  onToggle,
  dirty,
  saving,
  onSave,
}: CallableBindingProps) {
  const candidatesQuery = useLibraryCandidates(CALLABLE_FN_KINDS, ownRef)
  const candidates = candidatesQuery.data ?? []
  // Compute aliases from the *selected* refs only; the picker rows show
  // the alias each ref will resolve to so authors know what to type in
  // the Code body.
  const aliasMap = useMemo(
    () => resolveCallableAliases(selectedRefs, explicitAliases),
    [selectedRefs, explicitAliases],
  )
  const aliasByRef = useMemo(() => {
    const out = new Map<string, string>()
    for (const [alias, ref] of Object.entries(aliasMap)) {
      out.set(ref, alias)
    }
    return out
  }, [aliasMap])

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Other Library callables your Python body is permitted to invoke.
        Each selected ref gets a deterministic <em>alias</em> — that's the
        function name to call in your code (e.g.
        <code className="mx-1 rounded bg-muted px-1 py-0.5 text-[10px]">get_user(user_id=&quot;...&quot;)</code>).
      </p>
      {candidatesQuery.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading callables…
        </div>
      ) : candidates.length === 0 ? (
        <p className="rounded-md border border-dashed bg-muted/20 p-3 text-[11px] text-muted-foreground">
          No other Code, Composite, or Built-in callables in this org.
          Author one with <strong>+ New callable</strong> to make it
          available here.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {candidates.map((candidate) => {
            const checked = selectedRefs.includes(candidate.tool_ref)
            const alias = checked ? aliasByRef.get(candidate.tool_ref) : undefined
            return (
              <li key={candidate.tool_definition_id} className="flex items-start gap-3 px-3 py-2">
                <Checkbox
                  checked={checked}
                  onCheckedChange={() => onToggle(candidate.tool_ref)}
                  aria-label={`Bind ${candidate.tool_ref}`}
                  className="mt-0.5"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{candidate.display_name}</span>
                    <Badge variant="outline" className="text-[10px] capitalize">
                      {candidate.kind}
                    </Badge>
                    <code className="rounded bg-muted px-1 py-0.5 text-[11px] text-muted-foreground">
                      {candidate.tool_ref}
                    </code>
                    {alias && (
                      <Badge variant="default" className="text-[10px]">
                        Call as <code className="ml-1">{alias}(...)</code>
                      </Badge>
                    )}
                  </div>
                  {candidate.description && (
                    <p className="truncate text-[11px] text-muted-foreground">
                      {candidate.description}
                    </p>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
      <Button
        size="sm"
        onClick={onSave}
        disabled={!dirty || saving}
        aria-label="Save callable bindings"
      >
        {saving ? (
          <Loader2 className="mr-2 h-3 w-3 animate-spin" />
        ) : (
          <Save className="mr-2 h-3 w-3" />
        )}
        Save
      </Button>
    </div>
  )
}

export function ApisTab({
  ownRef,
  selectedRefs,
  explicitAliases,
  onToggle,
  dirty,
  saving,
  onSave,
}: CallableBindingProps) {
  const candidatesQuery = useLibraryCandidates(CALLABLE_API_KINDS, ownRef)
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

  // Group candidate APIs by their connection for the provider layout.
  // Tools without a connection (shouldn't happen for
  // api/integration but tolerate gracefully) bucket under "Other".
  const grouped = useMemo(() => {
    const buckets = new Map<string, { name: string; items: ToolDefinition[] }>()
    for (const tool of candidatesQuery.data ?? []) {
      const key = tool.connection_id ?? '__none__'
      const name =
        connectionsById.get(tool.connection_id ?? '')?.display_name
        ?? connectionsById.get(tool.connection_id ?? '')?.provider
        ?? 'Other'
      const bucket = buckets.get(key) ?? { name, items: [] }
      bucket.items.push(tool)
      buckets.set(key, bucket)
    }
    return Array.from(buckets.values())
  }, [candidatesQuery.data, connectionsById])

  const aliasMap = useMemo(
    () => resolveCallableAliases(selectedRefs, explicitAliases),
    [selectedRefs, explicitAliases],
  )
  const aliasByRef = useMemo(() => {
    const out = new Map<string, string>()
    for (const [alias, ref] of Object.entries(aliasMap)) {
      out.set(ref, alias)
    }
    return out
  }, [aliasMap])

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Library API tools your Python body is permitted to invoke. Pick
        endpoints from any connected host — selecting one binds it as a
        callable on this code, with a deterministic alias to call in
        your body.
      </p>
      {candidatesQuery.isLoading || connectionsQuery.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading APIs…
        </div>
      ) : grouped.length === 0 ? (
        <p className="rounded-md border border-dashed bg-muted/20 p-3 text-[11px] text-muted-foreground">
          No API endpoints in this org. Connect a provider in the
          Connections tab, or add a custom-host endpoint via the
          <strong className="mx-1">+ New callable</strong> dropdown.
        </p>
      ) : (
        <div className="space-y-3">
          {grouped.map((group) => (
            <section key={group.name} className="rounded-md border border-border">
              <header className="flex items-center justify-between gap-2 border-b border-border bg-muted/30 px-3 py-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {group.name}
                </span>
                <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {group.items.length}
                </span>
              </header>
              <ul className="divide-y divide-border">
                {group.items.map((candidate) => {
                  const checked = selectedRefs.includes(candidate.tool_ref)
                  const alias = checked ? aliasByRef.get(candidate.tool_ref) : undefined
                  return (
                    <li key={candidate.tool_definition_id} className="flex items-start gap-3 px-3 py-2">
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => onToggle(candidate.tool_ref)}
                        aria-label={`Bind ${candidate.tool_ref}`}
                        className="mt-0.5"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium">{candidate.display_name}</span>
                          <Badge variant="outline" className="text-[10px] capitalize">
                            {candidate.kind}
                          </Badge>
                          <code className="rounded bg-muted px-1 py-0.5 text-[11px] text-muted-foreground">
                            {candidate.tool_ref}
                          </code>
                          {alias && (
                            <Badge variant="default" className="text-[10px]">
                              Call as <code className="ml-1">{alias}(...)</code>
                            </Badge>
                          )}
                        </div>
                        {candidate.description && (
                          <p className="truncate text-[11px] text-muted-foreground">
                            {candidate.description}
                          </p>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
      <Button
        size="sm"
        onClick={onSave}
        disabled={!dirty || saving}
        aria-label="Save API bindings"
      >
        {saving ? (
          <Loader2 className="mr-2 h-3 w-3 animate-spin" />
        ) : (
          <Save className="mr-2 h-3 w-3" />
        )}
        Save
      </Button>
    </div>
  )
}

export function ConnectionSummary({ tool }: { tool: ToolDefinition }) {
  if (!tool.connection_id) {
    return (
      <p className="rounded-md border border-dashed bg-muted/20 p-3 text-[11px] text-muted-foreground">
        No connection bound. Custom API endpoints require a host —
        select one from the Connections tab and re-create this tool
        via <strong>+ New API</strong>.
      </p>
    )
  }
  return (
    <div className="space-y-2 rounded-md border border-border bg-muted/10 p-3 text-xs">
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">Connection ID</span>
        <code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{tool.connection_id}</code>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Manage credentials, base URL, and disconnect in the Connections
        tab.
      </p>
    </div>
  )
}

export function BuiltinSummary({
  entry,
  tool,
}: {
  entry: CallableEntry
  tool: ToolDefinition
}) {
  return (
    <div className="space-y-2 rounded-md border border-border bg-muted/10 p-3 text-xs">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
            Kind
          </span>
          <span>{KIND_LABEL[entry.kind]}</span>
        </div>
        <div>
          <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
            Version
          </span>
          <span>v{entry.version}</span>
        </div>
        <div>
          <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
            Status
          </span>
          <span>{entry.isActive ? 'active' : 'inactive'}</span>
        </div>
        <div>
          <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
            Tool ref
          </span>
          <code className="text-[11px]">{tool.tool_ref ?? entry.name}</code>
        </div>
      </div>
      <p className="pt-2 text-[11px] text-muted-foreground">
        Built-in and protocol-managed callables aren't editable from the
        Library. They surface here so steps can reference them and
        usage backrefs are visible.
      </p>
    </div>
  )
}

export function VariableUsageTab({
  references,
  writes,
}: {
  references: FactReference[]
  writes: Record<string, string>
}) {
  // Group rows by fact name so the same fact bound from many steps collapses
  // into one block.
  const grouped = useMemo(() => {
    const map = new Map<string, FactReference[]>()
    for (const ref of references) {
      const existing = map.get(ref.factName)
      if (existing) existing.push(ref)
      else map.set(ref.factName, [ref])
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [references])

  const writeEntries = useMemo(
    () => Object.entries(writes).sort(([a], [b]) => a.localeCompare(b)),
    [writes],
  )

  if (grouped.length === 0 && writeEntries.length === 0) {
    return (
      <p className="rounded-md border border-dashed bg-muted/20 p-3 text-[11px] text-muted-foreground">
        No variable reads or writes declared. Bind a step&apos;s tool arg with{' '}
        <code className="rounded bg-muted px-1">$facts.&lt;name&gt;</code> for reads, or add an{' '}
        <strong>output mapping</strong> on the Schema tab for writes.
      </p>
    )
  }

  return (
    <div className="space-y-3 text-[11px]">
      {writeEntries.length > 0 && (
        <section className="space-y-1">
          <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Writes {writeEntries.length} fact{writeEntries.length === 1 ? '' : 's'} (from output_mapping)
          </Label>
          <ul className="space-y-1">
            {writeEntries.map(([factName, expr]) => (
              <li
                key={factName}
                className="flex items-center justify-between gap-2 rounded-md border bg-muted/20 p-2"
              >
                <code className="rounded bg-muted px-1 py-0.5 font-mono text-foreground">
                  facts.{factName}
                </code>
                <span className="text-muted-foreground">←</span>
                <code className="truncate rounded bg-background/50 px-1 font-mono text-[10px] text-muted-foreground">
                  {expr}
                </code>
              </li>
            ))}
          </ul>
        </section>
      )}

      {grouped.length > 0 && (
        <section className="space-y-1">
          <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Reads {grouped.length} fact{grouped.length === 1 ? '' : 's'} (from bound args)
          </Label>
          <ul className="space-y-2">
            {grouped.map(([factName, callsites]) => (
              <li key={factName} className="space-y-1 rounded-md border bg-muted/20 p-2">
                <div className="flex items-center justify-between gap-2">
                  <code className="rounded bg-muted px-1 py-0.5 font-mono text-foreground">
                    facts.{factName}
                  </code>
                  <span className="text-[10px] text-muted-foreground">
                    {callsites.length} call{callsites.length === 1 ? '' : 's'}
                  </span>
                </div>
                <ul className="space-y-0.5 pl-2">
                  {callsites.map((c, i) => (
                    <li key={`${c.stepId}-${i}`} className="truncate">
                      <Link
                        to={{
                          pathname: `/agents/${c.agentId}/canvas`,
                          search: `?view=canvas&scenario=${encodeURIComponent(c.scenarioId)}&step=${encodeURIComponent(c.stepId)}`,
                        }}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        <code className="rounded bg-background/50 px-1 text-[10px]">{c.argKey}</code>
                        <span className="mx-1">←</span>
                        <span>{c.agentName}</span>
                        <span className="text-muted-foreground/60"> · </span>
                        <span>{c.scenarioName}</span>
                        <span className="text-muted-foreground/60"> · </span>
                        <span>{c.stepName}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

export function CalledBySection({
  refs,
  loading,
  ready,
}: {
  refs: CallableUsageRef[]
  loading: boolean
  ready: boolean
}) {
  const showSpinner = loading && !ready
  return (
    <section className="space-y-2 border-t border-border/60 pt-3">
      <div className="flex items-center justify-between gap-2">
        <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
          Called by {refs.length} step{refs.length === 1 ? '' : 's'}
        </Label>
        {loading && ready && (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-label="Refreshing usage" />
        )}
      </div>
      {showSpinner ? (
        <div className="flex items-center gap-2 rounded-md border border-dashed bg-muted/20 p-3 text-[11px] text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Scanning agents…
        </div>
      ) : refs.length === 0 ? (
        <p className="rounded-md border border-dashed bg-muted/20 p-3 text-[11px] text-muted-foreground">
          No steps currently bind this callable. Bindings appear here once a step adds it to its
          tool policy.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-md border">
          {refs.map((ref, i) => (
            <li key={`${ref.agentId}-${ref.stepId}-${i}`} className="px-2 py-1.5">
              <Link
                to={{
                  pathname: `/agents/${ref.agentId}/canvas`,
                  search: `?view=canvas&scenario=${encodeURIComponent(ref.scenarioId)}&step=${encodeURIComponent(ref.stepId)}`,
                }}
                className="flex items-start justify-between gap-2 text-[11px] hover:text-foreground"
              >
                <div className="min-w-0 flex-1 space-y-0.5">
                  <div className="truncate">
                    <span className="font-medium text-foreground">{ref.agentName}</span>
                    <span className="text-muted-foreground"> · </span>
                    <span>{ref.scenarioName}</span>
                    <span className="text-muted-foreground"> · </span>
                    <span>{ref.stepName}</span>
                  </div>
                  {ref.mode && ref.mode !== 'allowed' && (
                    <Badge variant="outline" className="text-[9px]">
                      {ref.mode}
                    </Badge>
                  )}
                </div>
                <ArrowUpRight className="h-3 w-3 shrink-0 text-muted-foreground" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
