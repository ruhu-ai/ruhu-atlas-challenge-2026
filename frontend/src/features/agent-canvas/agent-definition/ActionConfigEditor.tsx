/**
 * Action State Code Editor — 6-tab editor for action state configuration.
 *
 * Replaces the tool_policy editor when a state has type="action".
 * Tabs: Code | Input Schema | Functions | APIs | Tools | Variables
 *
 * Design reference: docs/tooling-and-llm-redesign/Ruhu-Tooling-System-Redesign.md Section 9
 */

import { useState, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { cn } from '@/lib/utils'
import { toolService, type CallableCatalogItem, type ActionConfigTestResult } from '@/api/services/tools.service'

// ── Types ──────────────────────────────────────────────────────────────────

export interface ActionConfig {
  code: string
  callable_functions_code: string
  callable_api_refs: string[]
  callable_integrations: string[]
  callable_system_refs: string[]
  input_schema?: Record<string, unknown>
  timeout_seconds: number
}

export const DEFAULT_ACTION_CONFIG: ActionConfig = {
  code: '# Write your action logic here\n# Call APIs and tools using the callable names shown in the APIs and Tools tabs\n# Example: answer = knowledge_lookup(query=vars.get("_last_user_text", ""))\n# Read facts with vars["key"], write with variables["key"] = value\n# Set result to control transitions: result = {"status": "success"}\n\nresult = {"status": "success"}\n',
  callable_functions_code: '',
  callable_api_refs: [],
  callable_integrations: [],
  callable_system_refs: [],
  input_schema: {},
  timeout_seconds: 30,
}

type TabId = 'code' | 'schema' | 'functions' | 'apis' | 'tools' | 'variables'

interface ActionConfigEditorProps {
  config: ActionConfig
  onChange: (config: ActionConfig) => void
  agentId: string | null
  stateId: string | null
  factSchema: string[]
}

// ── Tab Button ─────────────────────────────────────────────────────────────

function TabButton({ id, label, active, onClick }: { id: TabId; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'px-3 py-1.5 text-xs font-medium transition-colors',
        active
          ? 'border-b-2 border-primary text-foreground'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {label}
    </button>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────

export function ActionConfigEditor({
  config,
  onChange,
  agentId,
  stateId,
  factSchema,
}: ActionConfigEditorProps) {
  const [activeTab, setActiveTab] = useState<TabId>('code')
  const [testResult, setTestResult] = useState<ActionConfigTestResult | null>(null)
  const [testRunning, setTestRunning] = useState(false)
  const [testError, setTestError] = useState<string | null>(null)

  // Fetch callable catalog for APIs and Tools tabs
  const { data: catalog } = useQuery({
    queryKey: ['callable-catalog', agentId],
    queryFn: () => toolService.getCallableCatalog(agentId!),
    enabled: !!agentId,
    staleTime: 60_000,
  })

  const update = (patch: Partial<ActionConfig>) => onChange({ ...config, ...patch })

  const runTest = useCallback(async () => {
    if (!agentId || !stateId || testRunning) return
    setTestRunning(true)
    setTestError(null)
    setTestResult(null)
    try {
      const result = await toolService.testActionConfig(agentId, stateId, {
        code: config.code,
        callable_functions_code: config.callable_functions_code,
        callable_api_refs: config.callable_api_refs,
        callable_integrations: config.callable_integrations,
        callable_system_refs: config.callable_system_refs,
        test_facts: {},
        timeout_seconds: Math.min(config.timeout_seconds, 10),
      })
      setTestResult(result)
    } catch (err) {
      setTestError(err instanceof Error ? err.message : 'Test failed')
    } finally {
      setTestRunning(false)
    }
  }, [agentId, stateId, config.code, config.callable_functions_code, config.timeout_seconds, testRunning])

  // Count enabled items for tab badges
  const apiCount = config.callable_api_refs.length
  const integrationCount = config.callable_integrations.length
  const systemCount = config.callable_system_refs.length
  const toolCount = integrationCount + systemCount

  return (
    <div className="rounded-md border border-white/10 overflow-hidden">
      {/* Tab bar */}
      <div className="flex border-b border-white/10 bg-muted/20">
        <TabButton id="code" label="Code" active={activeTab === 'code'} onClick={() => setActiveTab('code')} />
        <TabButton id="schema" label="Input Schema" active={activeTab === 'schema'} onClick={() => setActiveTab('schema')} />
        <TabButton id="functions" label="Functions" active={activeTab === 'functions'} onClick={() => setActiveTab('functions')} />
        <TabButton id="apis" label={`APIs${apiCount ? ` (${apiCount})` : ''}`} active={activeTab === 'apis'} onClick={() => setActiveTab('apis')} />
        <TabButton id="tools" label={`Tools${toolCount ? ` (${toolCount})` : ''}`} active={activeTab === 'tools'} onClick={() => setActiveTab('tools')} />
        <TabButton id="variables" label="Variables" active={activeTab === 'variables'} onClick={() => setActiveTab('variables')} />
      </div>

      {/* Tab content */}
      <div className="min-h-[200px]">
        {activeTab === 'code' && (
          <div>
            <textarea
              value={config.code}
              onChange={(e) => update({ code: e.target.value })}
              className="w-full min-h-[240px] resize-none bg-background p-3 font-mono text-xs leading-relaxed focus:outline-none"
              placeholder="# Your action logic here..."
              spellCheck={false}
            />
            <div className="border-t border-white/10 px-3 py-1.5 flex items-center justify-between">
              <span className="text-[10px] text-muted-foreground">
                {apiCount + toolCount > 0
                  ? `${apiCount} API${apiCount !== 1 ? 's' : ''} + ${toolCount} tool${toolCount !== 1 ? 's' : ''} available`
                  : 'Check APIs and Tools tabs to enable callable functions'}
              </span>
              <button
                type="button"
                onClick={runTest}
                disabled={testRunning || !agentId || !stateId}
                className={cn(
                  'px-3 py-1 text-[10px] font-medium rounded transition-colors',
                  testRunning
                    ? 'bg-muted text-muted-foreground cursor-wait'
                    : 'bg-primary/10 text-primary hover:bg-primary/20',
                )}
              >
                {testRunning ? 'Running...' : 'Run Test'}
              </button>
            </div>
            {(testResult || testError) && (
              <div className="border-t border-white/10 bg-muted/20 p-3 max-h-[200px] overflow-y-auto">
                {testError && (
                  <p className="text-xs text-red-400">{testError}</p>
                )}
                {testResult && (
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-2">
                      <span className={cn(
                        'text-[10px] font-medium px-1.5 py-0.5 rounded',
                        testResult.status === 'success' ? 'bg-emerald-500/20 text-emerald-400' :
                        testResult.status === 'timeout' ? 'bg-amber-500/20 text-amber-400' :
                        'bg-red-500/20 text-red-400',
                      )}>
                        {testResult.status}
                      </span>
                      {testResult.error && (
                        <span className="text-[10px] text-red-400">{testResult.error}</span>
                      )}
                    </div>
                    {testResult.logs.length > 0 && (
                      <div>
                        <p className="text-[10px] text-muted-foreground mb-0.5">Logs:</p>
                        {testResult.logs.map((log, i) => (
                          <pre key={i} className="text-[10px] font-mono text-foreground/80">{log}</pre>
                        ))}
                      </div>
                    )}
                    {testResult.output && (
                      <div>
                        <p className="text-[10px] text-muted-foreground mb-0.5">Output:</p>
                        <pre className="text-[10px] font-mono text-foreground/80">
                          {JSON.stringify(testResult.output, null, 2)}
                        </pre>
                      </div>
                    )}
                    {Object.keys(testResult.variables_modified).length > 0 && (
                      <div>
                        <p className="text-[10px] text-muted-foreground mb-0.5">Variables modified:</p>
                        <pre className="text-[10px] font-mono text-foreground/80">
                          {JSON.stringify(testResult.variables_modified, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {activeTab === 'schema' && (
          <textarea
            value={JSON.stringify(config.input_schema ?? {}, null, 2)}
            onChange={(e) => {
              try {
                update({ input_schema: JSON.parse(e.target.value) })
              } catch {
                // Don't update on invalid JSON
              }
            }}
            className="w-full min-h-[240px] resize-none bg-background p-3 font-mono text-xs leading-relaxed focus:outline-none"
            placeholder="{}"
            spellCheck={false}
          />
        )}

        {activeTab === 'functions' && (
          <div>
            <p className="px-3 pt-2 text-[10px] text-muted-foreground">
              Define helper functions here. They are prepended to your code before execution.
            </p>
            <textarea
              value={config.callable_functions_code}
              onChange={(e) => update({ callable_functions_code: e.target.value })}
              className="w-full min-h-[200px] resize-none bg-background p-3 font-mono text-xs leading-relaxed focus:outline-none"
              placeholder={'def format_currency(amount, currency="USD"):\n    return f"{currency} {amount:,.2f}"'}
              spellCheck={false}
            />
          </div>
        )}

        {activeTab === 'apis' && (
          <APIsTab
            agentId={agentId}
            items={catalog?.apis ?? []}
            selectedRefs={config.callable_api_refs}
            onToggle={(ref) => {
              const next = config.callable_api_refs.includes(ref)
                ? config.callable_api_refs.filter((r) => r !== ref)
                : [...config.callable_api_refs, ref]
              update({ callable_api_refs: next })
            }}
          />
        )}

        {activeTab === 'tools' && (
          <ToolsTab
            agentId={agentId}
            integrations={catalog?.integrations ?? []}
            builtin={catalog?.builtin ?? []}
            selectedIntegrations={config.callable_integrations}
            selectedSystemRefs={config.callable_system_refs}
            onToggleIntegration={(category) => {
              const next = config.callable_integrations.includes(category)
                ? config.callable_integrations.filter((c) => c !== category)
                : [...config.callable_integrations, category]
              update({ callable_integrations: next })
            }}
            onToggleSystem={(ref) => {
              const next = config.callable_system_refs.includes(ref)
                ? config.callable_system_refs.filter((r) => r !== ref)
                : [...config.callable_system_refs, ref]
              update({ callable_system_refs: next })
            }}
          />
        )}

        {activeTab === 'variables' && (
          <VariablesTab factSchema={factSchema} />
        )}
      </div>
    </div>
  )
}

// ── APIs Tab ───────────────────────────────────────────────────────────────

function APIsTab({
  agentId,
  items,
  selectedRefs,
  onToggle,
}: {
  agentId: string | null
  items: CallableCatalogItem[]
  selectedRefs: string[]
  onToggle: (ref: string) => void
}) {
  return (
    <div className="p-3 max-h-[300px] overflow-y-auto space-y-2">
      <p className="text-[10px] text-muted-foreground mb-2">
        Your organization's custom APIs. Use the callable alias shown here in Action Code; the underlying tool ref stays unchanged.
      </p>
      {!agentId ? (
        <p className="text-xs text-muted-foreground py-4 text-center">
          Save the agent first to browse callable APIs.
        </p>
      ) : items.length === 0 ? (
        <p className="text-xs text-muted-foreground py-4 text-center">No custom APIs configured.</p>
      ) : (
        items.map((item) => (
          <label
            key={item.ref}
            className="flex items-start gap-2 rounded-md border border-white/10 bg-white/5 p-2 cursor-pointer hover:bg-white/10"
          >
            <input
              type="checkbox"
              checked={selectedRefs.includes(item.ref)}
              onChange={() => onToggle(item.ref)}
              className="mt-0.5"
            />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs font-medium">{item.callable_name}</span>
                {item.http_method && (
                  <span className="text-[10px] text-muted-foreground uppercase">{item.http_method}</span>
                )}
              </div>
              <p className="text-[10px] text-muted-foreground mt-0.5">{item.description}</p>
              <div className="mt-0.5 space-y-0.5">
                <code className="block text-[10px] text-muted-foreground">{item.ref}</code>
                {item.function_name && item.function_name !== item.callable_name && (
                  <p className="text-[10px] text-muted-foreground">
                    Existing tool name: <code className="bg-muted px-1 rounded">{item.function_name}</code>
                  </p>
                )}
                {item.endpoint_path && (
                  <code className="block text-[10px] text-muted-foreground">{item.endpoint_path}</code>
                )}
              </div>
            </div>
          </label>
        ))
      )}
      <p className="text-[10px] text-muted-foreground pt-1">
        Checked APIs are callable in code as: <code className="bg-muted px-1 rounded">result = callable_name(param=value)</code>
      </p>
    </div>
  )
}

// ── Tools Tab ──────────────────────────────────────────────────────────────

function ToolsTab({
  agentId,
  integrations,
  builtin,
  selectedIntegrations,
  selectedSystemRefs,
  onToggleIntegration,
  onToggleSystem,
}: {
  agentId: string | null
  integrations: CallableCatalogItem[]
  builtin: CallableCatalogItem[]
  selectedIntegrations: string[]
  selectedSystemRefs: string[]
  onToggleIntegration: (category: string) => void
  onToggleSystem: (ref: string) => void
}) {
  // Group integrations by category (ref prefix before '.')
  const integrationGroups = useMemo(() => {
    const groups: Record<string, { category: string; providerSlug: string | null; items: CallableCatalogItem[] }> = {}
    for (const item of integrations) {
      const category = item.ref.split('.')[0]
      if (!groups[category]) {
        groups[category] = { category, providerSlug: item.provider_slug ?? null, items: [] }
      }
      groups[category].items.push(item)
    }
    return Object.values(groups)
  }, [integrations])

  return (
    <div className="p-3 max-h-[300px] overflow-y-auto space-y-3">
      <p className="text-[10px] text-muted-foreground">
        Select tools this code step can call. Use the callable alias shown below in Action Code; the underlying tool ref stays unchanged.
      </p>

      {!agentId && (
        <p className="text-xs text-muted-foreground py-4 text-center">
          Save the agent first to browse callable tools.
        </p>
      )}

      {integrationGroups.length > 0 && (
        <div className="space-y-2">
          <p className="text-[10px] font-medium text-muted-foreground">Integrations</p>
          {integrationGroups.map((group) => (
            <label
              key={group.category}
              className="flex items-start gap-2 rounded-md border border-white/10 bg-white/5 p-2 cursor-pointer hover:bg-white/10"
            >
              <input
                type="checkbox"
                checked={selectedIntegrations.includes(group.category)}
                onChange={() => onToggleIntegration(group.category)}
                className="mt-0.5"
              />
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-medium">{group.category}</span>
                  {group.providerSlug && (
                    <span className="text-[10px] text-muted-foreground capitalize">{group.providerSlug}</span>
                  )}
                </div>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Call as <code className="bg-muted px-1 rounded">{group.category}(action="...")</code>
                </p>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Available actions: {group.items.map((i) => i.ref.split('.')[1]).join(' · ')}
                </p>
              </div>
            </label>
          ))}
        </div>
      )}

      {builtin.length > 0 && (
        <div className="space-y-2">
          <p className="text-[10px] font-medium text-muted-foreground">Built-in</p>
          {builtin.map((item) => (
            <label
              key={item.ref}
              className="flex items-start gap-2 rounded-md border border-white/10 bg-white/5 p-2 cursor-pointer hover:bg-white/10"
            >
              <input
                type="checkbox"
                checked={selectedSystemRefs.includes(item.ref)}
                onChange={() => onToggleSystem(item.ref)}
                className="mt-0.5"
              />
              <div>
                <span className="text-xs font-medium">{item.display_name}</span>
                <span className="ml-2 text-[10px] text-muted-foreground">Built-in</span>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Call as <code className="bg-muted px-1 rounded">{item.callable_name}(...)</code>
                </p>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Tool ref: <code className="bg-muted px-1 rounded">{item.ref}</code>
                </p>
              </div>
            </label>
          ))}
        </div>
      )}

      {agentId && integrationGroups.length === 0 && builtin.length === 0 && (
        <p className="text-xs text-muted-foreground py-4 text-center">
          No integrations connected. Add them in the Integrations tab.
        </p>
      )}
    </div>
  )
}

// ── Variables Tab ──────────────────────────────────────────────────────────

function VariablesTab({ factSchema }: { factSchema: string[] }) {
  return (
    <div className="p-3 max-h-[300px] overflow-y-auto space-y-2">
      <p className="text-[10px] text-muted-foreground">
        Available conversation variables. Access with <code className="bg-muted px-1 rounded">vars["key"]</code> or <code className="bg-muted px-1 rounded">variables["key"]</code>.
      </p>
      {factSchema.length === 0 ? (
        <p className="text-xs text-muted-foreground py-4 text-center">
          No facts defined in this agent's fact schema.
        </p>
      ) : (
        <div className="space-y-1">
          {factSchema.map((name) => (
            <div key={name} className="flex items-center gap-2 px-2 py-1 rounded bg-muted/30">
              <code className="text-xs font-mono">{name}</code>
            </div>
          ))}
        </div>
      )}
      <p className="text-[10px] text-muted-foreground pt-1">
        Write new variables with <code className="bg-muted px-1 rounded">variables["key"] = value</code>. They persist to conversation state.
      </p>
    </div>
  )
}
