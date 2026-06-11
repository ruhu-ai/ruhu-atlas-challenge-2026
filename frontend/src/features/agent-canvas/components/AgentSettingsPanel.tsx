import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Checkbox } from '@/components/atoms/checkbox'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { LLM_MODELS, LLM_PROVIDERS } from '@/types/agent'
import type { AgentSettings, AgentType, AgentLLMProvider } from '@/types/agent-definition'
import { PersonaSummaryCard } from './PersonaSummaryCard'

type AgentStatus = 'draft' | 'published' | 'active' | 'archived'

interface AgentSettingsPanelProps {
  settings: AgentSettings
  onChange: (next: AgentSettings) => void
  agentName?: string
  onNameChange?: (name: string) => void
  status?: AgentStatus
  onStatusChange?: (status: AgentStatus) => void
  /** Switches the canvas sidebar to the Persona tab. The summary card calls
   * this from its Edit button so settings remain a single source of truth
   * for persona (no editing in the drawer). */
  onOpenPersonaTab?: () => void
}

export function AgentSettingsPanel({
  settings,
  onChange,
  agentName,
  onNameChange,
  status,
  onStatusChange,
  onOpenPersonaTab,
}: AgentSettingsPanelProps) {
  const selectableLlmProviders = LLM_PROVIDERS.filter((provider) => provider.available !== false)

  const update = (patch: Partial<AgentSettings>) => onChange({ ...settings, ...patch })

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <h3 className="font-medium">Agent Settings</h3>
      </div>
      <div className="space-y-4 p-4">
        {onNameChange !== undefined && (
          <div className="space-y-1.5">
            <Label htmlFor="agentNamePanel" className="text-xs text-muted-foreground">
              Agent Name
            </Label>
            <Input
              id="agentNamePanel"
              value={agentName ?? ''}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder="Enter agent name"
              className="h-8 text-sm"
            />
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="agent-description-panel" className="text-xs text-muted-foreground">
            Description
          </Label>
          <textarea
            id="agent-description-panel"
            value={settings.description}
            onChange={(event) => update({ description: event.target.value })}
            placeholder="Describe what this agent does..."
            className="min-h-[60px] w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          />
        </div>

        {onStatusChange !== undefined && status !== undefined && (
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Status</Label>
            <Select value={status} onValueChange={(v) => onStatusChange(v as AgentStatus)}>
              <SelectTrigger className="h-8 text-sm">
                <SelectValue placeholder="Select status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="draft">Draft</SelectItem>
                <SelectItem value="published">Published (Live)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Agent Type</Label>
          <Select
            value={settings.agent_type}
            onValueChange={(value) => update({ agent_type: value as AgentType })}
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="chat">Chat</SelectItem>
              <SelectItem value="voice">Voice</SelectItem>
              <SelectItem value="multimodal">Multimodal</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {onOpenPersonaTab && (
          <PersonaSummaryCard
            persona={settings.persona ?? null}
            onOpenPersonaTab={onOpenPersonaTab}
          />
        )}

        <div className="border-t border-white/10 pt-4">
          <p className="mb-3 text-xs font-medium text-muted-foreground">AI Model</p>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Provider</Label>
          <Select
            value={settings.llm_config.provider}
            onValueChange={(value) =>
              update({
                llm_config: {
                  ...settings.llm_config,
                  provider: value as AgentLLMProvider,
                  model: (LLM_MODELS[value as AgentLLMProvider] || [])[0]?.value || settings.llm_config.model,
                },
              })
            }
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {selectableLlmProviders.map((provider) => (
                <SelectItem key={provider.value} value={provider.value}>
                  {provider.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Model</Label>
          <Select
            value={settings.llm_config.model}
            onValueChange={(value) =>
              update({
                llm_config: {
                  ...settings.llm_config,
                  model: value,
                },
              })
            }
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(LLM_MODELS[settings.llm_config.provider] || []).map((model) => (
                <SelectItem key={model.value} value={model.value}>
                  {model.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">
            Temperature: {settings.llm_config.temperature.toFixed(1)}
          </Label>
          <input
            type="range"
            min="0"
            max="2"
            step="0.1"
            value={settings.llm_config.temperature}
            onChange={(event) =>
              update({
                llm_config: {
                  ...settings.llm_config,
                  temperature: Number(event.target.value) || 0,
                },
              })
            }
            className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-slate-700"
          />
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Intent Classifier</Label>
          <Select
            value={settings.llm_config.classifier.strategy}
            onValueChange={(value) =>
              update({
                llm_config: {
                  ...settings.llm_config,
                  classifier: {
                    ...settings.llm_config.classifier,
                    strategy: value as AgentSettings['llm_config']['classifier']['strategy'],
                  },
                },
              })
            }
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="off">Off (Runtime-only)</SelectItem>
              <SelectItem value="main_llm">Main LLM (Vertex Gemini)</SelectItem>
              <SelectItem value="prefill">Prefill (LoRA — requires trained adapter)</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {settings.llm_config.classifier.strategy === 'prefill'
              ? 'Backend rejects prefill until a production-status LoRA exists for this agent.'
              : settings.llm_config.classifier.strategy === 'main_llm'
                ? 'A frontier LLM classifies each turn against the step intent catalog.'
                : 'Skips classification — kernel routes only on facts, tool outcomes, or otherwise.'}
          </p>
        </div>

        {(settings.agent_type === 'voice' || settings.agent_type === 'multimodal') && (
          <>
            <div className="border-t border-white/10 pt-4">
              <p className="mb-3 text-xs font-medium text-muted-foreground">Voice</p>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Voice</Label>
              <Select
                value={settings.voice_config.voice_id}
                onValueChange={(value) =>
                  update({
                    voice_config: {
                      ...settings.voice_config,
                      voice_id: value,
                    },
                  })
                }
              >
                <SelectTrigger className="h-8 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="en-US-Chirp3-HD-Kore">Kore (US Neutral)</SelectItem>
                  <SelectItem value="en-US-Chirp3-HD-Leda">Leda (US Female)</SelectItem>
                  <SelectItem value="en-US-Chirp3-HD-Orus">Orus (US Male)</SelectItem>
                  <SelectItem value="en-GB-Chirp3-HD-Aoede">Aoede (UK Female)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </>
        )}

        <div className="rounded-lg border border-dashed border-white/10 bg-white/5 px-3 py-2 text-xs text-muted-foreground">
          Agent settings are persisted on the agent registration itself. Workflow authoring, evaluation, and publish now
          operate on the same definition contract.
        </div>

        <div className="border-t border-white/10 pt-4">
          <p className="mb-3 text-xs font-medium text-muted-foreground">System Prompt</p>
        </div>
        <div className="space-y-1.5">
          <textarea
            value={settings.system_prompt}
            onChange={(event) => update({ system_prompt: event.target.value })}
            placeholder="You are a helpful AI assistant..."
            className="min-h-[140px] w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-xs font-mono ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          />
        </div>

        <div className="flex items-center gap-2 rounded-md border border-border/80 bg-background/60 px-3 py-2 text-xs text-muted-foreground">
          <Checkbox checked disabled />
          <span>Knowledge source access is configured from the Knowledge tab.</span>
        </div>
      </div>
    </div>
  )
}
