/**
 * Rich Step Editor Component
 *
 * Workflow step builder with:
 * - Inline quotes for "Say" actions
 * - @variable syntax highlighting
 * - Trigger/action chips
 * - Retry configuration
 * - Escalation paths
 */

import React, { useState } from 'react';
import { Card } from '@/components/atoms/card';
import { Button } from '@/components/atoms/button';
import { Input } from '@/components/atoms/input';
import { Label } from '@/components/atoms/label';
import { Badge } from '@/components/atoms/badge';
import { Switch } from '@/components/atoms/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/atoms/popover';
import {
  MessageSquare,
  Zap,
  GitBranch,
  Play,
  RefreshCw,
  AlertTriangle,
  ChevronRight,
  Plus,
  X,
  AtSign,
  Quote,
  ArrowRight,
  PhoneForwarded,
  Flag,
  CheckCircle2,
  Settings,
  Clock,
  Repeat,
  Shield,
} from 'lucide-react';
import { cn } from '@/lib/utils';

// ==================== Types ====================

export type StepType = 'say' | 'listen' | 'condition' | 'tool' | 'trigger' | 'escalate';
export type TriggerType = 'run' | 'trigger' | 'mark' | 'set' | 'call';
export type ActionStatus = 'pending' | 'success' | 'failed' | 'skipped';

export interface VariableReference {
  name: string;
  type: 'context' | 'user' | 'session' | 'custom';
  description?: string;
}

export interface TriggerAction {
  id: string;
  type: TriggerType;
  target: string;
  params?: Record<string, unknown>;
  label?: string;
}

export interface RetryConfig {
  enabled: boolean;
  maxAttempts: number;
  delayMs: number;
  backoffMultiplier: number;
  onFailure: 'continue' | 'escalate' | 'end' | 'goto';
  failureTarget?: string;
}

export interface ConditionBranch {
  id: string;
  expression: string;
  label: string;
  targetStepId?: string;
}

export interface WorkflowStep {
  id: string;
  type: StepType;
  label: string;
  content: string;
  sayText?: string;
  conditions?: ConditionBranch[];
  triggers?: TriggerAction[];
  retryConfig?: RetryConfig;
  escalationPath?: string;
  nextStepId?: string;
  metadata?: Record<string, unknown>;
}

// Maps canvas node type → display label shown in the step badge
const NODE_TYPE_LABELS: Record<string, string> = {
  message:   'Message',
  default:   'Message',   // nodes with undefined type fall back to 'default'
  condition: 'Condition',
  code:      'Code',
  ai:        'AI',
  tool:      'Tool',
  transfer:  'Transfer',
  closing:   'End Call',
  // abstract step type fallbacks
  say:       'Message',
  listen:    'Listen',
  trigger:   'Trigger',
  escalate:  'Escalate',
};

// ==================== Available Variables ====================

const AVAILABLE_VARIABLES: VariableReference[] = [
  { name: 'user_name', type: 'user', description: 'Customer name' },
  { name: 'user_id', type: 'user', description: 'Customer ID' },
  { name: 'account_number', type: 'user', description: 'Account number' },
  { name: 'info_confirmed', type: 'session', description: 'Info confirmation status' },
  { name: 'identity_verified', type: 'session', description: 'Identity verification status' },
  { name: 'intent', type: 'context', description: 'Detected user intent' },
  { name: 'sentiment', type: 'context', description: 'Sentiment score' },
  { name: 'last_response', type: 'context', description: 'Last user response' },
  { name: 'retry_count', type: 'session', description: 'Current retry count' },
  { name: 'call_duration', type: 'session', description: 'Call duration in seconds' },
];

const AVAILABLE_TRIGGERS: { type: TriggerType; label: string; icon: React.ReactNode }[] = [
  { type: 'run', label: 'Run', icon: <Play className="h-3 w-3" /> },
  { type: 'trigger', label: 'Trigger', icon: <Zap className="h-3 w-3" /> },
  { type: 'mark', label: 'Mark', icon: <CheckCircle2 className="h-3 w-3" /> },
  { type: 'set', label: 'Set', icon: <Settings className="h-3 w-3" /> },
  { type: 'call', label: 'Call', icon: <PhoneForwarded className="h-3 w-3" /> },
];

// ==================== Inline Text Editor with Variables ====================

interface InlineTextEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  showQuotes?: boolean;
  className?: string;
}

function InlineTextEditor({
  value,
  onChange,
  placeholder,
  showQuotes = false,
  className,
}: InlineTextEditorProps) {
  const [showVariablePicker, setShowVariablePicker] = useState(false);
  const [cursorPosition, setCursorPosition] = useState(0);

  // Highlight @variables in the text
  const renderHighlightedText = (text: string) => {
    const parts = text.split(/(@\w+)/g);
    return parts.map((part, index) => {
      if (part.startsWith('@')) {
        const varName = part.slice(1);
        const variable = AVAILABLE_VARIABLES.find((v) => v.name === varName);
        return (
          <span
            key={index}
            className={cn(
              'inline-flex items-center px-1.5 py-0.5 mx-0.5 rounded text-xs font-medium',
              variable?.type === 'user' && 'bg-blue-500/20 text-blue-300',
              variable?.type === 'session' && 'bg-purple-500/20 text-purple-300',
              variable?.type === 'context' && 'bg-green-500/20 text-green-300',
              !variable && 'bg-muted text-muted-foreground'
            )}
          >
            <AtSign className="h-3 w-3 mr-0.5" />
            {varName}
          </span>
        );
      }
      return <span key={index}>{part}</span>;
    });
  };

  const insertVariable = (varName: string) => {
    const newValue = value.slice(0, cursorPosition) + `@${varName}` + value.slice(cursorPosition);
    onChange(newValue);
    setShowVariablePicker(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === '@') {
      setShowVariablePicker(true);
      setCursorPosition((e.target as HTMLTextAreaElement).selectionStart + 1);
    }
  };

  return (
    <div className={cn('relative', className)}>
      {showQuotes && (
        <Quote className="absolute left-3 top-3 h-4 w-4 text-emerald-400/50" />
      )}
      <div className="relative">
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onSelect={(e) => setCursorPosition((e.target as HTMLTextAreaElement).selectionStart)}
          placeholder={placeholder}
          className={cn(
            'w-full rounded-lg border border-border bg-muted/50 px-3 py-2.5 text-sm text-foreground',
            'placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none',
            'focus:ring-2 focus:ring-primary/20 resize-none transition-all',
            showQuotes && 'pl-9 italic'
          )}
          rows={3}
        />

        {/* Variable Picker Popover */}
        <Popover open={showVariablePicker} onOpenChange={setShowVariablePicker}>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="absolute right-2 top-2 h-7 px-2 text-muted-foreground hover:text-foreground"
              onClick={() => setShowVariablePicker(true)}
            >
              <AtSign className="h-4 w-4" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-64 p-0" align="end">
            <div className="p-2 border-b border-border">
              <p className="text-xs font-medium text-muted-foreground">Insert Variable</p>
            </div>
            <div className="max-h-48 overflow-y-auto p-1">
              {AVAILABLE_VARIABLES.map((variable) => (
                <button
                  key={variable.name}
                  onClick={() => insertVariable(variable.name)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 text-sm rounded hover:bg-muted text-left"
                >
                  <Badge
                    variant="outline"
                    className={cn(
                      'text-xs px-1.5',
                      variable.type === 'user' && 'border-blue-500/50 text-blue-300',
                      variable.type === 'session' && 'border-purple-500/50 text-purple-300',
                      variable.type === 'context' && 'border-green-500/50 text-green-300'
                    )}
                  >
                    @{variable.name}
                  </Badge>
                  <span className="text-xs text-muted-foreground">{variable.description}</span>
                </button>
              ))}
            </div>
          </PopoverContent>
        </Popover>
      </div>

      {/* Preview with highlighted variables */}
      {value && value.includes('@') && (
        <div className="mt-2 p-2 rounded bg-muted border border-border text-sm">
          <span className="text-xs text-muted-foreground block mb-1">Preview:</span>
          {showQuotes && <Quote className="inline h-3 w-3 text-emerald-400/50 mr-1" />}
          {renderHighlightedText(value)}
          {showQuotes && <Quote className="inline h-3 w-3 text-emerald-400/50 ml-1 rotate-180" />}
        </div>
      )}
    </div>
  );
}

// ==================== Trigger Action Chip ====================

interface TriggerChipProps {
  trigger: TriggerAction;
  onRemove: () => void;
  onEdit: (trigger: TriggerAction) => void;
}

function TriggerChip({ trigger, onRemove, onEdit }: TriggerChipProps) {
  const triggerMeta = AVAILABLE_TRIGGERS.find((t) => t.type === trigger.type);

  const getChipColor = (type: TriggerType) => {
    switch (type) {
      case 'run':
        return 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30';
      case 'trigger':
        return 'bg-orange-500/20 text-orange-300 border-orange-500/30';
      case 'mark':
        return 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30';
      case 'set':
        return 'bg-purple-500/20 text-purple-300 border-purple-500/30';
      case 'call':
        return 'bg-red-500/20 text-red-300 border-red-500/30';
      default:
        return 'bg-muted text-muted-foreground border-border';
    }
  };

  return (
    <div
      className={cn(
        'inline-flex items-center gap-1.5 px-2 py-1 rounded-full border text-xs font-medium cursor-pointer',
        'hover:opacity-80 transition-opacity',
        getChipColor(trigger.type)
      )}
      onClick={() => onEdit(trigger)}
    >
      {triggerMeta?.icon}
      <span>{trigger.type}</span>
      <span className="font-mono">{trigger.target}</span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        className="ml-1 hover:bg-black/20 dark:hover:bg-white/10 rounded-full p-0.5"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

// ==================== Add Trigger Button ====================

interface AddTriggerButtonProps {
  onAdd: (trigger: TriggerAction) => void;
}

function AddTriggerButton({ onAdd }: AddTriggerButtonProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedType, setSelectedType] = useState<TriggerType>('run');
  const [targetValue, setTargetValue] = useState('');

  const handleAdd = () => {
    if (!targetValue.trim()) return;
    onAdd({
      id: `trigger-${Date.now()}`,
      type: selectedType,
      target: targetValue,
    });
    setTargetValue('');
    setIsOpen(false);
  };

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="border-dashed border-border text-muted-foreground hover:text-foreground h-7 px-2"
        >
          <Plus className="h-3 w-3 mr-1" />
          Add Trigger
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-3" align="start">
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Trigger Type</Label>
            <div className="flex flex-wrap gap-1">
              {AVAILABLE_TRIGGERS.map((t) => (
                <button
                  key={t.type}
                  onClick={() => setSelectedType(t.type)}
                  className={cn(
                    'flex items-center gap-1 px-2 py-1 rounded text-xs border',
                    selectedType === t.type
                      ? 'bg-primary/20 border-primary/50 text-primary'
                      : 'border-border text-muted-foreground hover:bg-muted'
                  )}
                >
                  {t.icon}
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Target</Label>
            <Input
              value={targetValue}
              onChange={(e) => setTargetValue(e.target.value)}
              placeholder={
                selectedType === 'run' ? 'verify_identity_check' :
                selectedType === 'trigger' ? 'compliance_flag' :
                selectedType === 'mark' ? 'account_onboarded' :
                selectedType === 'set' ? 'variable_name' :
                'phone_number'
              }
              className="h-8 text-sm"
            />
          </div>
          <Button size="sm" className="w-full" onClick={handleAdd}>
            Add Trigger
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

// ==================== Retry Configuration ====================

interface RetryConfigEditorProps {
  config: RetryConfig;
  onChange: (config: RetryConfig) => void;
  steps: WorkflowStep[];
}

function RetryConfigEditor({ config, onChange, steps }: RetryConfigEditorProps) {
  return (
    <div className="space-y-3 p-3 rounded-lg bg-amber-500/5 border border-amber-500/20">
      <div className="flex items-center justify-between">
        <Label className="flex items-center gap-2 text-sm text-amber-300">
          <Repeat className="h-4 w-4" />
          Retry Configuration
        </Label>
        <Switch
          checked={config.enabled}
          onCheckedChange={(enabled) => onChange({ ...config, enabled })}
        />
      </div>

      {config.enabled && (
        <div className="space-y-3 pt-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Max Attempts</Label>
              <Input
                type="number"
                min={1}
                max={10}
                value={config.maxAttempts}
                onChange={(e) => onChange({ ...config, maxAttempts: parseInt(e.target.value) || 1 })}
                className="h-8"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Delay (ms)</Label>
              <Input
                type="number"
                min={100}
                step={100}
                value={config.delayMs}
                onChange={(e) => onChange({ ...config, delayMs: parseInt(e.target.value) || 1000 })}
                className="h-8"
              />
            </div>
          </div>

          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">On Failure</Label>
            <Select
              value={config.onFailure}
              onValueChange={(value: RetryConfig['onFailure']) => onChange({ ...config, onFailure: value })}
            >
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="continue">Continue to next step</SelectItem>
                <SelectItem value="escalate">Escalate to human</SelectItem>
                <SelectItem value="end">End conversation</SelectItem>
                <SelectItem value="goto">Go to specific step</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {config.onFailure === 'goto' && (
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Failure Target Step</Label>
              <Select
                value={config.failureTarget || ''}
                onValueChange={(value) => onChange({ ...config, failureTarget: value })}
              >
                <SelectTrigger className="h-8">
                  <SelectValue placeholder="Select step..." />
                </SelectTrigger>
                <SelectContent>
                  {steps.map((step) => (
                    <SelectItem key={step.id} value={step.id}>
                      {step.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="p-2 rounded bg-muted text-xs text-muted-foreground">
            <p>Retry up to <strong className="text-amber-300">{config.maxAttempts} times</strong>.</p>
            <p>If still fails, <strong className="text-amber-300">
              {config.onFailure === 'continue' && 'proceed to next step'}
              {config.onFailure === 'escalate' && 'escalate to human agent'}
              {config.onFailure === 'end' && 'end the conversation'}
              {config.onFailure === 'goto' && `go to "${steps.find(s => s.id === config.failureTarget)?.label || 'selected step'}"`}
            </strong>.</p>
          </div>
        </div>
      )}
    </div>
  );
}

// ==================== Condition Branch Editor ====================

interface ConditionBranchEditorProps {
  branches: ConditionBranch[];
  onChange: (branches: ConditionBranch[]) => void;
  steps: WorkflowStep[];
}

function ConditionBranchEditor({ branches, onChange, steps }: ConditionBranchEditorProps) {
  const addBranch = () => {
    if (branches.length >= 2) return;
    onChange([
      ...branches,
      {
        id: `branch-${Date.now()}`,
        expression: '',
        label: `Condition ${branches.length + 1}`,
      },
    ]);
  };

  const updateBranch = (id: string, updates: Partial<ConditionBranch>) => {
    onChange(branches.map((b) => (b.id === id ? { ...b, ...updates } : b)));
  };

  const removeBranch = (id: string) => {
    onChange(branches.filter((b) => b.id !== id));
  };

  return (
    <div className="space-y-3">
      <Label className="flex items-center gap-2 text-sm text-amber-600 dark:text-amber-300">
        <GitBranch className="h-4 w-4" />
        Conditional Branches
      </Label>

      <div className="space-y-2">
        {branches.map((branch, index) => (
            <div
              key={branch.id || `branch-${index}`}
              className="p-3 rounded-lg bg-muted/50 border border-border space-y-2"
            >
              <div className="flex items-center justify-between">
              <span className={cn(
                'text-xs font-medium',
                index === 0 ? 'text-emerald-400' : index === branches.length - 1 ? 'text-red-400' : 'text-amber-400'
              )}>
                {index === 0 ? 'If' : index === branches.length - 1 ? 'Else' : 'Else If'}
              </span>
                {branches.length > 1 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-destructive hover:text-destructive"
                    onClick={() => removeBranch(branch.id)}
                >
                  <X className="h-3 w-3" />
                </Button>
              )}
            </div>

            {index !== branches.length - 1 && (
              <InlineTextEditor
                value={branch.expression}
                onChange={(value) => updateBranch(branch.id, { expression: value })}
                placeholder="Customer provides @info_confirmed"
                className="text-sm"
              />
            )}

            <div className="flex items-center gap-2">
              <ArrowRight className={cn(
                'h-4 w-4',
                index === 0 ? 'text-emerald-400' : index === branches.length - 1 ? 'text-red-400' : 'text-amber-400'
              )} />
              <Select
                value={branch.targetStepId || ''}
                onValueChange={(value) => updateBranch(branch.id, { targetStepId: value })}
              >
                <SelectTrigger className="h-8 flex-1">
                  <SelectValue placeholder="Proceed to..." />
                </SelectTrigger>
                <SelectContent>
                  {steps.map((step) => (
                    <SelectItem key={step.id} value={step.id}>
                      {step.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        ))}
      </div>

        {branches.length < 2 && (
          <Button
            variant="outline"
            size="sm"
            className="w-full border-dashed border-border"
            onClick={addBranch}
        >
          <Plus className="h-3 w-3 mr-1" />
          Add Branch
        </Button>
      )}
    </div>
  );
}

// ==================== Escalation Path Viewer ====================

interface EscalationPathProps {
  step: WorkflowStep;
  allSteps: WorkflowStep[];
  onEscalationChange: (path: string) => void;
}

function EscalationPathViewer({ step, allSteps, onEscalationChange }: EscalationPathProps) {
  return (
    <div className="space-y-3 p-3 rounded-lg bg-red-500/5 border border-red-500/20">
      <div className="flex items-center justify-between">
        <Label className="flex items-center gap-2 text-sm text-red-300">
          <PhoneForwarded className="h-4 w-4" />
          Escalation Path
        </Label>
        <Badge variant="outline" className="text-xs border-red-500/30 text-red-300">
          <Shield className="h-3 w-3 mr-1" />
          Safety Net
        </Badge>
      </div>

      <p className="text-xs text-muted-foreground">
        Define where to route when this step encounters an unrecoverable error or the user requests escalation.
      </p>

      <Select
        value={step.escalationPath || ''}
        onValueChange={onEscalationChange}
      >
        <SelectTrigger className="h-8">
          <SelectValue placeholder="Select escalation target..." />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="human_agent">Transfer to Human Agent</SelectItem>
          <SelectItem value="supervisor">Escalate to Supervisor</SelectItem>
          <SelectItem value="callback">Schedule Callback</SelectItem>
          <SelectItem value="voicemail">Send to Voicemail</SelectItem>
          {allSteps
            .filter((s) => s.type === 'escalate')
            .map((s) => (
              <SelectItem key={s.id} value={s.id}>
                {s.label}
              </SelectItem>
            ))}
        </SelectContent>
      </Select>

      {step.escalationPath && (
        <div className="flex items-center gap-2 p-2 rounded bg-muted">
          <AlertTriangle className="h-4 w-4 text-amber-400" />
          <span className="text-xs text-muted-foreground">
            On escalation: <strong className="text-red-300">
              {step.escalationPath === 'human_agent' && 'Transfer to human agent'}
              {step.escalationPath === 'supervisor' && 'Escalate to supervisor'}
              {step.escalationPath === 'callback' && 'Schedule callback'}
              {step.escalationPath === 'voicemail' && 'Send to voicemail'}
              {!['human_agent', 'supervisor', 'callback', 'voicemail'].includes(step.escalationPath) &&
                allSteps.find((s) => s.id === step.escalationPath)?.label}
            </strong>
          </span>
        </div>
      )}
    </div>
  );
}

// ==================== Main Rich Step Editor ====================

interface RichStepEditorProps {
  step: WorkflowStep;
  stepIndex: number;
  allSteps: WorkflowStep[];
  onChange: (step: WorkflowStep) => void;
  onDelete: () => void;
}

export function RichStepEditor({
  step,
  stepIndex,
  allSteps,
  onChange,
  onDelete,
}: RichStepEditorProps) {
  const [isExpanded, setIsExpanded] = useState(true);

  const handleTriggerAdd = (trigger: TriggerAction) => {
    onChange({
      ...step,
      triggers: [...(step.triggers || []), trigger],
    });
  };

  const handleTriggerRemove = (triggerId: string) => {
    onChange({
      ...step,
      triggers: (step.triggers || []).filter((t) => t.id !== triggerId),
    });
  };

  const getStepIcon = () => {
    switch (step.type) {
      case 'say':
        return <MessageSquare className="h-4 w-4" />;
      case 'listen':
        return <MessageSquare className="h-4 w-4 rotate-180" />;
      case 'condition':
        return <GitBranch className="h-4 w-4" />;
      case 'tool':
        return <Zap className="h-4 w-4" />;
      case 'trigger':
        return <Play className="h-4 w-4" />;
      case 'escalate':
        return <PhoneForwarded className="h-4 w-4" />;
      default:
        return <ChevronRight className="h-4 w-4" />;
    }
  };

  const getStepColor = () => {
    switch (step.type) {
      case 'say':        return { text: 'text-emerald-400', badge: 'border-emerald-500/40 text-emerald-400', rail: 'border-emerald-500/70', bg: 'bg-emerald-500/15' };
      case 'listen':     return { text: 'text-blue-400',    badge: 'border-blue-500/40 text-blue-400',       rail: 'border-blue-500/70',    bg: 'bg-blue-500/15' };
      case 'condition':  return { text: 'text-amber-400',   badge: 'border-amber-500/40 text-amber-400',     rail: 'border-amber-500/70',   bg: 'bg-amber-500/15' };
      case 'tool':
                        return { text: 'text-orange-400',  badge: 'border-orange-500/40 text-orange-400',   rail: 'border-orange-500/70',  bg: 'bg-orange-500/15' };
      case 'trigger':    return { text: 'text-cyan-400',    badge: 'border-cyan-500/40 text-cyan-400',       rail: 'border-cyan-500/70',    bg: 'bg-cyan-500/15' };
      case 'escalate':   return { text: 'text-red-400',     badge: 'border-red-500/40 text-red-400',         rail: 'border-red-500/70',     bg: 'bg-red-500/15' };
      default:           return { text: 'text-muted-foreground', badge: 'border-border text-muted-foreground', rail: 'border-border', bg: 'bg-muted' };
    }
  };

  const colors = getStepColor();

  return (
    <Card className={cn(
      'group relative overflow-hidden transition-all duration-150',
      'bg-card shadow-sm hover:shadow-md',
      'border-l-[3px]',
      colors.rail
    )}>
      {/* Header */}
      <div
        className="flex items-center justify-between p-3 cursor-pointer hover:bg-muted/40 transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-2.5 min-w-0">
          {/* Step number */}
          <div className={cn(
            'flex-shrink-0 flex items-center justify-center h-6 w-6 rounded-full text-[10px] font-bold',
            colors.bg, colors.text
          )}>
            {stepIndex + 1}
          </div>
          {/* Icon */}
          <div className={cn('flex-shrink-0 flex items-center justify-center h-7 w-7 rounded-md', colors.bg)}>
            <span className={colors.text}>{getStepIcon()}</span>
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <Badge variant="outline" className={cn('text-[10px] px-1.5 py-0 font-medium uppercase tracking-wide', colors.badge)}>
                {NODE_TYPE_LABELS[(step.metadata?.originalType as string) || step.type] || step.type}
              </Badge>
              {/* Inline condition branch chips */}
              {step.type === 'condition' && (step.conditions || []).length > 0 && (
                <div className="flex items-center gap-1">
                  {(step.conditions || []).map((b, i) => (
                    <span key={b.id || `branch-${i}`} className={cn(
                      'text-[10px] px-1.5 py-0.5 rounded-full border font-medium',
                      i === 0 ? 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10' :
                      i === (step.conditions!.length - 1) ? 'border-red-500/30 text-red-400 bg-red-500/10' :
                      'border-amber-500/30 text-amber-400 bg-amber-500/10'
                    )}>
                      {i === 0 ? 'If' : i === (step.conditions!.length - 1) ? 'Else' : 'Else If'}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <h3 className="font-medium text-sm truncate">{step.label}</h3>
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {step.triggers && step.triggers.length > 0 && (
            <Badge variant="outline" className="text-[10px] border-cyan-500/30 text-cyan-300">
              {step.triggers.length}×
            </Badge>
          )}
          {step.retryConfig?.enabled && (
            <Badge variant="outline" className="text-[10px] border-amber-500/30 text-amber-300">
              <Repeat className="h-2.5 w-2.5 mr-0.5" />
              retry
            </Badge>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Content */}
      {isExpanded && (
        <div className="px-3 pb-3 space-y-3 border-t border-border pt-3">
          {/* Label */}
          <div className="space-y-1">
            <Label className="text-xs">Step Label</Label>
            <Input
              value={step.label}
              onChange={(e) => onChange({ ...step, label: e.target.value })}
              placeholder="Enter step label"
              className="h-8"
            />
          </div>

          {/* Say Text with Quotes */}
          {(step.type === 'say' || step.type === 'listen') && (
            <div className="space-y-1">
              <Label className="text-xs flex items-center gap-2">
                <Quote className="h-3 w-3 text-emerald-400" />
                {step.type === 'say' ? 'Agent Says' : 'Expected Response'}
              </Label>
              <InlineTextEditor
                value={step.sayText || ''}
                onChange={(value) => onChange({ ...step, sayText: value })}
                placeholder={step.type === 'say' ? 'Hello @user_name, how can I help you today?' : 'Customer provides their @account_number'}
                showQuotes={step.type === 'say'}
              />
            </div>
          )}

          {/* Condition Branches */}
            {step.type === 'condition' && (
              <ConditionBranchEditor
                branches={step.conditions || [
                  { id: 'true', expression: '', label: 'If', targetStepId: '' },
                  { id: 'false', expression: '', label: 'Else', targetStepId: '' },
                ]}
                onChange={(conditions) => onChange({ ...step, conditions })}
                steps={allSteps}
              />
            )}

            {/* Primary transition */}
            {step.type !== 'condition' && step.type !== 'escalate' && (
              <div className="space-y-2 rounded-lg border border-border bg-muted/40 p-3">
                <Label className="flex items-center gap-2 text-sm text-sky-300">
                  <ArrowRight className="h-4 w-4" />
                  Proceed To
                </Label>
                <Select
                  value={step.nextStepId || '__none__'}
                  onValueChange={(value) => onChange({
                    ...step,
                    nextStepId: value === '__none__' ? undefined : value,
                  })}
                >
                  <SelectTrigger className="h-8">
                    <SelectValue placeholder="Select next step..." />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">No next step</SelectItem>
                    {allSteps
                      .filter((candidate) => candidate.id !== step.id)
                      .map((candidate) => (
                        <SelectItem key={candidate.id} value={candidate.id}>
                          {candidate.label}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  This route now writes to the actual workflow edge used by the agent compiler.
                </p>
              </div>
            )}

            {/* Trigger Actions */}
            <div className="space-y-2">
            <Label className="text-xs flex items-center gap-2">
              <Zap className="h-3 w-3 text-cyan-400" />
              Trigger Actions
            </Label>
            <div className="flex flex-wrap gap-2">
              {(step.triggers || []).map((trigger) => (
                <TriggerChip
                  key={trigger.id}
                  trigger={trigger}
                  onRemove={() => handleTriggerRemove(trigger.id)}
                  onEdit={() => {}}
                />
              ))}
              <AddTriggerButton onAdd={handleTriggerAdd} />
            </div>
          </div>

          {/* Retry Configuration */}
          <RetryConfigEditor
            config={step.retryConfig || {
              enabled: false,
              maxAttempts: 2,
              delayMs: 1000,
              backoffMultiplier: 2,
              onFailure: 'continue',
            }}
            onChange={(retryConfig) => onChange({ ...step, retryConfig })}
            steps={allSteps}
          />

          {/* Escalation Path */}
          <EscalationPathViewer
            step={step}
            allSteps={allSteps}
            onEscalationChange={(path) => onChange({ ...step, escalationPath: path })}
          />
        </div>
      )}
    </Card>
  );
}

export default RichStepEditor;
