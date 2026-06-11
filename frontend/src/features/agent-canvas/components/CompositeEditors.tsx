/**
 * Editors for composite-kind callables and output mappings.
 *
 * Extracted from LibraryView.tsx (RP-4.4): the sequential calls editor
 * (CompositeStepsEditor + its per-step args editor) and the
 * OutputMappingEditor used by both the Calls and Output detail tabs.
 */
import { Loader2, Plus, Save, Trash2 } from 'lucide-react'

import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'

import type { CompositeStep } from './library-view-helpers'

// ────────────────────────────────────────────────────────────────────────────
// CompositeStepsEditor — list editor for kind='composite' callables.
// Each row picks a sub-callable ref (typed for now; the search popover from
// ScenarioLanesCanvas could be plugged in later) and maps args using the
// $args.<key> / $prev.<path> conventions the CompositeExecutor parses.
// ────────────────────────────────────────────────────────────────────────────

interface CompositeStepsEditorProps {
  steps: CompositeStep[]
  onChange: (next: CompositeStep[]) => void
  dirty: boolean
  saving: boolean
  onSave: () => void
}

export function CompositeStepsEditor({ steps, onChange, dirty, saving, onSave }: CompositeStepsEditorProps) {
  const updateStep = (index: number, updater: (step: CompositeStep) => CompositeStep) =>
    onChange(steps.map((step, i) => (i === index ? updater(step) : step)))

  const addStep = () => onChange([...steps, { ref: '', args: {} }])
  const removeStep = (index: number) => onChange(steps.filter((_, i) => i !== index))

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted-foreground">
        Steps run sequentially. Args use{' '}
        <code className="rounded bg-muted px-1">$args.&lt;key&gt;</code> for the parent call&apos;s args
        and <code className="rounded bg-muted px-1">$prev.&lt;path&gt;</code> for the previous
        step&apos;s output. The last step&apos;s output is returned.
      </p>
      {steps.length === 0 ? (
        <p className="rounded-md border border-dashed bg-muted/20 p-3 text-[11px] text-muted-foreground">
          No steps yet. Add one to start composing.
        </p>
      ) : (
        <ul className="space-y-2">
          {steps.map((step, index) => (
            <li key={index} className="space-y-1.5 rounded-md border bg-muted/10 p-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[10px] text-muted-foreground">{index + 1}.</span>
                <Input
                  value={step.ref}
                  onChange={(event) => updateStep(index, (s) => ({ ...s, ref: event.target.value }))}
                  placeholder="namespace.callable_ref"
                  className="h-7 flex-1 font-mono text-xs"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={() => removeStep(index)}
                  aria-label={`Remove step ${index + 1}`}
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
              <CompositeArgsEditor
                args={step.args}
                onChange={(nextArgs) => updateStep(index, (s) => ({ ...s, args: nextArgs }))}
              />
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={addStep}>
          <Plus className="mr-1 h-3 w-3" />
          Step
        </Button>
        <Button
          size="sm"
          onClick={onSave}
          disabled={!dirty || saving}
          className="ml-auto h-7 text-xs"
        >
          {saving ? <Loader2 className="mr-2 h-3 w-3 animate-spin" /> : <Save className="mr-2 h-3 w-3" />}
          Save composition
        </Button>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// OutputMappingEditor — declares how a tool's successful result writes back
// into conversation facts. Stored on ToolDefinition.metadata.output_mapping
// and applied by the kernel in _execute_step_tool_policy after a successful
// invoke. Expressions support two forms:
//   - "$.path.to.value"  — dotted traversal into result.output
//   - "top_level_key"    — direct key read on result.output
// ────────────────────────────────────────────────────────────────────────────

interface OutputMappingEditorProps {
  mapping: Record<string, string>
  onChange: (next: Record<string, string>) => void
  dirty: boolean
  saving: boolean
  onSave: () => void
}

export function OutputMappingEditor({ mapping, onChange, dirty, saving, onSave }: OutputMappingEditorProps) {
  const entries = Object.entries(mapping)

  const renameKey = (oldKey: string, newKey: string) => {
    if (!newKey || newKey === oldKey || newKey in mapping) return
    const next: Record<string, string> = {}
    for (const [k, v] of Object.entries(mapping)) next[k === oldKey ? newKey : k] = v
    onChange(next)
  }

  const setValue = (key: string, value: string) => onChange({ ...mapping, [key]: value })
  const removeKey = (key: string) => {
    const next = { ...mapping }
    delete next[key]
    onChange(next)
  }
  const addRow = () => {
    let candidate = 'fact_name'
    let i = 1
    while (candidate in mapping) {
      i += 1
      candidate = `fact_name_${i}`
    }
    onChange({ ...mapping, [candidate]: '' })
  }

  return (
    <div className="space-y-2 border-t border-border/60 pt-3">
      <div className="flex items-center justify-between">
        <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
          Output mapping — facts this callable writes
        </Label>
        <Button variant="ghost" size="sm" className="h-6 text-[10px]" onClick={addRow}>
          <Plus className="mr-1 h-2.5 w-2.5" />
          row
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground">
        Each row maps a fact to an extraction expression. Use{' '}
        <code className="rounded bg-muted px-1">$.path.into.result</code> for nested values,
        or a top-level key. Applied after a successful tool call.
      </p>
      {entries.length === 0 ? (
        <p className="rounded-md border border-dashed bg-muted/20 p-2 text-[11px] text-muted-foreground">
          No output mappings yet. Without a mapping, this callable will not write any facts.
        </p>
      ) : (
        <ul className="space-y-1">
          {entries.map(([factName, expr]) => (
            <li key={factName} className="flex items-center gap-1 text-[11px]">
              <Input
                value={factName}
                onChange={(event) => renameKey(factName, event.target.value)}
                placeholder="fact_name"
                className="h-6 w-28 font-mono text-[11px]"
              />
              <span className="text-muted-foreground">←</span>
              <Input
                value={expr}
                onChange={(event) => setValue(factName, event.target.value)}
                placeholder="$.data.user.name"
                className="h-6 flex-1 font-mono text-[11px]"
              />
              <Button
                variant="ghost"
                size="icon"
                className="h-5 w-5"
                onClick={() => removeKey(factName)}
                aria-label={`Remove ${factName}`}
              >
                <Trash2 className="h-2.5 w-2.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Button
        size="sm"
        onClick={onSave}
        disabled={!dirty || saving}
        className="w-full"
      >
        {saving ? <Loader2 className="mr-2 h-3 w-3 animate-spin" /> : <Save className="mr-2 h-3 w-3" />}
        Save mapping
      </Button>
    </div>
  )
}

interface CompositeArgsEditorProps {
  args: Record<string, string>
  onChange: (next: Record<string, string>) => void
}

function CompositeArgsEditor({ args, onChange }: CompositeArgsEditorProps) {
  const entries = Object.entries(args)

  const renameKey = (oldKey: string, newKey: string) => {
    if (!newKey || newKey === oldKey || newKey in args) return
    const next: Record<string, string> = {}
    for (const [k, v] of Object.entries(args)) next[k === oldKey ? newKey : k] = v
    onChange(next)
  }

  const setValue = (key: string, value: string) => onChange({ ...args, [key]: value })
  const removeKey = (key: string) => {
    const next = { ...args }
    delete next[key]
    onChange(next)
  }

  const addArg = () => {
    let candidate = 'arg'
    let i = 1
    while (candidate in args) {
      i += 1
      candidate = `arg${i}`
    }
    onChange({ ...args, [candidate]: '' })
  }

  return (
    <div className="space-y-1 pl-4">
      {entries.length === 0 ? (
        <p className="text-[10px] text-muted-foreground">No args mapped.</p>
      ) : (
        entries.map(([key, value]) => (
          <div key={key} className="flex items-center gap-1 text-[11px]">
            <Input
              value={key}
              onChange={(event) => renameKey(key, event.target.value)}
              placeholder="input_key"
              className="h-6 w-28 font-mono text-[11px]"
            />
            <span className="text-muted-foreground">←</span>
            <Input
              value={value}
              onChange={(event) => setValue(key, event.target.value)}
              placeholder="$args.foo / $prev.path / literal"
              className="h-6 flex-1 font-mono text-[11px]"
            />
            <Button
              variant="ghost"
              size="icon"
              className="h-5 w-5"
              onClick={() => removeKey(key)}
              aria-label={`Remove ${key}`}
            >
              <Trash2 className="h-2.5 w-2.5" />
            </Button>
          </div>
        ))
      )}
      <Button variant="ghost" size="sm" className="h-6 text-[10px]" onClick={addArg}>
        <Plus className="mr-1 h-2.5 w-2.5" />
        arg
      </Button>
    </div>
  )
}
