/**
 * TestConversationPanel Component
 * Interactive panel for testing agent conversations without starting a full voice session
 * Integrates with the POST /agents/{agent_id}/test endpoint
 */
import React, { useState, useRef, useEffect } from 'react';
import type { Agent, TestConversationRequest } from '../../../types/agent';
import { useTestConversation } from '../hooks/useAgentApi';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  processing_time_ms?: number;
  tokens_used?: number;
}

interface TestConversationPanelProps {
  agent: Agent;
}

export const TestConversationPanel: React.FC<TestConversationPanelProps> = ({
  agent,
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [context, setContext] = useState<Record<string, unknown>>({});
  const [showContextEditor, setShowContextEditor] = useState(false);
  const [contextJson, setContextJson] = useState('{}');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const testMutation = useTestConversation(agent.id);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = async () => {
    if (!input.trim() || testMutation.isPending) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input.trim(),
      timestamp: new Date(),
    };

    setMessages(prev => [...prev, userMessage]);
    setInput('');

    try {
      const request: TestConversationRequest = {
        message: input.trim(),
        context: Object.keys(context).length > 0 ? context : undefined,
      };

      const response = await testMutation.mutateAsync(request);

      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: response.response_message,
        timestamp: new Date(),
        processing_time_ms: response.processing_time_ms,
        tokens_used: response.tokens_used || undefined,
      };

      setMessages(prev => [...prev, assistantMessage]);
    } catch (error) {
      const errorMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `❌ Error: ${error instanceof Error ? error.message : 'Failed to get response'}`,
        timestamp: new Date(),
      };
      setMessages(prev => [...prev, errorMessage]);
    }
  };

  const handleClearConversation = () => {
    setMessages([]);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleContextChange = (value: string) => {
    setContextJson(value);
    try {
      const parsed = JSON.parse(value);
      setContext(parsed);
    } catch {
      // Invalid JSON, don't update context
    }
  };

  const addQuickContext = (key: string, value: any) => {
    const newContext = { ...context, [key]: value };
    setContext(newContext);
    setContextJson(JSON.stringify(newContext, null, 2));
  };

  return (
    <div className="flex flex-col h-full bg-card border border-border rounded-lg">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-lg">💬</span>
          <h2 className="text-base font-semibold text-foreground">Test Conversation</h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowContextEditor(!showContextEditor)}
            className={`
              px-3 py-1.5 text-sm font-medium rounded-md transition-colors
              ${showContextEditor
                ? 'bg-primary text-white'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent'
              }
            `}
          >
            {showContextEditor ? '📝 Hide Context' : '📝 Add Context'}
          </button>
          <button
            onClick={handleClearConversation}
            disabled={messages.length === 0}
            className="px-3 py-1.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-accent rounded-md disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            🗑️ Clear
          </button>
        </div>
      </div>

      {/* Context Editor */}
      {showContextEditor && (
        <div className="px-4 py-3 border-b border-border bg-muted space-y-3">
          <div>
            <label className="block text-sm font-medium text-foreground mb-1.5">
              Conversation Context (JSON)
            </label>
            <textarea
              value={contextJson}
              onChange={(e) => handleContextChange(e.target.value)}
              rows={4}
              className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground placeholder-[#737373] focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent resize-none font-mono text-xs"
              placeholder='{"customer_name": "John", "account_type": "premium"}'
            />
          </div>

          <div>
            <p className="text-xs font-medium text-foreground mb-2">Quick Context:</p>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => addQuickContext('customer_name', 'John Doe')}
                className="px-2 py-1 text-xs bg-card border border-border rounded hover:border-primary text-muted-foreground hover:text-foreground transition-colors"
              >
                + Customer Name
              </button>
              <button
                onClick={() => addQuickContext('account_type', 'premium')}
                className="px-2 py-1 text-xs bg-card border border-border rounded hover:border-primary text-muted-foreground hover:text-foreground transition-colors"
              >
                + Account Type
              </button>
              <button
                onClick={() => addQuickContext('order_id', 'ORD-12345')}
                className="px-2 py-1 text-xs bg-card border border-border rounded hover:border-primary text-muted-foreground hover:text-foreground transition-colors"
              >
                + Order ID
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-16 h-16 mb-4 bg-card rounded-full flex items-center justify-center text-2xl">
              💬
            </div>
            <h3 className="text-lg font-medium text-foreground mb-1">
              Test Your Agent
            </h3>
            <p className="text-sm text-[#737373] max-w-md">
              Send a message to test how your agent responds. This uses the test endpoint
              without starting a full voice session.
            </p>
            <div className="mt-4 p-3 bg-muted border border-border rounded-md max-w-md">
              <p className="text-xs text-muted-foreground">
                <strong className="text-foreground">Tip:</strong> Use the context editor to simulate
                different customer scenarios and test how your agent handles various situations.
              </p>
            </div>
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`
                    max-w-[80%] rounded-lg px-4 py-3
                    ${message.role === 'user'
                      ? 'bg-primary text-white'
                      : message.content.startsWith('❌')
                      ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                      : 'bg-card text-foreground border border-border'
                    }
                  `}
                >
                  <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                  <div className="flex items-center gap-3 mt-2 pt-2 border-t border-current/10">
                    <span className="text-xs opacity-70">
                      {message.timestamp.toLocaleTimeString()}
                    </span>
                    {message.processing_time_ms && (
                      <span className="text-xs opacity-70">
                        ⚡ {message.processing_time_ms}ms
                      </span>
                    )}
                    {message.tokens_used && (
                      <span className="text-xs opacity-70">
                        🎫 {message.tokens_used} tokens
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input Area */}
      <div className="px-4 py-3 border-t border-border">
        {testMutation.isError && (
          <div className="mb-3 p-3 bg-red-500/10 border border-red-500/20 rounded-md">
            <p className="text-sm text-red-400">
              ⚠️ Error: {testMutation.error?.message || 'Failed to send message'}
            </p>
          </div>
        )}

        <div className="flex gap-2">
          <div className="flex-1 relative">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={handleKeyPress}
              rows={2}
              disabled={testMutation.isPending}
              className="w-full px-3 py-2 bg-background border border-border rounded-md text-foreground placeholder-[#737373] focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent resize-none disabled:opacity-50"
              placeholder="Type your message... (Enter to send, Shift+Enter for new line)"
            />
            {input.length > 0 && (
              <span className="absolute bottom-2 right-2 text-xs text-[#737373]">
                {input.length}/2000
              </span>
            )}
          </div>
          <button
            onClick={handleSendMessage}
            disabled={!input.trim() || testMutation.isPending}
            className="px-4 py-2 bg-primary text-white rounded-md hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium"
          >
            {testMutation.isPending ? (
              <span className="flex items-center gap-2">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                <span>Sending...</span>
              </span>
            ) : (
              '📤 Send'
            )}
          </button>
        </div>

        <div className="mt-2 flex items-center gap-4 text-xs text-[#737373]">
          <div className="flex items-center gap-1.5">
            <span>🧠</span>
            <span>{agent.llm_config.provider}/{agent.llm_config.model}</span>
          </div>
          {Object.keys(context).length > 0 && (
            <div className="flex items-center gap-1.5">
              <span>📝</span>
              <span>{Object.keys(context).length} context field(s)</span>
            </div>
          )}
          <div className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${
              agent.status === 'active' ? 'bg-green-400' : 'bg-gray-400'
            }`} />
            <span className="capitalize">{agent.status}</span>
          </div>
        </div>
      </div>
    </div>
  );
};
