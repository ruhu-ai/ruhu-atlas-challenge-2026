/**
 * Properties Panel Component
 *
 * Context-aware right sidebar that displays configuration options
 * based on the selected node type in the Agent Canvas.
 */

import { Node } from 'reactflow';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card';
import { Button } from '@/components/atoms/button';
import { Input } from '@/components/atoms/input';
import { Label } from '@/components/atoms/label';
import { Textarea } from '@/components/atoms/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs';
import { X, Settings } from 'lucide-react';
import { useState, useEffect } from 'react';
import { useDebounce } from 'use-debounce';
import { CodeEditor } from '@/components/molecules/code-editor';

interface PropertiesPanelProps {
  selectedNode: Node | null;
  onUpdate: (nodeId: string, data: any) => void;
  onClose: () => void;
}

export function PropertiesPanel({ selectedNode, onUpdate, onClose }: PropertiesPanelProps) {
  const [localData, setLocalData] = useState<any>({});
  const [debouncedData] = useDebounce(localData, 500);

  // Load node data when selection changes
  useEffect(() => {
    if (selectedNode) {
      setLocalData(selectedNode.data || {});
    }
  }, [selectedNode?.id]);

  // Auto-save when debounced data changes
  useEffect(() => {
    if (selectedNode && Object.keys(debouncedData).length > 0) {
      onUpdate(selectedNode.id, debouncedData);
    }
  }, [debouncedData]);

  const handleChange = (field: string, value: any) => {
    setLocalData((prev: any) => ({ ...prev, [field]: value }));
  };

  if (!selectedNode) {
    return (
      <div className="w-80 bg-gray-50 border-l p-6 flex items-center justify-center">
        <div className="text-center text-gray-500">
          <Settings className="h-12 w-12 mx-auto mb-3 text-gray-400" />
          <p className="text-sm">Select a node to edit properties</p>
        </div>
      </div>
    );
  }

  const nodeType = selectedNode.type || 'default';

  return (
    <div className="w-80 bg-white border-l flex flex-col">
      {/* Header */}
      <div className="p-4 border-b flex items-center justify-between">
        <h3 className="font-semibold text-lg">Node Properties</h3>
        <Button variant="ghost" size="sm" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        <Tabs defaultValue="config" className="w-full">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="config">Config</TabsTrigger>
            <TabsTrigger value="code">Code</TabsTrigger>
            <TabsTrigger value="advanced">Advanced</TabsTrigger>
          </TabsList>

          <TabsContent value="config" className="space-y-4 mt-4">
            {/* Node Label */}
            <div className="space-y-2">
              <Label htmlFor="label">Node Label</Label>
              <Input
                id="label"
                value={localData.label || ''}
                onChange={(e) => handleChange('label', e.target.value)}
                placeholder="Enter node label"
              />
            </div>

            {/* Node-specific configurations */}
            {nodeType === 'message' && <MessageNodeConfig data={localData} onChange={handleChange} />}
            {nodeType === 'condition' && <ConditionNodeConfig data={localData} onChange={handleChange} />}
            {nodeType === 'code' && <CodeNodeConfig data={localData} onChange={handleChange} />}
            {nodeType === 'ai' && <AINodeConfig data={localData} onChange={handleChange} />}
            {nodeType === 'transfer' && <TransferNodeConfig data={localData} onChange={handleChange} />}
            {nodeType === 'tool' && <ToolNodeConfig data={localData} onChange={handleChange} />}
          </TabsContent>

          <TabsContent value="code" className="mt-4 space-y-4">
            {nodeType === 'code' && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="python-code-editor">Python Code</Label>
                  <CodeEditor
                    value={localData.code || ''}
                    onChange={(value) => handleChange('code', value)}
                    language="python"
                    height="400px"
                  />
                </div>
                <div className="text-xs text-muted-foreground bg-blue-50 p-3 rounded">
                  <p className="font-medium mb-1">Available Context:</p>
                  <ul className="list-disc list-inside space-y-1">
                    <li>user - User profile and session data</li>
                    <li>intent - Recognized user intent</li>
                    <li>variables - Global agent variables</li>
                  </ul>
                </div>
              </>
            )}
            {nodeType === 'condition' && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="condition-code">Condition Expression</Label>
                  <CodeEditor
                    value={localData.condition || ''}
                    onChange={(value) => handleChange('condition', value)}
                    language="python"
                    height="200px"
                  />
                </div>
                <div className="text-xs text-muted-foreground bg-blue-50 p-3 rounded">
                  <p>Write Python expressions that return True or False</p>
                  <p className="mt-1">Example: user.age &gt; 18 and user.premium == True</p>
                </div>
              </>
            )}
            {nodeType !== 'code' && nodeType !== 'condition' && (
              <div className="text-sm text-muted-foreground">
                <p>This node type doesn't support custom code.</p>
                <p className="mt-2">Try a <strong>Python Code</strong> or <strong>Condition</strong> node instead.</p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="advanced" className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="node-id">Node ID</Label>
              <Input id="node-id" value={selectedNode.id} disabled />
            </div>
            <div className="space-y-2">
              <Label htmlFor="node-type">Node Type</Label>
              <Input id="node-type" value={nodeType} disabled />
            </div>
          </TabsContent>
        </Tabs>
      </div>

      {/* Footer */}
      <div className="p-4 border-t bg-gray-50">
        <p className="text-xs text-gray-500">Changes auto-save</p>
      </div>
    </div>
  );
}

// Message Node Configuration
function MessageNodeConfig({ data, onChange }: { data: any; onChange: (field: string, value: any) => void }) {
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="message">Message Text</Label>
        <Textarea
          id="message"
          value={data.message || ''}
          onChange={(e) => onChange('message', e.target.value)}
          placeholder="What should the agent say?"
          rows={4}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="voice">Voice</Label>
        <Select value={data.voice || 'en-US-Chirp3-HD-Kore'} onValueChange={(value) => onChange('voice', value)}>
          <SelectTrigger>
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
      <div className="space-y-2">
        <Label htmlFor="wait-response" className="flex items-center gap-2">
          <input
            type="checkbox"
            id="wait-response"
            checked={data.waitForResponse || false}
            onChange={(e) => onChange('waitForResponse', e.target.checked)}
            className="rounded"
          />
          Wait for user response
        </Label>
      </div>
    </>
  );
}

// Condition Node Configuration
function ConditionNodeConfig({ data, onChange }: { data: any; onChange: (field: string, value: any) => void }) {
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="condition">Condition Expression</Label>
        <Textarea
          id="condition"
          value={data.condition || ''}
          onChange={(e) => onChange('condition', e.target.value)}
          placeholder="e.g., user.age > 18"
          rows={3}
        />
      </div>
      <div className="text-xs text-gray-500 bg-blue-50 p-2 rounded">
        <p>Use Python-like expressions.</p>
        <p>Available variables: user, session, intent</p>
      </div>
    </>
  );
}

// Code Node Configuration
function CodeNodeConfig({ data, onChange }: { data: any; onChange: (field: string, value: any) => void }) {
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="python-code">Python Code Preview</Label>
        <Textarea
          id="python-code"
          value={data.code || ''}
          onChange={(e) => onChange('code', e.target.value)}
          placeholder="# Write your Python code here"
          rows={6}
          className="font-mono text-sm"
        />
      </div>
      <div className="text-xs text-gray-500 bg-blue-50 p-2 rounded">
        <p>💡 Switch to the <strong>Code</strong> tab for the full Monaco editor with syntax highlighting and IntelliSense.</p>
      </div>
    </>
  );
}

// AI Assistant Node Configuration
function AINodeConfig({ data, onChange }: { data: any; onChange: (field: string, value: any) => void }) {
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="prompt">AI Prompt</Label>
        <Textarea
          id="prompt"
          value={data.prompt || ''}
          onChange={(e) => onChange('prompt', e.target.value)}
          placeholder="Describe what the AI should do..."
          rows={4}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="model">Model</Label>
        <Select value={data.model || 'gpt-4'} onValueChange={(value) => onChange('model', value)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="gpt-4">GPT-4</SelectItem>
            <SelectItem value="gpt-3.5-turbo">GPT-3.5 Turbo</SelectItem>
            <SelectItem value="claude-3-sonnet">Claude 3 Sonnet</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </>
  );
}

// Transfer Node Configuration
function TransferNodeConfig({ data, onChange }: { data: any; onChange: (field: string, value: any) => void }) {
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="transfer-to">Transfer To</Label>
        <Select value={data.transferTo || 'human'} onValueChange={(value) => onChange('transferTo', value)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="human">Human Agent</SelectItem>
            <SelectItem value="phone">Phone Number</SelectItem>
            <SelectItem value="voicemail">Voicemail</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {data.transferTo === 'phone' && (
        <div className="space-y-2">
          <Label htmlFor="phone-number">Phone Number</Label>
          <Input
            id="phone-number"
            value={data.phoneNumber || ''}
            onChange={(e) => onChange('phoneNumber', e.target.value)}
            placeholder="+1234567890"
          />
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor="transfer-message">Transfer Message</Label>
        <Textarea
          id="transfer-message"
          value={data.transferMessage || ''}
          onChange={(e) => onChange('transferMessage', e.target.value)}
          placeholder="Please hold while I transfer you..."
          rows={2}
        />
      </div>
    </>
  );
}

// Tool Node Configuration
function ToolNodeConfig({ data, onChange }: { data: any; onChange: (field: string, value: any) => void }) {
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="tool-name">Tool Name</Label>
        <Input
          id="tool-name"
          value={data.toolName || ''}
          onChange={(e) => onChange('toolName', e.target.value)}
          placeholder="e.g., check_balance, create_ticket"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="tool-type">Tool Type</Label>
        <Select value={data.toolType || 'http'} onValueChange={(value) => onChange('toolType', value)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="http">HTTP Tool</SelectItem>
            <SelectItem value="composite">Composite Tool</SelectItem>
            <SelectItem value="mcp">MCP Server Tool</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="tool-timeout">Timeout (seconds)</Label>
        <Input
          id="tool-timeout"
          type="number"
          value={data.timeout || 30}
          onChange={(e) => onChange('timeout', parseInt(e.target.value) || 30)}
          min={1}
          max={300}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="param-mapping">Parameter Mapping (JSON)</Label>
        <Textarea
          id="param-mapping"
          value={data.paramMapping || ''}
          onChange={(e) => onChange('paramMapping', e.target.value)}
          placeholder='{"account_id": "{{user.account_id}}"}'
          rows={4}
          className="font-mono text-sm"
        />
      </div>
      <div className="text-xs text-gray-500 bg-emerald-50 p-2 rounded">
        <p className="font-medium mb-1">Tool Execution:</p>
        <ul className="list-disc list-inside space-y-1">
          <li>Tools with timeout &gt;30s run asynchronously</li>
          <li>Use <code className="bg-white/50 px-1 rounded">{'{{variable}}'}</code> for dynamic params</li>
          <li>Results are available as <code className="bg-white/50 px-1 rounded">tool.result</code></li>
        </ul>
      </div>
    </>
  );
}
