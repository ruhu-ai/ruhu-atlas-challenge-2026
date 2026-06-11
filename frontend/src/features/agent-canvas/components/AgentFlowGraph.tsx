// Canonical Graph view. Read-only projection of AgentDocument.
//
// Mounted by CleanAgentCanvasLayout via <AgentDocumentProvider>; rendered
// by AgentDefinitionWorkspace under surface === 'graph'. Click on a step
// navigates to the Document surface — this view never authors.
//
// Do NOT replace this surface without updating
// canvas-graph-surface.test.tsx; that test pins the contract.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Handle,
  Position,
  applyNodeChanges,
  type Node,
  type NodeChange,
  type Edge,
  type NodeProps,
  type EdgeProps,
  EdgeLabelRenderer,
  BaseEdge,
  getSmoothStepPath,
  useReactFlow,
} from 'reactflow'
import dagre from 'dagre'
import 'reactflow/dist/style.css'

import {
  Flag,
  MessageCircle,
  ClipboardList,
  Plug,
  ArrowRightFromLine,
  CheckCircle2,
  Workflow,
  RotateCcw,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/atoms/button'
import { useAgentDocument } from '@/features/agent-canvas/contexts/AgentDocumentContext'
import type {
  AgentScenario,
  AgentStep,
  AgentStepTransition,
} from '@/types/agent-document'

// ────────────────────────────────────────────────────────────────────────────
// Step mode → visual identity mapping. Mirrors getStepAccentClass in
// ScenarioLanesCanvas so the flow view feels continuous with the document.
// ────────────────────────────────────────────────────────────────────────────

type StepMode =
  | 'entry'
  | 'conversational'
  | 'fact_collection'
  | 'tool_execution'
  | 'handoff'
  | 'completion'

interface ModeStyle {
  label: string
  Icon: typeof Flag
  accent: string
  iconText: string
}

// Sierra-grade palette restraint: every node card gets the same neutral
// border. Differentiation comes from the small icon-chip in the header —
// and even there we collapse to THREE color families, not six:
//   - blue  — entry (the agent's start point)
//   - emerald — terminal modes (completion, handoff)
//   - slate — middle modes (conversational, fact_collection,
//     tool_execution) distinguished by ICON + LABEL, not color.
//
// Side effect: the canvas reads as one cohesive surface rather than a
// fruit salad of pastels. Icons still tell users which mode each step is.
const MODE_STYLE: Record<StepMode, ModeStyle> = {
  entry: {
    label: 'Entry',
    Icon: Flag,
    accent: 'bg-blue-500/10',
    iconText: 'text-blue-600',
  },
  conversational: {
    label: 'Conversational',
    Icon: MessageCircle,
    accent: 'bg-slate-500/10',
    iconText: 'text-slate-500',
  },
  fact_collection: {
    label: 'Fact collection',
    Icon: ClipboardList,
    accent: 'bg-slate-500/10',
    iconText: 'text-slate-500',
  },
  tool_execution: {
    label: 'Tool execution',
    Icon: Plug,
    accent: 'bg-slate-500/10',
    iconText: 'text-slate-500',
  },
  handoff: {
    label: 'Handoff',
    Icon: ArrowRightFromLine,
    accent: 'bg-emerald-500/10',
    iconText: 'text-emerald-600',
  },
  completion: {
    label: 'Completion',
    Icon: CheckCircle2,
    accent: 'bg-emerald-500/10',
    iconText: 'text-emerald-600',
  },
}

function deriveStepMode(step: AgentStep, isStartStep: boolean): StepMode {
  if (step.completion) return 'completion'
  if (step.handoff) return 'handoff'
  if (step.action_config != null || (step.tool_policy?.length ?? 0) > 0) return 'tool_execution'
  if ((step.fact_requirements?.length ?? 0) > 0) return 'fact_collection'
  if (isStartStep) return 'entry'
  return 'conversational'
}

// ────────────────────────────────────────────────────────────────────────────
// Custom node — ElevenLabs-style card with icon header + title + description.
// ────────────────────────────────────────────────────────────────────────────

interface StepNodeData {
  step: AgentStep
  mode: StepMode
  scenarioName: string
  isStartStep: boolean
  onSelect: (scenarioId: string, stepId: string) => void
  scenarioId: string
}

function StepNode({ data }: NodeProps<StepNodeData>) {
  const style = MODE_STYLE[data.mode]
  const { Icon } = style
  const description = data.step.say?.trim() || data.step.description?.trim() || ''

  return (
    <div
      onClick={() => data.onSelect(data.scenarioId, data.step.id)}
      className={cn(
        'group w-[280px] cursor-pointer rounded-2xl border border-slate-200 bg-white shadow-sm transition-all',
        'hover:border-slate-300 hover:shadow-md hover:-translate-y-0.5',
      )}
    >
      {/* Connection handles — invisible but functional */}
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-2 !border-slate-400 !bg-white" />
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !border-2 !border-slate-400 !bg-white" />

      {/* Header: icon + type label */}
      <div className="flex items-center gap-2 px-5 pt-4 pb-1.5">
        <span className={cn('flex h-5 w-5 items-center justify-center rounded-md', style.accent)}>
          <Icon className={cn('h-3.5 w-3.5', style.iconText)} />
        </span>
        <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-slate-500">
          {style.label}
        </span>
        {data.isStartStep && (
          <span className="ml-auto rounded-full border border-blue-500/40 bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-700">
            Start
          </span>
        )}
      </div>

      {/* Title */}
      <div className="px-5 pb-2">
        <h3 className="truncate text-[15px] font-semibold leading-snug text-slate-900">
          {data.step.name}
        </h3>
      </div>

      {/* Description (truncated) */}
      {description && (
        <div className="px-5 pb-4">
          <p className="line-clamp-2 text-xs leading-relaxed text-slate-500">
            {description}
          </p>
        </div>
      )}
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// Scenario label "header" node (a non-interactive container marker like
// ElevenLabs' "Global" frame). Rendered when multiple scenarios are visible.
// ────────────────────────────────────────────────────────────────────────────

function ScenarioHeaderNode({ data }: NodeProps<{ name: string }>) {
  return (
    <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-3.5 py-1.5 shadow-sm backdrop-blur-sm">
      <Workflow className="h-3.5 w-3.5 text-slate-400" />
      <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500">
        {data.name}
      </span>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// Custom edge — thin gray curve with a floating pill label for the `when`
// condition. Matches ElevenLabs' "Tech question" style.
// ────────────────────────────────────────────────────────────────────────────

interface FlowEdgeData {
  whenLabel: string | null
  tone: 'amber' | 'blue' | 'purple' | 'slate'
  // Position within the group of edges sharing the same source AND target.
  // Used to vertically offset labels so multiple edges between the same
  // pair of nodes don't stack their chips on top of one another.
  siblingIndex: number
  siblingCount: number
}

const TONE_CHIP: Record<FlowEdgeData['tone'], string> = {
  amber: 'border-amber-500/40 bg-amber-500/10 text-amber-700',
  blue: 'border-blue-500/40 bg-blue-500/10 text-blue-700',
  purple: 'border-purple-500/40 bg-purple-500/10 text-purple-700',
  slate: 'border-slate-300 bg-white text-slate-600',
}

const LABEL_STACK_SPACING_PX = 26

function StepEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
}: EdgeProps<FlowEdgeData>) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 12,
  })

  // Spread sibling labels symmetrically around the path midpoint.
  // For a single edge: offset = 0 (centered).
  // For 3 siblings: offsets = [-26, 0, +26].
  const siblingCount = data?.siblingCount ?? 1
  const siblingIndex = data?.siblingIndex ?? 0
  const yOffset = siblingCount > 1
    ? (siblingIndex - (siblingCount - 1) / 2) * LABEL_STACK_SPACING_PX
    : 0

  return (
    <>
      {/* Lighter, thinner edges so labels carry the visual weight, not the
          line itself. Sierra-style. */}
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{ stroke: '#e2e8f0', strokeWidth: 1.25 }} />
      {data?.whenLabel && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY + yOffset}px)`,
              pointerEvents: 'all',
            }}
            className={cn(
              'rounded-full border px-2.5 py-0.5 text-[10px] font-medium shadow-sm',
              TONE_CHIP[data.tone],
            )}
          >
            {data.whenLabel}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// Edge label + tone derivation. Matches the TokenInput tone palette in
// ScenarioLanesCanvas so the flow and document feel continuous.
// ────────────────────────────────────────────────────────────────────────────

function edgeMetadata(transition: AgentStepTransition): { whenLabel: string | null; tone: FlowEdgeData['tone'] } {
  const when = transition.when
  if (when.kind === 'otherwise') {
    return { whenLabel: 'otherwise', tone: 'slate' }
  }
  if (when.kind === 'fact_present' || when.kind === 'fact_missing') {
    return { whenLabel: when.fact_name ? `@${when.fact_name}` : when.kind, tone: 'amber' }
  }
  if (when.kind === 'outcome') {
    // Edge-owned outcome: chip shows the stable event token. Description
    // hover-text would live on the chip; that's a polish item.
    return { whenLabel: `@${when.event}`, tone: 'blue' }
  }
  if (when.kind === 'tool_outcome') {
    return { whenLabel: when.outcome ? `tool: ${when.outcome}` : 'tool outcome', tone: 'purple' }
  }
  if (when.kind === 'guard_failure') {
    return { whenLabel: when.guard_id ? `guard: ${when.guard_id}` : 'guard', tone: 'amber' }
  }
  if (when.kind === 'all_required_facts_present') {
    return { whenLabel: 'facts complete', tone: 'amber' }
  }
  return { whenLabel: when.kind, tone: 'slate' }
}

// ────────────────────────────────────────────────────────────────────────────
// Layout — dagre top-down. One pass per scenario, then a second pass that
// positions the scenario headers and offsets each scenario's nodes.
// ────────────────────────────────────────────────────────────────────────────

const NODE_WIDTH = 280
const NODE_HEIGHT = 110

function layoutScenario(scenario: AgentScenario, onSelect: StepNodeData['onSelect']): { nodes: Node[]; edges: Edge[] } {
  // Auto-layout via dagre — used as the FALLBACK position for any step
  // without a saved entry in `scenario.flow_layout`. Per-step saved
  // positions take precedence so user drags survive across reloads and
  // are visible to teammates.
  const dag = new dagre.graphlib.Graph()
  dag.setDefaultEdgeLabel(() => ({}))
  // Sierra-style breathing room. Larger ranksep so vertical levels don't
  // feel cramped; larger nodesep so siblings have a real gap. The 30-40%
  // bump from the previous values is the perceptual sweet spot — much
  // less and it still feels dense, much more and the canvas is just empty
  // space.
  dag.setGraph({ rankdir: 'TB', nodesep: 90, ranksep: 130, marginx: 32, marginy: 32 })

  for (const step of scenario.steps) {
    dag.setNode(step.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  }

  const seenEdges = new Set<string>()
  for (const step of scenario.steps) {
    for (const transition of step.transitions) {
      // Only flow edges that target a step in THIS scenario; cross-scenario
      // routes show up at the document level and would clutter the flow.
      if (!scenario.steps.some((s) => s.id === transition.to_step_id)) continue
      const key = `${step.id}->${transition.to_step_id}:${transition.id}`
      if (seenEdges.has(key)) continue
      seenEdges.add(key)
      dag.setEdge(step.id, transition.to_step_id)
    }
  }

  dagre.layout(dag)

  const savedLayout = scenario.flow_layout ?? {}

  // Cache dagre's clean position per step so we can fall back to it for
  // any node whose saved position collides with another node (see overlap
  // detection below). dagre returns the CENTER point; we shift by half a
  // node so nodes render top-left aligned for ReactFlow.
  const dagrePositions = new Map<string, { x: number; y: number }>()
  for (const step of scenario.steps) {
    const pos = dag.node(step.id)
    dagrePositions.set(step.id, {
      x: (pos?.x ?? 0) - NODE_WIDTH / 2,
      y: (pos?.y ?? 0) - NODE_HEIGHT / 2,
    })
  }

  // First pass: assign positions, preferring saved entries over dagre.
  const initialNodes = scenario.steps.map((step) => {
    const isStartStep = step.id === scenario.start_step_id
    const mode = deriveStepMode(step, isStartStep)
    const saved = savedLayout[step.id]
    const position = saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)
      ? { x: saved.x, y: saved.y }
      : dagrePositions.get(step.id) ?? { x: 0, y: 0 }
    return {
      id: step.id,
      type: 'step',
      position,
      data: { step, mode, scenarioName: scenario.name, isStartStep, onSelect, scenarioId: scenario.id } satisfies StepNodeData,
    } as Node
  })

  // Second pass: detect overlaps — multiple nodes pinned to the same spot
  // (within OVERLAP_TOLERANCE_PX). Failure mode is when a saved layout has
  // collapsed several nodes onto each other (often from accidental drags
  // or from a stale layout that didn't include newly-added steps). For
  // each colliding cluster, drop everyone in the cluster back to dagre's
  // computed position so the flow stays readable. The user can drag
  // again afterwards to re-arrange intentionally.
  const OVERLAP_TOLERANCE_PX = 12
  const buckets = new Map<string, string[]>()
  for (const node of initialNodes) {
    const key = `${Math.round(node.position.x / OVERLAP_TOLERANCE_PX)}_${Math.round(node.position.y / OVERLAP_TOLERANCE_PX)}`
    const list = buckets.get(key) ?? []
    list.push(node.id)
    buckets.set(key, list)
  }
  const overlappingIds = new Set<string>()
  for (const list of buckets.values()) {
    if (list.length > 1) list.forEach((id) => overlappingIds.add(id))
  }

  const nodes: Node[] = initialNodes.map((node) => {
    if (!overlappingIds.has(node.id)) return node
    const dagrePos = dagrePositions.get(node.id)
    if (!dagrePos) return node
    return { ...node, position: dagrePos }
  })

  // Two-pass edge construction so we can stamp `siblingIndex` /
  // `siblingCount` on each edge — the label renderer uses these to
  // offset chips vertically so multiple edges between the same pair of
  // nodes don't stack their labels at the same midpoint.
  type DraftEdge = {
    id: string
    source: string
    target: string
    transition: AgentStepTransition
    meta: ReturnType<typeof edgeMetadata>
  }
  const drafts: DraftEdge[] = []
  for (const step of scenario.steps) {
    for (const transition of step.transitions) {
      if (!scenario.steps.some((s) => s.id === transition.to_step_id)) continue
      drafts.push({
        id: `e-${step.id}-${transition.id}`,
        source: step.id,
        target: transition.to_step_id,
        transition,
        meta: edgeMetadata(transition),
      })
    }
  }
  const siblingsByPair = new Map<string, DraftEdge[]>()
  for (const draft of drafts) {
    const key = `${draft.source}->${draft.target}`
    const list = siblingsByPair.get(key) ?? []
    list.push(draft)
    siblingsByPair.set(key, list)
  }
  const edges: Edge[] = drafts.map((draft) => {
    const siblings = siblingsByPair.get(`${draft.source}->${draft.target}`) ?? [draft]
    return {
      id: draft.id,
      source: draft.source,
      target: draft.target,
      type: 'step',
      data: {
        ...draft.meta,
        siblingIndex: siblings.indexOf(draft),
        siblingCount: siblings.length,
      } satisfies FlowEdgeData,
    }
  })

  return { nodes, edges }
}

// ────────────────────────────────────────────────────────────────────────────
// Inner flow canvas — needs ReactFlowProvider parent to access useReactFlow.
// ────────────────────────────────────────────────────────────────────────────

const NODE_TYPES = { step: StepNode, scenarioHeader: ScenarioHeaderNode }
const EDGE_TYPES = { step: StepEdge }

interface FlowCanvasProps {
  scenario: AgentScenario
  onSelectStep: (scenarioId: string, stepId: string) => void
  onPersistLayout: (scenarioId: string, layout: Record<string, { x: number; y: number }>) => void
  onResetLayout: (scenarioId: string) => void
}

function FlowCanvas({ scenario, onSelectStep, onPersistLayout, onResetLayout }: FlowCanvasProps) {
  const { fitView } = useReactFlow()

  // We hold node positions in local state so drags are smooth (60fps)
  // without round-tripping through the document on every pixel move.
  // Only `onNodeDragStop` flushes to the document — see Decision B in
  // the team review: write only when the user actually drags, never on
  // pristine renders, so a teammate's untouched session can't clobber
  // someone else's saved layout.
  const initial = useMemo(() => layoutScenario(scenario, onSelectStep), [scenario, onSelectStep])
  // Scenario id ref lets us re-seed local state when the user switches
  // scenarios without losing per-scenario layouts in the document.
  const lastScenarioId = useRef<string | null>(null)

  const [nodes, setNodes] = useStateWithSeed(initial.nodes, scenario.id, lastScenarioId)
  const edges = initial.edges

  // Mirror nodes into a ref so handleNodeDragStop can read latest positions
  // WITHOUT calling setNodes(updater) — invoking a side effect like
  // onPersistLayout (which dispatches setState on AgentDocumentProvider)
  // inside a state updater is the React "setState during render" warning,
  // and in StrictMode the updater runs twice → dirty bit + document write
  // fire twice per drag-stop. The ref keeps the side effect outside React's
  // reducer phase entirely.
  const nodesRef = useRef(nodes)
  useEffect(() => {
    nodesRef.current = nodes
  }, [nodes])

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current))
  }, [setNodes])

  // Persist on drag-stop only. Layout map comes from the ref-mirrored
  // latest positions, NOT from a state-updater callback — see ref comment
  // above. Steps without entries fall back to dagre next render.
  const handleNodeDragStop = useCallback(() => {
    const layout: Record<string, { x: number; y: number }> = {}
    for (const node of nodesRef.current) {
      layout[node.id] = { x: node.position.x, y: node.position.y }
    }
    onPersistLayout(scenario.id, layout)
  }, [onPersistLayout, scenario.id])

  // Re-fit when the scenario changes
  useEffect(() => {
    if (scenario.id !== lastScenarioId.current) {
      requestAnimationFrame(() => {
        fitView({ padding: 0.2, duration: 300 })
      })
    }
  }, [scenario.id, fitView])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onNodeDragStop={handleNodeDragStop}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.2 }}
      proOptions={{ hideAttribution: true }}
      minZoom={0.2}
      maxZoom={2}
      defaultEdgeOptions={{ type: 'step' }}
      nodesDraggable
      nodesConnectable={false}
      elementsSelectable
      panOnDrag
      panOnScroll={false}
      zoomOnScroll
      zoomOnPinch
    >
      <Background variant={BackgroundVariant.Dots} color="#e2e8f0" gap={24} size={1.2} />
      <Controls position="bottom-right" showInteractive={false} className="!shadow-sm" />
      <MiniMap
        position="bottom-left"
        pannable
        zoomable
        nodeColor={(node) => {
          const data = node.data as Partial<StepNodeData> | undefined
          if (!data?.mode) return '#cbd5e1'
          const tones: Record<StepMode, string> = {
            entry: '#3b82f6',
            conversational: '#94a3b8',
            fact_collection: '#f59e0b',
            tool_execution: '#8b5cf6',
            handoff: '#f43f5e',
            completion: '#10b981',
          }
          return tones[data.mode]
        }}
        maskColor="rgba(255, 255, 255, 0.6)"
        className="!rounded-lg !border !border-border !shadow-sm"
      />

      {/* Auto-arrange — always available. Clears scenario.flow_layout so
          dagre's top-down layout takes over on next render. The button copy
          adapts to whether a saved layout exists; behaviour is the same. */}
      <div className="absolute right-3 top-3 z-10">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onResetLayout(scenario.id)}
          className="h-7 gap-1.5 bg-white/90 text-xs shadow-sm backdrop-blur-sm"
          title="Re-run automatic layout (clears any saved positions)"
        >
          <RotateCcw className="h-3 w-3" />
          {Object.keys(scenario.flow_layout ?? {}).length > 0 ? 'Reset layout' : 'Auto-arrange'}
        </Button>
      </div>
    </ReactFlow>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// useStateWithSeed — like useState, but re-seeds when a key changes. Keeps
// node-position drags responsive while still resetting cleanly when the
// user switches scenarios or the upstream scenario receives a structural
// edit (steps added/removed in the Document tab).
// ────────────────────────────────────────────────────────────────────────────

function useStateWithSeed<T>(seed: T, key: string, lastKeyRef: React.MutableRefObject<string | null>): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [state, setState] = useState<T>(seed)
  useEffect(() => {
    if (lastKeyRef.current !== key) {
      lastKeyRef.current = key
      setState(seed)
    }
  }, [key, seed, lastKeyRef])
  return [state, setState]
}

// ────────────────────────────────────────────────────────────────────────────
// Public component — selects the focused scenario and renders the flow.
// Click on a node deep-links back to the Document tab with that step expanded.
// ────────────────────────────────────────────────────────────────────────────

export function AgentFlowGraph() {
  const navigate = useNavigate()
  const location = useLocation()
  const { document, selectedScenarioId, setSelectedStepId, updateScenario } = useAgentDocument()

  const selectedScenario = useMemo(
    () => document.scenarios.find((s) => s.id === selectedScenarioId) ?? document.scenarios[0] ?? null,
    [document.scenarios, selectedScenarioId],
  )

  const handleSelectStep = useCallback(
    (scenarioId: string, stepId: string) => {
      setSelectedStepId(stepId)
      const params = new URLSearchParams(location.search)
      params.set('view', 'canvas')
      params.set('surface', 'document')
      params.set('scenario', scenarioId)
      params.set('step', stepId)
      navigate({ pathname: location.pathname, search: `?${params.toString()}` }, { replace: true })
    },
    [location.pathname, location.search, navigate, setSelectedStepId],
  )

  const handlePersistLayout = useCallback(
    (scenarioId: string, layout: Record<string, { x: number; y: number }>) => {
      updateScenario(scenarioId, (scenario) => ({ ...scenario, flow_layout: layout }))
    },
    [updateScenario],
  )

  const handleResetLayout = useCallback(
    (scenarioId: string) => {
      updateScenario(scenarioId, (scenario) => ({ ...scenario, flow_layout: {} }))
    },
    [updateScenario],
  )

  if (!selectedScenario) {
    return (
      <div className="flex h-full items-center justify-center bg-gradient-to-b from-slate-50 to-white p-8 text-sm text-muted-foreground">
        Add a scenario to see the flow.
      </div>
    )
  }

  if (selectedScenario.steps.length === 0) {
    return (
      <div className="flex h-full items-center justify-center bg-gradient-to-b from-slate-50 to-white p-8 text-sm text-muted-foreground">
        This scenario has no steps yet. Add a step in the Document view to see it here.
      </div>
    )
  }

  return (
    <div className="h-full bg-gradient-to-b from-slate-50 to-white">
      <ReactFlowProvider>
        <FlowCanvas
          scenario={selectedScenario}
          onSelectStep={handleSelectStep}
          onPersistLayout={handlePersistLayout}
          onResetLayout={handleResetLayout}
        />
      </ReactFlowProvider>
    </div>
  )
}
