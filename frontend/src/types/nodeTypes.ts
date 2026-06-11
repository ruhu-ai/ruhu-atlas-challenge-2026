/**
 * Canonical node type definitions — single source of truth (TypeScript mirror).
 *
 * Must stay in sync with schemas/node_types.py.
 */

export const NODE_TYPE = {
  START: 'start',
  MESSAGE: 'message',
  LISTEN: 'listen',
  AI: 'ai',
  CONDITION: 'condition',
  CODE: 'code',
  TOOL: 'tool',
  TRANSFER: 'transfer',
  CLOSING: 'closing',
} as const

export type NodeType = (typeof NODE_TYPE)[keyof typeof NODE_TYPE]

/** The palette types users can drag onto the canvas (excludes listen). */
export const PALETTE_NODE_TYPES: NodeType[] = [
  'start',
  'message',
  'condition',
  'code',
  'ai',
  'tool',
  'transfer',
  'closing',
]

export const CODE_CAPABLE_TYPES: NodeType[] = ['code', 'condition']
