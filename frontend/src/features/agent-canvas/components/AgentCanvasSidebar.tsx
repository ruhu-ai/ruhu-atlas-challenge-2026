/**
 * Agent Canvas Sidebar
 *
 * Left sidebar navigation for agent canvas with context-sensitive content.
 * Expandable sidebar for agent authoring.
 *
 * Modes:
 * - Canvas: Shows Node Palette for building workflows
 * - Channels: Shows channel configuration
 */

import {
  ArrowLeft,
  ClipboardCheck,
  File,
  FileText,
  GitBranch,
  Globe,
  Library,
  Network,
  Plus,
  Radio,
  Rocket,
  Shield,
  User,
  Workflow,
} from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { cn } from '@/lib/utils'
import type { AgentSummary } from '@/types/agent-definition'

export interface SidebarScenarioItem {
  id: string
  name: string
  isStart: boolean
}

export type SidebarView =
  | 'canvas'
  | 'persona'
  | 'rules'
  | 'supporting-docs'
  | 'channels'
  | 'library'
  | 'widget'
  | 'testing'
  | 'releases'
  | 'versions'

interface AgentCanvasSidebarProps {
  activeView: SidebarView
  onViewChange: (view: SidebarView) => void
  onBack: () => void
  supportedViews?: SidebarView[]
  isNewAgent?: boolean
  agents?: AgentSummary[]
  selectedAgentId?: string | null
  onSelectAgent?: (id: string) => void
  /**
   * Scenarios under the currently selected agent. Listed in the sidebar
   * Clicking a scenario navigates to it in the active surface.
   */
  agentScenarios?: SidebarScenarioItem[]
  selectedScenarioId?: string | null
  onSelectScenario?: (scenarioId: string) => void
  onAddScenario?: () => void
  agentFactCount?: number
}

export function AgentCanvasSidebar({
  activeView,
  onViewChange,
  onBack,
  supportedViews,
  isNewAgent = false,
  agents = [],
  selectedAgentId,
  onSelectAgent,
  agentScenarios = [],
  selectedScenarioId,
  onSelectScenario,
  onAddScenario,
  agentFactCount = 0,
}: AgentCanvasSidebarProps) {
  const handleViewClick = (view: SidebarView) => {
    onViewChange(view)
  }

  const supports = (view: SidebarView): boolean => supportedViews == null || supportedViews.includes(view)

  return (
    <div className="flex h-full w-64 flex-col border-r border-white/10 bg-card/50">
      {/* Back button */}
      <div className="border-b border-white/10 p-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          className="w-full justify-start text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Agents
        </Button>
      </div>

      {/* Navigation */}
      <div className="border-b border-white/10 p-2 space-y-0.5">
        {/* Canvas */}
        {supports('canvas') && (
          <button
            onClick={() => handleViewClick('canvas')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'canvas'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <Network className="h-5 w-5 shrink-0" />
            Canvas
          </button>
        )}

        {/* Persona — identity + behaviour. Sits between Canvas (logic) and the
            channel-shaped views (Channels/Widget) so the workflow reads
            "build → identity → channels → tools → publish". */}
        {supports('persona') && (
          <button
            onClick={() => handleViewClick('persona')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'persona'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
            data-testid="sidebar-persona-button"
          >
            <User className="h-5 w-5 shrink-0" />
            Persona
          </button>
        )}

        {/* Rules */}
        {supports('rules') && (
          <button
            onClick={() => handleViewClick('rules')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'rules'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <Shield className="h-5 w-5 shrink-0" />
            Rules
          </button>
        )}

        {/* Supporting Docs */}
        {supports('supporting-docs') && (
          <button
            onClick={() => handleViewClick('supporting-docs')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'supporting-docs'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <FileText className="h-5 w-5 shrink-0" />
            Knowledge
          </button>
        )}

        {/* Channels */}
        {supports('channels') && (
          <button
            onClick={() => handleViewClick('channels')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'channels'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <Radio className="h-5 w-5 shrink-0" />
            Channels
          </button>
        )}

        {/* Library — unified surface for tools (Custom APIs / code / system)
            and provider connections (formerly the standalone Integrations
            page). Tools are the primary noun and connections are an
            attribute of tools. */}
        {supports('library') && (
          <button
            onClick={() => handleViewClick('library')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'library'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <Library className="h-5 w-5 shrink-0" />
            Library
          </button>
        )}

        {/* Widget */}
        {supports('widget') && (
          <button
            onClick={() => handleViewClick('widget')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'widget'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <Globe className="h-5 w-5 shrink-0" />
            Widget
          </button>
        )}

        {/* Evaluation */}
        {supports('testing') && (
          <button
            onClick={() => handleViewClick('testing')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'testing'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <ClipboardCheck className="h-5 w-5 shrink-0" />
            Evaluation
          </button>
        )}

        {/* Publish */}
        {supports('releases') && (
          <button
            onClick={() => handleViewClick('releases')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'releases'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <Rocket className="h-5 w-5 shrink-0" />
            Publish
          </button>
        )}

        {/* Versions */}
        {supports('versions') && (
          <button
            onClick={() => handleViewClick('versions')}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              activeView === 'versions'
                ? 'bg-gray-500/20 text-foreground'
                : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground'
            )}
          >
            <GitBranch className="h-5 w-5 shrink-0" />
            Versions
          </button>
        )}

        {/* Agent definition navigation */}
        {(
          <div className="mt-3 border-t border-white/10 pt-3">
            <div className="flex items-center justify-between px-3 pb-1">
              <span className="text-xs font-semibold text-muted-foreground">
                Agent Definitions
              </span>
            </div>
            <div className="space-y-0.5">
              {agents.map((agent) => {
                const isActive = agent.id === selectedAgentId
                return (
                  <button
                    key={agent.id}
                    onClick={() => onSelectAgent?.(agent.id)}
                    className={cn(
                      'flex w-full items-center gap-2.5 rounded-md px-3 py-1.5 text-left text-sm transition-colors',
                      isActive
                        ? 'bg-gray-500/20 text-foreground'
                        : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground',
                    )}
                  >
                    <Workflow className="h-4 w-4 shrink-0" />
                    <span className="flex-1 truncate">{agent.name}</span>
                  </button>
                )
              })}
              {agents.length === 0 && (
                <p className="px-3 py-2 text-xs text-muted-foreground/60">No agent definitions yet.</p>
              )}
            </div>

            <div className="mt-3 flex items-center justify-between px-3 pb-1">
              <span className="text-xs font-semibold text-muted-foreground">
                Scenarios
              </span>
              {onAddScenario && (
                <button
                  onClick={onAddScenario}
                  className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-gray-500/10 hover:text-foreground"
                  title="Add scenario"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <div className="space-y-0.5">
              {agentScenarios.map((scenario) => {
                const isActive = scenario.id === selectedScenarioId
                return (
                  <button
                    key={scenario.id}
                    onClick={() => onSelectScenario?.(scenario.id)}
                    className={cn(
                      'flex w-full items-center gap-2.5 rounded-md px-3 py-1.5 text-left text-sm transition-colors',
                      isActive
                        ? 'bg-gray-500/20 text-foreground'
                        : 'text-muted-foreground hover:bg-gray-500/10 hover:text-foreground',
                    )}
                  >
                    <File className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 flex-1 truncate">{scenario.name}</span>
                    {scenario.isStart && (
                      <span className="shrink-0 text-[10px] text-amber-300" title="Start scenario">
                        ★
                      </span>
                    )}
                  </button>
                )
              })}
              {agentScenarios.length === 0 && (
                <p className="px-3 py-2 text-xs text-muted-foreground/60">No scenarios yet.</p>
              )}
            </div>

            <div className="mt-3 rounded-md border border-white/10 bg-white/5 px-3 py-2 text-xs text-muted-foreground">
              {agentScenarios.length} {agentScenarios.length === 1 ? 'scenario' : 'scenarios'} · {agentFactCount} facts
            </div>
          </div>
        )}
      </div>

      {/* Context-sensitive content */}
      <div className="flex-1 overflow-y-auto">
        {/* Rules placeholder */}
        {activeView === 'rules' && (
          <div className="p-3">
            <p className="text-xs text-muted-foreground">
              Manage agent rules in the main panel →
            </p>
          </div>
        )}

        {/* Knowledge placeholder */}
        {activeView === 'supporting-docs' && (
          <div className="p-3">
            <p className="text-xs text-muted-foreground">
              Manage knowledge documents in the main panel →
            </p>
          </div>
        )}

        {/* Channels placeholder */}
        {activeView === 'channels' && (
          <div className="p-3">
            <p className="text-xs text-muted-foreground">
              Configure deployment channels in the main panel →
            </p>
          </div>
        )}

        {/* Library placeholder */}
        {activeView === 'library' && (
          <div className="p-3">
            <p className="text-xs text-muted-foreground">
              Author tools and connect CRM / Calendar / Ticketing in the
              main panel →
            </p>
          </div>
        )}

        {/* Evaluation placeholder */}
        {activeView === 'testing' && (
          <div className="p-3">
            <p className="text-xs text-muted-foreground">
              Run evaluations and configure deploy gates in the main panel →
            </p>
          </div>
        )}

        {/* Releases placeholder */}
        {activeView === 'releases' && (
          <div className="p-3">
            <p className="text-xs text-muted-foreground">
              Review publish readiness and release the current agent draft in the main panel →
            </p>
          </div>
        )}
      </div>

      {/* Save reminder for new agents */}
      {isNewAgent && activeView !== 'canvas' && (
        <div className="border-t border-white/10 p-3">
          <div className="rounded-md bg-muted border border-border p-2">
            <p className="text-xs text-muted-foreground">
              Save the agent first to configure this section.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
