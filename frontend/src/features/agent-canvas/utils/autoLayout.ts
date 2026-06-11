/**
 * Auto-Layout Utility for Agent Canvas
 *
 * Uses dagre for directed flow layout to automatically position nodes
 * when Atlas generates a workflow from natural language.
 */

import dagre from 'dagre'
import type { Node, Edge } from 'reactflow'

// ==================== Layout Constants ====================

const NODE_WIDTH = 220
const NODE_HEIGHT = 80
const HORIZONTAL_SPACING = 80
const VERTICAL_SPACING = 60

interface LayoutOptions {
  direction?: 'TB' | 'LR' // top-to-bottom or left-to-right
  nodeWidth?: number
  nodeHeight?: number
}

// ==================== Auto-Layout ====================

/**
 * Apply dagre auto-layout to existing React Flow nodes and edges.
 *
 * Returns a new array of nodes with updated positions. Does not mutate input.
 */
export function autoLayoutNodes(
  nodes: Node[],
  edges: Edge[],
  options: LayoutOptions = {},
): Node[] {
  const {
    direction = 'TB',
    nodeWidth = NODE_WIDTH,
    nodeHeight = NODE_HEIGHT,
  } = options

  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({
    rankdir: direction,
    nodesep: HORIZONTAL_SPACING,
    ranksep: VERTICAL_SPACING,
    marginx: 50,
    marginy: 50,
  })

  // Add nodes
  for (const node of nodes) {
    g.setNode(node.id, { width: nodeWidth, height: nodeHeight })
  }

  // Add edges
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target)
  }

  // Run layout
  try {
    dagre.layout(g)
  } catch {
    // Dagre can fail on cyclic flows — fall back to grid layout
    return fallbackGridLayout(nodes, nodeWidth, nodeHeight)
  }

  // Apply positions (dagre returns center positions, React Flow uses top-left)
  return nodes.map((node) => {
    const dagreNode = g.node(node.id)
    if (!dagreNode) return node

    return {
      ...node,
      position: {
        x: dagreNode.x - nodeWidth / 2,
        y: dagreNode.y - nodeHeight / 2,
      },
    }
  })
}

/**
 * Fallback grid layout for when dagre fails (e.g., cyclic flows).
 */
function fallbackGridLayout(
  nodes: Node[],
  nodeWidth: number,
  nodeHeight: number,
): Node[] {
  const cols = Math.ceil(Math.sqrt(nodes.length))
  return nodes.map((node, i) => ({
    ...node,
    position: {
      x: 100 + (i % cols) * (nodeWidth + HORIZONTAL_SPACING),
      y: 100 + Math.floor(i / cols) * (nodeHeight + VERTICAL_SPACING),
    },
  }))
}
