/**
 * Template Detail Modal Component
 *
 * Shows detailed information about a template and lets the user clone it into
 * a new agent. Works with AgentTemplate (from list API) — no scenario_document
 * or evaluation_config; uses step_count / tool_types / default_agent_settings.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Star, Users, Sparkles, Workflow, Settings, GitBranch, Wrench, Mic, MessageSquare, Layers,
  CheckCircle2, AlertCircle, Plug,
} from 'lucide-react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/atoms/dialog';
import { Button } from '@/components/atoms/button';
import { Badge } from '@/components/atoms/badge';
import { Input } from '@/components/atoms/input';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/atoms/accordion';
import { cn } from '@/lib/utils';
import type {
  AgentTemplate,
  CloneAgentTemplateResponse,
  AgentTemplateRequiredToolsResponse,
} from '@/api/services/template.service';
import { agentTemplateService } from '@/api/services/template.service';

export interface TemplateDetailModalProps {
  template: AgentTemplate;
  onClose: () => void;
  onUseTemplate: (template: AgentTemplate, cloneResponse?: CloneAgentTemplateResponse) => void;
  showCloneButton?: boolean;
}

function AgentTypeIcon({ type }: { type: string }) {
  if (type === 'chat') return <MessageSquare className="h-4 w-4" />;
  if (type === 'multimodal') return <Layers className="h-4 w-4" />;
  return <Mic className="h-4 w-4" />;
}

export function TemplateDetailModal({
  template,
  onClose,
  onUseTemplate,
  showCloneButton = true,
}: TemplateDetailModalProps) {
  const [showCloneForm, setShowCloneForm] = useState(false);
  const [agentName, setAgentName] = useState(`${template.name} Copy`);
  const [agentDescription, setAgentDescription] = useState('');
  const [cloning, setCloning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const getCategoryColor = (category: string) => {
    switch (category) {
      case 'sales': return 'bg-purple-100 text-purple-800 border-purple-200';
      case 'customer-service': return 'bg-blue-100 text-blue-800 border-blue-200';
      case 'healthcare': return 'bg-green-100 text-green-800 border-green-200';
      case 'e-commerce': return 'bg-orange-100 text-orange-800 border-orange-200';
      case 'telecom': return 'bg-cyan-100 text-cyan-800 border-cyan-200';
      default: return 'bg-gray-100 text-foreground border-gray-200';
    }
  };

  // Per-org required-tools view. The backend returns satisfied=null for
  // unauth'd callers; auth'd callers get a real boolean. We surface this
  // as a checklist preview so the user knows what they'll need to set up.
  const { data: requiredToolsResponse } = useQuery<AgentTemplateRequiredToolsResponse>({
    queryKey: ['agent-template-required-tools', template.template_id],
    queryFn: () => agentTemplateService.getRequiredTools(template.template_id),
    staleTime: 30_000,
  });
  const requiredTools = requiredToolsResponse?.tools ?? [];
  const trulyRequired = requiredTools.filter((t) => t.required);
  const optionalTools = requiredTools.filter((t) => !t.required);
  const unsatisfiedRequiredCount = trulyRequired.filter((t) => t.satisfied === false).length;
  const hasSatisfactionData = requiredTools.some((t) => t.satisfied !== null);

  const handleClone = async () => {
    if (!agentName.trim()) {
      setError('Please enter an agent name');
      return;
    }
    try {
      setCloning(true);
      setError(null);
      const response = await agentTemplateService.cloneTemplate(template.template_id, {
        agent_name: agentName.trim(),
      });
      onUseTemplate(template, response);
    } catch (err) {
      console.error('Failed to clone template:', err);
      setError('Failed to create agent from template. Please try again.');
      setCloning(false);
    }
  };

  const { system_prompt, agent_type } = template.default_agent_settings;

  // First paragraph of description (before any newline) for the header summary
  const descriptionSummary = template.description.split('\n\n')[0];

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between gap-2">
            <span>{template.name}</span>
            {template.is_featured && (
              <Star className="h-5 w-5 fill-yellow-400 text-yellow-400 flex-shrink-0" />
            )}
          </DialogTitle>
          <DialogDescription className="sr-only">{descriptionSummary}</DialogDescription>
        </DialogHeader>

        <div className="space-y-6">
          {/* Clone configuration form — replaces template details */}
          {showCloneForm && (
            <div className="space-y-5">
              <h3 className="text-lg font-semibold">Customize Your Agent</h3>

              {error && (
                <div className="bg-destructive/10 border border-destructive/30 rounded-md p-3 text-sm text-destructive">
                  {error}
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-muted-foreground mb-1">
                  Agent Name <span className="text-red-500">*</span>
                </label>
                <Input
                  value={agentName}
                  onChange={(e) => setAgentName(e.target.value)}
                  placeholder="Enter agent name"
                  disabled={cloning}
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-muted-foreground mb-1">
                  Description (Optional)
                </label>
                <textarea
                  value={agentDescription}
                  onChange={(e) => setAgentDescription(e.target.value)}
                  placeholder="Describe what this agent will do"
                  rows={3}
                  disabled={cloning}
                  className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>

              <div className="rounded-md border p-4 bg-muted">
                <div className="flex items-start gap-3">
                  <Settings className="h-5 w-5 text-muted-foreground mt-0.5 flex-shrink-0" />
                  <div className="text-sm text-muted-foreground">
                    <p className="font-medium text-muted-foreground mb-1">What happens next</p>
                    <ul className="space-y-1 list-disc list-inside text-xs">
                      <li>Agent will be created as a <strong>draft</strong></li>
                      <li>All {template.step_count} states and transitions will be copied</li>
                      <li>You can configure LLM, voice, and tool settings in the canvas</li>
                      <li>Publish when ready to make it live</li>
                    </ul>
                  </div>
                </div>
              </div>

              <div className="flex gap-3 pt-2">
                <Button onClick={handleClone} disabled={cloning} className="flex-1">
                  {cloning ? (
                    <>
                      <Sparkles className="h-4 w-4 mr-2 animate-spin" />
                      Creating Agent...
                    </>
                  ) : (
                    <>
                      <Sparkles className="h-4 w-4 mr-2" />
                      Create Agent
                    </>
                  )}
                </Button>
                <Button variant="outline" onClick={() => setShowCloneForm(false)} disabled={cloning}>
                  Back
                </Button>
              </div>
            </div>
          )}

          {/* Template details — hidden when clone form is active */}
          {!showCloneForm && <>
          {/* Metadata badges */}
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className={cn(getCategoryColor(template.category))}>
              {template.category.replace(/-/g, ' ')}
            </Badge>
            <Badge variant="outline" className="flex items-center gap-1">
              <AgentTypeIcon type={agent_type} />
              {agent_type}
            </Badge>
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <GitBranch className="h-4 w-4" />
              <span>{template.step_count} states</span>
            </div>
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <Users className="h-4 w-4" />
              <span>{template.usage_count} uses</span>
            </div>
          </div>

          {/* Description — show full multi-paragraph version */}
          <div>
            <h3 className="font-semibold mb-2">Description</h3>
            <div className="text-muted-foreground text-sm space-y-2 whitespace-pre-line">
              {template.description}
            </div>
          </div>

          {/* Tags */}
          {template.tags.length > 0 && (
            <div>
              <h3 className="font-semibold mb-3">Tags</h3>
              <div className="flex flex-wrap gap-2">
                {template.tags.map((tag) => (
                  <Badge key={tag} variant="secondary">
                    {tag}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {/* Summary stats */}
          <div className="bg-muted rounded-lg p-4">
            <h3 className="font-semibold mb-3">Template includes</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="flex items-center gap-2">
                <GitBranch className="h-4 w-4 text-muted-foreground" />
                <span className="text-muted-foreground">States:</span>{' '}
                <span className="font-medium">{template.step_count}</span>
              </div>
              <div className="flex items-center gap-2">
                <Wrench className="h-4 w-4 text-muted-foreground" />
                <span className="text-muted-foreground">Tool namespaces:</span>{' '}
                <span className="font-medium">
                  {template.tool_types.length > 0 ? template.tool_types.length : '—'}
                </span>
              </div>
            </div>
          </div>

          {/* Detail accordion */}
          <Accordion type="multiple" className="w-full">
            {/* Agent configuration */}
            <AccordionItem value="config">
              <AccordionTrigger>
                <div className="flex items-center gap-2">
                  <Settings className="h-5 w-5 text-primary" />
                  <span className="font-semibold">Default Agent Configuration</span>
                </div>
              </AccordionTrigger>
              <AccordionContent>
                <div className="space-y-4 pt-2">
                  <div>
                    <h4 className="text-sm font-medium text-muted-foreground mb-2">Agent Type</h4>
                    <Badge variant="outline" className="flex items-center gap-1 w-fit">
                      <AgentTypeIcon type={agent_type} />
                      {agent_type}
                    </Badge>
                  </div>
                  {system_prompt && (
                    <div>
                      <h4 className="text-sm font-medium text-muted-foreground mb-2">System Prompt</h4>
                      <div className="bg-muted rounded-md p-3 text-sm text-foreground whitespace-pre-wrap font-mono leading-relaxed">
                        {system_prompt}
                      </div>
                    </div>
                  )}
                </div>
              </AccordionContent>
            </AccordionItem>

            {/* Required integrations — onboarding metadata, NOT runtime contract.
                See docs/templates/Template-Required-Tools-Onboarding-Spec.md. */}
            {requiredTools.length > 0 && (
              <AccordionItem value="required-integrations">
                <AccordionTrigger>
                  <div className="flex items-center gap-2">
                    <Plug className="h-5 w-5 text-primary" />
                    <span className="font-semibold">Required Integrations</span>
                    <Badge variant="secondary" className="ml-2">
                      {requiredTools.length}
                    </Badge>
                    {hasSatisfactionData && unsatisfiedRequiredCount > 0 && (
                      <Badge variant="outline" className="ml-1 border-amber-300 bg-amber-50 text-amber-900">
                        {unsatisfiedRequiredCount} required not set up
                      </Badge>
                    )}
                  </div>
                </AccordionTrigger>
                <AccordionContent>
                  <div className="space-y-3 pt-2">
                    <p className="text-sm text-muted-foreground">
                      {trulyRequired.length > 0 && optionalTools.length > 0
                        ? `This template needs ${trulyRequired.length} integration${trulyRequired.length === 1 ? '' : 's'} configured to publish, and uses ${optionalTools.length} optional one${optionalTools.length === 1 ? '' : 's'} on conditional branches. You can clone first and set them up afterwards.`
                        : trulyRequired.length > 0
                          ? `This template needs ${trulyRequired.length} integration${trulyRequired.length === 1 ? '' : 's'} configured before it can publish. You can clone first and set up afterwards.`
                          : `This template uses ${optionalTools.length} optional integration${optionalTools.length === 1 ? '' : 's'} on conditional branches. None are required to publish.`}
                    </p>
                    {requiredTools.map((tool) => (
                      <div
                        key={tool.tool_ref}
                        className="rounded-md border p-3 bg-card"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                              <span className="font-medium text-sm">{tool.display_name}</span>
                              <code className="text-xs text-muted-foreground font-mono">
                                {tool.tool_ref}
                              </code>
                              {tool.required ? (
                                <Badge variant="outline" className="text-[10px] border-amber-300 bg-amber-50 text-amber-900">
                                  Required
                                </Badge>
                              ) : (
                                <Badge variant="outline" className="text-[10px]">
                                  Optional
                                </Badge>
                              )}
                            </div>
                            <p className="text-xs text-muted-foreground">{tool.description}</p>
                            {tool.provider_hints.length > 0 && (
                              <div className="mt-2 flex flex-wrap gap-1">
                                {tool.provider_hints.map((p) => (
                                  <Badge key={p} variant="outline" className="text-xs">
                                    {p}
                                  </Badge>
                                ))}
                              </div>
                            )}
                          </div>
                          {tool.satisfied === true && (
                            <div
                              className="flex items-center gap-1 text-xs text-emerald-700"
                              title="Already configured for your organization"
                            >
                              <CheckCircle2 className="h-4 w-4" />
                              <span>Set up</span>
                            </div>
                          )}
                          {tool.satisfied === false && (
                            <div
                              className="flex items-center gap-1 text-xs text-amber-700"
                              title="Not yet configured — set up after clone"
                            >
                              <AlertCircle className="h-4 w-4" />
                              <span>Not set up</span>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </AccordionContent>
              </AccordionItem>
            )}

            {/* Capabilities */}
            {template.tool_types.length > 0 && (
              <AccordionItem value="capabilities">
                <AccordionTrigger>
                  <div className="flex items-center gap-2">
                    <Wrench className="h-5 w-5 text-primary" />
                    <span className="font-semibold">Tool Integrations</span>
                    <Badge variant="secondary" className="ml-2">
                      {template.tool_types.length}
                    </Badge>
                  </div>
                </AccordionTrigger>
                <AccordionContent>
                  <div className="space-y-2 pt-2">
                    <p className="text-sm text-muted-foreground mb-3">
                      This template calls tools in the following namespaces. Wire up the
                      corresponding tool executors after creating the agent.
                    </p>
                    {template.tool_types.map((toolType) => (
                      <div key={toolType} className="p-2 bg-muted rounded-md text-sm flex items-center gap-2">
                        <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
                        <code className="font-mono">{toolType}.*</code>
                      </div>
                    ))}
                  </div>
                </AccordionContent>
              </AccordionItem>
            )}

            {/* Workflow note */}
            <AccordionItem value="workflow">
              <AccordionTrigger>
                <div className="flex items-center gap-2">
                  <Workflow className="h-5 w-5 text-primary" />
                  <span className="font-semibold">Conversation Flow</span>
                  <Badge variant="secondary" className="ml-2">
                    {template.step_count} states
                  </Badge>
                </div>
              </AccordionTrigger>
              <AccordionContent>
                <div className="pt-2 text-sm text-muted-foreground space-y-2">
                  <p>
                    The flow diagram is available in the canvas editor after creating your agent.
                    The full agent definition ({template.step_count} states) will be loaded and ready
                    to customise.
                  </p>
                  {template.description.includes('Flow:') && (
                    <div className="bg-muted rounded-md p-3 font-mono text-xs leading-relaxed whitespace-pre-line text-muted-foreground">
                      {template.description
                        .split('\n\n')
                        .find((block) => block.trimStart().startsWith('Flow:')) ?? ''}
                    </div>
                  )}
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>

          </>}

          {/* Actions — shown when clone form is hidden */}
          {!showCloneForm && showCloneButton && (
            <div className="flex gap-3">
              <Button onClick={() => setShowCloneForm(true)} className="flex-1">
                <Sparkles className="h-4 w-4 mr-2" />
                Use This Template
              </Button>
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
