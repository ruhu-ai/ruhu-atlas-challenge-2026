import { buildCanvasDraftFingerprint, type AgentCanvasConfig } from '../fingerprint'
import type { Node, Edge } from 'reactflow'

describe('buildCanvasDraftFingerprint', () => {
  const config: AgentCanvasConfig = {
    agentName: 'Demo Agent',
    agentDescription: 'Demo description',
    agentType: 'voice',
    systemPrompt: 'You are helpful',
    status: 'draft',
    llmProvider: 'gemini',
    llmModel: 'gpt-4o-mini',
    temperature: 0.7,
    voice: 'alloy',
  }

  const nodes: Node[] = [
    { id: 'node-b', type: 'tool', position: { x: 120.4, y: 80.9 }, data: { label: 'B' } },
    { id: 'node-a', type: 'start', position: { x: 50.2, y: 10.1 }, data: { label: 'A' } },
  ]

  const edges: Edge[] = [
    { id: 'edge-2', source: 'node-b', target: 'node-a', data: {} },
    { id: 'edge-1', source: 'node-a', target: 'node-b', data: {} },
  ]

  it('normalizes nodes, edges, and config consistently', () => {
    const fingerprint = buildCanvasDraftFingerprint(config, nodes, edges)
    const parsed = JSON.parse(fingerprint)

    expect(parsed.config.agentName).toBe('Demo Agent')
    expect(parsed.nodes.map((node: Node) => node.id)).toEqual(['node-a', 'node-b'])
    expect(parsed.edges.map((edge: Edge) => edge.id)).toEqual(['edge-1', 'edge-2'])
    expect(parsed.nodes[0]).toMatchObject({ x: 50, y: 10 })
  })
})
