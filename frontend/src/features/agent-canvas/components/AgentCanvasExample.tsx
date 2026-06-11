/**
 * AgentCanvasExample
 * Example page demonstrating how to use all Agent Canvas components together
 * This serves as both documentation and a starting point for implementation
 */
import React from 'react';
import { AgentConfigPanel } from './AgentConfigPanel';
import { ProviderSelector } from './ProviderSelector';
import { TestConversationPanel } from './TestConversationPanel';
import { useAgent, useUpdateAgent } from '../hooks/useAgentApi';
import type { LLMConfig, STTConfig, TTSConfig } from '../../../types/agent';
import { createLogger } from '@/utils/logger';

const canvasLogger = createLogger({ prefix: '[Canvas]' });

interface AgentCanvasExampleProps {
  agentId: string;
}

export const AgentCanvasExample: React.FC<AgentCanvasExampleProps> = ({ agentId }) => {
  const { data: agent, isLoading, error } = useAgent(agentId);
  const updateMutation = useUpdateAgent(agentId);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-background">
        <div className="text-center">
          <div className="w-16 h-16 mx-auto mb-4 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
          <p className="text-muted-foreground">Loading agent...</p>
        </div>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="flex items-center justify-center h-screen bg-background">
        <div className="text-center max-w-md p-6 bg-card border border-border rounded-lg">
          <div className="text-4xl mb-4">⚠️</div>
          <h2 className="text-xl font-semibold text-foreground mb-2">
            Agent Not Found
          </h2>
          <p className="text-muted-foreground">
            {error ? 'Failed to load agent' : 'The requested agent could not be found'}
          </p>
        </div>
      </div>
    );
  }

  const handleLLMConfigChange = async (config: LLMConfig) => {
    await updateMutation.mutateAsync({ llm_config: config });
  };

  const handleSTTConfigChange = async (config: STTConfig) => {
    await updateMutation.mutateAsync({ stt_config: config });
  };

  const handleTTSConfigChange = async (config: TTSConfig) => {
    await updateMutation.mutateAsync({ tts_config: config });
  };

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 bg-card border-b border-border">
        <div className="flex items-center gap-3">
          <button
            onClick={() => window.history.back()}
            className="p-2 text-muted-foreground hover:text-foreground hover:bg-accent rounded-md transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div>
            <h1 className="text-xl font-semibold text-foreground">{agent.name}</h1>
            <p className="text-sm text-[#737373]">Agent Configuration & Testing</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className={`
            inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium
            ${agent.status === 'active' ? 'bg-green-500/10 text-green-400' :
              agent.status === 'draft' ? 'bg-gray-500/10 text-gray-400' :
              'bg-red-500/10 text-red-400'}
          `}>
            <span className={`h-2 w-2 rounded-full ${
              agent.status === 'active' ? 'bg-green-400' :
              agent.status === 'draft' ? 'bg-gray-400' :
              'bg-red-400'
            }`} />
            <span className="capitalize">{agent.status}</span>
          </div>

          {agent.is_deployed && (
            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-primary/10 rounded-md text-sm font-medium text-primary">
              <span>🚀</span>
              <span>Deployed</span>
            </div>
          )}
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 overflow-hidden">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-full p-6">
          {/* Left Panel: Configuration */}
          <div className="lg:col-span-1 space-y-6 overflow-y-auto">
            {/* Agent Configuration */}
            <AgentConfigPanel
              agent={agent}
              onUpdate={(updatedAgent) => {
                canvasLogger.log('Agent updated:', updatedAgent);
              }}
            />

            {/* Provider Selectors */}
            <div className="space-y-4">
              <h2 className="text-sm font-semibold text-foreground px-1">
                🔌 AI Providers
              </h2>

              <ProviderSelector
                type="llm"
                config={agent.llm_config}
                onChange={handleLLMConfigChange as (config: STTConfig | LLMConfig | TTSConfig) => void}
              />

              <ProviderSelector
                type="stt"
                config={agent.stt_config}
                onChange={handleSTTConfigChange as (config: STTConfig | LLMConfig | TTSConfig) => void}
              />

              <ProviderSelector
                type="tts"
                config={agent.tts_config}
                onChange={handleTTSConfigChange as (config: STTConfig | LLMConfig | TTSConfig) => void}
              />
            </div>

            {/* Provider Abstraction Info */}
            <div className="p-4 bg-card border border-primary/20 rounded-lg">
              <h3 className="text-sm font-semibold text-primary mb-2">
                ⭐ Zero Vendor Lock-in
              </h3>
              <p className="text-xs text-muted-foreground leading-relaxed">
                Switch between providers instantly. No code changes required.
                Test different models, optimize costs, and maintain full flexibility.
              </p>
            </div>
          </div>

          {/* Right Panel: Testing */}
          <div className="lg:col-span-2 overflow-hidden">
            <TestConversationPanel agent={agent} />
          </div>
        </div>
      </div>

      {/* Footer (Optional) */}
      <footer className="px-6 py-3 bg-card border-t border-border">
        <div className="flex items-center justify-between text-xs text-[#737373]">
          <div className="flex items-center gap-4">
            <span>Last updated: {new Date(agent.updated_at).toLocaleString()}</span>
            <span>•</span>
            <span>{agent.total_conversations.toLocaleString()} conversations</span>
          </div>
          <div className="flex items-center gap-2">
            <span>Powered by Ruhu</span>
            <span>🤖</span>
          </div>
        </div>
      </footer>
    </div>
  );
};

/**
 * Usage Example:
 *
 * import { AgentCanvasExample } from '@/features/agent-canvas/components/AgentCanvasExample';
 *
 * function AgentPage() {
 *   const { agentId } = useParams();
 *   return <AgentCanvasExample agentId={agentId} />;
 * }
 */
