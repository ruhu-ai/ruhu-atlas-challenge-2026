import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
} from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import { Document } from '@tiptap/extension-document'
import { Text } from '@tiptap/extension-text'
import { Plus } from 'lucide-react'
import { useAgentDocument } from '@/features/agent-canvas/contexts/AgentDocumentContext'
import type { AgentDocument } from '@/types/agent-document'
import { documentExtensions } from './document-editor/extensions'
import { documentToEditorJSON } from './document-editor/serialize'
import { editorJSONToDocument } from './document-editor/deserialize'
import { addScenarioAtEnd } from './document-editor/commands'
import './document-editor/styles.css'

export interface AgentDocumentEditorHandle {
  save: () => Promise<boolean>
  hasUnsavedChanges: () => boolean
  scrollToScenario: (scenarioId: string) => void
}

interface AgentDocumentEditorProps {
  /** Kept for prop-shape compatibility with the surrounding workspace; the
   * editor itself reads from the AgentDocumentProvider mounted upstream. */
  agentId: string
  onDirtyChange: (dirty: boolean) => void
}

// The doc node accepts `scenario+` as content. Override the default Document
// extension so ProseMirror only allows scenarios at the top level.
const RootDocument = Document.extend({
  content: 'scenario+',
})

// Sync TipTap → provider this many ms after the last keystroke.
// Coarser than per-keystroke avoids re-render thrash; finer than save-only
// keeps the Graph view live-ish as the user types.
const SYNC_DEBOUNCE_MS = 200

export const AgentDocumentEditor = forwardRef<
  AgentDocumentEditorHandle,
  AgentDocumentEditorProps
>(({ onDirtyChange }, ref) => {
  const {
    document,
    updateDocument,
    save,
    isDirty,
    isLoading,
    isError,
    hasAgentId,
  } = useAgentDocument()

  // The last AgentDocument we pushed into TipTap via setContent. Used to
  // detect "external" document changes (server load, save success) vs.
  // "self" changes (the user typing) — only the former trigger setContent.
  const lastSyncedDocRef = useRef<AgentDocument | null>(null)
  // Pending sync timer so we can cancel/flush before save.
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const editor = useEditor({
    extensions: [RootDocument, Text, ...documentExtensions],
    editorProps: {
      attributes: {
        class: 'doc-editor-content',
        spellcheck: 'true',
      },
    },
    onUpdate: ({ editor: ed }) => {
      // IME composition (CJK input methods) fires interim onUpdate events.
      // Pushing those into the AgentDocument breaks composition mid-character.
      if (ed.view.composing) return

      if (debounceTimerRef.current != null) {
        clearTimeout(debounceTimerRef.current)
      }
      debounceTimerRef.current = setTimeout(() => {
        debounceTimerRef.current = null
        if (ed.isDestroyed) return
        const json = ed.getJSON()
        // Pass the previous document as baseline so non-editor-managed
        // fields (guards, action_config, etc.) survive the round-trip.
        updateDocument((previous) => editorJSONToDocument(json, previous))
      }, SYNC_DEBOUNCE_MS)
    },
  })

  // Cancel pending debounce on unmount so a stale sync doesn't fire after
  // the user has navigated away.
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current != null) {
        clearTimeout(debounceTimerRef.current)
        debounceTimerRef.current = null
      }
    }
  }, [])

  // Push provider state into TipTap when:
  //   - editor mounts and provider's document is hydrated (first load), OR
  //   - save success absorbed server normalizations (isDirty flips false and
  //     `document` is a new reference).
  // Skip when isDirty — user is mid-edit, don't clobber their unsaved work.
  // emitUpdate:false breaks the load → onUpdate → updateDocument → setDocument
  // → load loop.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return
    if (isDirty) return
    if (lastSyncedDocRef.current === document) return
    lastSyncedDocRef.current = document
    // Defer setContent — Tiptap's React NodeView mount calls flushSync,
    // which React rejects mid-commit. Microtask runs after the current
    // commit finishes but before paint.
    queueMicrotask(() => {
      if (editor.isDestroyed) return
      editor.commands.setContent(documentToEditorJSON(document) as never, {
        emitUpdate: false,
      })
    })
  }, [editor, document, isDirty])

  // Forward dirty signal upstream so the workspace's surface-branched dirty
  // logic ('document' branch) sees provider state.
  useEffect(() => {
    onDirtyChange(isDirty)
  }, [isDirty, onDirtyChange])

  const handleSave = useCallback(async (): Promise<boolean> => {
    // Flush any pending debounce so we save the latest editor state.
    if (debounceTimerRef.current != null) {
      clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
      if (editor && !editor.isDestroyed) {
        const json = editor.getJSON()
        updateDocument((previous) => editorJSONToDocument(json, previous))
      }
    }
    // Provider's save() reads from documentRef (synchronous mirror), so
    // the just-flushed updateDocument is visible to the save.
    return save()
  }, [editor, save, updateDocument])

  const scrollToScenario = useCallback((scenarioId: string) => {
    if (!editor) return
    let foundPos: number | null = null
    editor.state.doc.descendants((node, pos) => {
      if (foundPos != null) return false
      if (node.type.name === 'scenario' && node.attrs?.scenarioId === scenarioId) {
        foundPos = pos
        return false
      }
      return false
    })
    if (foundPos == null) return
    const dom = editor.view.nodeDOM(foundPos as number)
    if (dom instanceof HTMLElement) {
      dom.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [editor])

  useImperativeHandle(ref, () => ({
    save: handleSave,
    hasUnsavedChanges: () => isDirty,
    scrollToScenario,
  }), [handleSave, isDirty, scrollToScenario])

  if (!hasAgentId || isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-sm text-muted-foreground">Loading document…</span>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <p className="text-sm text-muted-foreground">
          Could not load the agent document. Try refreshing.
        </p>
      </div>
    )
  }

  return (
    <div className="doc-editor h-full">
      <EditorContent editor={editor} />
      {editor && (
        <div
          style={{
            padding: '0 max(24px, calc((100% - 760px) / 2)) 32px',
          }}
        >
          <button
            type="button"
            onClick={() => addScenarioAtEnd(editor)}
            className="doc-add-scenario"
          >
            <Plus className="h-3.5 w-3.5" />
            Add scenario
          </button>
        </div>
      )}
    </div>
  )
})

AgentDocumentEditor.displayName = 'AgentDocumentEditor'
