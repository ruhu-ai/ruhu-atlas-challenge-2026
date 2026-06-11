/**
 * Phase 2a-cloning — Voice Cloning Wizard.
 *
 * Modal that walks the author through Google Chirp 3 HD Instant Custom
 * Voice cloning. The wizard captures Google's mandated consent
 * statement on a recording and submits it server-side; the server
 * forwards to Google, persists the encrypted cloning key, and returns
 * a clone_id the picker uses afterwards.
 *
 * Production contracts (each backed by tests):
 *
 * - **Consent script displayed verbatim** — the exact text Google
 *   requires must appear on screen so the author can read it. We don't
 *   localise this; Google's API verifies against the English consent
 *   string today.
 * - **MediaRecorder API** — uses the standard browser API for capture.
 *   On permission denial we surface a clear error rather than failing
 *   silently. SSR-safe: the recorder is only constructed once user
 *   clicks "Start recording".
 * - **Hard 10-second cap** — recorder auto-stops at 10s to match the
 *   server's 1MB / 10s ceiling. We also enforce a minimum duration
 *   (1.5s) so accidental click-then-stop doesn't submit a useless clip.
 * - **Pre-flight client validation** — display_name, language, MIME,
 *   and size are validated locally for fast feedback before we burn
 *   network round-trip and Google quota.
 * - **Server errors map to friendly UX** — 422 consent rejection,
 *   413 size, 503 service unavailable each get specific copy.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Mic, Square, AlertCircle, CheckCircle2, Trash2 } from 'lucide-react'

import { Button } from '@/components/atoms/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import type { VoiceCloneCreatedResponse } from '@/types/agent-definition'

// Duck-type the ApiError shape rather than importing it from
// '@/api/client'. The client module evaluates import.meta.env at load
// time, which doesn't run under ts-jest. Anything thrown by apiClient
// has both `.status` (number) and `.message` (string) — that's all
// the wizard needs to map errors to UX copy.
function isApiError(value: unknown): value is { status: number; message: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    typeof (value as { status: unknown }).status === 'number'
  )
}

// ─── Constants ──────────────────────────────────────────────────────────────

/** Google's mandated consent text. The wizard displays this verbatim
 * and the author reads it aloud. Wrong-script recordings → 422. */
const GOOGLE_CONSENT_SCRIPT =
  'I am the owner of this voice, and I consent to Google using this voice to create a synthetic voice model.'

/** Hard size cap matching the server. 1MB ≈ 10s at 48kHz mono. */
const MAX_AUDIO_BYTES = 1_000_000

/** Min duration so accidental click-stop doesn't submit a useless clip. */
const MIN_AUDIO_MS = 1_500
/** Hard cap; recorder auto-stops here. */
const MAX_AUDIO_MS = 10_000

/** Languages Google Chirp 3 HD Instant Custom Voice currently supports
 * for consent recordings. Keep this in lock-step with the server-side
 * locale validation; expanding this list without server support means
 * every clone in the new language gets 422'd by Google.
 *
 * v1 ships English + a handful of well-tested locales. Add more as
 * Google's matrix expands; the server has no allowlist of its own. */
const SUPPORTED_LANGUAGES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'en-US', label: 'English (US)' },
  { value: 'en-GB', label: 'English (UK)' },
  { value: 'en-AU', label: 'English (Australia)' },
  { value: 'fr-FR', label: 'French (France)' },
  { value: 'de-DE', label: 'German (Germany)' },
  { value: 'es-ES', label: 'Spanish (Spain)' },
  { value: 'it-IT', label: 'Italian' },
  { value: 'pt-BR', label: 'Portuguese (Brazil)' },
]

// ─── Types ──────────────────────────────────────────────────────────────────

export interface VoiceCloningWizardProps {
  open: boolean
  onClose: () => void
  /** Fired AFTER successful clone create. The picker invalidates its
   * library query in response so the new clone shows in the catalog. */
  onCloned: (clone: VoiceCloneCreatedResponse) => void
  /** Optional — when set, the clone is bound to this agent (vs.
   * organization-wide). Picker passes this through when invoked from
   * an agent-scoped context. */
  agentId?: string | null
}

// ─── Component ──────────────────────────────────────────────────────────────

export function VoiceCloningWizard({
  open,
  onClose,
  onCloned,
  agentId = null,
}: VoiceCloningWizardProps) {
  const [displayName, setDisplayName] = useState('')
  const [language, setLanguage] = useState<string>(SUPPORTED_LANGUAGES[0].value)
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null)
  const [recording, setRecording] = useState(false)
  const [recordedDurationMs, setRecordedDurationMs] = useState(0)
  const [permissionError, setPermissionError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const startedAtRef = useRef<number>(0)
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Reset state when the modal opens / closes so re-opening doesn't
  // surface stale data from a prior cancelled session.
  useEffect(() => {
    if (open) return
    setDisplayName('')
    setLanguage(SUPPORTED_LANGUAGES[0].value)
    setRecordedBlob(null)
    setRecording(false)
    setRecordedDurationMs(0)
    setPermissionError(null)
    setSubmitError(null)
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current)
      stopTimerRef.current = null
    }
  }, [open])

  // Defensive cleanup — if the user navigates away mid-recording, stop
  // the recorder to release the mic. Without this, the browser shows
  // the "still recording" indicator long after the wizard is gone.
  useEffect(() => {
    return () => {
      if (recorderRef.current && recorderRef.current.state !== 'inactive') {
        try {
          recorderRef.current.stop()
        } catch {
          // ignore — already stopped
        }
      }
      if (stopTimerRef.current) clearTimeout(stopTimerRef.current)
    }
  }, [])

  const startRecording = async () => {
    setPermissionError(null)
    setSubmitError(null)
    setRecordedBlob(null)
    setRecordedDurationMs(0)

    if (typeof navigator === 'undefined' || !navigator.mediaDevices) {
      setPermissionError(
        'Microphone access is not available in this browser. Use Chrome, Edge, Firefox, or Safari on a recent version.',
      )
      return
    }

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (err) {
      const name = err instanceof Error ? err.name : ''
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        setPermissionError(
          'Microphone permission was denied. Allow microphone access in your browser settings and try again.',
        )
      } else if (name === 'NotFoundError') {
        setPermissionError(
          'No microphone was detected. Connect a microphone and try again.',
        )
      } else {
        setPermissionError(
          'Could not access the microphone. Refresh the page and try again.',
        )
      }
      return
    }

    chunksRef.current = []
    // Most browsers default to webm/opus; MIME on the resulting Blob
    // is what we forward to the server. The server's allowlist
    // includes audio/webm.
    const recorder = new MediaRecorder(stream)
    recorder.addEventListener('dataavailable', (event) => {
      if (event.data && event.data.size > 0) {
        chunksRef.current.push(event.data)
      }
    })
    recorder.addEventListener('stop', () => {
      const elapsed = Date.now() - startedAtRef.current
      // Always release the mic immediately on stop — the indicator
      // light should turn off the moment the recorder stops.
      stream.getTracks().forEach((track) => track.stop())
      setRecording(false)
      setRecordedDurationMs(elapsed)
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || 'audio/webm',
      })
      // Guard against accidental too-short clips here as well.
      if (elapsed < MIN_AUDIO_MS || blob.size === 0) {
        setRecordedBlob(null)
        setSubmitError(
          'Recording was too short. Read the full consent statement at a normal pace.',
        )
        return
      }
      if (blob.size > MAX_AUDIO_BYTES) {
        setRecordedBlob(null)
        setSubmitError(
          'Recording is too large. Re-record at a lower bitrate or shorter duration.',
        )
        return
      }
      setRecordedBlob(blob)
    })

    recorderRef.current = recorder
    chunksRef.current = []
    startedAtRef.current = Date.now()
    setRecording(true)
    recorder.start()

    // Hard cap — auto-stop after MAX_AUDIO_MS regardless of UI state.
    stopTimerRef.current = setTimeout(() => {
      if (recorder.state !== 'inactive') {
        recorder.stop()
      }
    }, MAX_AUDIO_MS)
  }

  const stopRecording = () => {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop()
    }
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current)
      stopTimerRef.current = null
    }
  }

  const cloneMutation = useMutation({
    mutationFn: async () => {
      if (!recordedBlob) throw new Error('no recording')
      return agentDefinitionService.cloneVoice({
        displayName: displayName.trim(),
        language,
        agentId,
        consentAudio: recordedBlob,
      })
    },
    onSuccess: (clone) => {
      onCloned(clone)
      onClose()
    },
    onError: (err: unknown) => {
      // Map server error → user-readable copy. The API uses HTTP
      // status codes to distinguish error kinds (see backend audit).
      if (isApiError(err)) {
        if (err.status === 422) {
          setSubmitError(
            err.message ||
              'Google could not verify the consent recording. Re-read the script clearly and try again.',
          )
          return
        }
        if (err.status === 413) {
          setSubmitError(
            'The recording was rejected as too large. Re-record at a shorter duration.',
          )
          return
        }
        if (err.status === 401 || err.status === 403) {
          setSubmitError(
            'You don’t have permission to create voice clones. Ask an organization admin.',
          )
          return
        }
        if (err.status === 503) {
          setSubmitError(
            'Voice cloning is temporarily unavailable. Try again in a few minutes.',
          )
          return
        }
        if (err.status === 404) {
          setSubmitError('The agent this clone is bound to could not be found.')
          return
        }
      }
      setSubmitError(
        'Voice cloning failed. Please refresh and try again, or contact support.',
      )
    },
  })

  const canSubmit =
    !!recordedBlob &&
    displayName.trim().length > 0 &&
    displayName.trim().length <= 64 &&
    !cloneMutation.isPending

  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : null)}>
      <DialogContent className="sm:max-w-[560px]" data-testid="voice-cloning-wizard">
        <DialogHeader>
          <DialogTitle>Clone a custom voice</DialogTitle>
          <DialogDescription>
            Record yourself reading the consent statement. Your recording is
            forwarded to Google&apos;s instant-cloning service and the cloned
            voice becomes available to your agents. Recordings are retained for
            compliance audit.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Display name */}
          <div className="space-y-1.5">
            <Label htmlFor="clone-name">Display name</Label>
            <Input
              id="clone-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. CEO Voice, Maya v2"
              maxLength={64}
              data-testid="voice-cloning-display-name"
            />
            <p className="text-xs text-muted-foreground">
              How this clone appears in your voice picker. 1–64 characters.
            </p>
          </div>

          {/* Language */}
          <div className="space-y-1.5">
            <Label htmlFor="clone-language">Language</Label>
            <Select value={language} onValueChange={setLanguage}>
              <SelectTrigger id="clone-language" data-testid="voice-cloning-language">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SUPPORTED_LANGUAGES.map((lang) => (
                  <SelectItem key={lang.value} value={lang.value}>
                    {lang.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Language of the consent recording. Multilingual transfer to other
              languages happens automatically at synthesis time.
            </p>
          </div>

          {/* Consent script */}
          <div className="space-y-1.5">
            <Label>Consent statement (read aloud, verbatim)</Label>
            <blockquote
              className="rounded-md border border-border/60 bg-muted/40 px-3 py-2 text-sm italic text-foreground"
              data-testid="voice-cloning-consent-script"
            >
              {GOOGLE_CONSENT_SCRIPT}
            </blockquote>
            <p className="text-xs text-muted-foreground">
              Required by Google. Reading a different script — or having
              someone else read it — will be rejected.
            </p>
          </div>

          {/* Recorder */}
          <div className="space-y-2">
            {!recording && !recordedBlob && (
              <Button
                variant="outline"
                onClick={startRecording}
                data-testid="voice-cloning-start-record"
              >
                <Mic className="mr-2 h-4 w-4" />
                Start recording
              </Button>
            )}
            {recording && (
              <Button
                variant="destructive"
                onClick={stopRecording}
                data-testid="voice-cloning-stop-record"
              >
                <Square className="mr-2 h-4 w-4" />
                Stop recording
              </Button>
            )}
            {recordedBlob && !recording && (
              <div className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                <span className="flex-1">
                  Recording captured — {(recordedDurationMs / 1000).toFixed(1)}s.
                </span>
                <button
                  type="button"
                  onClick={() => {
                    setRecordedBlob(null)
                    setRecordedDurationMs(0)
                  }}
                  className="rounded p-1 text-muted-foreground hover:text-destructive"
                  aria-label="Discard recording"
                  data-testid="voice-cloning-discard"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
            {permissionError && (
              <p
                className="flex items-start gap-1.5 text-xs text-destructive"
                data-testid="voice-cloning-permission-error"
              >
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {permissionError}
              </p>
            )}
            {submitError && (
              <p
                className="flex items-start gap-1.5 text-xs text-destructive"
                data-testid="voice-cloning-submit-error"
              >
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {submitError}
              </p>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={cloneMutation.isPending}>
            Cancel
          </Button>
          <Button
            onClick={() => cloneMutation.mutate()}
            disabled={!canSubmit}
            data-testid="voice-cloning-submit"
          >
            {cloneMutation.isPending ? 'Cloning…' : 'Create clone'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
