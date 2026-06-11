import { useEffect, useRef } from 'react'
import { useRoomContext } from '@livekit/components-react'
import { DisconnectReason, RoomEvent, type Room } from 'livekit-client'
import { toast } from 'sonner'
import type { ActiveVoiceSession, VoiceTranscriptEvent } from './types'

export function E2EVoiceRoomHarness({
  session,
  connect,
  onRoomReady,
  onConnected,
  onDisconnected,
}: {
  session: ActiveVoiceSession
  connect: boolean
  onRoomReady: (room: Room | null) => void
  onConnected: () => void
  onDisconnected: (reason?: DisconnectReason) => void
}) {
  useEffect(() => {
    if (!connect || !window.__RUHU_E2E_MOCK_VOICE__?.enabled) {
      onRoomReady(null)
      return
    }

    let disconnected = false

    const fakeRoom = {
      name: session.roomName,
      localParticipant: {
        trackPublications: new Map(),
        sendText: async (text: string, options?: { topic?: string }) => {
          window.__RUHU_E2E_MOCK_VOICE__?.onSendText?.({
            sessionId: session.id,
            roomName: session.roomName,
            text,
            topic: options?.topic,
          })
          return { id: `text-${Date.now()}` }
        },
        setMicrophoneEnabled: async () => {},
        unpublishTrack: async () => {},
      },
      disconnect: async () => {
        if (disconnected) return
        disconnected = true
        onRoomReady(null)
        onDisconnected(DisconnectReason.CLIENT_INITIATED)
      },
    } as unknown as Room

    onRoomReady(fakeRoom)
    onConnected()
    window.__RUHU_E2E_MOCK_VOICE__?.onRoomMounted?.({
      sessionId: session.id,
      roomName: session.roomName,
    })

    return () => {
      onRoomReady(null)
    }
  }, [connect, onConnected, onDisconnected, onRoomReady, session.id, session.roomName])

  return null
}

export function LiveKitRoomBridge({
  onRoomReady,
}: {
  onRoomReady: (room: Room | null) => void
}) {
  const room = useRoomContext()

  useEffect(() => {
    onRoomReady(room)
    return () => onRoomReady(null)
  }, [room, onRoomReady])

  return null
}

export function VoiceConnectionEvents({
  onConnected,
  onReconnecting,
  onReconnected,
}: {
  onConnected: () => void
  onReconnecting: () => void
  onReconnected: () => void
}) {
  const room = useRoomContext()

  useEffect(() => {
    if (!room) return

    room.on(RoomEvent.Connected, onConnected)
    room.on(RoomEvent.Reconnecting, onReconnecting)
    room.on(RoomEvent.Reconnected, onReconnected)

    return () => {
      room.off(RoomEvent.Connected, onConnected)
      room.off(RoomEvent.Reconnecting, onReconnecting)
      room.off(RoomEvent.Reconnected, onReconnected)
    }
  }, [room, onConnected, onReconnecting, onReconnected])

  return null
}

export function VoiceDataHandler({
  onAgentStateChange,
  onUserSpeaking,
  onTranscript,
}: {
  onAgentStateChange: (state: string) => void
  onUserSpeaking: (speaking: boolean) => void
  onTranscript: (entry: VoiceTranscriptEvent) => void
}) {
  const room = useRoomContext()
  const agentIdleTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const userIdleTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const processedSegmentIds = useRef(new Set<string>())
  const partialTranscriptIdsBySegmentKey = useRef(new Map<string, string>())

  const onAgentStateChangeRef = useRef(onAgentStateChange)
  const onUserSpeakingRef = useRef(onUserSpeaking)
  const onTranscriptRef = useRef(onTranscript)
  onAgentStateChangeRef.current = onAgentStateChange
  onUserSpeakingRef.current = onUserSpeaking
  onTranscriptRef.current = onTranscript

  useEffect(() => {
    if (!room) return
    const IDLE_DELAY = 800

    const isLocalParticipant = (participant: any): boolean => {
      const localSid = room.localParticipant?.sid
      const participantSid = typeof participant?.sid === 'string' ? participant.sid : null
      if (localSid && participantSid) {
        return participantSid === localSid
      }
      const localIdentity = room.localParticipant?.identity || ''
      const participantIdentity = typeof participant?.identity === 'string' ? participant.identity : ''
      if (localIdentity && participantIdentity) {
        return participantIdentity === localIdentity
      }
      return false
    }

    const handleActiveSpeakers = (speakers: any[]) => {
      const agentSpeaking = speakers.some((participant: any) => !isLocalParticipant(participant))
      const userNow = speakers.some((participant: any) => isLocalParticipant(participant))

      if (agentSpeaking) {
        if (agentIdleTimeout.current) {
          clearTimeout(agentIdleTimeout.current)
          agentIdleTimeout.current = null
        }
        onAgentStateChangeRef.current('speaking')
      } else if (!agentIdleTimeout.current) {
        agentIdleTimeout.current = setTimeout(() => {
          onAgentStateChangeRef.current('idle')
          agentIdleTimeout.current = null
        }, IDLE_DELAY)
      }

      if (userNow) {
        if (userIdleTimeout.current) {
          clearTimeout(userIdleTimeout.current)
          userIdleTimeout.current = null
        }
        onUserSpeakingRef.current(true)
      } else if (!userIdleTimeout.current) {
        userIdleTimeout.current = setTimeout(() => {
          onUserSpeakingRef.current(false)
          userIdleTimeout.current = null
        }, IDLE_DELAY)
      }
    }
    room.on(RoomEvent.ActiveSpeakersChanged, handleActiveSpeakers)

    const handleTranscription = (segments: any[], participant?: any) => {
      // The agent participant is any remote participant — i.e., not the local user.
      // The backend builds participant identities as "{channel}:{conv_id}:{session_id}",
      // none of which start with "agent", so identity prefix checks are incorrect.
      const isAgent = !participant || participant.sid !== room.localParticipant?.sid

      for (const segment of segments) {
        const segmentId = segment?.id ? String(segment.id) : null
        const text = String(segment?.text || segment?.content || '').trim()
        if (!text) continue

        const speaker: 'user' | 'agent' = isAgent ? 'agent' : 'user'
        const segmentKey = segmentId ? `${speaker}:${segmentId}` : null
        const isFinal = Boolean(segment?.final)

        if (segmentKey) {
          const existingTranscriptId = partialTranscriptIdsBySegmentKey.current.get(segmentKey)
          if (existingTranscriptId) {
            onTranscriptRef.current({
              id: existingTranscriptId,
              text,
              speaker,
              isFinal,
              segmentKey,
            })
            if (isFinal) {
              partialTranscriptIdsBySegmentKey.current.delete(segmentKey)
              processedSegmentIds.current.add(segmentId!)
            }
            continue
          }
        }

        if (!isFinal) {
          // Always use a stable key for partials so they replace in-place
          // rather than appending new entries (prevents "I'm I'm" stuttering).
          const stableKey = segmentKey || `${speaker}:partial_fallback`
          const partialId = `partial:${stableKey}`
          partialTranscriptIdsBySegmentKey.current.set(stableKey, partialId)
          onTranscriptRef.current({
            id: partialId,
            text,
            speaker,
            isFinal: false,
            segmentKey: stableKey,
          })
          continue
        }

        if (segmentId && processedSegmentIds.current.has(segmentId)) continue
        if (segmentId) processedSegmentIds.current.add(segmentId)
        onTranscriptRef.current({
          id: segmentKey ? `final:${segmentKey}` : undefined,
          text,
          speaker,
          isFinal: true,
          segmentKey: segmentKey ?? undefined,
        })
        if (segmentKey) {
          partialTranscriptIdsBySegmentKey.current.delete(segmentKey)
        }
      }
    }
    room.on(RoomEvent.TranscriptionReceived, handleTranscription as any)

    const handleDataReceived = (
      payload: Uint8Array,
      _participant: unknown,
      _kind: unknown,
      topic?: string,
    ) => {
      let message: { type?: string; message?: string } | null = null
      try {
        message = JSON.parse(new TextDecoder().decode(payload)) as { type?: string; message?: string }
      } catch {
        message = null
      }
      const isErrorPacket = topic === 'ruhu_error' || message?.type === 'error'
      if (!isErrorPacket) return
      toast.error(message?.message || 'A voice pipeline error occurred.')
    }
    room.on(RoomEvent.DataReceived, handleDataReceived)

    return () => {
      room.off(RoomEvent.ActiveSpeakersChanged, handleActiveSpeakers)
      room.off(RoomEvent.TranscriptionReceived, handleTranscription as any)
      room.off(RoomEvent.DataReceived, handleDataReceived)
      if (agentIdleTimeout.current) clearTimeout(agentIdleTimeout.current)
      if (userIdleTimeout.current) clearTimeout(userIdleTimeout.current)
    }
  }, [room])

  return null
}
