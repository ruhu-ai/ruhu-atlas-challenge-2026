/**
 * Read-only persona snapshot rendered inside `AgentSettingsPanel`.
 *
 * Shows the customer-facing identity (name, pronouns, role) at a glance
 * with a button that switches the canvas sidebar to the dedicated Persona
 * tab. The summary intentionally does not include behavioural fields
 * (formality, restricted topics) — those are separated by lifecycle
 * (versioned, draft → publish) and live in the Persona tab itself.
 */
import { ArrowUpRight, Sparkles } from 'lucide-react'

import { Button } from '@/components/atoms/button'
import type { CosmeticPersona } from '@/types/agent-definition'

export interface PersonaSummaryCardProps {
  persona?: CosmeticPersona | null
  onOpenPersonaTab: () => void
}

export function PersonaSummaryCard({ persona, onOpenPersonaTab }: PersonaSummaryCardProps) {
  const hasPersona = !!(
    persona &&
    (persona.persona_name ||
      persona.role_title ||
      persona.pronouns ||
      persona.avatar_url ||
      persona.greeting_template ||
      persona.signoff_template)
  )

  const pronounsLabel =
    persona?.pronouns === 'custom' ? persona?.pronouns_custom : persona?.pronouns

  return (
    <div
      className="space-y-2 rounded-md border border-border/80 bg-background/60 p-3"
      data-testid="persona-summary-card"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5" />
          Persona
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 gap-1 px-1.5 text-xs"
          onClick={onOpenPersonaTab}
          data-testid="persona-summary-edit"
        >
          Edit
          <ArrowUpRight className="h-3 w-3" />
        </Button>
      </div>

      {hasPersona ? (
        <div className="space-y-1 text-xs">
          {persona?.persona_name && (
            <p className="font-medium text-foreground">{persona.persona_name}</p>
          )}
          {(persona?.role_title || pronounsLabel) && (
            <p className="text-muted-foreground">
              {[persona?.role_title, pronounsLabel].filter(Boolean).join(' · ')}
            </p>
          )}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          No persona set. The agent will speak as a generic assistant.
        </p>
      )}
    </div>
  )
}
