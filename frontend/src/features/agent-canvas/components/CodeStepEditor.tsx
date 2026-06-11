/**
 * CodeStepEditor — tabbed code step editor.
 *
 * Tabs:
 *   Code         — main Python code (Monaco editor)
 *   Input Schema — optional JSON schema for step inputs
 *   Functions    — inline helper function definitions only
 *   Tools        — select which external tools this step can call
 *   Variables    — read-only view of scenario variables
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CodeEditor } from '@/components/molecules/code-editor'
import { Checkbox } from '@/components/atoms/checkbox'
import { Badge } from '@/components/atoms/badge'
import { cn } from '@/lib/utils'
import { toolService, type ExternalToolCatalogItem } from '@/api/services/tools.service'

interface CodeStepEditorProps {
  code: string
  language: string
  callableToolRefs: string[]
  callableFunctionsCode: string
  inputSchema: Record<string, unknown> | null | undefined
  agentId?: string
  scenarioVariables?: string[]
  onChange: (updates: {
    code?: string
    language?: string
    callable_tool_refs?: string[]
    callable_functions_code?: string
    input_schema?: Record<string, unknown> | null
  }) => void
}

type Tab = 'code' | 'schema' | 'functions' | 'tools' | 'variables'

const TABS: { id: Tab; label: string }[] = [
  { id: 'code', label: 'Code' },
  { id: 'schema', label: 'Input Schema' },
  { id: 'functions', label: 'Functions' },
  { id: 'tools', label: 'Tools' },
  { id: 'variables', label: 'Variables' },
]

export function CodeStepEditor({
  code,
  language,
  callableToolRefs,
  callableFunctionsCode,
  inputSchema,
  agentId,
  scenarioVariables = [],
  onChange,
}: CodeStepEditorProps) {
  const [activeTab, setActiveTab] = useState<Tab>('code')

  // Fetch agent's external tool catalog for the Tools tab
  const isValidAgentId = !!agentId && agentId !== 'new' && !agentId.includes('/')
  const { data: catalog = [] } = useQuery({
    queryKey: ['tool-catalog', agentId],
    queryFn: () => toolService.getCatalog(agentId!),
    enabled: isValidAgentId,
    staleTime: 30_000,
  })

  const toggleToolRef = (ref: string) => {
    const current = new Set(callableToolRefs)
    if (current.has(ref)) {
      current.delete(ref)
    } else {
      current.add(ref)
    }
    onChange({ callable_tool_refs: Array.from(current) })
  }

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      {/* Tab bar */}
      <div className="flex border-b border-border bg-muted/30">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'px-4 py-2 text-xs font-medium transition-colors border-b-2 -mb-px',
              activeTab === tab.id
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {tab.label}
            {tab.id === 'tools' && callableToolRefs.length > 0 && (
              <Badge variant="secondary" className="ml-1.5 text-[10px] px-1.5 py-0">
                {callableToolRefs.length}
              </Badge>
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'code' && (
          <div>
            <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border bg-muted/20">
              <span className="inline-flex h-7 items-center rounded border border-border bg-background px-2 text-xs font-medium text-foreground">
                Python
              </span>
              {callableToolRefs.length > 0 && (
                <span className="text-[10px] text-muted-foreground">
                  {callableToolRefs.length} tool{callableToolRefs.length !== 1 ? 's' : ''} available as callable functions
                </span>
              )}
            </div>
            <CodeEditor
              value={code}
              onChange={(val) => onChange({ code: val })}
              language={language}
              height="280px"
            />
          </div>
        )}

        {activeTab === 'schema' && (
          <CodeEditor
            value={inputSchema ? JSON.stringify(inputSchema, null, 2) : '{}'}
            onChange={(val) => {
              try {
                onChange({ input_schema: JSON.parse(val) })
              } catch {
                // Don't update on invalid JSON — user is still typing
              }
            }}
            language="json"
            height="240px"
          />
        )}

        {activeTab === 'functions' && (
          <div>
            <div className="px-3 py-1.5 border-b border-border bg-muted/20">
              <p className="text-[10px] text-muted-foreground">
                Define helper functions here. Only function definitions are allowed.
              </p>
            </div>
            <CodeEditor
              value={callableFunctionsCode}
              onChange={(val) => onChange({ callable_functions_code: val })}
              language="python"
              height="240px"
              placeholder="def format_currency(amount, currency='USD'):\n    return f'{currency} {amount:,.2f}'"
            />
          </div>
        )}

        {activeTab === 'tools' && (
          <div className="p-4 max-h-[300px] overflow-y-auto">
            {catalog.length === 0 ? (
              <div className="text-center py-8 text-sm text-muted-foreground">
                {agentId
                  ? 'No external tools available for this agent. Create tools on the Tools & APIs page.'
                  : 'Save the agent first to see available tools.'}
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground mb-3">
                  Select tools this code step can call as functions. Selected tools become available as
                  {' '}<code className="text-[11px] bg-muted px-1 rounded">result = tool_name(param=value)</code>.
                </p>
                {catalog.map((tool: ExternalToolCatalogItem) => {
                  const isSelected = callableToolRefs.includes(tool.ref)
                  return (
                    <label
                      key={tool.ref}
                      className={cn(
                        'flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors',
                        isSelected ? 'border-cyan-500 bg-cyan-500/5' : 'border-border hover:border-muted-foreground/30',
                      )}
                    >
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => toggleToolRef(tool.ref)}
                        className="mt-0.5"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{tool.display_name}</span>
                          <Badge variant="outline" className="text-[10px]">{tool.provider}</Badge>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                          {tool.description}
                        </p>
                        <code className="text-[10px] text-muted-foreground mt-1 block font-mono">
                          {tool.function_name}()
                        </code>
                      </div>
                    </label>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {activeTab === 'variables' && (
          <div className="p-4 max-h-[300px] overflow-y-auto">
            <p className="text-xs text-muted-foreground mb-3">
              Access variables via <code className="text-[11px] bg-muted px-1 rounded">vars["name"]</code> or
              {' '}<code className="text-[11px] bg-muted px-1 rounded">variables["name"]</code>.
              Modified values persist across steps.
            </p>
            {scenarioVariables.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                No scenario variables defined yet.
              </p>
            ) : (
              <div className="space-y-1">
                {scenarioVariables.map((name) => (
                  <div key={name} className="flex items-center gap-2 px-2 py-1 rounded bg-muted/50">
                    <code className="text-xs font-mono">{name}</code>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
