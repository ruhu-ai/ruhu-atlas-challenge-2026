import { Sparkles } from 'lucide-react'

import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Textarea } from '@/components/atoms/textarea'
import { useAgentDocument } from '@/features/agent-canvas/contexts/AgentDocumentContext'

function csvToList(value: string): string[] {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function listToCsv(values: string[] | undefined): string {
  return (values ?? []).join(', ')
}

/**
 * Right-column companion to AgentSettingsPanel. Binds to the document-level
 * ``agent_capability_manifest`` so the authored identity / capabilities /
 * limitations stay reachable while a scenario is being edited.
 *
 * System prompt lives in AgentSettingsPanel (settings.system_prompt).
 * Identity here is the assistant's voice / positioning; capabilities and
 * limitations are how the agent narrates what it can and can't do.
 */
export function IdentityAndCapabilitiesCard() {
  const { document, updateDocument, isLoading, isError, hasAgentId } = useAgentDocument()

  const manifest = document.agent_capability_manifest ?? {
    assistant_identity: '',
    capabilities: [],
    limitations: [],
  }

  if (!hasAgentId || isLoading || isError) {
    return null
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-muted-foreground" />
          <h3 className="font-medium">Identity &amp; Capabilities</h3>
        </div>
      </div>
      <div className="space-y-4 p-4">
        <div className="space-y-1.5">
          <Label htmlFor="manifest_identity" className="text-xs text-muted-foreground">
            Assistant identity
          </Label>
          <Textarea
            id="manifest_identity"
            value={manifest.assistant_identity}
            onChange={(event) =>
              updateDocument((previous) => ({
                ...previous,
                agent_capability_manifest: {
                  assistant_identity: event.target.value,
                  capabilities: previous.agent_capability_manifest?.capabilities ?? [],
                  limitations: previous.agent_capability_manifest?.limitations ?? [],
                },
              }))
            }
            rows={3}
            placeholder="Who is this agent? e.g. I'm Ruhu's sales assistant."
            className="text-sm"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="manifest_capabilities" className="text-xs text-muted-foreground">
            Capabilities
          </Label>
          <Input
            id="manifest_capabilities"
            value={listToCsv(manifest.capabilities)}
            onChange={(event) =>
              updateDocument((previous) => ({
                ...previous,
                agent_capability_manifest: {
                  assistant_identity: previous.agent_capability_manifest?.assistant_identity ?? '',
                  capabilities: csvToList(event.target.value),
                  limitations: previous.agent_capability_manifest?.limitations ?? [],
                },
              }))
            }
            placeholder="answer product questions, book demos"
            className="h-8 text-sm"
          />
          <p className="text-[11px] text-muted-foreground">
            Comma-separated. Used by the runtime to answer &ldquo;what can you do?&rdquo; questions.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="manifest_limitations" className="text-xs text-muted-foreground">
            Limitations
          </Label>
          <Input
            id="manifest_limitations"
            value={listToCsv(manifest.limitations)}
            onChange={(event) =>
              updateDocument((previous) => ({
                ...previous,
                agent_capability_manifest: {
                  assistant_identity: previous.agent_capability_manifest?.assistant_identity ?? '',
                  capabilities: previous.agent_capability_manifest?.capabilities ?? [],
                  limitations: csvToList(event.target.value),
                },
              }))
            }
            placeholder="no external actions without configured tools"
            className="h-8 text-sm"
          />
          <p className="text-[11px] text-muted-foreground">
            Comma-separated. Sets the &ldquo;what I can&rsquo;t do&rdquo; contract.
          </p>
        </div>
      </div>
    </div>
  )
}
