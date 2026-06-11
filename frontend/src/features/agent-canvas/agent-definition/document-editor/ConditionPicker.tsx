import { useMemo, useState } from 'react'
import type { Editor } from '@tiptap/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/atoms/popover'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Textarea } from '@/components/atoms/textarea'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'

// Edge-owned outcomes: each authored transition picks ONE condition
// kind. The form fields differ per kind so authors aren't dragging a
// generic "value" string around — they fill in the field that matches
// the kind's domain (event vs fact name vs tool outcome vs guard id).
type ConditionKindMeta = {
  /** Kind discriminator (matches backend Pydantic union member). */
  value: string
  /** Label shown in the kind <Select>. */
  label: string
  /** Field config for the primary identifier. ``null`` for the two
   *  no-payload kinds (otherwise, all_required_facts_present). */
  primaryField: {
    key: 'event' | 'fact_name' | 'outcome' | 'guard_id'
    label: string
    placeholder: string
  } | null
  /** When set, the picker also renders a description textarea — used
   *  only by ``OutcomeCondition`` so authors can write the LLM-evaluated
   *  meaning of the edge. */
  showDescription?: boolean
}

const CONDITION_KINDS: ConditionKindMeta[] = [
  {
    value: 'outcome',
    label: 'Outcome (LLM-evaluated)',
    primaryField: {
      key: 'event',
      label: 'Event token',
      placeholder: 'product_question',
    },
    showDescription: true,
  },
  {
    value: 'tool_outcome',
    label: 'Tool outcome',
    primaryField: {
      key: 'outcome',
      label: 'Outcome code',
      placeholder: 'action_code_success',
    },
  },
  {
    value: 'fact_present',
    label: 'Fact present',
    primaryField: {
      key: 'fact_name',
      label: 'Fact name',
      placeholder: 'email',
    },
  },
  {
    value: 'fact_missing',
    label: 'Fact missing',
    primaryField: {
      key: 'fact_name',
      label: 'Fact name',
      placeholder: 'email',
    },
  },
  {
    value: 'all_required_facts_present',
    label: 'All required facts present',
    primaryField: null,
  },
  {
    value: 'guard_failure',
    label: 'Guard failure',
    primaryField: {
      key: 'guard_id',
      label: 'Guard id',
      placeholder: 'channel_allowed',
    },
  },
  {
    value: 'otherwise',
    label: 'Otherwise (fallback)',
    primaryField: null,
  },
]

interface StepOption {
  id: string
  name: string
  scenarioId: string
  scenarioName: string
}

interface ConditionPickerProps {
  editor: Editor
  getPos: () => number | undefined
  whenKind: string
  whenValue: string
  whenDescription: string
  toStepId: string
  toStepName: string
}

export function ConditionPicker({
  editor,
  getPos,
  whenKind,
  whenValue,
  whenDescription,
  toStepId,
  toStepName,
}: ConditionPickerProps) {
  const [open, setOpen] = useState(false)
  const kindMeta = CONDITION_KINDS.find((c) => c.value === whenKind) ?? CONDITION_KINDS[0]

  // Walk the editor doc to enumerate step retarget options grouped by
  // scenario. Cross-scenario targets emit a `scenario_routes` entry on
  // save (handled in the deserializer).
  const stepOptions = useMemo<StepOption[]>(() => {
    const out: StepOption[] = []
    editor.state.doc.descendants((node) => {
      if (node.type.name !== 'scenario') return true
      const scenarioId = String(node.attrs.scenarioId ?? '')
      let scenarioName = ''
      node.content.forEach((sub) => {
        if (sub.type.name === 'scenarioName') scenarioName = sub.textContent
      })
      node.content.forEach((stepNode) => {
        if (stepNode.type.name !== 'step') return
        const stepId = String(stepNode.attrs.stepId ?? '')
        if (!stepId) return
        let stepName = ''
        stepNode.content.forEach((sub) => {
          if (sub.type.name === 'stepName') stepName = sub.textContent
        })
        out.push({
          id: stepId,
          name: stepName || stepId,
          scenarioId,
          scenarioName: scenarioName || scenarioId,
        })
      })
      return false
    })
    return out
  }, [editor])

  const groupedStepOptions = useMemo(() => {
    const groups = new Map<string, { scenarioId: string; scenarioName: string; steps: StepOption[] }>()
    for (const step of stepOptions) {
      const existing = groups.get(step.scenarioId)
      if (existing) {
        existing.steps.push(step)
      } else {
        groups.set(step.scenarioId, {
          scenarioId: step.scenarioId,
          scenarioName: step.scenarioName,
          steps: [step],
        })
      }
    }
    return Array.from(groups.values())
  }, [stepOptions])

  const update = (patch: Partial<{
    whenKind: string
    whenValue: string
    whenDescription: string
    toStepId: string
    toStepName: string
  }>) => {
    const pos = getPos()
    if (pos == null) return
    const tr = editor.state.tr
    if (patch.whenKind !== undefined) tr.setNodeAttribute(pos, 'whenKind', patch.whenKind)
    if (patch.whenValue !== undefined) tr.setNodeAttribute(pos, 'whenValue', patch.whenValue)
    if (patch.whenDescription !== undefined) tr.setNodeAttribute(pos, 'whenDescription', patch.whenDescription)
    if (patch.toStepId !== undefined) tr.setNodeAttribute(pos, 'toStepId', patch.toStepId)
    if (patch.toStepName !== undefined) tr.setNodeAttribute(pos, 'toStepName', patch.toStepName)
    editor.view.dispatch(tr)
  }

  const handleKindChange = (next: string) => {
    const meta = CONDITION_KINDS.find((c) => c.value === next)
    update({
      whenKind: next,
      // Drop kind-specific fields when switching to a kind that doesn't use them.
      whenValue: meta?.primaryField ? whenValue : '',
      whenDescription: meta?.showDescription ? whenDescription : '',
    })
  }

  const handleTargetChange = (next: string) => {
    const target = stepOptions.find((s) => s.id === next)
    update({
      toStepId: next,
      toStepName: target?.name ?? next,
    })
  }

  const handleJumpToTarget = (event: React.MouseEvent) => {
    event.stopPropagation()
    if (!toStepId) return
    let foundPos: number | null = null
    editor.state.doc.descendants((node, pos) => {
      if (foundPos != null) return false
      if (node.type.name === 'step' && node.attrs?.stepId === toStepId) {
        foundPos = pos
        return false
      }
      return false
    })
    if (foundPos == null) return
    const dom = editor.view.nodeDOM(foundPos as number)
    if (dom instanceof HTMLElement) {
      dom.scrollIntoView({ behavior: 'smooth', block: 'center' })
      dom.classList.add('doc-step-jump-highlight')
      window.setTimeout(() => dom.classList.remove('doc-step-jump-highlight'), 1200)
    }
  }

  return (
    <span className="doc-transition-pill">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="doc-transition-condition"
            data-kind={whenKind}
            title="Click to edit condition"
          >
            {renderConditionChip(whenKind, whenValue)}
          </button>
        </PopoverTrigger>
        <PopoverContent
          className="w-[340px] p-3"
          align="start"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="space-y-3">
            <div>
              <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                When
              </Label>
              <Select value={whenKind} onValueChange={handleKindChange}>
                <SelectTrigger className="mt-1 h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CONDITION_KINDS.map((c) => (
                    <SelectItem key={c.value} value={c.value} className="text-xs">
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {kindMeta.primaryField && (
              <div>
                <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  {kindMeta.primaryField.label}
                </Label>
                <Input
                  value={whenValue}
                  onChange={(e) => update({ whenValue: e.target.value })}
                  className="mt-1 h-8 font-mono text-xs"
                  placeholder={kindMeta.primaryField.placeholder}
                />
              </div>
            )}
            {kindMeta.showDescription && (
              <div>
                <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  Description
                </Label>
                <Textarea
                  value={whenDescription}
                  onChange={(e) => update({ whenDescription: e.target.value })}
                  className="mt-1 min-h-[60px] text-xs"
                  placeholder="When the user asks about pricing, plans, or cost details."
                  rows={2}
                />
                <p className="mt-1 text-[10px] text-muted-foreground">
                  The LLM uses this description to decide whether the user's
                  message matches this outcome. Be specific (≥8 chars).
                </p>
              </div>
            )}
            <div>
              <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Go to step
              </Label>
              <Select value={toStepId || ''} onValueChange={handleTargetChange}>
                <SelectTrigger className="mt-1 h-8 text-xs">
                  <SelectValue placeholder="Select step" />
                </SelectTrigger>
                <SelectContent>
                  {groupedStepOptions.map((group) => (
                    <SelectGroup key={group.scenarioId}>
                      {groupedStepOptions.length > 1 && (
                        <SelectLabel className="text-[10px] uppercase tracking-wide text-muted-foreground/70">
                          {group.scenarioName}
                        </SelectLabel>
                      )}
                      {group.steps.map((step) => (
                        <SelectItem key={step.id} value={step.id} className="text-xs">
                          {step.name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </PopoverContent>
      </Popover>
      <button
        type="button"
        className="doc-transition-target"
        onClick={handleJumpToTarget}
        title={toStepId ? `Jump to ${toStepName || toStepId}` : 'No target step set'}
      >
        {' → ⚪ '}
        {toStepName || '(no target)'}
      </button>
    </span>
  )
}

// Renders the inline pill text for each condition kind. Outcomes show
// as `@event`, tool outcomes as
// `tool: <code>`, fact-presence as `@fact set/unset`, etc.
function renderConditionChip(whenKind: string, whenValue: string): string {
  switch (whenKind) {
    case 'outcome':
      return `@${whenValue || '(empty)'}`
    case 'tool_outcome':
      return `tool: ${whenValue || '(empty)'}`
    case 'fact_present':
      return `@${whenValue || '(empty)'} set`
    case 'fact_missing':
      return `@${whenValue || '(empty)'} unset`
    case 'all_required_facts_present':
      return 'facts complete'
    case 'guard_failure':
      return `guard: ${whenValue || '(empty)'}`
    case 'otherwise':
      return 'otherwise'
    default:
      return whenKind || 'otherwise'
  }
}
