import { useCallback, useEffect, useRef, useState } from 'react'
import { Room, RoomEvent, Track } from 'livekit-client'
import { useWidgetContext } from './WidgetProvider'
import type { CallState, WidgetAttachment } from './widget-types'
import { shouldAppendTranscriptByFingerprint } from './voiceTranscriptDeduper'

export interface VoiceTranscript {
  id: string
  text: string
  speaker: 'user' | 'agent'
  timestamp: Date
  isFinal: boolean
}

interface UseWidgetVoice {
  callState: CallState
  isMuted: boolean
  isSpeakerMuted: boolean
  audioLevel: number
  callDuration: number
  isAgentSpeaking: boolean
  transcripts: VoiceTranscript[]
  voiceError: string | null
  startCall: () => Promise<void>
  endCall: () => Promise<void>
  sendText: (text: string, options?: { attachmentIds?: string[] }) => Promise<void>
  sendAttachmentFile: (file: File, attachment: WidgetAttachment) => Promise<void>
  toggleMute: () => void
  toggleSpeaker: () => void
}

export function useWidgetVoice(): UseWidgetVoice {
  const { startVoice, endVoice } = useWidgetContext()
  const [callState, setCallState] = useState<CallState>('idle')
  const [isMuted, setIsMuted] = useState(false)
  const [isSpeakerMuted, setIsSpeakerMuted] = useState(false)
  const [audioLevel, setAudioLevel] = useState(0)
  const [callDuration, setCallDuration] = useState(0)
  const [isAgentSpeaking, setIsAgentSpeaking] = useState(false)
  const [transcripts, setTranscripts] = useState<VoiceTranscript[]>([])
  const [voiceError, setVoiceError] = useState<string | null>(null)

  const roomRef = useRef<Room | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval>>()
  const seenTranscriptIdsRef = useRef<Set<string>>(new Set())
  const seenTranscriptFingerprintsRef = useRef<Map<string, number>>(new Map())
  const partialTranscriptIdsBySegmentKeyRef = useRef<Map<string, string>>(new Map())
  // Ref so that TrackSubscribed callbacks always read the current muted value
  // without the startCall callback needing to re-close over the state variable.
  const isSpeakerMutedRef = useRef(isSpeakerMuted)

  useEffect(() => {
    isSpeakerMutedRef.current = isSpeakerMuted
  }, [isSpeakerMuted])

  useEffect(() => {
    if (callState === 'active') {
      timerRef.current = setInterval(() => setCallDuration((value) => value + 1), 1000)
    } else {
      setCallDuration(0)
      if (timerRef.current) clearInterval(timerRef.current)
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [callState])

  // Cleanup on unmount: disconnect room and purge any audio elements added to the DOM.
  useEffect(() => {
    return () => {
      if (roomRef.current) {
        roomRef.current.disconnect()
        roomRef.current = null
      }
      document.querySelectorAll<HTMLAudioElement>('[id^="ruhu-widget-audio-"]').forEach((el) => el.remove())
    }
  }, [])

  const startCall = useCallback(async () => {
    setCallState('connecting')
    setVoiceError(null)
    seenTranscriptIdsRef.current.clear()
    seenTranscriptFingerprintsRef.current.clear()
    partialTranscriptIdsBySegmentKeyRef.current.clear()
    setTranscripts([])

    try {
      const transport = await startVoice()

      // Static import at top of file — no dynamic import latency on first call.
      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      roomRef.current = room

      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind !== Track.Kind.Audio) return
        const audioEl = track.attach()
        audioEl.id = `ruhu-widget-audio-${track.sid}`
        audioEl.autoplay = true
        ;(audioEl as any).playsInline = true
        // Use the ref so newly subscribed tracks always pick up the current muted state.
        audioEl.muted = isSpeakerMutedRef.current
        document.body.appendChild(audioEl)
        void audioEl.play().catch(() => undefined)
      })

      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => el.remove())
      })

      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        if (speakers.length === 0) {
          setAudioLevel(0)
          setIsAgentSpeaking(false)
          return
        }
        setAudioLevel(Math.max(...speakers.map((speaker) => speaker.audioLevel || 0)) * 100)
        setIsAgentSpeaking(
          speakers.some((speaker) => speaker.sid !== room.localParticipant?.sid),
        )
      })

      // Use the typed RoomEvent enum — RoomEvent.TranscriptionReceived was added in
      // livekit-client v2.x and is the correct event for speech-to-text segments.
      room.on(RoomEvent.TranscriptionReceived, (segments: any[], participant?: any) => {
        // Any participant that is not the local user is the agent.
        const speaker = !participant || participant.sid !== room.localParticipant?.sid ? 'agent' : 'user'
        for (const segment of segments) {
          const text = String(segment?.text || segment?.content || '').trim()
          if (!text) continue
          const segmentId = segment?.id ? String(segment.id) : null
          const segmentKey = segmentId ? `${speaker}:${segmentId}` : `fallback:${speaker}`
          const isFinal = Boolean(segment?.final)

          const partialId = partialTranscriptIdsBySegmentKeyRef.current.get(segmentKey)
          if (partialId) {
            setTranscripts((prev) => prev.map((item) => (
              item.id === partialId
                ? { ...item, text, isFinal, timestamp: new Date() }
                : item
            )))
            if (isFinal) {
              partialTranscriptIdsBySegmentKeyRef.current.delete(segmentKey)
              if (segmentId) seenTranscriptIdsRef.current.add(segmentId)
            }
            if (!isFinal && segmentId) {
              seenTranscriptIdsRef.current.delete(segmentId)
            }
            continue
          }

          if (!isFinal) {
            const newPartialId = `partial:${segmentKey}`
            partialTranscriptIdsBySegmentKeyRef.current.set(segmentKey, newPartialId)
            setTranscripts((prev) => [...prev, {
              id: newPartialId,
              text,
              speaker,
              timestamp: new Date(),
              isFinal: false,
            }])
            continue
          }

          if (segmentId && seenTranscriptIdsRef.current.has(segmentId)) continue
          const nowMs = Date.now()
          if (!shouldAppendTranscriptByFingerprint(seenTranscriptFingerprintsRef.current, { speaker, text, nowMs })) {
            continue
          }
          if (segmentId) seenTranscriptIdsRef.current.add(segmentId)
          setTranscripts((prev) => [...prev, {
            id: segmentId || `${Date.now()}-${Math.random()}`,
            text,
            speaker,
            timestamp: new Date(),
            isFinal: true,
          }])
        }
      })

      room.on(RoomEvent.DataReceived, (payload: Uint8Array, _participant: unknown, _kind: unknown, topic?: string) => {
        let message: { type?: string; message?: string } | null = null
        try {
          message = JSON.parse(new TextDecoder().decode(payload)) as { type?: string; message?: string }
        } catch {
          message = null
        }
        if (topic === 'ruhu_error' || message?.type === 'error') {
          setVoiceError(message?.message || 'A voice error occurred.')
        }
      })

      room.on(RoomEvent.Disconnected, () => {
        // Clean up any audio elements left in the DOM when the room closes.
        document.querySelectorAll<HTMLAudioElement>('[id^="ruhu-widget-audio-"]').forEach((el) => el.remove())
        setCallState('ended')
        setTimeout(() => setCallState('idle'), 1500)
      })

      await room.connect(transport.url, transport.token)
      // startAudio() is required to unblock audio in browsers with autoplay policy.
      try {
        await room.startAudio()
      } catch {
        // Autoplay was blocked — audio will play once the user interacts.
      }
      await room.localParticipant.setMicrophoneEnabled(true)
      setCallState('active')
      setIsMuted(false)
    } catch (error) {
      const detail = (error as { message?: string })?.message || 'Failed to start voice call.'
      setVoiceError(detail)
      setCallState('idle')
      if (roomRef.current) {
        roomRef.current.disconnect()
        roomRef.current = null
      }
      document.querySelectorAll<HTMLAudioElement>('[id^="ruhu-widget-audio-"]').forEach((el) => el.remove())
    }
  }, [startVoice])

  const endCall = useCallback(async () => {
    setCallState('ended')
    setAudioLevel(0)
    setIsAgentSpeaking(false)
    if (roomRef.current) {
      roomRef.current.disconnect()
      roomRef.current = null
    }
    document.querySelectorAll<HTMLAudioElement>('[id^="ruhu-widget-audio-"]').forEach((el) => el.remove())
    await endVoice()
    setTimeout(() => setCallState('idle'), 1500)
  }, [endVoice])

  const sendText = useCallback(async (text: string, options?: { attachmentIds?: string[] }) => {
    const room = roomRef.current
    if (!room?.localParticipant || callState !== 'active') {
      throw new Error('Voice call is not active')
    }
    await room.localParticipant.sendText(text.trim(), {
      topic: 'lk.chat',
      attributes: options?.attachmentIds?.length
        ? { attachment_ids: JSON.stringify(options.attachmentIds) }
        : undefined,
    })
  }, [callState])

  const sendAttachmentFile = useCallback(async (file: File, attachment: WidgetAttachment) => {
    const room = roomRef.current
    if (!room?.localParticipant || callState !== 'active') {
      throw new Error('Voice call is not active')
    }
    if (typeof room.localParticipant.sendFile !== 'function') {
      throw new Error('File transfer is not available for this voice session')
    }
    await (room.localParticipant as any).sendFile(file, {
      topic: 'lk.attachment',
      attributes: {
        attachment_id: attachment.attachment_id,
        filename: attachment.filename || file.name,
        mime_type: attachment.content_type || file.type || 'application/octet-stream',
      },
    })
  }, [callState])

  const toggleMute = useCallback(() => {
    setIsMuted((current) => {
      const next = !current
      if (roomRef.current?.localParticipant) {
        roomRef.current.localParticipant.setMicrophoneEnabled(!next)
      }
      return next
    })
  }, [])

  const toggleSpeaker = useCallback(() => {
    setIsSpeakerMuted((current) => {
      const next = !current
      isSpeakerMutedRef.current = next
      document.querySelectorAll<HTMLAudioElement>('[id^="ruhu-widget-audio-"]').forEach((element) => {
        element.muted = next
      })
      return next
    })
  }, [])

  return {
    callState,
    isMuted,
    isSpeakerMuted,
    audioLevel,
    callDuration,
    isAgentSpeaking,
    transcripts,
    voiceError,
    startCall,
    endCall,
    sendText,
    sendAttachmentFile,
    toggleMute,
    toggleSpeaker,
  }
}
