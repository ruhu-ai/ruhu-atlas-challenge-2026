import type { Edge, Node } from 'reactflow'

export interface AgentCanvasConfig {
  agentName?: string
  agentDescription?: string
  agentType?: string
  systemPrompt?: string
  status?: string
  llmProvider?: string
  llmModel?: string
  temperature?: number | string
  classifierStrategy?: 'off' | 'main_llm' | 'prefill'
  voice?: string
}

export function buildCanvasDraftFingerprint(
  config: AgentCanvasConfig,
  nodes: Node[],
  edges: Edge[],
  canvasData: Record<string, unknown> = {},
): string {
  const normalizedNodes = [...nodes]
    .map((node) => ({
      id: node.id,
      type: node.type,
      x: Math.round(node.position.x),
      y: Math.round(node.position.y),
      data: node.data || {},
    }))
    .sort((a, b) => a.id.localeCompare(b.id))

  const normalizedEdges = [...edges]
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: edge.type || 'default',
      label: edge.label || '',
      data: edge.data || {},
    }))
    .sort((a, b) => a.id.localeCompare(b.id))

  const normalizedConfig = {
    agentName: config.agentName,
    agentDescription: config.agentDescription,
    agentType: config.agentType,
    systemPrompt: config.systemPrompt,
    status: config.status,
    llmProvider: config.llmProvider,
    llmModel: config.llmModel,
    temperature: config.temperature,
    classifierStrategy: config.classifierStrategy,
    voice: config.voice,
  }

  return JSON.stringify({
    config: normalizedConfig,
    nodes: normalizedNodes,
    edges: normalizedEdges,
    canvasData,
  })
}
