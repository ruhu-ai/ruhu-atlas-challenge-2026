/**
 * Setup Checklist
 *
 * Post-clone onboarding screen — shown right after a user creates an agent
 * from a template that references org-scoped tools they haven't configured
 * yet. Drives the satisfaction state from
 * GET /agent-templates/:id/required-tools, polling every 5s while the page
 * is foregrounded so the row flips to ✓ when the user finishes setup in
 * another tab.
 *
 * See docs/templates/Template-Required-Tools-Onboarding-Spec.md §5.6.2.
 */

import { useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  ArrowRight,
  Loader2,
  Plug,
} from 'lucide-react'

import { Button } from '@/components/atoms/button'
import { Badge } from '@/components/atoms/badge'
import { agentTemplateService } from '@/api/services/template.service'

export interface SetupChecklistProps {
  agentId: string
  agentName?: string
  /**
   * Source template ID — typically carried via URL query string from
   * the post-clone redirect; the page wrapper falls back to
   * agent_settings.source_template_id when the query param is absent.
   * Null only when the agent was never cloned from a template.
   */
  templateId: string | null
  /**
   * True while we're still resolving template provenance from
   * settings (avoids flashing the "no template provenance" empty
   * state during the lookup).
   */
  provenanceLoading?: boolean
}

const POLL_INTERVAL_MS = 5_000

export function SetupChecklist({
  agentId,
  agentName,
  templateId,
  provenanceLoading = false,
}: SetupChecklistProps) {
  const navigate = useNavigate()

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['agent-template-required-tools', templateId],
    queryFn: () =>
      templateId ? agentTemplateService.getRequiredTools(templateId) : Promise.resolve(null),
    enabled: Boolean(templateId),
    refetchInterval: POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    staleTime: 0,
  })

  // Surface fresh satisfaction state when the user comes back from the
  // Integrations tab after configuring a tool. Refetching on focus is
  // the cheap version of bidirectional pub/sub.
  useEffect(() => {
    const onFocus = () => {
      void refetch()
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refetch])

  const tools = data?.tools ?? []
  const requiredTools = tools.filter((t) => t.required)
  const optionalTools = tools.filter((t) => !t.required)
  // Publish gate is only on required tools (Axis 1 of the gradient).
  // Optional tools warn but don't block — they degrade gracefully at
  // runtime via Axis 2's per-step error outcomes (when shipped).
  const requiredUnsatisfiedCount = requiredTools.filter((t) => t.satisfied === false).length
  const optionalUnsatisfiedCount = optionalTools.filter((t) => t.satisfied === false).length
  const allRequiredSatisfied = requiredUnsatisfiedCount === 0

  // Resolve a template's agent-relative setup_url_path against the
  // current agent. Templates use paths like `canvas?view=library` (no
  // leading slash) so they don't need to know the agent id at author
  // time. Absolute paths (leading /) are passed through unchanged.
  const resolveSetupUrl = useCallback(
    (templatePath: string): string =>
      templatePath.startsWith('/') ? templatePath : `/agents/${agentId}/${templatePath}`,
    [agentId],
  )

  const handleContinue = () => {
    navigate(`/agents/${agentId}/canvas`)
  }

  const handleSkip = () => {
    navigate(`/agents/${agentId}/canvas`)
  }

  if (provenanceLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
        <span className="ml-2 text-muted-foreground">Loading setup checklist…</span>
      </div>
    )
  }

  if (!templateId) {
    // No provenance — likely an agent not cloned from a template. The
    // setup checklist has nothing to show; bounce to the canvas.
    return (
      <div className="max-w-2xl mx-auto py-12 px-6">
        <p className="text-muted-foreground">
          This agent has no template provenance — no setup checklist available.
        </p>
        <Button onClick={handleContinue} className="mt-4">
          Continue to canvas
        </Button>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
        <span className="ml-2 text-muted-foreground">Loading setup checklist…</span>
      </div>
    )
  }

  const renderToolRow = (tool: typeof tools[number]) => {
    const satisfied = tool.satisfied === true
    return (
      <div
        key={tool.tool_ref}
        className="rounded-lg border bg-card p-4 transition-colors"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              {satisfied ? (
                <CheckCircle2 className="h-5 w-5 text-emerald-600 flex-shrink-0" />
              ) : (
                <AlertCircle className="h-5 w-5 text-amber-600 flex-shrink-0" />
              )}
              <h3 className="font-medium">{tool.display_name}</h3>
              <code className="text-xs text-muted-foreground font-mono">
                {tool.tool_ref}
              </code>
            </div>
            <p className="text-sm text-muted-foreground ml-7">{tool.description}</p>
            {tool.provider_hints.length > 0 && (
              <div className="mt-2 ml-7 flex flex-wrap gap-1">
                {tool.provider_hints.map((p) => (
                  <Badge key={p} variant="outline" className="text-xs">
                    {p}
                  </Badge>
                ))}
              </div>
            )}
          </div>
          <div className="flex-shrink-0">
            {satisfied ? (
              <Badge variant="outline" className="border-emerald-300 bg-emerald-50 text-emerald-900">
                Configured
              </Badge>
            ) : (
              <Button
                size="sm"
                variant={tool.required ? 'primary' : 'outline'}
                onClick={() => navigate(resolveSetupUrl(tool.setup_url_path))}
              >
                Set up
                <ExternalLink className="ml-1 h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </div>
        {tool.documentation_url && (
          <div className="mt-3 ml-7">
            <a
              href={tool.documentation_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-primary hover:underline inline-flex items-center gap-1"
            >
              Documentation
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto py-10 px-6">
      <div className="mb-8">
        <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
          <Plug className="h-4 w-4" />
          <span>Onboarding</span>
        </div>
        <h1 className="text-2xl font-semibold mb-2">
          Set up connections
          {agentName ? ` for "${agentName}"` : ''}
        </h1>
        <p className="text-sm text-muted-foreground">
          {tools.length === 0
            ? 'This agent has no external integrations to configure. You can publish straight away.'
            : requiredTools.length === 0
              ? `This agent uses ${optionalTools.length} optional integration${optionalTools.length === 1 ? '' : 's'}. None are required to publish.`
              : `This agent needs ${requiredTools.length} integration${requiredTools.length === 1 ? '' : 's'} configured before it can publish${optionalTools.length > 0 ? `, plus ${optionalTools.length} optional one${optionalTools.length === 1 ? '' : 's'}` : ''}.`}
        </p>
      </div>

      {requiredTools.length > 0 && (
        <div className="mb-8">
          <div className="mb-3 flex items-baseline gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Required
            </h2>
            <span className="text-xs text-muted-foreground">
              {requiredTools.length - requiredUnsatisfiedCount}/{requiredTools.length} configured
            </span>
          </div>
          <p className="text-xs text-muted-foreground mb-3">
            These integrations must be configured before you can publish.
          </p>
          <div className="space-y-3">
            {requiredTools.map(renderToolRow)}
          </div>
        </div>
      )}

      {optionalTools.length > 0 && (
        <div className="mb-8">
          <div className="mb-3 flex items-baseline gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Optional
            </h2>
            <span className="text-xs text-muted-foreground">
              {optionalTools.length - optionalUnsatisfiedCount}/{optionalTools.length} configured
            </span>
          </div>
          <p className="text-xs text-muted-foreground mb-3">
            These integrations enable additional branches (e.g. alternate
            resolution paths). You can publish without them — branches that
            need them will fail gracefully at runtime; all other paths work.
          </p>
          <div className="space-y-3">
            {optionalTools.map(renderToolRow)}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 pt-4 border-t">
        <Button onClick={handleContinue} disabled={!allRequiredSatisfied}>
          Continue to canvas
          <ArrowRight className="ml-2 h-4 w-4" />
        </Button>
        {!allRequiredSatisfied && (
          <Button variant="ghost" onClick={handleSkip}>
            Skip — I'll do this later
          </Button>
        )}
        {!allRequiredSatisfied && (
          <span className="text-xs text-muted-foreground ml-auto">
            {requiredUnsatisfiedCount} required integration{requiredUnsatisfiedCount === 1 ? '' : 's'} still needed before you can publish.
          </span>
        )}
        {allRequiredSatisfied && optionalUnsatisfiedCount > 0 && (
          <span className="text-xs text-muted-foreground ml-auto">
            {optionalUnsatisfiedCount} optional integration{optionalUnsatisfiedCount === 1 ? '' : 's'} not yet set up — publishing is unblocked.
          </span>
        )}
      </div>
    </div>
  )
}
