/**
 * AgentConfigPanel Component
 * Main configuration panel for agent settings, prompts, and behavior
 */
import React, { useState } from 'react';
import type { Agent, AgentUpdateRequest } from '../../../types/agent';
import { useUpdateAgent } from '../hooks/useAgentApi';

interface AgentConfigPanelProps {
  agent: Agent;
  onUpdate?: (agent: Agent) => void;
}

export const AgentConfigPanel: React.FC<AgentConfigPanelProps> = ({
  agent,
  onUpdate
}) => {
  const [activeTab, setActiveTab] = useState<'general' | 'prompt' | 'behavior' | 'advanced'>('general');
  const [formData, setFormData] = useState<Partial<Agent>>({
    name: agent.name,
    description: agent.description,
    system_prompt: agent.system_prompt,
    context_window: agent.context_window,
    behavior_settings: agent.behavior_settings,
    interruption_config: agent.interruption_config,
  });

  const updateMutation = useUpdateAgent(agent.id);

  const handleSave = async () => {
    try {
      const updateData: AgentUpdateRequest = {
        name: formData.name,
        description: formData.description,
        system_prompt: formData.system_prompt,
        context_window: formData.context_window,
        behavior_settings: formData.behavior_settings,
        interruption_config: formData.interruption_config,
      };

      const updatedAgent = await updateMutation.mutateAsync(updateData);
      onUpdate?.(updatedAgent);
    } catch (error) {
      console.error('Failed to update agent:', error);
    }
  };

  const handleFieldChange = (field: keyof typeof formData, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  const tabs = [
    { id: 'general', label: 'General', icon: '⚙️' },
    { id: 'prompt', label: 'System Prompt', icon: '📝' },
    { id: 'behavior', label: 'Behavior', icon: '🎭' },
    { id: 'advanced', label: 'Advanced', icon: '🔧' },
  ] as const;

  return (
    <div className="flex flex-col h-full bg-card border border-border rounded-lg">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-lg">⚙️</span>
          <h2 className="text-base font-semibold text-foreground">Agent Configuration</h2>
        </div>
        <button
          onClick={handleSave}
          disabled={updateMutation.isPending}
          className="px-3 py-1.5 text-sm font-medium text-white bg-primary rounded-md hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-4 pt-3 border-b border-border">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`
              flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-t-md transition-colors
              ${activeTab === tab.id
                ? 'bg-card text-foreground border-b-2 border-primary'
                : 'text-muted-foreground hover:text-foreground hover:bg-card/50'
              }
            `}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* General Tab */}
        {activeTab === 'general' && (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                Agent Name
              </label>
              <input
                type="text"
                value={formData.name || ''}
                onChange={(e) => handleFieldChange('name', e.target.value)}
                className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground placeholder-[#737373] focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                placeholder="Enter agent name..."
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                Description
              </label>
              <textarea
                value={formData.description || ''}
                onChange={(e) => handleFieldChange('description', e.target.value)}
                rows={3}
                className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground placeholder-[#737373] focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent resize-none"
                placeholder="Describe what this agent does..."
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                Status
              </label>
              <div className="flex items-center gap-2">
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
                  <div className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary/10 rounded-md text-sm font-medium text-primary">
                    <span>🚀</span>
                    <span>Deployed</span>
                  </div>
                )}
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                Context Window
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={formData.context_window || 10}
                onChange={(e) => handleFieldChange('context_window', parseInt(e.target.value))}
                className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
              />
              <p className="mt-1 text-xs text-[#737373]">
                Number of previous messages to keep in context (1-100)
              </p>
            </div>
          </div>
        )}

        {/* Prompt Tab */}
        {activeTab === 'prompt' && (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                System Prompt
              </label>
              <textarea
                value={formData.system_prompt || ''}
                onChange={(e) => handleFieldChange('system_prompt', e.target.value)}
                rows={15}
                className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground placeholder-[#737373] focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent resize-none font-mono text-sm"
                placeholder="You are a helpful assistant..."
              />
              <p className="mt-1 text-xs text-[#737373]">
                This prompt defines the agent's personality and behavior
              </p>
            </div>

            <div className="p-3 bg-muted border border-border rounded-md">
              <h4 className="text-sm font-medium text-foreground mb-2">💡 Prompt Tips</h4>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li>• Be specific about the agent's role and responsibilities</li>
                <li>• Include response format guidelines</li>
                <li>• Specify tone and personality traits</li>
                <li>• Add any domain-specific knowledge or constraints</li>
              </ul>
            </div>
          </div>
        )}

        {/* Behavior Tab */}
        {activeTab === 'behavior' && (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                Max Turn Duration (seconds)
              </label>
              <input
                type="number"
                min={30}
                max={600}
                value={formData.behavior_settings?.max_turn_duration || 300}
                onChange={(e) => handleFieldChange('behavior_settings', {
                  ...formData.behavior_settings,
                  max_turn_duration: parseInt(e.target.value)
                })}
                className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
              />
              <p className="mt-1 text-xs text-[#737373]">
                Maximum duration for a single conversation turn
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                Silence Timeout (milliseconds)
              </label>
              <input
                type="number"
                min={500}
                max={10000}
                step={100}
                value={formData.behavior_settings?.silence_timeout || 2000}
                onChange={(e) => handleFieldChange('behavior_settings', {
                  ...formData.behavior_settings,
                  silence_timeout: parseInt(e.target.value)
                })}
                className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
              />
              <p className="mt-1 text-xs text-[#737373]">
                Time to wait before considering silence as end of speech
              </p>
            </div>

            <div className="pt-3 border-t border-border">
              <div className="flex items-center justify-between">
                <div>
                  <label className="text-sm font-medium text-foreground">
                    Interruption Handling
                  </label>
                  <p className="text-xs text-[#737373] mt-0.5">
                    Allow users to interrupt agent mid-speech
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.interruption_config?.enabled || false}
                    onChange={(e) => handleFieldChange('interruption_config', {
                      ...formData.interruption_config,
                      enabled: e.target.checked
                    })}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-muted peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                </label>
              </div>
            </div>

            {formData.interruption_config?.enabled && (
              <div className="pl-4 space-y-3 border-l-2 border-primary/30">
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5">
                    Interruption Threshold
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.1}
                    value={formData.interruption_config?.threshold || 0.5}
                    onChange={(e) => handleFieldChange('interruption_config', {
                      ...formData.interruption_config,
                      threshold: parseFloat(e.target.value)
                    })}
                    className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                  />
                  <p className="mt-1 text-xs text-[#737373]">
                    Confidence threshold for detecting interruptions (0-1)
                  </p>
                </div>

                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5">
                    Cooldown (milliseconds)
                  </label>
                  <input
                    type="number"
                    min={100}
                    max={5000}
                    step={100}
                    value={formData.interruption_config?.cooldown_ms || 500}
                    onChange={(e) => handleFieldChange('interruption_config', {
                      ...formData.interruption_config,
                      cooldown_ms: parseInt(e.target.value)
                    })}
                    className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                  />
                  <p className="mt-1 text-xs text-[#737373]">
                    Minimum time between interruption detections
                  </p>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Advanced Tab */}
        {activeTab === 'advanced' && (
          <div className="space-y-4">
            <div className="p-3 bg-muted border border-border rounded-md">
              <h4 className="text-sm font-medium text-foreground mb-2">📊 Statistics</h4>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <p className="text-[#737373]">Total Conversations</p>
                  <p className="text-foreground font-semibold mt-0.5">
                    {agent.total_conversations.toLocaleString()}
                  </p>
                </div>
                <div>
                  <p className="text-[#737373]">Total Messages</p>
                  <p className="text-foreground font-semibold mt-0.5">
                    {agent.total_messages.toLocaleString()}
                  </p>
                </div>
                {agent.avg_response_time && (
                  <div>
                    <p className="text-[#737373]">Avg Response Time</p>
                    <p className="text-foreground font-semibold mt-0.5">
                      {agent.avg_response_time.toFixed(0)}ms
                    </p>
                  </div>
                )}
                {agent.success_rate && (
                  <div>
                    <p className="text-[#737373]">Success Rate</p>
                    <p className="text-foreground font-semibold mt-0.5">
                      {(agent.success_rate * 100).toFixed(1)}%
                    </p>
                  </div>
                )}
              </div>
            </div>

            <div className="p-3 bg-muted border border-border rounded-md">
              <h4 className="text-sm font-medium text-foreground mb-2">🔗 IDs & References</h4>
              <div className="space-y-2 text-xs">
                <div>
                  <p className="text-[#737373]">Agent ID</p>
                  <code className="block mt-1 px-2 py-1 bg-background text-muted-foreground rounded font-mono">
                    {agent.id}
                  </code>
                </div>
                <div>
                  <p className="text-[#737373]">Organization ID</p>
                  <code className="block mt-1 px-2 py-1 bg-background text-muted-foreground rounded font-mono">
                    {agent.organization_id}
                  </code>
                </div>
                {agent.active_canvas_version_id && (
                  <div>
                    <p className="text-[#737373]">Active Canvas Version</p>
                    <code className="block mt-1 px-2 py-1 bg-background text-muted-foreground rounded font-mono">
                      {agent.active_canvas_version_id}
                    </code>
                  </div>
                )}
              </div>
            </div>

            <div className="p-3 bg-muted border border-border rounded-md">
              <h4 className="text-sm font-medium text-foreground mb-2">📅 Timestamps</h4>
              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between">
                  <span className="text-[#737373]">Created</span>
                  <span className="text-muted-foreground">
                    {new Date(agent.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737373]">Last Updated</span>
                  <span className="text-muted-foreground">
                    {new Date(agent.updated_at).toLocaleString()}
                  </span>
                </div>
                {agent.deployed_at && (
                  <div className="flex justify-between">
                    <span className="text-[#737373]">Deployed</span>
                    <span className="text-muted-foreground">
                      {new Date(agent.deployed_at).toLocaleString()}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      {updateMutation.isError && (
        <div className="px-4 py-3 border-t border-border bg-red-500/10">
          <p className="text-sm text-red-400">
            ⚠️ Error: {updateMutation.error?.message || 'Failed to save changes'}
          </p>
        </div>
      )}

      {updateMutation.isSuccess && (
        <div className="px-4 py-3 border-t border-border bg-green-500/10">
          <p className="text-sm text-green-400">
            ✅ Changes saved successfully
          </p>
        </div>
      )}
    </div>
  );
};
