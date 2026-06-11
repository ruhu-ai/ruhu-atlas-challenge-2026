/**
 * Phase 2a-base — Voice picker component.
 *
 * Reusable voice selection UI: filterable grid of voices, audio
 * preview button per voice, single-select. Used inside PersonaTab's
 * Behaviour section (this PR) and will be reused by 2b's per-language
 * voice override UI.
 *
 * Production notes:
 *
 * - Catalog fetch goes through React Query so re-renders don't refetch.
 *   Filters change the query key so a filter switch re-fetches once.
 * - Audio preview uses a single shared `<audio>` element so clicking
 *   one preview cancels another in flight — prevents overlap.
 * - "Select" is a callback; the parent owns the persisted value and
 *   re-renders this component with the active selection highlighted.
 * - No business logic about budget caps lives here — that's a 2a-paid
 *   concern. Empty/error states are deliberately minimal.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pause, Play, Search, Sparkles, Trash2, User2 } from 'lucide-react'

import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import type {
  VoiceCatalogEntry,
  VoiceGender,
  VoiceLibraryFilters,
} from '@/types/agent-definition'
import { VoiceCloningWizard } from './VoiceCloningWizard'

export interface VoicePickerProps {
  /** Currently-selected voice_id; entry is highlighted when present in
   * the listed voices. Pass empty string to render with no selection. */
  selectedVoiceId: string
  onSelect: (voiceId: string, entry: VoiceCatalogEntry) => void
  /** Optional language filter — when 2b uses this picker per-language,
   * pass the BCP-47 tag here so only matching voices are listed. */
  defaultLanguage?: string
  /** Phase 2a-cloning — when set, the "Clone a custom voice" button
   * opens the wizard with this agent_id bound. When omitted, clones
   * are organization-wide. */
  agentId?: string | null
  /** Phase 2a-cloning — when false, the Clone button is hidden (e.g.
   * for users without admin role, or for places that reuse the picker
   * but shouldn't expose cloning). Default true. */
  enableCloning?: boolean
}

const GENDER_OPTIONS: ReadonlyArray<{ label: string; value: VoiceGender | 'all' }> = [
  { label: 'Any gender', value: 'all' },
  { label: 'Female', value: 'female' },
  { label: 'Male', value: 'male' },
  { label: 'Neutral', value: 'neutral' },
]

export function VoicePicker({
  selectedVoiceId,
  onSelect,
  defaultLanguage,
  agentId = null,
  enableCloning = true,
}: VoicePickerProps) {
  const queryClient = useQueryClient()
  const [language, setLanguage] = useState(defaultLanguage ?? '')
  const [gender, setGender] = useState<VoiceGender | 'all'>('all')
  const [accent, setAccent] = useState('')
  const [cloneWizardOpen, setCloneWizardOpen] = useState(false)

  const filters: VoiceLibraryFilters = useMemo(() => {
    const f: VoiceLibraryFilters = {}
    if (language.trim()) f.language = language.trim()
    if (gender !== 'all') f.gender = gender
    if (accent.trim()) f.accent = accent.trim()
    return f
  }, [language, gender, accent])

  const query = useQuery({
    queryKey: ['voice-library', filters],
    queryFn: () => agentDefinitionService.listVoiceLibrary(filters),
    staleTime: 5 * 60 * 1000,
  })

  // Single shared <audio> element — click on a preview button stops
  // any in-flight preview before starting the new one.
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [previewingId, setPreviewingId] = useState<string | null>(null)

  useEffect(() => {
    return () => {
      audioRef.current?.pause()
    }
  }, [])

  const handlePreview = (voiceId: string) => {
    if (previewingId === voiceId) {
      audioRef.current?.pause()
      setPreviewingId(null)
      return
    }
    if (audioRef.current) {
      audioRef.current.pause()
    }
    const url = agentDefinitionService.voicePreviewUrl(voiceId)
    const audio = new Audio(url)
    audio.addEventListener('ended', () => {
      setPreviewingId((current) => (current === voiceId ? null : current))
    })
    audio.addEventListener('error', () => {
      setPreviewingId(null)
    })
    audioRef.current = audio
    setPreviewingId(voiceId)
    void audio.play().catch(() => {
      setPreviewingId(null)
    })
  }

  const voices = query.data?.voices ?? []

  // Phase 2a-cloning — soft-delete a tenant clone. Optimistic-ish:
  // we invalidate the catalog after success so the deleted clone
  // disappears from the list. Errors surface as a generic toast (the
  // mutation owner — VoicePicker — decides UX; we keep it minimal).
  const deleteCloneMutation = useMutation({
    mutationFn: (cloneId: string) =>
      agentDefinitionService.deleteVoiceClone(cloneId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['voice-library'] })
    },
  })

  return (
    <div className="space-y-3" data-testid="voice-picker">
      {/* Clone CTA — only shown when caller opted in. Position above
          filters so authors discover it before scrolling the catalog. */}
      {enableCloning && (
        <div className="flex items-center justify-between rounded-md border border-dashed border-border/60 bg-muted/20 px-3 py-2">
          <div className="space-y-0.5 text-xs">
            <p className="font-medium text-foreground">Custom voices</p>
            <p className="text-muted-foreground">
              Clone a voice from a 10-second consent recording. Custom voices
              appear in this picker alongside the catalog.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCloneWizardOpen(true)}
            data-testid="voice-picker-clone-button"
          >
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            Clone a voice
          </Button>
        </div>
      )}

      <VoiceCloningWizard
        open={cloneWizardOpen}
        onClose={() => setCloneWizardOpen(false)}
        onCloned={() => {
          void queryClient.invalidateQueries({ queryKey: ['voice-library'] })
        }}
        agentId={agentId}
      />

      {/* Filters */}
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            placeholder="Language (e.g. en, en-GB)"
            className="pl-8"
            data-testid="voice-picker-language"
          />
        </div>
        <Select
          value={gender}
          onValueChange={(v) => setGender(v as VoiceGender | 'all')}
        >
          <SelectTrigger data-testid="voice-picker-gender">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {GENDER_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={accent}
          onChange={(e) => setAccent(e.target.value)}
          placeholder="Accent (e.g. British)"
          data-testid="voice-picker-accent"
        />
      </div>

      {/* Voice grid */}
      {query.isLoading ? (
        <p className="text-xs text-muted-foreground">Loading voices…</p>
      ) : query.isError ? (
        <p className="text-xs text-destructive">
          Failed to load voices. Refresh to try again.
        </p>
      ) : voices.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No voices match these filters.
        </p>
      ) : (
        <ul
          className="grid grid-cols-1 gap-2 md:grid-cols-2"
          data-testid="voice-picker-list"
        >
          {voices.map((entry) => {
            const isSelected = entry.voice_id === selectedVoiceId
            const isPreviewing = previewingId === entry.voice_id
            // Cloned voices come back from the catalog merge with
            // provider="<base>_clone" so we can render the badge +
            // delete action without inspecting metadata.
            const isClone = entry.provider.endsWith('_clone')
            return (
              <li
                key={entry.voice_id}
                className={`rounded-md border px-3 py-2.5 transition-colors ${
                  isSelected
                    ? 'border-primary bg-primary/5'
                    : 'border-border/60 bg-background hover:border-border'
                }`}
                data-testid={`voice-picker-entry-${entry.voice_id}`}
                data-selected={isSelected ? 'true' : 'false'}
                data-clone={isClone ? 'true' : 'false'}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 space-y-0.5">
                    <div className="flex items-center gap-1.5">
                      {isClone ? (
                        <Sparkles className="h-3.5 w-3.5 shrink-0 text-amber-500" />
                      ) : (
                        <User2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      )}
                      <p className="truncate text-sm font-medium">{entry.display_name}</p>
                      {isClone && (
                        <Badge variant="secondary" className="h-5 bg-amber-500/10 text-[10px] text-amber-700">
                          Cloned
                        </Badge>
                      )}
                      {isSelected && (
                        <Badge variant="secondary" className="h-5 text-[10px]">
                          Selected
                        </Badge>
                      )}
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      {[entry.language, entry.gender, entry.accent]
                        .filter(Boolean)
                        .join(' · ')}
                    </p>
                    {entry.description && (
                      <p className="line-clamp-2 text-[11px] text-muted-foreground">
                        {entry.description}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col gap-1.5">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handlePreview(entry.voice_id)}
                      aria-label={`${isPreviewing ? 'Stop' : 'Play'} preview for ${entry.display_name}`}
                      className="h-7 px-2"
                      data-testid={`voice-picker-preview-${entry.voice_id}`}
                    >
                      {isPreviewing ? (
                        <Pause className="h-3.5 w-3.5" />
                      ) : (
                        <Play className="h-3.5 w-3.5" />
                      )}
                    </Button>
                    {!isSelected && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onSelect(entry.voice_id, entry)}
                        className="h-7 px-2 text-xs"
                        data-testid={`voice-picker-select-${entry.voice_id}`}
                      >
                        Select
                      </Button>
                    )}
                    {isClone && !isSelected && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => deleteCloneMutation.mutate(entry.voice_id)}
                        disabled={deleteCloneMutation.isPending}
                        aria-label={`Delete clone ${entry.display_name}`}
                        className="h-7 px-2 text-muted-foreground hover:text-destructive"
                        data-testid={`voice-picker-delete-${entry.voice_id}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
