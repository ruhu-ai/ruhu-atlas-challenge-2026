/**
 * TypeScript types for Agent Management
 * Aligned with backend schemas in schemas/core.py
 */

export type AgentStatus = 'draft' | 'published' | 'active' | 'inactive' | 'training' | 'deployed' | 'archived';

/**
 * Agent modality types:
 * - chat: Text-only agents for web chat, WhatsApp, SMS
 * - voice: Voice-only agents for phone calls
 * - multimodal: Unified agents handling both voice AND chat with seamless switching
 */
export type AgentType = 'chat' | 'voice' | 'multimodal';

export type ProviderType = 'stt' | 'llm' | 'tts';

export type STTProvider = 'google' | 'openai' | 'whisper' | 'canary';
export type LLMProvider = 'openai' | 'anthropic' | 'gemini' | 'vertex' | 'vllm';
export type TTSProvider = 'google' | 'openai' | 'cosyvoice3';

/**
 * Provider configuration interfaces
 */
export interface STTConfig {
  provider: STTProvider;
  model?: string;
  language?: string;
  smart_format?: boolean;
  punctuate?: boolean;
  [key: string]: any;
}

export interface LLMConfig {
  provider: LLMProvider;
  model: string;
  temperature?: number;
  max_tokens?: number;
  top_p?: number;
  [key: string]: any;
}

export interface TTSConfig {
  provider: TTSProvider;
  voice_id?: string;
  voice_name?: string;
  stability?: number;
  similarity_boost?: number;
  [key: string]: any;
}

export interface VoiceConfig {
  provider: TTSProvider;
  voice_id?: string;
  voice_name?: string;
  [key: string]: any;
}

export interface BehaviorSettings {
  max_turn_duration?: number;
  silence_timeout?: number;
  interruption_enabled?: boolean;
  [key: string]: any;
}

export interface InterruptionConfig {
  enabled: boolean;
  threshold?: number;
  cooldown_ms?: number;
  [key: string]: any;
}

/**
 * Agent model matching backend
 */
export interface Agent {
  id: string;
  organization_id: string;
  created_by?: string;
  name: string;
  description?: string;
  avatar_url?: string;
  agent_type: AgentType;
  status: AgentStatus;
  system_prompt: string;
  voice_config: VoiceConfig;
  llm_config: LLMConfig;
  stt_config: STTConfig;
  tts_config: TTSConfig;
  behavior_settings: BehaviorSettings;
  interruption_config: InterruptionConfig;
  knowledge_base_ids: string[];
  context_window: number;
  active_canvas_version_id?: string;
  active_release_id?: string;
  is_deployed: boolean;
  deployment_url?: string;
  deployed_at?: string;
  deployment_gate_enabled: boolean;
  min_pass_rate: number;
  min_simulation_runs: number;
  max_test_staleness_hours: number;
  is_widget_enabled: boolean;
  widget_mode: 'chat' | 'voice' | 'multimodal';
  widget_config: Record<string, unknown>;
  total_conversations: number;
  total_messages: number;
  avg_response_time?: number;
  success_rate?: number;
  created_at: string;
  updated_at: string;
}

/**
 * Agent creation request
 */
export interface AgentCreateRequest {
  name: string;
  description?: string;
  agent_type?: AgentType;
  system_prompt: string;
  voice_config?: Partial<VoiceConfig>;
  llm_config?: Partial<LLMConfig>;
  stt_config?: Partial<STTConfig>;
  tts_config?: Partial<TTSConfig>;
  behavior_settings?: Partial<BehaviorSettings>;
  interruption_config?: Partial<InterruptionConfig>;
  context_window?: number;
}

/**
 * Agent update request
 */
export interface AgentUpdateRequest {
  name?: string;
  description?: string;
  avatar_url?: string;
  status?: AgentStatus;
  system_prompt?: string;
  voice_config?: Partial<VoiceConfig>;
  llm_config?: Partial<LLMConfig>;
  stt_config?: Partial<STTConfig>;
  tts_config?: Partial<TTSConfig>;
  behavior_settings?: Partial<BehaviorSettings>;
  interruption_config?: Partial<InterruptionConfig>;
  knowledge_base_ids?: string[];
  context_window?: number;
  is_deployed?: boolean;
  deployment_gate_enabled?: boolean;
  min_pass_rate?: number;
  min_simulation_runs?: number;
  max_test_staleness_hours?: number;
}

/**
 * Test conversation types
 */
export interface TestConversationRequest {
  message: string;
  context?: Record<string, unknown>;
}

export interface TestConversationResponse {
  agent_id: string;
  request_message: string;
  response_message: string;
  processing_time_ms: number;
  llm_provider: string;
  model: string;
  tokens_used?: number;
  metadata: Record<string, unknown>;
}

/**
 * Provider options for UI
 */
export interface ProviderOption {
  value: string;
  label: string;
  description?: string;
  icon?: string;
  available?: boolean;
  availabilityNote?: string;
}

const envFlagEnabled = (flag: unknown): boolean => {
  if (typeof flag === 'boolean') return flag;
  if (typeof flag === 'string') return flag.toLowerCase() === 'true';
  return false;
};

const ENABLE_WHISPER_STT = envFlagEnabled((import.meta as any).env?.VITE_ENABLE_WHISPER_STT);
const ENABLE_CANARY_STT = envFlagEnabled((import.meta as any).env?.VITE_ENABLE_CANARY_STT);
const ENABLE_VLLM_LLM = envFlagEnabled((import.meta as any).env?.VITE_ENABLE_VLLM_LLM);
const ENABLE_COSYVOICE3_TTS = envFlagEnabled((import.meta as any).env?.VITE_ENABLE_COSYVOICE3_TTS);

export const STT_PROVIDERS: ProviderOption[] = [
  {
    value: 'google',
    label: 'Google Speech-to-Text',
    description: 'Cloud STT with strong multilingual support',
    icon: '☁️'
  },
  {
    value: 'openai',
    label: 'OpenAI Whisper',
    description: 'High accuracy, API-based',
    icon: '🗣️'
  },
  {
    value: 'whisper',
    label: 'Whisper (Self-hosted)',
    description: ENABLE_WHISPER_STT
      ? 'Self-hosted Whisper model'
      : 'Coming soon: enabled after self-hosted Whisper runtime is deployed',
    icon: '🏠',
    available: ENABLE_WHISPER_STT,
    availabilityNote: 'Requires deployed Whisper runtime and backend endpoint configuration.'
  },
  {
    value: 'canary',
    label: 'Canary-1B-v2 (Self-hosted)',
    description: ENABLE_CANARY_STT
      ? 'Self-hosted NVIDIA Canary1_v2 streaming STT'
      : 'Coming soon: enabled after Canary1_v2 serving and runtime integration are deployed',
    icon: '🦜',
    available: ENABLE_CANARY_STT,
    availabilityNote: 'Requires deployed Canary1_v2 streaming service and backend runtime integration.'
  }
];

export const LLM_PROVIDERS: ProviderOption[] = [
  {
    value: 'vertex',
    label: 'Google Vertex AI',
    description: 'Gemini via Vertex ADC (recommended)',
    icon: '☁️'
  },
  {
    value: 'gemini',
    label: 'Google Gemini',
    description: 'Gemini 3.0 Flash default',
    icon: '💎'
  },
  {
    value: 'openai',
    label: 'OpenAI',
    description: 'GPT-4o, GPT-4-turbo',
    icon: '🤖'
  },
  {
    value: 'anthropic',
    label: 'Anthropic',
    description: 'Claude 3.5 Sonnet',
    icon: '🧠'
  },
  {
    value: 'vllm',
    label: 'vLLM (Self-hosted)',
    description: ENABLE_VLLM_LLM
      ? 'Llama 3.1, custom models'
      : 'Coming soon: enabled after self-hosted vLLM/Qwen runtime is deployed',
    icon: '🏠',
    available: ENABLE_VLLM_LLM,
    availabilityNote: 'Requires deployed vLLM runtime and backend endpoint configuration.'
  }
];

export const TTS_PROVIDERS: ProviderOption[] = [
  {
    value: 'google',
    label: 'Google TTS (Chirp 3 HD)',
    description: 'Cloud neural TTS, multilingual and low-latency',
    icon: '☁️'
  },
  {
    value: 'openai',
    label: 'OpenAI TTS',
    description: 'Multiple voice options',
    icon: '🗣️'
  },
  {
    value: 'cosyvoice3',
    label: 'CosyVoice3 (Self-hosted)',
    description: ENABLE_COSYVOICE3_TTS
      ? 'Self-hosted neural TTS optimized for multilingual/MEA voice quality'
      : 'Coming soon: enabled after self-hosted CosyVoice3 runtime is deployed',
    icon: '🏠',
    available: ENABLE_COSYVOICE3_TTS,
    availabilityNote: 'Requires deployed CosyVoice3 runtime and backend endpoint configuration.'
  }
];

/**
 * Model options by provider
 */
export const LLM_MODELS: Record<LLMProvider, ProviderOption[]> = {
  openai: [
    { value: 'gpt-4o', label: 'GPT-4o', description: 'Latest multimodal model' },
    { value: 'gpt-4-turbo', label: 'GPT-4 Turbo', description: 'Fast, efficient' },
    { value: 'gpt-4', label: 'GPT-4', description: 'Most capable' },
    { value: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo', description: 'Fast, cost-effective' }
  ],
  anthropic: [
    { value: 'claude-3-5-sonnet-20240620', label: 'Claude 3.5 Sonnet', description: 'Latest, most capable' },
    { value: 'claude-3-opus-20240229', label: 'Claude 3 Opus', description: 'Most intelligent' },
    { value: 'claude-3-sonnet-20240229', label: 'Claude 3 Sonnet', description: 'Balanced' },
    { value: 'claude-3-haiku-20240307', label: 'Claude 3 Haiku', description: 'Fastest' }
  ],
  gemini: [
    { value: 'gemini-3-flash-preview', label: 'Gemini 3.0 Flash', description: 'Default production model' },
    { value: 'gemini-3-pro-preview', label: 'Gemini 3.0 Pro', description: 'Highest reasoning capability' },
    { value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash', description: 'Fast, multimodal' },
    { value: 'gemini-2.0-flash-lite', label: 'Gemini 2.0 Flash Lite', description: 'Cost-effective' }
  ],
  vertex: [
    { value: 'gemini-3-flash-preview', label: 'Gemini 3.0 Flash (Vertex)', description: 'Default production model' },
    { value: 'gemini-3-pro-preview', label: 'Gemini 3.0 Pro (Vertex)', description: 'Highest reasoning capability' },
    { value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash (Vertex)', description: 'Fast, multimodal' },
    { value: 'gemini-2.0-flash-lite', label: 'Gemini 2.0 Flash Lite (Vertex)', description: 'Cost-effective' }
  ],
  vllm: [
    { value: 'meta-llama/Llama-3.1-70B', label: 'Llama 3.1 70B', description: 'Large model' },
    { value: 'meta-llama/Llama-3.1-8B', label: 'Llama 3.1 8B', description: 'Fast, efficient' }
  ]
};

export const STT_MODELS: Record<STTProvider, ProviderOption[]> = {
  google: [
    { value: 'chirp_3', label: 'Chirp 3', description: 'Latest; 85+ languages including African' },
    { value: 'latest_long', label: 'Latest Long', description: 'Best for multi-turn conversations' },
    { value: 'latest_short', label: 'Latest Short', description: 'Best for short utterances' },
    { value: 'chirp_2', label: 'Chirp 2', description: 'Enhanced multilingual model' }
  ],
  openai: [
    { value: 'whisper-1', label: 'Whisper-1', description: 'OpenAI API' }
  ],
  whisper: [
    { value: 'large-v3', label: 'Large v3', description: 'Most accurate' },
    { value: 'medium', label: 'Medium', description: 'Balanced' },
    { value: 'small', label: 'Small', description: 'Fast' }
  ],
  canary: [
    { value: 'canary-1b-v2', label: 'Canary-1B-v2', description: 'NVIDIA streaming STT candidate for real-time African voice' }
  ]
};
