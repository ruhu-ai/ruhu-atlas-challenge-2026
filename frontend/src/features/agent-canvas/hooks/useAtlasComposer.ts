/**
 * useAtlasComposer — input-box state for the Atlas AI panel: the draft
 * message text, selected file attachments, and long-paste chunks captured
 * as attachments instead of being dumped into the textarea.
 *
 * Extracted from AtlasAIPanel.tsx (RP-4.4). Pure client state — sending
 * the composed turn lives in useAtlasTurnActions.
 */

import { useRef, useState, type ChangeEvent, type ClipboardEvent } from 'react'

import { LONG_PASTE_THRESHOLD } from '../components/atlas-panel-helpers'

export interface PastedChunk {
  id: number
  content: string
}

export function useAtlasComposer() {
  const [input, setInput] = useState('')
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [pastedChunks, setPastedChunks] = useState<PastedChunk[]>([])
  const nextPastedChunkIdRef = useRef(1)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const handleAttachmentChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length > 0) {
      setSelectedFiles((prev) => [...prev, ...files])
    }
    event.target.value = ''
  }

  const removeSelectedFile = (file: File) => {
    setSelectedFiles((prev) =>
      prev.filter((item) => item.name !== file.name || item.size !== file.size || item.lastModified !== file.lastModified),
    )
  }

  const removePastedChunk = (chunkId: number) => {
    setPastedChunks((prev) => prev.filter((chunk) => chunk.id !== chunkId))
  }

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const pastedText = event.clipboardData?.getData('text/plain') || ''
    if (pastedText.length <= LONG_PASTE_THRESHOLD) return
    event.preventDefault()
    const nextId = nextPastedChunkIdRef.current
    nextPastedChunkIdRef.current += 1
    setPastedChunks((prev) => [...prev, { id: nextId, content: pastedText }])
  }

  const resetComposer = () => {
    setInput('')
    setSelectedFiles([])
    setPastedChunks([])
  }

  return {
    input,
    setInput,
    selectedFiles,
    setSelectedFiles,
    pastedChunks,
    setPastedChunks,
    fileInputRef,
    handleAttachmentChange,
    removeSelectedFile,
    removePastedChunk,
    handlePaste,
    resetComposer,
  }
}
