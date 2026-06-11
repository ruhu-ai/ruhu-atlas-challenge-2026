/**
 * Reducer-based state management for agent configuration fields.
 *
 * Consolidates the ~12 related agent config useState calls into a single
 * useReducer, reducing re-render scope and providing a clean dispatch API.
 */

import { useReducer } from 'react'
import type { AgentType, AgentStatus, LLMProvider } from '@/types/agent'
import { LLM_MODELS } from '@/types/agent'

export type ClassifierStrategy = 'off' | 'main_llm' | 'prefill'

export interface AgentConfigState {
  agentName: string
  agentDescription: string
  systemPrompt: string
  status: AgentStatus
  llmProvider: LLMProvider
  llmModel: string
  temperature: string
  classifierStrategy: ClassifierStrategy
  voice: string
  agentType: AgentType
  updatedAt: Date | null
  canvasVersionId: string | null
  canvasVersionNumber: number
}

type AgentConfigAction =
  | { type: 'SET_FIELD'; field: keyof AgentConfigState; value: any }
  | { type: 'SET_LLM_PROVIDER'; provider: LLMProvider }
  | { type: 'LOAD_FROM_AGENT'; agent: any }

const DEFAULT_LLM_TEMPERATURE = 1.0

function agentConfigReducer(state: AgentConfigState, action: AgentConfigAction): AgentConfigState {
  switch (action.type) {
    case 'SET_FIELD':
      return { ...state, [action.field]: action.value }

    case 'SET_LLM_PROVIDER': {
      const models = LLM_MODELS[action.provider] || []
      return {
        ...state,
        llmProvider: action.provider,
        llmModel: models.length > 0 ? models[0].value : state.llmModel,
      }
    }

    case 'LOAD_FROM_AGENT': {
      const agent = action.agent
      const agentType = (agent.agent_type as AgentType) || 'voice'
      const savedModel = agent.llm_config?.model || 'gemini-3-flash-preview'
      const rawProvider = agent.llm_config?.provider
      const rawTemperature = Number(agent.llm_config?.temperature)
      const resolvedTemperature = Number.isFinite(rawTemperature)
        ? rawTemperature
        : DEFAULT_LLM_TEMPERATURE
      const classifierConfig = agent.llm_config?.classifier || {}
      const rawStrategy = classifierConfig?.strategy
      const classifierStrategy: ClassifierStrategy =
        rawStrategy === 'off' || rawStrategy === 'prefill' || rawStrategy === 'main_llm'
          ? rawStrategy
          : 'main_llm'
      // Sanitize: reject values not in the frontend enum.
      const VALID_PROVIDERS: LLMProvider[] = ['openai', 'anthropic', 'gemini', 'vertex', 'vllm']
      const savedProvider: LLMProvider | undefined = VALID_PROVIDERS.includes(rawProvider)
        ? rawProvider as LLMProvider
        : undefined
      return {
        ...state,
        agentName: agent.name || 'Untitled Agent',
        agentDescription: agent.description || '',
        systemPrompt: agent.system_prompt || 'You are a helpful AI voice assistant.',
        status: agent.status || 'draft',
        agentType,
        llmProvider: savedProvider || deriveProviderFromModel(savedModel),
        llmModel: savedModel,
        temperature: String(resolvedTemperature),
        classifierStrategy,
        voice: agent.voice_config?.voice_id || 'en-US-Chirp3-HD-Kore',
        updatedAt: agent.updated_at ? new Date(agent.updated_at) : null,
      }
    }

    default:
      return state
  }
}

function deriveProviderFromModel(model: string): LLMProvider {
  if (model.startsWith('gpt-') || model.startsWith('o1')) return 'openai'
  if (model.startsWith('claude-')) return 'anthropic'
  if (model.startsWith('gemini-')) return 'vertex'
  if (model.includes('llama') || model.includes('Llama') || model.includes('meta-')) return 'vllm'
  return 'vertex'
}

export function useAgentConfig(initialAgentType: AgentType = 'voice') {
  const [config, dispatch] = useReducer(agentConfigReducer, {
    agentName: 'Untitled Agent',
    agentDescription: '',
    systemPrompt: 'You are a helpful AI voice assistant.',
    status: 'draft' as AgentStatus,
    llmProvider: 'vertex' as LLMProvider,
    llmModel: 'gemini-3-flash-preview',
    temperature: String(DEFAULT_LLM_TEMPERATURE),
    classifierStrategy: 'main_llm' as ClassifierStrategy,
    voice: 'en-US-Chirp3-HD-Kore',
    agentType: initialAgentType,
    updatedAt: null,
    canvasVersionId: null,
    canvasVersionNumber: 1,
  })

  return { config, dispatch }
}

export type { AgentConfigAction }
