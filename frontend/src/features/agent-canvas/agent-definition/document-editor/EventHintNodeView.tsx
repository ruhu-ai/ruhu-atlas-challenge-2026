import { useEffect, useRef, useState } from 'react'
import { NodeViewContent, NodeViewWrapper, type NodeViewProps } from '@tiptap/react'
import { toast } from 'sonner'
import { renameEventHint } from './commands'

// Editable hint row.
//   [@keyInput]   description (editable text)
//
// The key input commits on blur or Enter; commit walks the document and
// updates every transition that referenced the old key under
// `intent_detected:<key>` so renames don't silently break routes.
export function EventHintNodeView({ node, editor, getPos }: NodeViewProps) {
  const persistedKey = String(node.attrs.hintKey ?? '')
  const [keyDraft, setKeyDraft] = useState(persistedKey)
  const inputRef = useRef<HTMLInputElement | null>(null)

  // Re-sync when the node's persisted key changes externally (e.g., propagation
  // from another rename or query refetch).
  useEffect(() => {
    setKeyDraft(persistedKey)
  }, [persistedKey])

  const commit = () => {
    const trimmed = keyDraft.trim()
    if (trimmed === persistedKey) {
      setKeyDraft(persistedKey)
      return
    }
    const pos = getPos()
    if (pos == null) {
      setKeyDraft(persistedKey)
      return
    }
    const result = renameEventHint(editor, pos, persistedKey, trimmed)
    if (!result.ok) {
      toast.error(result.reason ?? 'Could not rename hint')
      setKeyDraft(persistedKey)
    }
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      inputRef.current?.blur()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      setKeyDraft(persistedKey)
      inputRef.current?.blur()
    }
  }

  return (
    <NodeViewWrapper data-event-hint="" data-hint-key={persistedKey}>
      <span className="doc-hint-row">
        <input
          ref={inputRef}
          value={keyDraft}
          onChange={(e) => setKeyDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={handleKeyDown}
          contentEditable={false}
          className="doc-hint-key-input"
          placeholder="intent_name"
          spellCheck={false}
          // Editable key prefix is rendered as @<key>; the @ is decorative.
        />
        <NodeViewContent className="doc-hint-description" />
      </span>
    </NodeViewWrapper>
  )
}
