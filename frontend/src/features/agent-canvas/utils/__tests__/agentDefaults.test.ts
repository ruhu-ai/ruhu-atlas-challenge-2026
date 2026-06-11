import { buildInitialAgentSettings, defaultAgentSettings } from '../agentDefaults'

describe('agent default presets', () => {
  it('defaults all agent types to the main_llm classifier strategy', () => {
    expect(defaultAgentSettings('chat').llm_config.classifier).toEqual({
      strategy: 'main_llm',
    })
    expect(defaultAgentSettings('voice').llm_config.classifier).toEqual({
      strategy: 'main_llm',
    })
    expect(defaultAgentSettings('multimodal').llm_config.classifier).toEqual({
      strategy: 'main_llm',
    })
  })

  it('builds initial agent settings from the selected preset and preserves seeded knowledge ids', () => {
    expect(buildInitialAgentSettings('voice', ['kb-1', 'kb-2'])).toMatchObject({
      agent_type: 'voice',
      system_prompt: 'You are a helpful AI voice assistant.',
      knowledge_base_ids: ['kb-1', 'kb-2'],
      llm_config: {
        classifier: {
          strategy: 'main_llm',
        },
      },
    })
  })
})
