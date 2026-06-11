/**
 * Voice Call Room component.
 *
 * Features:
 * - Visual listening indicator
 * - Real-time transcript display
 * - Processing/thinking states
 * - Interrupt button
 * - Audio visualization
 */

import { useEffect, useState, useRef, useCallback } from 'react'
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useRoomContext,
  useParticipants,
  useConnectionState,
  useLocalParticipant,
} from '@livekit/components-react'
import '@livekit/components-styles'
import { ConnectionState, RoomEvent } from 'livekit-client'
import { Card, CardContent } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import {
  Phone,
  PhoneOff,
  Mic,
  MicOff,
  Square,
  MessageSquare,
} from 'lucide-react'
import { SunOrbitVisualizer } from './SunOrbitVisualizer'
import { useCreateVoiceSession, useEndVoiceSession, useVoiceHealth } from '../hooks/useVoiceSessions'
import { toast } from 'sonner'
import { createLogger } from '@/utils/logger'
import { useAuthStore } from '@/store/auth.store'

const voiceLogger = createLogger({ prefix: '[Voice]' })

interface VoiceCallRoomProps {
  serverUrl: string
  token: string
  roomName: string
  agentName: string
  onDisconnect?: () => void
  autoConnect?: boolean
}

/**
 * Transcript Display Component
 */
function TranscriptDisplay({ 
  transcripts 
}: { 
  transcripts: Array<{ id: string; text: string; speaker: 'user' | 'agent'; timestamp: Date }> 
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [transcripts])
  
  if (transcripts.length === 0) {
    return (
      <div className="text-center text-muted-foreground text-sm py-8">
        <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <p>Conversation will appear here</p>
      </div>
    )
  }
  
  return (
    <div 
      ref={scrollRef}
      className="space-y-3 max-h-48 overflow-y-auto pr-2"
    >
      {transcripts.map((t) => (
        <div
          key={t.id}
          className={`flex ${t.speaker === 'user' ? 'justify-end' : 'justify-start'}`}
        >
          <div
            className={`max-w-[80%] px-4 py-2 rounded-2xl text-sm leading-relaxed ${
              t.speaker === 'user'
                ? 'bg-primary text-primary-foreground rounded-br-md'
                : 'bg-accent text-accent-foreground rounded-bl-md'
            }`}
          >
            {t.text}
          </div>
        </div>
      ))}
    </div>
  )
}

// Map voice agent state to SunOrbitVisualizer intensity
function getVisualizerIntensity(state: string, userSpeaking: boolean): number {
  if (userSpeaking) return 0.5
  switch (state) {
    case 'speaking': return 0.8
    case 'thinking':
    case 'processing': return 0.4
    case 'idle': return 0.2
    default: return 0
  }
}

/**
 * Room Status with Agent State
 */
function RoomStatus({
  agentName,
  agentState,
  userSpeaking,
  transcripts,
}: {
  agentName: string
  agentState: { state: string; message?: string }
  userSpeaking: boolean
  transcripts: Array<{ id: string; text: string; speaker: 'user' | 'agent'; timestamp: Date }>
}) {
  const connectionState = useConnectionState()
  const participants = useParticipants()
  const { localParticipant } = useLocalParticipant()
  const localIdentity = localParticipant?.identity
  const localIncluded = !!localIdentity && participants.some((p) => p.identity === localIdentity)
  const participantCount = participants.length + (localParticipant && !localIncluded ? 1 : 0)

  const getConnectionStatus = () => {
    switch (connectionState) {
      case ConnectionState.Connected:
        return { label: 'Connected', variant: 'success' as const }
      case ConnectionState.Connecting:
        return { label: 'Connecting...', variant: 'warning' as const }
      case ConnectionState.Disconnected:
        return { label: 'Disconnected', variant: 'secondary' as const }
      case ConnectionState.Reconnecting:
        return { label: 'Reconnecting...', variant: 'warning' as const }
      default:
        return { label: 'Unknown', variant: 'secondary' as const }
    }
  }

  const status = getConnectionStatus()
  const visualizerIntensity = getVisualizerIntensity(agentState.state, userSpeaking)
  const visualizerActive = agentState.state === 'speaking' || userSpeaking

  return (
    <div className="space-y-4">
      {/* Sun Orbit Visualizer + Agent Info */}
      <div className="flex flex-col items-center gap-3">
        <SunOrbitVisualizer
          size={140}
          intensity={visualizerIntensity}
          isActive={visualizerActive}
        />

        {/* Agent Name + Connected Badge */}
        <div className="flex items-center gap-2">
          <span className="font-semibold">{agentName}</span>
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
      </div>

      {/* Transcript Display */}
      <div className="rounded-lg border border-border bg-background/50 p-4">
        <div className="flex items-center gap-2 mb-3 text-sm font-medium text-muted-foreground">
          <MessageSquare className="h-4 w-4" />
          Conversation
        </div>
        <TranscriptDisplay transcripts={transcripts} />
      </div>

      {/* Participant Count */}
      <div className="text-xs text-muted-foreground text-center">
        {participantCount} participant{participantCount !== 1 ? 's' : ''} in room
      </div>
    </div>
  )
}

/**
 * Voice Call Room Component
 */
export function VoiceCallRoom({
  serverUrl,
  token,
  roomName,
  agentName,
  onDisconnect,
  autoConnect = true,
}: VoiceCallRoomProps) {
  const [agentState, setAgentState] = useState<{ state: string; message?: string }>({ state: 'idle' })
  const [userSpeaking, setUserSpeaking] = useState(false)
  const [transcripts, setTranscripts] = useState<Array<{ id: string; text: string; speaker: 'user' | 'agent'; timestamp: Date }>>([])
  const disconnectHandledRef = useRef(false)

  const handleTranscript = useCallback((entry: {
    id?: string
    text: string
    speaker: 'user' | 'agent'
  }) => {
    const id = entry.id || `voice_${Date.now()}_${Math.random()}`
    setTranscripts(prev => {
      const existingIndex = prev.findIndex((item) => item.id === id)
      if (existingIndex === -1) {
        return [...prev, { ...entry, id, timestamp: new Date() }]
      }
      return prev.map((item) => (
        item.id === id
          ? { ...item, text: entry.text, speaker: entry.speaker, timestamp: new Date() }
          : item
      ))
    })
  }, [])
  const handleRoomDisconnected = useCallback(() => {
    // Prevent double-firing (End Call button + onDisconnected event)
    if (disconnectHandledRef.current) return
    disconnectHandledRef.current = true
    onDisconnect?.()
  }, [onDisconnect])
  const handleRoomError = useCallback((error: Error) => {
    voiceLogger.error('LiveKit error:', error)
  }, [])

  if (!serverUrl || !token) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="text-center text-destructive">
            <p>Missing LiveKit configuration</p>
            <p className="mt-2 text-sm text-muted-foreground">
              Server URL and token are required to start a call
            </p>
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
      <Card className="w-full max-w-md">
        <CardContent className="space-y-4 pt-6">
          <LiveKitRoom
            key={roomName}
            serverUrl={serverUrl}
            token={token}
            connect={autoConnect}
            audio={false}
            video={false}
            onDisconnected={handleRoomDisconnected}
            onError={handleRoomError}
            className="livekit-room"
          >
            {/* Audio Renderer */}
            <RoomAudioRenderer />

            {/* Data Handler for Agent Messages */}
            <DataHandler
              onAgentStateChange={setAgentState}
              onUserSpeaking={setUserSpeaking}
              onTranscript={handleTranscript}
            />

            {/* Enhanced Room Status */}
            <RoomStatus
              agentName={agentName}
              agentState={agentState}
              userSpeaking={userSpeaking}
              transcripts={transcripts}
            />

            {/* Controls */}
            <div className="mt-6 flex items-center justify-center gap-3">
              {/* Microphone toggle using LiveKit's recommended hook */}
              <MicrophoneButton />

              {/* Interrupt Button (only shown when agent is speaking) */}
              {agentState.state === 'speaking' && <InterruptButton />}

              <EndCallButton onDisconnect={handleRoomDisconnected} />
            </div>

            {/* Room Info */}
            <div className="mt-4 rounded-lg border border-border bg-accent/10 p-3">
              <div className="text-xs text-muted-foreground">
                <div className="flex justify-between">
                  <span>Room:</span>
                <span className="font-mono truncate max-w-[200px]">{roomName}</span>
              </div>
            </div>
          </div>
          </LiveKitRoom>
        </CardContent>
      </Card>
  )
}

/**
 * Microphone Button - uses LiveKit's recommended useTrackToggle hook
 * This is the production-ready approach per LiveKit documentation
 */
function MicrophoneButton() {
  const room = useRoomContext()
  const [micEnabled, setMicEnabled] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Auto-enable microphone on mount
  useEffect(() => {
    if (!room || micEnabled) return

    const enableMicrophone = async () => {
      setIsLoading(true)
      setError(null)

      try {
        // First, request microphone permission and get available devices
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        stream.getTracks().forEach(track => track.stop()) // Release the stream

        // Now enumerate devices to find audio inputs
        const devices = await navigator.mediaDevices.enumerateDevices()
        const audioInputs = devices.filter(d => d.kind === 'audioinput')
        voiceLogger.log('Available microphones:', audioInputs.map(d => d.label || d.deviceId))

        if (audioInputs.length === 0) {
          setError('No microphone found')
          setIsLoading(false)
          return
        }

        // Enable microphone using LiveKit
        await room.localParticipant.setMicrophoneEnabled(true)
        setMicEnabled(true)
        voiceLogger.log('Microphone enabled successfully')
      } catch (err) {
        voiceLogger.error('Failed to enable microphone:', err)
        setError(err instanceof Error ? err.message : 'Microphone error')
      } finally {
        setIsLoading(false)
      }
    }

    // Small delay to let room fully connect
    const timer = setTimeout(enableMicrophone, 500)
    return () => clearTimeout(timer)
  }, [room, micEnabled])

  const toggleMicrophone = async () => {
    if (!room) return
    setIsLoading(true)

    try {
      await room.localParticipant.setMicrophoneEnabled(!micEnabled)
      setMicEnabled(!micEnabled)
      setError(null)
    } catch (err) {
      voiceLogger.error('Failed to toggle microphone:', err)
      setError(err instanceof Error ? err.message : 'Microphone error')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="flex flex-col items-center gap-1">
      <Button
        variant={micEnabled ? 'outline' : 'destructive'}
        size="lg"
        className="rounded-full"
        onClick={toggleMicrophone}
        disabled={isLoading}
      >
        {isLoading ? (
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-current border-t-transparent" />
        ) : micEnabled ? (
          <Mic className="h-5 w-5" />
        ) : (
          <MicOff className="h-5 w-5" />
        )}
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  )
}

/**
 * End Call Button — disconnects room gracefully before parent unmounts LiveKitRoom.
 * Uses useRoomContext() to call room.disconnect() which unpublishes tracks
 * and closes the peer connection in the correct order, preventing
 * "DataChannel error" and "could not createOffer" warnings.
 */
function EndCallButton({ onDisconnect }: { onDisconnect: () => void }) {
  const room = useRoomContext()

  const handleEndCall = useCallback(async () => {
    try {
      // Unpublish local tracks first to avoid renegotiation on a closing connection
      const localPub = room?.localParticipant?.trackPublications
      if (localPub) {
        for (const pub of localPub.values()) {
          if (pub.track) {
            await room.localParticipant.unpublishTrack(pub.track).catch(() => {})
          }
        }
      }
      // Disconnect gracefully — this fires onDisconnected on LiveKitRoom
      await room?.disconnect()
    } catch {
      // Suppress post-close errors — disconnect is best-effort
    }
    onDisconnect()
  }, [room, onDisconnect])

  return (
    <Button
      variant="destructive"
      size="lg"
      className="rounded-full px-8"
      onClick={handleEndCall}
    >
      <PhoneOff className="mr-2 h-5 w-5" />
      End Call
    </Button>
  )
}

/**
 * Interrupt Button — uses useRoomContext() to publish interrupt signal
 * instead of leaking room onto window.
 */
function InterruptButton() {
  const room = useRoomContext()

  const handleInterrupt = useCallback(() => {
    if (room?.localParticipant) {
      room.localParticipant.publishData(
        new TextEncoder().encode(JSON.stringify({ type: 'interrupt' })),
        { reliable: true }
      )
    }
  }, [room])

  return (
    <Button
      variant="outline"
      size="lg"
      className="rounded-full border-orange-500 text-orange-500 hover:bg-orange-500/10"
      onClick={handleInterrupt}
    >
      <Square className="h-5 w-5 mr-1" />
      Stop
    </Button>
  )
}

/**
 * Data Handler Component - listens for agent messages via data channel
 */
function DataHandler({
  onAgentStateChange,
  onUserSpeaking,
  onTranscript,
}: {
  onAgentStateChange: (state: { state: string; message?: string }) => void
  onUserSpeaking: (speaking: boolean) => void
  onTranscript: (entry: { id?: string; text: string; speaker: 'user' | 'agent' }) => void
}) {
  const room = useRoomContext()
  useLocalParticipant() // Hook must be called but value not needed
  const agentIdleTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const userIdleTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Persist dedup state across effect re-runs (prevents duplicate transcripts)
  const processedSegmentIds = useRef(new Set<string>())
  const partialTranscriptIdsBySegmentKey = useRef(new Map<string, string>())

  // Use refs for callbacks so the effect doesn't re-run when props change identity
  const onAgentStateChangeRef = useRef(onAgentStateChange)
  const onUserSpeakingRef = useRef(onUserSpeaking)
  const onTranscriptRef = useRef(onTranscript)
  onAgentStateChangeRef.current = onAgentStateChange
  onUserSpeakingRef.current = onUserSpeaking
  onTranscriptRef.current = onTranscript

  useEffect(() => {
    if (!room) return

    // ----- 1. Active speakers → orb color (debounced for smooth transitions) -----
    const IDLE_DELAY = 800 // ms before switching back to idle

    const handleActiveSpeakers = (speakers: any[]) => {
      const ids: string[] = speakers.map((p: any) => p.identity || '')
      const agentSpeaking = ids.some(id => id.startsWith('agent'))
      const localId = room.localParticipant?.identity || ''
      const userSpeakingNow = ids.includes(localId)

      // Agent state: immediately show 'speaking', delay 'idle'
      if (agentSpeaking) {
        if (agentIdleTimeout.current) {
          clearTimeout(agentIdleTimeout.current)
          agentIdleTimeout.current = null
        }
        onAgentStateChangeRef.current({ state: 'speaking' })
      } else if (!agentIdleTimeout.current) {
        agentIdleTimeout.current = setTimeout(() => {
          onAgentStateChangeRef.current({ state: 'idle' })
          agentIdleTimeout.current = null
        }, IDLE_DELAY)
      }

      // User speaking: immediately show, delay hide
      if (userSpeakingNow) {
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

    // ----- 2. Transcription received (built-in livekit-agents transcription) -----
    // livekit-agents v1.x automatically publishes transcription segments.
    // This is the most reliable way to get agent (and user) text.
    const handleTranscription = (segments: any[], participant?: any) => {
        const isAgent = !participant || (participant.identity || '').startsWith('agent')
        for (const seg of segments) {
          const text = String(seg?.text || seg?.content || '').trim()
          if (!text) continue

          const speaker = isAgent ? 'agent' : 'user'
          const segmentId = seg?.id ? String(seg.id) : null
          const segmentKey = segmentId ? `${speaker}:${segmentId}` : `fallback:${speaker}`
          const isFinal = Boolean(seg?.final)

          const existingTranscriptId = partialTranscriptIdsBySegmentKey.current.get(segmentKey)
          if (existingTranscriptId) {
            onTranscriptRef.current({
              id: isFinal && segmentId ? segmentId : existingTranscriptId,
              text,
              speaker,
            })
            if (isFinal) {
              partialTranscriptIdsBySegmentKey.current.delete(segmentKey)
              if (segmentId) processedSegmentIds.current.add(segmentId)
            }
            continue
          }

          if (!isFinal) {
            const partialId = `partial:${segmentKey}`
            partialTranscriptIdsBySegmentKey.current.set(segmentKey, partialId)
            onTranscriptRef.current({ id: partialId, text, speaker })
            continue
          }

        if (segmentId && processedSegmentIds.current.has(segmentId)) continue
        if (segmentId) processedSegmentIds.current.add(segmentId)
        voiceLogger.log('Transcription:', speaker, text)
        onTranscriptRef.current({
          id: segmentKey ? `final:${segmentKey}` : undefined,
          text,
          speaker,
        })
        if (segmentKey) {
          partialTranscriptIdsBySegmentKey.current.delete(segmentKey)
        }
      }
    }
    room.on('transcriptionReceived' as any, handleTranscription)

    return () => {
      room.off(RoomEvent.ActiveSpeakersChanged, handleActiveSpeakers)
      room.off('transcriptionReceived' as any, handleTranscription)
      if (agentIdleTimeout.current) clearTimeout(agentIdleTimeout.current)
      if (userIdleTimeout.current) clearTimeout(userIdleTimeout.current)
    }
  }, [room]) // Only re-run when room changes, not on every callback identity change

  return null
}

/**
 * Voice Call Button
 */
interface VoiceCallButtonProps {
  agentId: string
  agentName: string
  onSessionCreated?: (sessionId: string) => void
  onSessionEnded?: () => void
}

export function VoiceCallButton({
  agentId,
  agentName,
  onSessionCreated,
  onSessionEnded,
}: VoiceCallButtonProps) {
  const [session, setSession] = useState<{
    id: string
    serverUrl: string
    token: string
    roomName: string
  } | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const [micPermissionStatus, setMicPermissionStatus] = useState<'unknown' | 'granted' | 'denied' | 'prompt'>('unknown')
  const [error, setError] = useState<string | null>(null)

  const createSession = useCreateVoiceSession()
  const endSession = useEndVoiceSession()
  const voiceHealth = useVoiceHealth()
  const isVoiceUnavailable = voiceHealth.data && !voiceHealth.data.voice_available

  useEffect(() => {
    let permissionStatus: PermissionStatus | null = null
    let onChange: (() => void) | null = null

    const checkMicPermission = async () => {
      try {
        if (navigator.permissions && navigator.permissions.query) {
          permissionStatus = await navigator.permissions.query({ name: 'microphone' as PermissionName })
          setMicPermissionStatus(permissionStatus.state as 'granted' | 'denied' | 'prompt')
          onChange = () => {
            setMicPermissionStatus(permissionStatus!.state as 'granted' | 'denied' | 'prompt')
          }
          permissionStatus.addEventListener('change', onChange)
        }
      } catch (error) {
        voiceLogger.debug('Permissions API not supported')
      }
    }
    checkMicPermission()

    return () => {
      if (permissionStatus && onChange) {
        permissionStatus.removeEventListener('change', onChange)
      }
    }
  }, [])

  const requestMicrophoneAccess = useCallback(async (): Promise<boolean> => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach(track => track.stop())
      setMicPermissionStatus('granted')
      return true
    } catch (error) {
      voiceLogger.error('Microphone access denied:', error)
      setMicPermissionStatus('denied')
      return false
    }
  }, [])

  // Fix 4: Clean up session on tab close / navigation away
  useEffect(() => {
    const handleTabClose = () => {
      if (session) {
        // Use fetch with keepalive instead of sendBeacon so we can issue
        // a DELETE (sendBeacon only sends POST, which would 405).
        // Auth is via httpOnly cookie (credentials: include) + in-memory
        // Bearer token from the Zustand auth store.
        const url = `${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/voice-sessions/${session.id}`
        fetch(url, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: 'tab_closed' }),
          credentials: 'include',
          keepalive: true,
        }).catch(() => {
          // Best-effort — ignore errors during unload
        })
      }
    }
    window.addEventListener('beforeunload', handleTabClose)
    return () => window.removeEventListener('beforeunload', handleTabClose)
  }, [session])

  const handleStartCall = useCallback(async () => {
    if (isStarting || createSession.isPending) return

    setIsStarting(true)
    let createdSessionId: string | null = null
    try {
      if (micPermissionStatus !== 'granted') {
        const hasPermission = await requestMicrophoneAccess()
        if (!hasPermission) {
          toast.error('Microphone access is required to make voice calls.')
          setIsStarting(false)
          return
        }
      }

      const response = await createSession.mutateAsync({ agent_id: agentId })
      createdSessionId = response.id

      sessionCreatedAtRef.current = Date.now()
      setSession({
        id: response.id,
        serverUrl: response.connection_url || '',
        token: response.access_token || '',
        roomName: response.room_name || response.id,
      })

      onSessionCreated?.(response.id)
    } catch (err: any) {
      voiceLogger.error('Failed to start call:', err)

      // Fix 3: If we got a session ID but connection failed, clean it up
      if (createdSessionId) {
        try {
          await endSession.mutateAsync({
            sessionId: createdSessionId,
            request: { reason: 'connection_failed' },
          })
        } catch (cleanupErr) {
          voiceLogger.error('Failed to clean up orphaned session:', cleanupErr)
        }
      }

      const detail = err?.detail || err?.response?.data?.detail
      const statusCode = err?.status || err?.response?.status
      let errorMessage: string
      if (statusCode === 503) {
        errorMessage = detail || 'Voice agent is not available right now. Please try again later.'
      } else if (statusCode === 429) {
        errorMessage = detail || 'Too many active voice sessions. Please try again later.'
      } else {
        errorMessage = detail || err?.message || 'Failed to start call. Please try again.'
      }
      setError(errorMessage)
      voiceHealth.refetch()
    } finally {
      setIsStarting(false)
    }
  }, [agentId, createSession, endSession, isStarting, micPermissionStatus, onSessionCreated, requestMicrophoneAccess])

  // Track when session was created to ignore React Strict Mode unmount disconnects
  const sessionCreatedAtRef = useRef<number>(0)

  const handleEndCall = useCallback(async () => {
    // In React Strict Mode (dev), LiveKitRoom unmounts immediately after mount,
    // firing onDisconnected. Ignore disconnects within the first 2 seconds —
    // a real user action or agent timeout won't happen that fast.
    const elapsed = Date.now() - sessionCreatedAtRef.current
    if (elapsed < 2000) {
      voiceLogger.debug('Ignoring early disconnect (likely React Strict Mode)', { elapsed })
      return
    }
    if (session) {
      try {
        await endSession.mutateAsync({
          sessionId: session.id,
          request: { reason: 'user_ended' },
        })
      } catch (error) {
        voiceLogger.error('Failed to end session:', error)
      }
    }
    setSession(null)
    onSessionEnded?.()
  }, [endSession, onSessionEnded, session])

  if (session) {
    return (
      <VoiceCallRoom
        serverUrl={session.serverUrl}
        token={session.token}
        roomName={session.roomName}
        agentName={agentName}
        onDisconnect={handleEndCall}
      />
    )
  }

  const isLoading = isStarting || createSession.isPending

  return (
    <div className="space-y-3 w-full">
      {micPermissionStatus === 'denied' && (
        <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-lg">
          <p className="font-medium">Microphone access denied</p>
          <p className="text-xs mt-1">
            Please enable microphone access in your browser settings.
          </p>
        </div>
      )}
      {error && (
        <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-lg">
          <p className="font-medium">Failed to start call</p>
          <p className="text-xs mt-1">{error}</p>
          <button onClick={() => setError(null)} className="text-xs mt-2 underline">Dismiss</button>
        </div>
      )}
      {isVoiceUnavailable && !error && (
        <div className="text-sm text-amber-600 bg-amber-50 dark:bg-amber-950/30 p-3 rounded-lg">
          <p className="font-medium">Voice agent unavailable</p>
          <p className="text-xs mt-1">
            The voice service is not ready. Please try again later.
          </p>
        </div>
      )}
      <Button
        onClick={() => { setError(null); handleStartCall() }}
        size="lg"
        className="w-full"
        disabled={isLoading || micPermissionStatus === 'denied' || !!isVoiceUnavailable}
      >
        {isLoading ? (
          <>
            <div className="mr-2 h-5 w-5 animate-spin rounded-full border-2 border-current border-t-transparent" />
            {micPermissionStatus !== 'granted' ? 'Requesting microphone...' : 'Starting call...'}
          </>
        ) : (
          <>
            <Phone className="mr-2 h-5 w-5" />
            Start Voice Call
          </>
        )}
      </Button>
    </div>
  )
}
