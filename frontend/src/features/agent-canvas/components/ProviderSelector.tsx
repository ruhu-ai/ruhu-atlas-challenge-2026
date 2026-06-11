/**
 * ProviderSelector Component
 * Unified component for selecting and configuring STT, LLM, and TTS providers
 * Implements provider abstraction layer for zero vendor lock-in
 */
import React, { useState } from 'react';
import type {
  STTConfig,
  LLMConfig,
  TTSConfig,
  ProviderType,
  STTProvider,
  LLMProvider,
  TTSProvider,
} from '../../../types/agent';
import {
  STT_PROVIDERS,
  LLM_PROVIDERS,
  TTS_PROVIDERS,
  LLM_MODELS,
  STT_MODELS,
} from '../../../types/agent';

const DEFAULT_LLM_TEMPERATURE = 1.0;

interface ProviderSelectorProps {
  type: ProviderType;
  config: STTConfig | LLMConfig | TTSConfig;
  onChange: (config: STTConfig | LLMConfig | TTSConfig) => void;
}

export const ProviderSelector: React.FC<ProviderSelectorProps> = ({
  type,
  config,
  onChange,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  // Get provider options based on type
  const providers = type === 'stt' ? STT_PROVIDERS :
                   type === 'llm' ? LLM_PROVIDERS :
                   TTS_PROVIDERS;

  const currentProvider = config.provider as string;

  const handleProviderChange = (provider: string) => {
    const newConfig = {
      ...config,
      provider,
      // Reset model when provider changes
      model: undefined,
    };
    onChange(newConfig as STTConfig | LLMConfig | TTSConfig);
  };

  const handleConfigChange = (key: string, value: any) => {
    onChange({
      ...config,
      [key]: value,
    });
  };

  const getProviderIcon = () => {
    switch (type) {
      case 'stt': return '🎤';
      case 'llm': return '🧠';
      case 'tts': return '🔊';
    }
  };

  const getProviderLabel = () => {
    switch (type) {
      case 'stt': return 'Speech-to-Text';
      case 'llm': return 'Language Model';
      case 'tts': return 'Text-to-Speech';
    }
  };

  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-accent transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="text-lg">{getProviderIcon()}</span>
          <div className="text-left">
            <h3 className="text-sm font-semibold text-foreground">{getProviderLabel()}</h3>
            <p className="text-xs text-[#737373]">
              {providers.find(p => p.value === currentProvider)?.label || 'Select provider'}
            </p>
          </div>
        </div>
        <svg
          className={`w-5 h-5 text-[#737373] transition-transform ${isExpanded ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-border">
          {/* Provider Selection */}
          <div className="pt-4">
            <label className="block text-sm font-medium text-foreground mb-2">
              Provider
            </label>
            <div className="grid grid-cols-1 gap-2">
              {providers.map((provider) => {
                const isAvailable = provider.available !== false
                return (
                <button
                  key={provider.value}
                  onClick={() => {
                    if (isAvailable) handleProviderChange(provider.value)
                  }}
                  disabled={!isAvailable}
                  className={`
                    flex items-start gap-3 p-3 rounded-md border transition-colors text-left
                    ${currentProvider === provider.value
                      ? 'border-primary bg-primary/10'
                      : isAvailable
                        ? 'border-border hover:border-border hover:bg-accent'
                        : 'border-border/60 bg-muted/40 opacity-70 cursor-not-allowed'
                    }
                  `}
                >
                  <span className="text-2xl">{provider.icon}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-foreground">{provider.label}</p>
                      {currentProvider === provider.value && (
                        <span className="text-xs text-primary">✓ Selected</span>
                      )}
                      {!isAvailable && (
                        <span className="text-[10px] text-amber-600 uppercase tracking-wide">Coming soon</span>
                      )}
                    </div>
                    {provider.description && (
                      <p className="text-xs text-[#737373] mt-0.5">{provider.description}</p>
                    )}
                    {!isAvailable && provider.availabilityNote && (
                      <p className="text-[11px] text-[#8a6f00] mt-1">{provider.availabilityNote}</p>
                    )}
                  </div>
                </button>
                )
              })}
            </div>
          </div>

          {/* Provider-Specific Configuration */}
          {currentProvider && (
            <div className="pt-4 border-t border-border space-y-4">
              <h4 className="text-sm font-medium text-foreground">Configuration</h4>

              {/* LLM Configuration */}
              {type === 'llm' && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-foreground mb-1.5">
                      Model
                    </label>
                    <select
                      value={(config as LLMConfig).model || ''}
                      onChange={(e) => handleConfigChange('model', e.target.value)}
                      className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                    >
                      <option value="">Select model...</option>
                      {LLM_MODELS[currentProvider as LLMProvider]?.map(model => (
                        <option key={model.value} value={model.value}>
                          {model.label} - {model.description}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-foreground mb-1.5">
                      Temperature
                    </label>
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.1"
                        value={(config as LLMConfig).temperature ?? DEFAULT_LLM_TEMPERATURE}
                        onChange={(e) => handleConfigChange('temperature', parseFloat(e.target.value))}
                        className="flex-1"
                      />
                      <span className="text-sm text-muted-foreground min-w-[3ch]">
                        {((config as LLMConfig).temperature ?? DEFAULT_LLM_TEMPERATURE).toFixed(1)}
                      </span>
                    </div>
                    <p className="text-xs text-[#737373] mt-1">
                      Higher values make output more random, lower values more deterministic
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-foreground mb-1.5">
                      Max Tokens
                    </label>
                    <input
                      type="number"
                      min="1"
                      max="4096"
                      value={(config as LLMConfig).max_tokens || 500}
                      onChange={(e) => handleConfigChange('max_tokens', parseInt(e.target.value))}
                      className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                    />
                    <p className="text-xs text-[#737373] mt-1">
                      Maximum number of tokens to generate
                    </p>
                  </div>
                </>
              )}

              {/* STT Configuration */}
              {type === 'stt' && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-foreground mb-1.5">
                      Model
                    </label>
                    <select
                      value={(config as STTConfig).model || ''}
                      onChange={(e) => handleConfigChange('model', e.target.value)}
                      className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                    >
                      <option value="">Select model...</option>
                      {STT_MODELS[currentProvider as STTProvider]?.map(model => (
                        <option key={model.value} value={model.value}>
                          {model.label} - {model.description}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-foreground mb-1.5">
                      Language
                    </label>
                    <select
                      value={(config as STTConfig).language || 'en-US'}
                      onChange={(e) => handleConfigChange('language', e.target.value)}
                      className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                    >
                      {/* Global */}
                      <option value="en-US">English (US)</option>
                      <option value="en-GB">English (UK)</option>
                      <option value="es-ES">Spanish</option>
                      <option value="fr-FR">French</option>
                      <option value="pt-BR">Portuguese (Brazil)</option>
                      {/* African Languages */}
                      <option value="yo-NG">Yoruba (Nigeria)</option>
                      <option value="ha-NG">Hausa (Nigeria)</option>
                      <option value="ig-NG">Igbo (Nigeria)</option>
                      <option value="sw">Swahili</option>
                      <option value="zu-ZA">Zulu (South Africa)</option>
                      <option value="am-ET">Amharic (Ethiopia)</option>
                      <option value="af-ZA">Afrikaans (South Africa)</option>
                      {/* Middle East & North Africa */}
                      <option value="ar">Arabic</option>
                      <option value="ar-EG">Arabic (Egypt)</option>
                      <option value="ar-SA">Arabic (Saudi Arabia)</option>
                      {/* Other */}
                      <option value="hi-IN">Hindi (India)</option>
                      <option value="tr-TR">Turkish</option>
                      <option value="de-DE">German</option>
                    </select>
                  </div>

                  <div className="flex items-center justify-between">
                    <div>
                      <label className="text-sm font-medium text-foreground">
                        Smart Formatting
                      </label>
                      <p className="text-xs text-[#737373] mt-0.5">
                        Automatic punctuation and capitalization
                      </p>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={(config as STTConfig).smart_format !== false}
                        onChange={(e) => handleConfigChange('smart_format', e.target.checked)}
                        className="sr-only peer"
                      />
                      <div className="w-11 h-6 bg-muted peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                    </label>
                  </div>
                </>
              )}

              {/* TTS Configuration */}
              {type === 'tts' && (
                <>
                  {currentProvider === 'openai' && (
                    <div>
                      <label className="block text-sm font-medium text-foreground mb-1.5">
                        Voice
                      </label>
                      <select
                        value={(config as TTSConfig).voice_name || 'alloy'}
                        onChange={(e) => handleConfigChange('voice_name', e.target.value)}
                        className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                      >
                        <option value="alloy">Alloy</option>
                        <option value="echo">Echo</option>
                        <option value="fable">Fable</option>
                        <option value="onyx">Onyx</option>
                        <option value="nova">Nova</option>
                        <option value="shimmer">Shimmer</option>
                      </select>
                    </div>
                  )}
                </>
              )}

              {/* Provider Benefits */}
              <div className="p-3 bg-muted border border-border rounded-md">
                <h5 className="text-xs font-medium text-foreground mb-1.5">
                  ⭐ Provider Abstraction Benefits
                </h5>
                <ul className="space-y-1 text-xs text-[#737373]">
                  <li>• Switch providers anytime (zero vendor lock-in)</li>
                  <li>• A/B test different models easily</li>
                  <li>• Optimize costs by choosing best provider</li>
                  <li>• Migration difficulty: 2/10 (very easy)</li>
                </ul>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
