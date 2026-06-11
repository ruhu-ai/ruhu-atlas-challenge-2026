import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  NodeViewContent,
  NodeViewWrapper,
  type NodeViewProps,
} from '@tiptap/react'
import { Code, Loader2, Plug, Plus, Star, Trash2, Workflow, X } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { toolService } from '@/api/services/tools.service'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/atoms/popover'
import {
  addDirectAnswerInStep,
  addEventHintToStep,
  addSayInStep,
  addStepInScenario,
  addTransitionInStep,
  deleteNodeAt,
  findScenarioPos,
  setScenarioAsStart,
  setStepAsStartInScenario,
} from './commands'
import { ConditionPicker } from './ConditionPicker'

// ─── Scenario ───────────────────────────────────────────────────────────

export function ScenarioNodeView({ node, editor, getPos }: NodeViewProps) {
  const handleAddStep = () => {
    const pos = getPos()
    if (pos == null) return
    addStepInScenario(editor, pos)
  }
  const handleSetStart = () => {
    const id = String(node.attrs.scenarioId ?? '')
    if (!id) return
    setScenarioAsStart(editor, id)
  }
  const handleDelete = () => {
    const pos = getPos()
    if (pos == null) return
    if (!window.confirm('Delete this scenario and all its steps?')) return
    deleteNodeAt(editor, pos)
  }

  return (
    <NodeViewWrapper
      data-scenario=""
      data-is-start={node.attrs.isStart ? 'true' : 'false'}
      className="doc-node-with-toolbar"
    >
      <div className="doc-toolbar" contentEditable={false}>
        <button
          type="button"
          onClick={handleAddStep}
          className="doc-toolbar-btn"
          title="Add step"
        >
          <Plus className="h-3 w-3" /> step
        </button>
        <button
          type="button"
          onClick={handleSetStart}
          disabled={Boolean(node.attrs.isStart)}
          className="doc-toolbar-btn"
          title={node.attrs.isStart ? 'Already the start scenario' : 'Set as start scenario'}
        >
          <Star className="h-3 w-3" />
        </button>
        <button
          type="button"
          onClick={handleDelete}
          className="doc-toolbar-btn doc-toolbar-btn-danger"
          title="Delete scenario"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      <NodeViewContent />
    </NodeViewWrapper>
  )
}

// ─── Step ───────────────────────────────────────────────────────────────

export function StepNodeView({ node, editor, getPos }: NodeViewProps) {
  // Detect which body blocks are already present so we can hide their
  // "+ add" buttons. Walking node.content is cheap (handful of children).
  let hasSay = false
  let hasDirect = false
  let hasHints = false
  node.content.forEach((child) => {
    if (child.type.name === 'sayBlock') hasSay = true
    if (child.type.name === 'directAnswerBlock') hasDirect = true
    if (child.type.name === 'eventHints') hasHints = true
  })

  const withPos = (fn: (pos: number) => void) => () => {
    const pos = getPos()
    if (pos == null) return
    fn(pos)
  }

  const handleAddTransition = withPos((pos) => addTransitionInStep(editor, pos))
  const handleAddSay = withPos((pos) => addSayInStep(editor, pos))
  const handleAddDirect = withPos((pos) => addDirectAnswerInStep(editor, pos))
  const handleAddHint = withPos((pos) => addEventHintToStep(editor, pos))
  const handleSetStart = () => {
    const pos = getPos()
    if (pos == null) return
    const scenarioPos = findScenarioPos(editor, pos + 1)
    if (scenarioPos == null) return
    const id = String(node.attrs.stepId ?? '')
    if (!id) return
    setStepAsStartInScenario(editor, scenarioPos, id)
  }
  const handleDelete = () => {
    const pos = getPos()
    if (pos == null) return
    if (!window.confirm('Delete this step? Transitions targeting it will dangle.')) return
    deleteNodeAt(editor, pos)
  }

  return (
    <NodeViewWrapper
      data-step=""
      data-is-start={node.attrs.isStart ? 'true' : 'false'}
      className="doc-node-with-toolbar"
    >
      <div className="doc-toolbar" contentEditable={false}>
        <button
          type="button"
          onClick={handleAddTransition}
          className="doc-toolbar-btn"
          title="Add transition"
        >
          <Plus className="h-3 w-3" /> transition
        </button>
        {!hasSay && (
          <button
            type="button"
            onClick={handleAddSay}
            className="doc-toolbar-btn"
            title="Add say block"
          >
            <Plus className="h-3 w-3" /> say
          </button>
        )}
        {!hasDirect && (
          <button
            type="button"
            onClick={handleAddDirect}
            className="doc-toolbar-btn"
            title="Add direct-answer prompt"
          >
            <Plus className="h-3 w-3" /> direct
          </button>
        )}
        {!hasHints && (
          <button
            type="button"
            onClick={handleAddHint}
            className="doc-toolbar-btn"
            title="Add event hint (creates intent classifier hint)"
          >
            <Plus className="h-3 w-3" /> hint
          </button>
        )}
        <button
          type="button"
          onClick={handleSetStart}
          disabled={Boolean(node.attrs.isStart)}
          className="doc-toolbar-btn"
          title={node.attrs.isStart ? 'Already the start step' : 'Set as start step'}
        >
          <Star className="h-3 w-3" />
        </button>
        <button
          type="button"
          onClick={handleDelete}
          className="doc-toolbar-btn doc-toolbar-btn-danger"
          title="Delete step"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      <NodeViewContent />
    </NodeViewWrapper>
  )
}

// ─── Transition ─────────────────────────────────────────────────────────

export function TransitionNodeView({ node, editor, getPos }: NodeViewProps) {
  const handleDelete = () => {
    const pos = getPos()
    if (pos == null) return
    deleteNodeAt(editor, pos)
  }

  const whenKind = String(node.attrs.whenKind ?? 'otherwise')
  const whenValue = String(node.attrs.whenValue ?? '')
  const whenDescription = String(node.attrs.whenDescription ?? '')
  const toStepId = String(node.attrs.toStepId ?? '')
  const toStepName = String(node.attrs.toStepName ?? '')

  return (
    <NodeViewWrapper
      data-transition=""
      data-transition-id={String(node.attrs.transitionId ?? '')}
      data-when-kind={whenKind}
      data-when-value={whenValue}
      data-when-description={whenDescription}
      data-to-step-id={toStepId}
      data-to-step-name={toStepName}
      data-priority={String(node.attrs.priority ?? 100)}
      className="doc-node-with-toolbar doc-transition-row"
    >
      <NodeViewContent />
      <span className="doc-transition-meta" contentEditable={false}>
        <ConditionPicker
          editor={editor}
          getPos={getPos}
          whenKind={whenKind}
          whenValue={whenValue}
          whenDescription={whenDescription}
          toStepId={toStepId}
          toStepName={toStepName}
        />
        <button
          type="button"
          onClick={handleDelete}
          className="doc-toolbar-btn doc-toolbar-btn-danger doc-transition-delete"
          title="Delete transition"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </span>
    </NodeViewWrapper>
  )
}

// ─── Action Summary ─────────────────────────────────────────────────────
//
// Renders the read-only ``action_config`` summary for an action step
// with each callable ref as a clickable chip that deep-links to the
// matching Library entry (``?view=library&callable_ref=<ref>``). The
// LibraryView reads ``callable_ref`` and selects the entry on mount.

function libraryDeepLink(pathname: string, search: string, ref: string): { pathname: string; search: string } {
  const params = new URLSearchParams(search)
  params.set('view', 'library')
  params.set('callable_ref', ref)
  return { pathname, search: `?${params.toString()}` }
}

function ActionRefChip({
  refName,
  icon: Icon,
}: {
  refName: string
  icon: typeof Code
}) {
  const location = useLocation()
  return (
    <Link
      to={libraryDeepLink(location.pathname, location.search, refName)}
      className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-1.5 py-0.5 font-mono text-[11px] text-foreground hover:border-primary/40 hover:bg-muted/40"
      title={`Open ${refName} in Library`}
    >
      <Icon className="h-3 w-3 text-muted-foreground" />
      {refName}
    </Link>
  )
}

export function ActionSummaryNodeView({ node }: NodeViewProps) {
  const toolRefs = (Array.isArray(node.attrs.toolRefs) ? node.attrs.toolRefs : []) as string[]
  const apiRefs = (Array.isArray(node.attrs.apiRefs) ? node.attrs.apiRefs : []) as string[]
  const integrationRefs = (Array.isArray(node.attrs.integrationRefs) ? node.attrs.integrationRefs : []) as string[]
  const hasAny = toolRefs.length + apiRefs.length + integrationRefs.length > 0

  return (
    <NodeViewWrapper
      data-action-summary=""
      className="doc-action-summary"
    >
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5 font-medium text-foreground">
          <Code className="h-3.5 w-3.5" />
          Runs Python code
        </span>
        {hasAny && <span aria-hidden>·</span>}
        {toolRefs.length > 0 && (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">tools</span>
            {toolRefs.map((ref) => (
              <ActionRefChip key={`tool:${ref}`} refName={ref} icon={Code} />
            ))}
          </span>
        )}
        {integrationRefs.length > 0 && (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">integrations</span>
            {integrationRefs.map((ref) => (
              <ActionRefChip key={`int:${ref}`} refName={ref} icon={Plug} />
            ))}
          </span>
        )}
        {apiRefs.length > 0 && (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">apis</span>
            {apiRefs.map((ref) => (
              <ActionRefChip key={`api:${ref}`} refName={ref} icon={Workflow} />
            ))}
          </span>
        )}
      </div>
    </NodeViewWrapper>
  )
}

// ─── Tool Policy block ─────────────────────────────────────────────────
//
// Wraps the chip list of ``step.tool_policy`` bindings. The chips
// themselves are ``ToolBinding`` atoms — the block is the container
// that gives them a header and houses TipTap's NodeViewContent (so
// child atoms can be inserted via slash-menu commands).

export function ToolPolicyNodeView() {
  return (
    <NodeViewWrapper data-tool-policy="" className="doc-tool-policy">
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5 font-medium text-foreground">
          <Workflow className="h-3.5 w-3.5" />
          Tools
        </span>
        <span aria-hidden>·</span>
        <NodeViewContent className="inline-flex flex-wrap items-center gap-1.5" />
      </div>
    </NodeViewWrapper>
  )
}

// ─── Tool Binding chip ─────────────────────────────────────────────────
//
// A single Library callable bound to this step's tool_policy. The chip
// is interactive: ref → Link to Library detail; × → remove the binding
// from the doc (which round-trips back into step.tool_policy on save).

export function ToolBindingNodeView({ node, editor, getPos }: NodeViewProps) {
  const location = useLocation()
  const ref = String(node.attrs.ref ?? '')
  const mode = String(node.attrs.mode ?? 'allowed')

  const handleDelete = () => {
    const pos = getPos()
    if (pos == null) return
    deleteNodeAt(editor, pos)
  }

  const handlePick = (pickedRef: string) => {
    const pos = getPos()
    if (pos == null) return
    editor.chain().focus().command(({ tr }) => {
      tr.setNodeAttribute(pos, 'ref', pickedRef)
      return true
    }).run()
  }

  if (!ref) {
    return (
      <NodeViewWrapper as="span" data-tool-binding="" className="inline-flex">
        <ToolBindingPicker onPick={handlePick} onCancel={handleDelete} />
      </NodeViewWrapper>
    )
  }

  return (
    <NodeViewWrapper as="span" data-tool-binding="" className="inline-flex">
      <span
        className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-1.5 py-0.5 text-[11px]"
        data-mode={mode}
      >
        <Link
          to={libraryDeepLink(location.pathname, location.search, ref)}
          className="inline-flex items-center gap-1 font-mono text-foreground hover:text-primary"
          title={`Open ${ref} in Library`}
        >
          <Workflow className="h-3 w-3 text-muted-foreground" />
          {ref}
        </Link>
        {mode !== 'allowed' && (
          <span className="rounded bg-muted px-1 text-[9px] uppercase tracking-wide text-muted-foreground">
            {mode}
          </span>
        )}
        <button
          type="button"
          onClick={handleDelete}
          className="ml-0.5 text-muted-foreground hover:text-destructive"
          title="Remove binding"
        >
          <Trash2 className="h-2.5 w-2.5" />
        </button>
      </span>
    </NodeViewWrapper>
  )
}

// Picker rendered in place of an empty toolBinding chip. Lists every
// callable definition in the org's Library and lets the author pick one;
// onPick stamps the chip's ``ref`` attr, onCancel removes the chip
// entirely. The popover opens automatically on mount so the slash-menu
// "Bind tool…" command flows into pick-without-extra-click.
function ToolBindingPicker({
  onPick,
  onCancel,
}: {
  onPick: (ref: string) => void
  onCancel: () => void
}) {
  const [open, setOpen] = useState(true)
  const [search, setSearch] = useState('')

  const callablesQuery = useQuery({
    queryKey: ['library-callables'],
    queryFn: () => toolService.listDefinitions({ enabled_only: false }),
    staleTime: 30_000,
    enabled: open,
  })

  const filtered = (callablesQuery.data ?? []).filter((tool) => {
    const q = search.trim().toLowerCase()
    if (!q) return true
    const ref = (tool.tool_ref ?? '').toLowerCase()
    const name = (tool.display_name ?? '').toLowerCase()
    const desc = (tool.description ?? '').toLowerCase()
    return ref.includes(q) || name.includes(q) || desc.includes(q)
  })

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) onCancel()
      }}
    >
      <PopoverTrigger asChild>
        <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-primary/50 bg-primary/5 px-1.5 py-0.5 text-[11px] text-primary cursor-pointer hover:bg-primary/10">
          <Workflow className="h-3 w-3" />
          Pick callable…
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onCancel()
            }}
            className="ml-0.5 text-muted-foreground hover:text-destructive"
            title="Cancel"
          >
            <X className="h-2.5 w-2.5" />
          </button>
        </span>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-2" align="start" onClick={(e) => e.stopPropagation()}>
        <div className="space-y-2">
          <input
            type="text"
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search callables…"
            className="h-8 w-full rounded-md border border-border bg-background px-2 text-xs outline-none focus:border-primary/50"
          />
          {callablesQuery.isLoading ? (
            <div className="flex items-center justify-center gap-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              Loading…
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-2 py-3 text-center text-xs text-muted-foreground">
              {(callablesQuery.data?.length ?? 0) === 0
                ? 'No callables in Library yet.'
                : 'No matches.'}
            </div>
          ) : (
            <ul className="max-h-64 space-y-0.5 overflow-y-auto">
              {filtered.map((tool) => (
                <li key={tool.tool_definition_id}>
                  <button
                    type="button"
                    onClick={() => {
                      const ref = tool.tool_ref ?? tool.function_name ?? ''
                      if (!ref) return
                      onPick(ref)
                      setOpen(false)
                    }}
                    className="flex w-full flex-col items-start gap-0.5 rounded px-2 py-1.5 text-left hover:bg-muted/40"
                  >
                    <span className="text-xs font-medium">
                      {tool.display_name || tool.tool_ref || tool.function_name}
                    </span>
                    <code className="block truncate text-[10px] text-muted-foreground">
                      {tool.tool_ref ?? tool.function_name ?? ''}
                    </code>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
