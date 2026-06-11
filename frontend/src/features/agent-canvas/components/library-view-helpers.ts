/**
 * Pure helpers, types, and constants for the Library view.
 *
 * Extracted from LibraryView.tsx (RP-4.4) — no React, no network; just the
 * callable taxonomy (display kinds, category mapping, kind-specific tab
 * sets) and the ToolDefinition → CallableEntry projection.
 */
import {
  Code,
  Globe,
  Plug,
  Settings2,
  Workflow,
} from 'lucide-react'

import type { ToolDefinition } from '@/api/services/tools.service'
import type { CallableUsageRef } from '@/features/agent-canvas/hooks/useCallableUsageIndex'

// ────────────────────────────────────────────────────────────────────────────
// Composite step model — each row picks a sub-callable ref + maps args. The
// args mapping uses the same convention as step bindings: $args.<key> pulls
// from the parent call, $prev.<dotted_path> pulls from the previous step's
// output, anything else is a literal.
// ────────────────────────────────────────────────────────────────────────────

export interface CompositeStep {
  ref: string
  args: Record<string, string>
}

// ────────────────────────────────────────────────────────────────────────────
// Output mapping — `{fact_name: extraction_expr}`. Expressions starting with
// `$.` are dotted paths into the tool result; anything else is a top-level
// key. The kernel applies this mapping after a successful tool call to
// write facts back into the conversation.
// ────────────────────────────────────────────────────────────────────────────

export function normalizeOutputMapping(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== 'object') return {}
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof key !== 'string' || !key.trim()) continue
    if (typeof value !== 'string' || !value.trim()) continue
    out[key.trim()] = value.trim()
  }
  return out
}

export function normalizeCompositeSteps(raw: unknown): CompositeStep[] {
  if (!Array.isArray(raw)) return []
  const out: CompositeStep[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue
    const obj = entry as Record<string, unknown>
    const ref = String(obj.ref ?? '').trim()
    if (!ref) continue
    const argsRaw = obj.args
    const args: Record<string, string> = {}
    if (argsRaw && typeof argsRaw === 'object') {
      for (const [key, value] of Object.entries(argsRaw)) {
        args[key] = typeof value === 'string' ? value : JSON.stringify(value)
      }
    }
    out.push({ ref, args })
  }
  return out
}

// ────────────────────────────────────────────────────────────────────────────
// Callable kind — the unified concept that unifies tools / APIs / code /
// built-ins / MCP. Phase-1 source is ToolSpec, so the runtime kinds we can
// display today are http_api, builtin_tool, mcp_tool, and composite. Code
// (author-written Python), openapi (imported specs), and reference_tool
// will come with slice 4 when their backend storage lands — see Decision 7
// in docs/canvas-redesign/01.
// ────────────────────────────────────────────────────────────────────────────

// Display kinds drive the Library UI. They are derived from the backend
// kind + metadata.ingestion_source — distinct from backend ToolKind so the
// runtime taxonomy and the user-facing categorisation can evolve
// independently. ``http_api`` covers user-authored Custom-API endpoints
// (with an ``openapi`` source variant for spec-imported ones);
// ``integration_tool`` covers provider-templated callables (Google Calendar,
// Slack, etc.) — kept separate from ``http_api`` so the "Integrations"
// filter shows them on their own.
export type CallableKind =
  | 'http_api'
  | 'openapi'
  | 'integration_tool'
  | 'builtin_tool'
  | 'mcp_tool'
  | 'composite'
  | 'code'
  | 'reference_tool' // legacy display kind kept for forward compatibility

export type CallableEntry = {
  id: string
  name: string
  displayName: string
  description: string
  kind: CallableKind
  category?: string
  version: string
  deprecated: boolean
  connection?: { name: string; slug: string; icon?: string }
  invocationCount: number
  successCount: number
  failureCount: number
  reliabilityScore: number
  isActive: boolean
  usedByCount: number
  raw: ToolDefinition
}

export const KIND_LABEL: Record<CallableKind, string> = {
  http_api: 'API',
  openapi: 'OpenAPI',
  integration_tool: 'Integration',
  builtin_tool: 'Built-in',
  mcp_tool: 'MCP',
  composite: 'Composite',
  code: 'Code',
  reference_tool: 'Reference',
}

export const KIND_ICON: Record<CallableKind, typeof Code> = {
  http_api: Globe,
  openapi: Globe,
  integration_tool: Plug,
  builtin_tool: Settings2,
  mcp_tool: Plug,
  composite: Workflow,
  code: Code,
  reference_tool: Workflow,
}

// Kind-specific detail-page tabs. Each kind shows ONLY the tabs that
// apply — disabled tabs feel unfinished (per the design review). Tab
// labels use the Ruhu authoring vocabulary (Code / Input Schema / Callable
// functions / APIs / Global Variables / Used by) where they apply, plus
// kind-specific tabs (Calls + Output for composites; Request +
// Connection for APIs; Summary for read-only built-ins).
export type DetailTabId =
  | 'code'
  | 'request'
  | 'calls'
  | 'schema'
  | 'callables'
  | 'apis'
  | 'output'
  | 'vars'
  | 'connection'
  | 'used'
  | 'summary'

export const TABS_BY_KIND: Record<CallableKind, Array<{ id: DetailTabId; label: string }>> = {
  code: [
    { id: 'code', label: 'Code' },
    { id: 'schema', label: 'Input Schema' },
    { id: 'callables', label: 'Callable functions' },
    { id: 'apis', label: 'APIs' },
    { id: 'vars', label: 'Global Variables' },
    { id: 'used', label: 'Used by' },
  ],
  composite: [
    { id: 'calls', label: 'Calls' },
    { id: 'schema', label: 'Input Schema' },
    { id: 'output', label: 'Output' },
    { id: 'used', label: 'Used by' },
  ],
  http_api: [
    { id: 'request', label: 'Request' },
    { id: 'schema', label: 'Input Schema' },
    { id: 'connection', label: 'Connection' },
    { id: 'used', label: 'Used by' },
  ],
  openapi: [
    { id: 'request', label: 'Request' },
    { id: 'schema', label: 'Input Schema' },
    { id: 'connection', label: 'Connection' },
    { id: 'used', label: 'Used by' },
  ],
  integration_tool: [
    { id: 'summary', label: 'Summary' },
    { id: 'schema', label: 'Input Schema' },
    { id: 'connection', label: 'Connection' },
    { id: 'used', label: 'Used by' },
  ],
  builtin_tool: [
    { id: 'summary', label: 'Summary' },
    { id: 'used', label: 'Used by' },
  ],
  mcp_tool: [
    { id: 'summary', label: 'Summary' },
    { id: 'used', label: 'Used by' },
  ],
  reference_tool: [
    { id: 'summary', label: 'Summary' },
    { id: 'used', label: 'Used by' },
  ],
}

export const KIND_BADGE_VARIANT: Record<CallableKind, 'default' | 'secondary' | 'outline'> = {
  http_api: 'secondary',
  openapi: 'secondary',
  integration_tool: 'secondary',
  builtin_tool: 'outline',
  mcp_tool: 'secondary',
  composite: 'outline',
  code: 'default',
  reference_tool: 'outline',
}

export interface ConnectionLite {
  connection_id: string
  display_name: string
  provider: string
  base_url?: string
}

export function toolToCallable(
  tool: ToolDefinition,
  connectionsById: Map<string, ConnectionLite>,
  usedByCount: number,
): CallableEntry {
  // Backend ToolDefinition.kind is 'api' | 'integration' | 'builtin' | 'code' | 'composite' | 'mcp'.
  // Combine with metadata.ingestion_source to pick the Library display kind:
  //   code                              → 'code'
  //   composite                         → 'composite'
  //   api + ingestion_source='openapi'  → 'openapi'
  //   api otherwise                     → 'http_api'
  //   integration                       → 'integration_tool'
  //   builtin                           → 'builtin_tool'
  //   mcp                               → 'mcp_tool'
  const ingestionSource = (tool.metadata?.ingestion_source ?? null) as string | null
  let kind: CallableKind
  if (tool.kind === 'code') {
    kind = 'code'
  } else if (tool.kind === 'composite') {
    kind = 'composite'
  } else if (tool.kind === 'builtin') {
    kind = 'builtin_tool'
  } else if (tool.kind === 'mcp') {
    kind = 'mcp_tool'
  } else if (tool.kind === 'integration') {
    kind = 'integration_tool'
  } else if (ingestionSource === 'openapi') {
    kind = 'openapi'
  } else {
    kind = 'http_api'
  }

  const conn = tool.connection_id ? connectionsById.get(tool.connection_id) : undefined
  const connection = conn
    ? { name: conn.display_name || conn.provider, slug: conn.provider }
    : undefined

  return {
    id: tool.tool_definition_id,
    name: tool.tool_ref,
    displayName: tool.display_name || tool.tool_ref,
    description: tool.description,
    kind,
    category: undefined,
    version: 'v1',
    deprecated: false,
    connection,
    invocationCount: 0,
    successCount: 0,
    failureCount: 0,
    reliabilityScore: 0,
    isActive: tool.enabled,
    usedByCount,
    raw: tool,
  }
}

// Library list categories — what users see as filter chips. ``mine`` is a
// virtual category that combines ``code`` + ``composite`` (everything the
// user authored). ``api`` folds ``openapi`` in — OpenAPI imports are a
// flavour of API, not a separate top-level concept. Provider-templated
// callables sit in ``integrations`` so they don't crowd custom-host APIs.
export type LibraryCategory =
  | 'all'
  | 'mine'
  | 'api'
  | 'integrations'
  | 'builtin'
  | 'mcp'

export const LIBRARY_CATEGORY_TABS: Array<{ value: LibraryCategory; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'mine', label: 'Mine' },
  { value: 'api', label: 'API' },
  { value: 'integrations', label: 'Integrations' },
  { value: 'builtin', label: 'Built-in' },
  { value: 'mcp', label: 'MCP' },
]

// Display kinds that belong to each category. ``mine`` aggregates the
// user-authored kinds; the others map 1:1 onto display kinds plus the
// OpenAPI fold-in for ``api``. Keep this map in sync with the filter
// predicate and the All-view group order.
export const CATEGORY_TO_KINDS: Record<Exclude<LibraryCategory, 'all'>, CallableKind[]> = {
  mine: ['code', 'composite'],
  api: ['http_api', 'openapi'],
  integrations: ['integration_tool'],
  builtin: ['builtin_tool', 'reference_tool'],
  mcp: ['mcp_tool'],
}

// Display order for the "All" view. ``mine`` (the user's own callables)
// comes first because that's what authors come back to most; framework
// kinds at the end. Each group renders as one section in the list.
export const ALL_VIEW_GROUP_ORDER: Array<Exclude<LibraryCategory, 'all'>> = [
  'mine',
  'api',
  'integrations',
  'builtin',
  'mcp',
]

// Kinds eligible to appear in the "Callable functions" picker — code,
// composite, and builtin all execute within the kernel without an HTTP
// hop, so they're the natural in-process callables the sandbox bridges.
export const CALLABLE_FN_KINDS = new Set(['code', 'composite', 'builtin'])

// Kinds eligible for the APIs picker — both author-created Custom-API
// endpoints and provider-templated integration tools route through the
// HTTP executor, so authors think of both as "APIs they can call".
export const CALLABLE_API_KINDS = new Set(['api', 'integration'])

// ────────────────────────────────────────────────────────────────────────────
// Vars cross-ref helpers
// ────────────────────────────────────────────────────────────────────────────

export interface FactReference {
  factName: string
  argKey: string
  agentName: string
  scenarioName: string
  stepName: string
  agentId: string
  scenarioId: string
  stepId: string
}

const FACT_TOKEN_RE = /\$facts\.([a-zA-Z_][\w.]*)/g

// Extract every `$facts.<name>` token mentioned in any binding's args across
// every step that calls this callable. Returns one row per (fact, callsite)
// so the same fact bound from N steps shows up N times.
export function extractFactReferences(refs: CallableUsageRef[]): FactReference[] {
  const out: FactReference[] = []
  for (const ref of refs) {
    if (!ref.args) continue
    for (const [argKey, value] of Object.entries(ref.args)) {
      if (typeof value !== 'string') continue
      const matches = value.matchAll(FACT_TOKEN_RE)
      for (const match of matches) {
        out.push({
          factName: match[1],
          argKey,
          agentName: ref.agentName,
          scenarioName: ref.scenarioName,
          stepName: ref.stepName,
          agentId: ref.agentId,
          scenarioId: ref.scenarioId,
          stepId: ref.stepId,
        })
      }
    }
  }
  return out
}
