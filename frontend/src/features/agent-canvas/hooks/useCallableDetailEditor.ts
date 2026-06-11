/**
 * Draft state + mutations for the Library callable detail panel.
 *
 * Extracted from LibraryView.tsx (RP-4.4) — owns every editable field
 * (HTTP request config, input schema, code body, composite steps, output
 * mapping, callable refs, display name), the per-field dirty flags, and
 * the update / delete / duplicate mutations with their toasts.
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { toolService } from '@/api/services/tools.service'
import {
  normalizeCompositeSteps,
  normalizeOutputMapping,
  type CallableEntry,
  type CompositeStep,
} from '@/features/agent-canvas/components/library-view-helpers'

export function useCallableDetailEditor(entry: CallableEntry, onClose: () => void) {
  const tool = entry.raw
  const queryClient = useQueryClient()

  // Local draft for editable fields. Initialised from `tool` and reset when
  // the selected entry changes.
  const [httpMethod, setHttpMethod] = useState(tool.http_method ?? 'GET')
  const [endpointPath, setEndpointPath] = useState(tool.endpoint_path ?? '')
  const [timeoutMs, setTimeoutMs] = useState(tool.timeout_ms ?? 5000)
  const [readOnly, setReadOnly] = useState(tool.read_only ?? false)
  const [schemaText, setSchemaText] = useState(() => JSON.stringify(tool.input_schema ?? {}, null, 2))
  const [schemaError, setSchemaError] = useState<string | null>(null)
  const [isEditingName, setIsEditingName] = useState(false)
  const [draftName, setDraftName] = useState(entry.displayName)
  // Code-kind only: refs of other Library callables this body can invoke.
  // Aliases are derived deterministically from the refs at save time
  // (see resolveCallableAliases) so the executor binds the same names
  // the UI advertises in the row chips.
  const initialCallableRefs = useMemo<string[]>(() => {
    const raw = tool.metadata?.callable_refs
    if (!Array.isArray(raw)) return []
    return raw.filter((entry): entry is string => typeof entry === 'string')
  }, [tool.metadata?.callable_refs])
  const [callableRefs, setCallableRefs] = useState<string[]>(initialCallableRefs)
  const initialCodeBody = String((tool.metadata?.code_body as string | undefined) ?? '')
  const [codeBody, setCodeBody] = useState(initialCodeBody)
  const initialCompositeSteps = useMemo<CompositeStep[]>(
    () => normalizeCompositeSteps(tool.metadata?.composite_steps),
    [tool.metadata?.composite_steps],
  )
  const [compositeSteps, setCompositeSteps] = useState<CompositeStep[]>(initialCompositeSteps)
  const initialOutputMapping = useMemo<Record<string, string>>(
    () => normalizeOutputMapping(tool.metadata?.output_mapping),
    [tool.metadata?.output_mapping],
  )
  const [outputMapping, setOutputMapping] = useState<Record<string, string>>(initialOutputMapping)

  useEffect(() => {
    setHttpMethod(tool.http_method ?? 'GET')
    setEndpointPath(tool.endpoint_path ?? '')
    setTimeoutMs(tool.timeout_ms ?? 5000)
    setReadOnly(tool.read_only ?? false)
    setSchemaText(JSON.stringify(tool.input_schema ?? {}, null, 2))
    setSchemaError(null)
    setCodeBody(String((tool.metadata?.code_body as string | undefined) ?? ''))
    setCompositeSteps(normalizeCompositeSteps(tool.metadata?.composite_steps))
    setOutputMapping(normalizeOutputMapping(tool.metadata?.output_mapping))
    setIsEditingName(false)
    setDraftName(entry.displayName)
    setCallableRefs(initialCallableRefs)
  }, [tool.tool_definition_id, tool.http_method, tool.endpoint_path, tool.timeout_ms, tool.read_only, tool.input_schema, tool.metadata, entry.displayName, initialCallableRefs])

  const httpDirty =
    httpMethod !== (tool.http_method ?? 'GET') ||
    endpointPath !== (tool.endpoint_path ?? '') ||
    timeoutMs !== (tool.timeout_ms ?? 5000) ||
    readOnly !== (tool.read_only ?? false)

  const schemaDirty = schemaText.trim() !== JSON.stringify(tool.input_schema ?? {}, null, 2).trim()
  const codeDirty = codeBody !== initialCodeBody
  const compositeDirty =
    JSON.stringify(compositeSteps) !== JSON.stringify(initialCompositeSteps)
  const outputMappingDirty =
    JSON.stringify(outputMapping) !== JSON.stringify(initialOutputMapping)
  const callableRefsDirty =
    JSON.stringify([...callableRefs].sort()) !== JSON.stringify([...initialCallableRefs].sort())

  const updateMutation = useMutation({
    mutationFn: (payload: Parameters<typeof toolService.updateDefinition>[1]) =>
      toolService.updateDefinition(tool.tool_definition_id, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['library-callables'] })
      toast.success('Callable updated')
    },
    onError: (error: Error) => {
      toast.error(`Update failed: ${error.message}`)
    },
  })

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: () => toolService.deleteDefinition(tool.tool_definition_id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['library-callables'] })
      toast.success(`Deleted "${entry.displayName}"`)
      setDeleteDialogOpen(false)
      onClose()
    },
    onError: (error: Error) => {
      toast.error(`Delete failed: ${error.message}`)
    },
  })

  const duplicateMutation = useMutation({
    mutationFn: () => {
      const stamp = Date.now().toString(36)
      const baseRef = tool.tool_ref
        ? `${tool.tool_ref}_copy_${stamp}`
        : `${entry.kind}.copy_${stamp}`
      // Re-use the source's metadata + schema so the duplicate is a
      // working starting point. Connection_id is preserved for API
      // tools so the new endpoint inherits the same host.
      return toolService.createDefinition({
        kind: tool.kind,
        connection_id: tool.connection_id ?? null,
        tool_ref: baseRef,
        display_name: `${entry.displayName} (copy)`,
        description: entry.description,
        http_method: tool.http_method ?? undefined,
        endpoint_path: tool.endpoint_path ?? undefined,
        timeout_ms: tool.timeout_ms ?? undefined,
        read_only: tool.read_only ?? undefined,
        input_schema: tool.input_schema ?? undefined,
        metadata: tool.metadata ?? undefined,
      })
    },
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ['library-callables'] })
      toast.success('Duplicated callable')
      // Phase 3 detail mode is selectedId-driven; switch focus to the
      // new copy so the user lands on the editable duplicate.
      onClose()
      setTimeout(() => {
        // small defer so the parent's selectedId effect picks the new
        // entry once the list query refetches.
        const params = new URLSearchParams(window.location.search)
        params.set('callable_id', created.tool_definition_id)
        window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`)
        window.dispatchEvent(new PopStateEvent('popstate'))
      }, 100)
    },
    onError: (error: Error) => {
      toast.error(`Duplicate failed: ${error.message}`)
    },
  })

  const saveHttp = () => {
    updateMutation.mutate({
      http_method: httpMethod,
      endpoint_path: endpointPath || null,
      timeout_ms: timeoutMs,
      read_only: readOnly,
    })
  }

  const saveSchema = () => {
    try {
      const parsed = JSON.parse(schemaText) as Record<string, unknown>
      setSchemaError(null)
      updateMutation.mutate({ input_schema: parsed })
    } catch (err) {
      setSchemaError((err as Error).message)
    }
  }

  const saveCode = () => {
    const nextMetadata = { ...(tool.metadata ?? {}), code_body: codeBody }
    updateMutation.mutate({ metadata: nextMetadata })
  }

  const saveName = () => {
    const next = draftName.trim()
    if (!next || next === entry.displayName) {
      setIsEditingName(false)
      setDraftName(entry.displayName)
      return
    }
    updateMutation.mutate({ display_name: next })
    setIsEditingName(false)
  }

  const saveCallableRefs = () => {
    const cleaned = Array.from(new Set(callableRefs.map((ref) => ref.trim()).filter(Boolean)))
    // Drop any stale callable_aliases that no longer reference a declared
    // ref — keeps the persisted shape in step with the runtime validator.
    const existingAliases = (tool.metadata?.callable_aliases ?? {}) as Record<string, string>
    const refSet = new Set(cleaned)
    const aliases: Record<string, string> = {}
    for (const [alias, ref] of Object.entries(existingAliases)) {
      if (typeof alias === 'string' && typeof ref === 'string' && refSet.has(ref)) {
        aliases[alias] = ref
      }
    }
    const nextMetadata: Record<string, unknown> = {
      ...(tool.metadata ?? {}),
      callable_refs: cleaned,
    }
    if (Object.keys(aliases).length > 0) {
      nextMetadata.callable_aliases = aliases
    } else {
      delete nextMetadata.callable_aliases
    }
    updateMutation.mutate({ metadata: nextMetadata })
  }

  const saveComposite = () => {
    const cleaned = compositeSteps
      .filter((s) => s.ref.trim())
      .map((s) => ({
        ref: s.ref.trim(),
        args: Object.fromEntries(Object.entries(s.args).filter(([k]) => k.trim())),
      }))
    const nextMetadata = { ...(tool.metadata ?? {}), composite_steps: cleaned }
    updateMutation.mutate({ metadata: nextMetadata })
  }

  const saveOutputMapping = () => {
    const cleaned: Record<string, string> = {}
    for (const [key, value] of Object.entries(outputMapping)) {
      if (key.trim() && value.trim()) cleaned[key.trim()] = value.trim()
    }
    const nextMetadata = { ...(tool.metadata ?? {}), output_mapping: cleaned }
    updateMutation.mutate({ metadata: nextMetadata })
  }

  return {
    httpMethod,
    setHttpMethod,
    endpointPath,
    setEndpointPath,
    timeoutMs,
    setTimeoutMs,
    readOnly,
    setReadOnly,
    schemaText,
    setSchemaText,
    schemaError,
    setSchemaError,
    isEditingName,
    setIsEditingName,
    draftName,
    setDraftName,
    callableRefs,
    setCallableRefs,
    codeBody,
    setCodeBody,
    compositeSteps,
    setCompositeSteps,
    outputMapping,
    setOutputMapping,
    httpDirty,
    schemaDirty,
    codeDirty,
    compositeDirty,
    outputMappingDirty,
    callableRefsDirty,
    deleteDialogOpen,
    setDeleteDialogOpen,
    updateMutation,
    deleteMutation,
    duplicateMutation,
    saveHttp,
    saveSchema,
    saveCode,
    saveName,
    saveCallableRefs,
    saveComposite,
    saveOutputMapping,
  }
}
