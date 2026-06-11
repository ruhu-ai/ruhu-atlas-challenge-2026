/**
 * useAtlasTurnActions — user-initiated turn actions for the Atlas panel:
 * sending a message (with attachments + API discovery), answering blocking
 * questions, approving/rejecting proposed delta changes, requesting apply
 * permission, and deciding permission requests (approve routes through the
 * permission-decisions endpoint, then the apply endpoint commits deltas).
 *
 * Extracted from AtlasAIPanel.tsx (RP-4.4). `turnActionRef` is the shared
 * re-entrancy guard across every action that posts a turn. The session
 * creator may approve their own permission requests — the safety contract
 * is explicit user confirmation, not a second human.
 */

import { useRef, useState, type Dispatch, type FormEvent, type MutableRefObject, type SetStateAction } from 'react'

import {
  atlasService,
  type AtlasTurnResponse,
  type AtlasPermissionDecision,
  type AtlasReviewDecision,
} from '@/api/services/atlas.service'

import { allDeltas } from '../components/atlas-shared'
import {
  type DisplayMessage,
  newDisplayMessageId,
  errorMessage,
  isValidAgentId,
  fileToAttachment,
  pastedChunkToAttachment,
  appendAttachmentContext,
  buildApiDiscoveryRequests,
} from '../components/atlas-panel-helpers'
import type { RunTurnArgs } from './useAtlasSession'
import type { PastedChunk } from './useAtlasComposer'

export function useAtlasTurnActions(args: {
  agentId?: string
  isRunningTurn: boolean
  runTurn: (turnArgs: RunTurnArgs) => Promise<boolean>
  turnPostRef: MutableRefObject<boolean>
  setMessages: Dispatch<SetStateAction<DisplayMessage[]>>
  currentSessionId: string | null
  input: string
  setInput: Dispatch<SetStateAction<string>>
  selectedFiles: File[]
  setSelectedFiles: Dispatch<SetStateAction<File[]>>
  pastedChunks: PastedChunk[]
  setPastedChunks: Dispatch<SetStateAction<PastedChunk[]>>
}) {
  const {
    agentId,
    isRunningTurn,
    runTurn,
    turnPostRef,
    setMessages,
    currentSessionId,
    input,
    setInput,
    selectedFiles,
    setSelectedFiles,
    pastedChunks,
    setPastedChunks,
  } = args

  const turnActionRef = useRef(false)
  const permissionDecisionRef = useRef(false)

  const [isDecidingPermissions, setIsDecidingPermissions] = useState(false)
  const [isReviewingChanges, setIsReviewingChanges] = useState(false)

  const handleSend = async (event?: FormEvent) => {
    event?.preventDefault()
    if (turnActionRef.current || turnPostRef.current || isRunningTurn || !isValidAgentId(agentId)) return
    const baseMessage = input.trim()
    if (!baseMessage && selectedFiles.length === 0 && pastedChunks.length === 0) return
    turnActionRef.current = true
    try {
      setInput('')
      setSelectedFiles([])
      setPastedChunks([])
      const fileAttachments = await Promise.all(selectedFiles.map(fileToAttachment))
      const pasteAttachments = pastedChunks.map(pastedChunkToAttachment)
      const attachments = [...fileAttachments, ...pasteAttachments]
      const apiDiscoveryRequests = buildApiDiscoveryRequests(baseMessage)
      const userMessage = appendAttachmentContext(
        baseMessage || (attachments.length === 1 ? `Review ${attachments[0].display_name}` : 'Review the attached files'),
        attachments,
      )
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('msg'),
          role: 'user',
          content:
            attachments.length > 0
              ? `${baseMessage || 'Shared attachments'}\n${attachments.map((item) => `- ${item.display_name}`).join('\n')}`
              : baseMessage,
          timestamp: new Date(),
        },
      ])
      await runTurn({
        message: userMessage,
        attachments,
        api_discovery_requests: apiDiscoveryRequests,
      })
    } catch (err) {
      console.error('Atlas: prepare turn failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
    } finally {
      turnActionRef.current = false
    }
  }

  const handleAnswerQuestions = async (answers: Record<string, string>) => {
    if (turnActionRef.current || turnPostRef.current || isRunningTurn) return
    turnActionRef.current = true
    try {
      const summary = Object.values(answers).filter(Boolean).join(', ')
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('msg'),
          role: 'user',
          content: `Answers: ${summary}`,
          timestamp: new Date(),
        },
      ])
      await runTurn({ question_answers: answers })
    } finally {
      turnActionRef.current = false
    }
  }

  const handleReviewChanges = async (response: AtlasTurnResponse, decision: 'approved' | 'rejected') => {
    if (turnActionRef.current || turnPostRef.current || isRunningTurn || isReviewingChanges) return
    const deltaIds = allDeltas(response.proposed_changes).map((delta) => delta.delta_id)
    if (deltaIds.length === 0) return
    turnActionRef.current = true
    setIsReviewingChanges(true)
    try {
      const reviewDecisions: AtlasReviewDecision[] = deltaIds.map((deltaId) => ({
        delta_id: deltaId,
        decision,
      }))
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('review'),
          role: 'user',
          content: decision === 'approved' ? 'Approved proposed changes for apply review.' : 'Rejected proposed changes.',
          timestamp: new Date(),
        },
      ])
      await runTurn({ review_decisions: reviewDecisions })
    } finally {
      turnActionRef.current = false
      setIsReviewingChanges(false)
    }
  }

  const handleRequestApply = async (response: AtlasTurnResponse) => {
    if (turnActionRef.current || turnPostRef.current || isRunningTurn || isReviewingChanges) return
    const approvedIds = new Set(response.review_state.approved_delta_ids ?? [])
    const deltaIds = allDeltas(response.proposed_changes)
      .map((delta) => delta.delta_id)
      .filter((deltaId) => approvedIds.has(deltaId))
    if (deltaIds.length === 0) {
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('sys'),
          role: 'system',
          content: 'Approve the proposed changes before requesting apply permission.',
          timestamp: new Date(),
        },
      ])
      return
    }
    turnActionRef.current = true
    setIsReviewingChanges(true)
    try {
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('apply-request'),
          role: 'user',
          content: 'Requested permission to apply the approved changes.',
          timestamp: new Date(),
        },
      ])
      await runTurn({ apply_request: { delta_ids: deltaIds } })
    } finally {
      turnActionRef.current = false
      setIsReviewingChanges(false)
    }
  }

  // Approve pending permission requests, then apply only the delta IDs covered by those requests.
  const handleApprovePermissions = async (response: AtlasTurnResponse) => {
    if (!currentSessionId || permissionDecisionRef.current || isDecidingPermissions) return
    permissionDecisionRef.current = true
    setIsDecidingPermissions(true)
    try {
      const decisions: AtlasPermissionDecision[] = response.pending_permission_requests.map((req) => ({
        request_id: req.request_id,
        decision: 'approved',
      }))
      const decisionResp = await atlasService.applyPermissionDecisions(currentSessionId, decisions)
      const stillPending = decisionResp.updated_requests.filter((r) => r.status === 'pending')
      if (stillPending.length > 0) {
        setMessages((prev) => [
          ...prev,
          {
            id: newDisplayMessageId('sys'),
            role: 'system',
            content: `${stillPending.length} permission(s) still pending — cannot apply yet.`,
            timestamp: new Date(),
          },
        ])
        return
      }
      const deltaIds = Array.from(
        new Set(response.pending_permission_requests.flatMap((request) => request.delta_ids)),
      )
      if (deltaIds.length === 0) {
        setMessages((prev) => [
          ...prev,
          {
            id: newDisplayMessageId('sys'),
            role: 'system',
            content: 'Permissions approved, no deltas to apply.',
            timestamp: new Date(),
          },
        ])
        return
      }
      const applyResp = await atlasService.applyChanges(currentSessionId, { delta_ids: deltaIds })
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('sys'),
          role: 'system',
          content:
            applyResp.status === 'applied'
              ? 'Changes applied to the agent draft.'
              : `Apply ${applyResp.status}${applyResp.error ? `: ${applyResp.error}` : '.'}`,
          timestamp: new Date(),
        },
      ])
    } catch (err) {
      console.error('Atlas: approve+apply failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
    } finally {
      permissionDecisionRef.current = false
      setIsDecidingPermissions(false)
    }
  }

  const handleRejectPermissions = async (response: AtlasTurnResponse) => {
    if (!currentSessionId || permissionDecisionRef.current || isDecidingPermissions) return
    permissionDecisionRef.current = true
    setIsDecidingPermissions(true)
    try {
      const decisions: AtlasPermissionDecision[] = response.pending_permission_requests.map((req) => ({
        request_id: req.request_id,
        decision: 'denied',
      }))
      await atlasService.applyPermissionDecisions(currentSessionId, decisions)
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('sys'),
          role: 'system',
          content: 'Permission requests denied.',
          timestamp: new Date(),
        },
      ])
    } catch (err) {
      console.error('Atlas: reject permissions failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
    } finally {
      permissionDecisionRef.current = false
      setIsDecidingPermissions(false)
    }
  }

  return {
    isDecidingPermissions,
    isReviewingChanges,
    handleSend,
    handleAnswerQuestions,
    handleReviewChanges,
    handleRequestApply,
    handleApprovePermissions,
    handleRejectPermissions,
  }
}
