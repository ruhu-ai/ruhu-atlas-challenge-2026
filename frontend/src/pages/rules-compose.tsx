import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  PlayCircle,
  Save,
  Sparkles,
  Wand2,
} from 'lucide-react'
import { toast } from 'sonner'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Textarea } from '@/components/atoms/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { runtimeRulesService } from '@/api/services/runtime-rules.service'
import type {
  ComposePolicyProposal,
  RuleChannel,
  RuleDecision,
  RuleDefinitionRevisionDocument,
  RuleEvaluationContext,
  RuleEvaluationRequest,
} from '@/types/runtime-rules'

const SAMPLE_PROMPTS: Array<{ label: string; text: string }> = [
  {
    label: 'Block credit card data',
    text: 'Block any message containing credit card numbers',
  },
  {
    label: 'Require approval for large transactions',
    text: 'Require approval for transactions over $10000 when calling process_transaction tool',
  },
  {
    label: 'Suppress handoff after hours',
    text: 'Suppress the human_handoff tool outside business hours',
  },
  {
    label: 'Warn on competitor mentions',
    text: 'Warn on "competitor" mentions in whatsapp',
  },
]

type DryRunChannel = RuleChannel | 'any'

type DryRunFormState = {
  channel: DryRunChannel
  text: string
  amount: string
  toolRef: string
  hour: string
}

const INITIAL_DRY_RUN: DryRunFormState = {
  channel: 'any',
  text: '',
  amount: '',
  toolRef: '',
  hour: '',
}

function buildEvaluationContext(
  proposal: ComposePolicyProposal,
  form: DryRunFormState,
): RuleEvaluationContext {
  const stage = proposal.rule_body?.stage ?? 'turn_ingress'
  const tool_ref = form.toolRef.trim() || proposal.binding_scope.tool_refs[0] || null
  const amount = form.amount.trim() === '' ? undefined : Number(form.amount)
  const hour = form.hour.trim() === '' ? undefined : Number(form.hour)
  return {
    stage,
    conversation: {
      organization_id: null,
      conversation_id: null,
      agent_id: null,
      step_id: null,
      channel: form.channel === 'any' ? null : form.channel,
      turn_count: 0,
    },
    turn: {
      event_type: null,
      text: form.text || null,
      text_length: form.text ? form.text.length : null,
      metadata: {},
    },
    tool: {
      ref: tool_ref,
      args: amount !== undefined && !Number.isNaN(amount) ? { amount } : {},
      outcome: null,
    },
    facts: {},
    metadata: {},
    time: {
      current_hour: hour !== undefined && !Number.isNaN(hour) ? hour : null,
      current_day: null,
    },
  }
}

function outcomeBadgeVariant(outcome: ComposePolicyProposal['outcome']) {
  switch (outcome) {
    case 'ready':
      return 'default'
    case 'needs_clarification':
      return 'secondary'
    case 'unsupported':
      return 'destructive'
  }
}

function effectKindBadgeVariant(kind: string | undefined) {
  switch (kind) {
    case 'block':
    case 'suppress_tool':
      return 'destructive'
    case 'warn':
      return 'secondary'
    case 'require_confirmation':
      return 'default'
    case 'trace':
      return 'outline'
    default:
      return 'outline'
  }
}

export default function RulesComposePage() {
  const [text, setText] = useState('')
  const [proposal, setProposal] = useState<ComposePolicyProposal | null>(null)
  const [decision, setDecision] = useState<RuleDecision | null>(null)
  const [dryRunForm, setDryRunForm] = useState<DryRunFormState>(INITIAL_DRY_RUN)
  const [ruleId, setRuleId] = useState('')
  const [organizationScope, setOrganizationScope] = useState<'organization' | 'system'>('organization')
  const [savedDraft, setSavedDraft] = useState<RuleDefinitionRevisionDocument | null>(null)

  const compileMutation = useMutation({
    mutationFn: (input: string) => runtimeRulesService.composeCompile({ text: input }),
    onSuccess: (data) => {
      setProposal(data)
      setDecision(null)
      setSavedDraft(null)
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Compile failed')
    },
  })

  useEffect(() => {
    const hint = proposal?.rule_body?.metadata?.compose_rule_id_hint as string | undefined
    if (hint) setRuleId(hint)
  }, [proposal])

  const evaluateMutation = useMutation({
    mutationFn: (request: RuleEvaluationRequest) => runtimeRulesService.evaluateProgram(request),
    onSuccess: (data) => setDecision(data),
    onError: (error: Error) => {
      toast.error(error.message || 'Dry run failed')
    },
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!proposal?.rule_body) throw new Error('No proposal to save')
      const trimmed = ruleId.trim()
      if (!trimmed) throw new Error('Rule id is required')
      return runtimeRulesService.composeSaveDraft({
        rule_id: trimmed,
        organization_scope: organizationScope,
        rule_body: proposal.rule_body,
        suggested_binding_scope: proposal.binding_scope,
      })
    },
    onSuccess: (draft) => {
      setSavedDraft(draft)
      toast.success(`Draft ${draft.rule_id}@${draft.revision} saved`)
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Save failed')
    },
  })

  const dryRunDisabled = useMemo(
    () => proposal?.rule_body == null || compileMutation.isPending || evaluateMutation.isPending,
    [proposal, compileMutation.isPending, evaluateMutation.isPending],
  )

  const saveDisabled = useMemo(
    () =>
      proposal?.outcome !== 'ready' ||
      proposal.rule_body == null ||
      ruleId.trim().length === 0 ||
      saveMutation.isPending,
    [proposal, ruleId, saveMutation.isPending],
  )

  const handleCompile = () => {
    const trimmed = text.trim()
    if (!trimmed) {
      toast.error('Enter a policy description first')
      return
    }
    compileMutation.mutate(trimmed)
  }

  const handleDryRun = () => {
    if (!proposal?.rule_body) return
    const rule = {
      ...proposal.rule_body,
      rule_id: proposal.rule_body.metadata?.compose_rule_id_hint as string | undefined ?? 'rule.compose.preview',
      revision: 1,
    }
    const request: RuleEvaluationRequest = {
      program: {
        library: {
          library_id: 'lib.compose.preview',
          version: 'preview',
          rules: [
            {
              rule_id: rule.rule_id ?? 'rule.compose.preview',
              revision: 1,
              name: rule.name,
              summary: rule.summary,
              stage: rule.stage,
              predicate: rule.predicate,
              effect: rule.effect,
              tags: rule.tags,
              metadata: rule.metadata,
            },
          ],
        },
        bindings: [
          {
            binding_id: 'bind.compose.preview',
            rule_id: rule.rule_id ?? 'rule.compose.preview',
            revision: 1,
            mode: 'enforce',
            order: 100,
            scope: {
              channels: proposal.binding_scope.channels,
              agent_ids: proposal.binding_scope.agent_ids,
              step_ids: proposal.binding_scope.step_ids,
              tool_refs: proposal.binding_scope.tool_refs,
              event_types: proposal.binding_scope.event_types,
            },
            metadata: {},
          },
        ],
      },
      context: buildEvaluationContext(proposal, dryRunForm),
    }
    evaluateMutation.mutate(request)
  }

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <Card>
          <CardHeader className="space-y-2">
            <div className="flex items-center gap-2">
              <Wand2 className="h-5 w-5 text-primary" />
              <CardTitle>Compose a policy from natural language</CardTitle>
            </div>
            <CardDescription>
              Describe a policy in plain language. The compiler turns it into the existing rules DSL,
              flags ambiguities, and lets you dry-run the result before saving. Internal preview surface
              for the natural-language policy authoring track.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="compose-text">Policy description</Label>
              <Textarea
                id="compose-text"
                rows={4}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder='e.g. "Require approval for transactions over $10000 when calling process_transaction tool"'
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={handleCompile} disabled={compileMutation.isPending}>
                {compileMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="mr-2 h-4 w-4" />
                )}
                Compile proposal
              </Button>
              {SAMPLE_PROMPTS.map((sample) => (
                <Button
                  key={sample.label}
                  variant="outline"
                  size="sm"
                  type="button"
                  onClick={() => setText(sample.text)}
                >
                  {sample.label}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>

        {proposal && <ProposalCard proposal={proposal} />}

        {proposal?.rule_body && (
          <DryRunCard
            proposal={proposal}
            form={dryRunForm}
            onFormChange={setDryRunForm}
            onRun={handleDryRun}
            disabled={dryRunDisabled}
            isPending={evaluateMutation.isPending}
            decision={decision}
          />
        )}

        {proposal?.rule_body && (
          <SaveDraftCard
            proposal={proposal}
            ruleId={ruleId}
            onRuleIdChange={setRuleId}
            organizationScope={organizationScope}
            onScopeChange={setOrganizationScope}
            onSave={() => saveMutation.mutate()}
            disabled={saveDisabled}
            isPending={saveMutation.isPending}
            savedDraft={savedDraft}
          />
        )}
      </div>
    </DashboardLayout>
  )
}

function ProposalCard({ proposal }: { proposal: ComposePolicyProposal }) {
  const { rule_body, expression, binding_scope, ambiguities, outcome } = proposal
  const effectKind = rule_body?.effect.kind
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="space-y-1">
            <CardTitle className="text-lg">Proposal</CardTitle>
            <CardDescription>{proposal.summary}</CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={outcomeBadgeVariant(outcome)}>{outcome.replace('_', ' ')}</Badge>
            {effectKind && (
              <Badge variant={effectKindBadgeVariant(effectKind)}>effect: {effectKind}</Badge>
            )}
            {rule_body?.stage && <Badge variant="outline">stage: {rule_body.stage}</Badge>}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="space-y-2">
            <Label className="text-xs uppercase tracking-wide text-muted-foreground">
              Generated DSL
            </Label>
            <pre className="overflow-x-auto rounded-md border bg-muted/50 p-3 text-xs">
              {expression || '—'}
            </pre>
            {rule_body && (
              <pre className="overflow-x-auto rounded-md border bg-muted/30 p-3 text-[11px] leading-relaxed text-muted-foreground">
                {JSON.stringify(rule_body.predicate, null, 2)}
              </pre>
            )}
          </div>
          <div className="space-y-3">
            <ScopeSummary proposal={proposal} />
            <ExampleScenarios proposal={proposal} />
            {ambiguities.length > 0 && <AmbiguityList ambiguities={ambiguities} />}
            {rule_body && (
              <div className="space-y-1">
                <Label className="text-xs uppercase tracking-wide text-muted-foreground">
                  Suggested name
                </Label>
                <p className="text-sm">{rule_body.name}</p>
              </div>
            )}
            {binding_scope.step_ids.length > 0 && (
              <p className="text-xs text-muted-foreground">
                Step-native scope is saved directly on the rule binding as <code>step_ids</code>.
              </p>
            )}
            {binding_scope.scenario_ids.length > 0 && (
              <p className="text-xs text-amber-700">
                Scenario scope is advisory for now. It is saved in rule metadata, but runtime enforcement has not landed yet.
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function ScopeSummary({ proposal }: { proposal: ComposePolicyProposal }) {
  const { binding_scope, rule_body } = proposal
  const chunks: Array<{ label: string; value: string }> = []
  if (binding_scope.channels.length) chunks.push({ label: 'channels', value: binding_scope.channels.join(', ') })
  if (binding_scope.tool_refs.length) chunks.push({ label: 'tools', value: binding_scope.tool_refs.join(', ') })
  if (binding_scope.step_ids.length) chunks.push({ label: 'steps', value: binding_scope.step_ids.join(', ') })
  if (binding_scope.agent_ids.length) chunks.push({ label: 'agents', value: binding_scope.agent_ids.join(', ') })
  if (binding_scope.scenario_ids.length) chunks.push({ label: 'scenarios (advisory)', value: binding_scope.scenario_ids.join(', ') })
  return (
    <div className="space-y-2">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        Enforcement scope
      </Label>
      {chunks.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Unscoped. The rule will apply globally unless you narrow it before publishing.
        </p>
      ) : (
        <ul className="space-y-1 text-sm">
          {chunks.map((chunk) => (
            <li key={chunk.label}>
              <span className="font-medium capitalize text-muted-foreground">{chunk.label}:</span>{' '}
              {chunk.value}
            </li>
          ))}
        </ul>
      )}
      {rule_body?.tags?.length ? (
        <div className="flex flex-wrap gap-1">
          {rule_body.tags.map((tag) => (
            <Badge key={tag} variant="outline">
              {tag}
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function ExampleScenarios({ proposal }: { proposal: ComposePolicyProposal }) {
  if (!proposal.example_match && !proposal.example_no_match) return null
  return (
    <div className="space-y-1">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        Example scenarios
      </Label>
      {proposal.example_match && (
        <p className="text-sm">
          <span className="font-medium text-emerald-600">Matches:</span> {proposal.example_match}
        </p>
      )}
      {proposal.example_no_match && (
        <p className="text-sm">
          <span className="font-medium text-amber-600">Skips:</span> {proposal.example_no_match}
        </p>
      )}
    </div>
  )
}

function AmbiguityList({ ambiguities }: { ambiguities: ComposePolicyProposal['ambiguities'] }) {
  return (
    <div className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-700 dark:bg-amber-950/40">
      <div className="flex items-center gap-2 text-sm font-medium text-amber-700 dark:text-amber-300">
        <AlertTriangle className="h-4 w-4" />
        Needs clarification
      </div>
      <ul className="space-y-1 text-sm">
        {ambiguities.map((item) => (
          <li key={item.code}>
            <p className="font-medium">{item.message}</p>
            {item.hint && <p className="text-xs text-muted-foreground">{item.hint}</p>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function DryRunCard({
  proposal,
  form,
  onFormChange,
  onRun,
  disabled,
  isPending,
  decision,
}: {
  proposal: ComposePolicyProposal
  form: DryRunFormState
  onFormChange: (form: DryRunFormState) => void
  onRun: () => void
  disabled: boolean
  isPending: boolean
  decision: RuleDecision | null
}) {
  const stage = proposal.rule_body?.stage
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <PlayCircle className="h-5 w-5 text-primary" />
          <CardTitle className="text-lg">Dry run</CardTitle>
        </div>
        <CardDescription>
          Build a synthetic context and evaluate the generated rule. Uses the existing
          <code className="mx-1">/api/rules/evaluate</code> endpoint.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="dry-text">Turn text</Label>
            <Textarea
              id="dry-text"
              rows={3}
              value={form.text}
              onChange={(event) => onFormChange({ ...form, text: event.target.value })}
              placeholder="What did the user say?"
            />
          </div>
          <div className="space-y-3">
            <div className="space-y-2">
              <Label htmlFor="dry-channel">Channel</Label>
              <Select
                value={form.channel}
                onValueChange={(value) => onFormChange({ ...form, channel: value as DryRunChannel })}
              >
                <SelectTrigger id="dry-channel">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="any">Any</SelectItem>
                  <SelectItem value="phone">Phone</SelectItem>
                  <SelectItem value="whatsapp">WhatsApp</SelectItem>
                  <SelectItem value="web_chat">Web chat</SelectItem>
                  <SelectItem value="web_widget">Web widget</SelectItem>
                  <SelectItem value="browser">Browser</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {stage === 'before_tool' && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="dry-tool-ref">Tool ref</Label>
                  <input
                    id="dry-tool-ref"
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    value={form.toolRef}
                    onChange={(event) => onFormChange({ ...form, toolRef: event.target.value })}
                    placeholder={proposal.binding_scope.tool_refs[0] || 'process_transaction'}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="dry-amount">Tool args.amount</Label>
                  <input
                    id="dry-amount"
                    type="number"
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    value={form.amount}
                    onChange={(event) => onFormChange({ ...form, amount: event.target.value })}
                  />
                </div>
              </>
            )}
            <div className="space-y-2">
              <Label htmlFor="dry-hour">Current hour (0–23)</Label>
              <input
                id="dry-hour"
                type="number"
                min={0}
                max={23}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={form.hour}
                onChange={(event) => onFormChange({ ...form, hour: event.target.value })}
                placeholder="leave blank for current time"
              />
            </div>
          </div>
        </div>
        <Button onClick={onRun} disabled={disabled}>
          {isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlayCircle className="mr-2 h-4 w-4" />}
          Evaluate
        </Button>
        {decision && <DecisionPreview decision={decision} />}
      </CardContent>
    </Card>
  )
}

function SaveDraftCard({
  proposal,
  ruleId,
  onRuleIdChange,
  organizationScope,
  onScopeChange,
  onSave,
  disabled,
  isPending,
  savedDraft,
}: {
  proposal: ComposePolicyProposal
  ruleId: string
  onRuleIdChange: (value: string) => void
  organizationScope: 'organization' | 'system'
  onScopeChange: (value: 'organization' | 'system') => void
  onSave: () => void
  disabled: boolean
  isPending: boolean
  savedDraft: RuleDefinitionRevisionDocument | null
}) {
  const blocked = proposal.outcome !== 'ready'
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Save className="h-5 w-5 text-primary" />
          <CardTitle className="text-lg">Save as draft</CardTitle>
        </div>
        <CardDescription>
          Persists the generated rule as a draft revision. Publishing and binding remain a separate
          reviewed action on the existing rules screen — Doc 04 §3 keeps that gate explicit.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {blocked && (
          <p className="text-sm text-amber-600">
            Resolve the ambiguities above before saving.
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="save-rule-id">Rule id</Label>
            <Input
              id="save-rule-id"
              value={ruleId}
              onChange={(event) => onRuleIdChange(event.target.value)}
              placeholder="rule.compose.<your.identifier>"
            />
            <p className="text-xs text-muted-foreground">
              Pre-filled from the compiler suggestion. Edit before saving if needed.
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="save-org-scope">Organization scope</Label>
            <Select value={organizationScope} onValueChange={(value) => onScopeChange(value as typeof organizationScope)}>
              <SelectTrigger id="save-org-scope">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="organization">Organization</SelectItem>
                <SelectItem value="system">System (superuser only)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <Button onClick={onSave} disabled={disabled}>
          {isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
          Save draft revision
        </Button>
        {savedDraft && (
          <div className="space-y-2 rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm dark:border-emerald-700 dark:bg-emerald-950/40">
            <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300">
              <CheckCircle2 className="h-4 w-4" />
              <span className="font-medium">
                Draft saved: {savedDraft.rule_id}@{savedDraft.revision}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              Status: {savedDraft.status}. Publish + create the binding from the
              <Link to="/rules" className="mx-1 underline">rules screen</Link>
              when ready.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function DecisionPreview({ decision }: { decision: RuleDecision }) {
  const matched = decision.matched_rules.length > 0
  return (
    <div className="space-y-2 rounded-md border bg-muted/30 p-3 text-sm">
      <div className="flex items-center gap-2">
        {matched ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
        ) : (
          <AlertTriangle className="h-4 w-4 text-muted-foreground" />
        )}
        <span className="font-medium">
          {matched ? 'Rule matched' : 'No match'}
        </span>
        {decision.terminal_effect && (
          <Badge variant={effectKindBadgeVariant(decision.terminal_effect.kind)}>
            terminal: {decision.terminal_effect.kind}
          </Badge>
        )}
      </div>
      {decision.terminal_effect?.message && (
        <p className="text-xs text-muted-foreground">{decision.terminal_effect.message}</p>
      )}
      {decision.traces.length > 0 && (
        <pre className="max-h-48 overflow-auto rounded-md bg-background p-2 text-[11px] leading-relaxed">
          {JSON.stringify(decision.traces, null, 2)}
        </pre>
      )}
    </div>
  )
}
