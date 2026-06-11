import { useState } from 'react'
import { NodeViewContent, NodeViewWrapper, type NodeViewProps } from '@tiptap/react'
import { Plus } from 'lucide-react'
import { addEventHintToStep, deleteNodeAt } from './commands'

// Renders the event-hints block as a collapsible toggle.
// Collapsed: "▸ N intent hints  @key1 @key2 @key3 …" (single line)
// Expanded:  toggle + the eventHint children (each shows @key + description)
//            + a trailing "+ Add hint" button inside the block.
export function EventHintsNodeView({ node, editor, getPos }: NodeViewProps) {
  const [expanded, setExpanded] = useState(false)
  const hintKeys: string[] = []
  node.content.forEach((child) => {
    const key = String(child.attrs?.hintKey ?? '').trim()
    if (key) hintKeys.push(key)
  })
  const count = hintKeys.length
  const previewKeys = hintKeys.slice(0, 4)
  const hasMore = hintKeys.length > previewKeys.length

  // Find the parent step's pos to add hints / remove the whole block.
  const handleAddHint = () => {
    const pos = getPos()
    if (pos == null) return
    // Walk up to step. The eventHints node's parent is the step.
    const $pos = editor.state.doc.resolve(pos + 1)
    for (let depth = $pos.depth; depth >= 0; depth--) {
      if ($pos.node(depth).type.name === 'step') {
        addEventHintToStep(editor, $pos.before(depth))
        return
      }
    }
  }

  const handleDeleteBlock = () => {
    const pos = getPos()
    if (pos == null) return
    if (!window.confirm('Remove all intent hints from this step?')) return
    deleteNodeAt(editor, pos)
  }

  return (
    <NodeViewWrapper data-event-hints="">
      <button
        type="button"
        contentEditable={false}
        onClick={() => setExpanded((prev) => !prev)}
        className="doc-hints-toggle"
        aria-expanded={expanded}
      >
        <span className="doc-hints-toggle-arrow">{expanded ? '▾' : '▸'}</span>
        <span>
          {count} intent hint{count === 1 ? '' : 's'}
        </span>
        {!expanded && previewKeys.length > 0 && (
          <span className="doc-hints-summary-keys">
            {previewKeys.map((key) => (
              <span key={key} className="doc-hints-summary-key">
                @{key}
              </span>
            ))}
            {hasMore && <span>…</span>}
          </span>
        )}
      </button>
      {/* Always render NodeViewContent so ProseMirror's transactions stay
       * consistent — collapse via display:none. */}
      <div
        className="doc-hints-content"
        style={{ display: expanded ? 'block' : 'none' }}
      >
        <NodeViewContent />
        <div className="doc-hints-actions" contentEditable={false}>
          <button
            type="button"
            onClick={handleAddHint}
            className="doc-toolbar-btn"
            title="Add a new intent hint"
          >
            <Plus className="h-3 w-3" /> hint
          </button>
          <button
            type="button"
            onClick={handleDeleteBlock}
            className="doc-toolbar-btn doc-toolbar-btn-danger"
            title="Remove all hints"
          >
            remove all
          </button>
        </div>
      </div>
    </NodeViewWrapper>
  )
}
