import type { AgentSettings, AgentType } from '@/types/agent-definition'

const SYSTEM_PROMPT_BY_TYPE: Record<AgentType, string> = {
  chat: 'You are a helpful AI chat assistant.',
  voice: 'You are a helpful AI voice assistant.',
  multimodal: 'You are a helpful AI multimodal assistant.',
}

const DEFAULT_SETTINGS_BY_TYPE: Record<AgentType, AgentSettings> = {
  chat: {
    description: '',
    agent_type: 'chat',
    system_prompt: SYSTEM_PROMPT_BY_TYPE.chat,
    llm_config: {
      provider: 'vertex',
      model: 'gemini-3-flash-preview',
      temperature: 1.0,
      classifier: { strategy: 'main_llm' },
    },
    voice_config: { voice_id: 'en-US-Chirp3-HD-Kore' },
    knowledge_base_ids: [],
  },
  voice: {
    description: '',
    agent_type: 'voice',
    system_prompt: SYSTEM_PROMPT_BY_TYPE.voice,
    llm_config: {
      provider: 'vertex',
      model: 'gemini-3-flash-preview',
      temperature: 1.0,
      classifier: { strategy: 'main_llm' },
    },
    voice_config: { voice_id: 'en-US-Chirp3-HD-Kore' },
    knowledge_base_ids: [],
  },
  multimodal: {
    description: '',
    agent_type: 'multimodal',
    system_prompt: SYSTEM_PROMPT_BY_TYPE.multimodal,
    llm_config: {
      provider: 'vertex',
      model: 'gemini-3-flash-preview',
      temperature: 1.0,
      classifier: { strategy: 'main_llm' },
    },
    voice_config: { voice_id: 'en-US-Chirp3-HD-Kore' },
    knowledge_base_ids: [],
  },
}

export function defaultAgentSettings(agentType: AgentType = 'voice'): AgentSettings {
  return {
    ...DEFAULT_SETTINGS_BY_TYPE[agentType],
    llm_config: {
      ...DEFAULT_SETTINGS_BY_TYPE[agentType].llm_config,
      classifier: { ...DEFAULT_SETTINGS_BY_TYPE[agentType].llm_config.classifier },
    },
    voice_config: { ...DEFAULT_SETTINGS_BY_TYPE[agentType].voice_config },
    knowledge_base_ids: [...DEFAULT_SETTINGS_BY_TYPE[agentType].knowledge_base_ids],
  }
}

export function buildInitialAgentSettings(
  agentType: AgentType,
  knowledgeBaseIds: string[] = [],
): AgentSettings {
  const defaults = defaultAgentSettings(agentType)
  return {
    ...defaults,
    knowledge_base_ids: knowledgeBaseIds,
  }
}
