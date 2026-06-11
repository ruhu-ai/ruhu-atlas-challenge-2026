/**
 * Persona Studio — agent canvas SidebarView.
 *
 * Two layers, mirroring the backend split (see `src/ruhu/persona.py` and
 * `docs/persona/phase-1.md`):
 *
 * • Identity (cosmetic) — persona_name, pronouns, avatar, role_title,
 *   greeting/signoff templates. Saves to `AgentSettings.persona` via
 *   `PATCH /agents/:id/settings`. Live-edit, applies immediately.
 *
 * • Behaviour (versioned) — formality, emoji_policy, restricted_topics.
 *   Saves to `AgentDocument.metadata.persona` via `PUT /agents/:id/agent-document`.
 *   Goes through draft → publish-review → publish.
 *
 * Phase 2c ships ``topic_enforcement`` as a 3-way policy
 * (off / log_only / block_and_retry). The Behaviour section surfaces
 * mode-specific captions so authors see exactly what the post-render
 * guard does in each mode. Default is `log_only` per [README 2-1].
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, ArrowRight, Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { Badge } from '@/components/atoms/badge'
import { Separator } from '@/components/atoms/separator'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import {
  DEFAULT_BEHAVIORAL_PERSONA,
  type AutoSwitchMode,
  type BehavioralPersona,
  type CosmeticPersona,
  type LanguageSwitchPolicy,
  type PersonaEmojiPolicy,
  type PersonaFormality,
  type PersonaPronouns,
  type TopicEnforcementPolicy,
  type UnsupportedLanguagePolicy,
} from '@/types/agent-definition'
import { VoicePicker } from './VoicePicker'

// Curated locale picker entries — keep in lock-step with
// ``src/ruhu/locale_defaults.py``. A new locale lands in BOTH places.
const LOCALE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'en-US', label: 'English (US)' },
  { value: 'en-GB', label: 'English (UK)' },
  { value: 'en-NG', label: 'English (Nigeria)' },
  { value: 'yo-NG', label: 'Yoruba (Nigeria)' },
  { value: 'ha-NG', label: 'Hausa (Nigeria)' },
  { value: 'ig-NG', label: 'Igbo (Nigeria)' },
  { value: 'sw-KE', label: 'Swahili (Kenya)' },
  { value: 'fr-FR', label: 'French (France)' },
]

const LANGUAGE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'en', label: 'English' },
  { value: 'yo', label: 'Yoruba' },
  { value: 'ha', label: 'Hausa' },
  { value: 'ig', label: 'Igbo' },
  { value: 'sw', label: 'Swahili' },
  { value: 'fr', label: 'French' },
  { value: 'es', label: 'Spanish' },
  { value: 'de', label: 'German' },
  { value: 'ar', label: 'Arabic' },
  { value: 'zh', label: 'Chinese' },
  { value: 'pt', label: 'Portuguese' },
]

// ─── Validation (mirrors `src/ruhu/persona.py`) ─────────────────────────────
//
// Keep these in lock-step with the backend validators. Backend is the source
// of truth — the frontend only short-circuits obvious errors before submit
// so users get fast feedback.

const PERSONA_NAME_MAX = 60
const ROLE_TITLE_MAX = 100
const PRONOUNS_CUSTOM_MAX = 30
const AVATAR_URL_MAX = 512
const GREETING_MAX = 500
const SIGNOFF_MAX = 300
const TOPIC_MAX = 200
const TOPIC_LIMIT = 10

const DANGEROUS_BASE = ['<', '>', '`', '{', '}']
const DANGEROUS_STRICT = [...DANGEROUS_BASE, '$']

function findControlChar(value: string): boolean {
  // Rejects C0 controls + DEL except \n. Tab and CR are explicitly disallowed
  // — this mirrors the backend (`src/ruhu/persona.py::_reject_dangerous`).
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i)
    if (code === 0x0a) continue
    if (code < 0x20 || code === 0x7f) return true
  }
  return false
}

function validatePersonaName(value: string): string | null {
  if (!value) return null
  if (value.length > PERSONA_NAME_MAX) return `Max ${PERSONA_NAME_MAX} characters`
  if (value !== value.trim()) return 'No leading or trailing whitespace'
  if (DANGEROUS_STRICT.some((ch) => value.includes(ch))) return 'Contains disallowed character'
  if (findControlChar(value) || value.includes('\n')) return 'Contains control characters'
  return null
}

function validateShortText(value: string, max: number): string | null {
  if (!value) return null
  if (value.length > max) return `Max ${max} characters`
  if (DANGEROUS_STRICT.some((ch) => value.includes(ch))) return 'Contains disallowed character'
  if (findControlChar(value) || value.includes('\n')) return 'Contains control characters'
  return null
}

function validateTemplate(value: string, max: number): string | null {
  if (!value) return null
  if (value.length > max) return `Max ${max} characters`
  if (DANGEROUS_BASE.some((ch) => value.includes(ch))) return 'Contains disallowed character'
  if (findControlChar(value) || value.includes('\n\n')) return 'No paragraph breaks'
  return null
}

function validateAvatarUrl(value: string): string | null {
  if (!value) return null
  if (value.length > AVATAR_URL_MAX) return `Max ${AVATAR_URL_MAX} characters`
  if (!value.startsWith('https://')) return 'Avatar URL must start with https://'
  return null
}

function validateRestrictedTopic(value: string): string | null {
  if (!value) return 'Topic must not be empty'
  if (value.length > TOPIC_MAX) return `Max ${TOPIC_MAX} characters`
  if (DANGEROUS_STRICT.some((ch) => value.includes(ch))) return 'Contains disallowed character'
  if (findControlChar(value) || value.includes('\n')) return 'Contains control characters'
  return null
}

// ─── Cosmetic editor state ───────────────────────────────────────────────────
//
// Backend treats empty-string-equivalent fields as `null`. Local form state
// uses empty strings for ergonomic input handling and converts on save.

interface CosmeticDraft {
  persona_name: string
  pronouns: PersonaPronouns | ''
  pronouns_custom: string
  avatar_url: string
  role_title: string
  greeting_template: string
  signoff_template: string
}

const EMPTY_COSMETIC: CosmeticDraft = {
  persona_name: '',
  pronouns: '',
  pronouns_custom: '',
  avatar_url: '',
  role_title: '',
  greeting_template: '',
  signoff_template: '',
}

function cosmeticToDraft(value: CosmeticPersona | null | undefined): CosmeticDraft {
  if (!value) return { ...EMPTY_COSMETIC }
  return {
    persona_name: value.persona_name ?? '',
    pronouns: (value.pronouns ?? '') as PersonaPronouns | '',
    pronouns_custom: value.pronouns_custom ?? '',
    avatar_url: value.avatar_url ?? '',
    role_title: value.role_title ?? '',
    greeting_template: value.greeting_template ?? '',
    signoff_template: value.signoff_template ?? '',
  }
}

function draftToCosmetic(draft: CosmeticDraft): CosmeticPersona | null {
  const name = draft.persona_name.trim()
  const allEmpty =
    !name &&
    !draft.pronouns &&
    !draft.pronouns_custom.trim() &&
    !draft.avatar_url.trim() &&
    !draft.role_title.trim() &&
    !draft.greeting_template.trim() &&
    !draft.signoff_template.trim()
  if (allEmpty) return null
  return {
    persona_name: name || null,
    pronouns: draft.pronouns ? (draft.pronouns as PersonaPronouns) : null,
    pronouns_custom:
      draft.pronouns === 'custom' && draft.pronouns_custom.trim()
        ? draft.pronouns_custom.trim()
        : null,
    avatar_url: draft.avatar_url.trim() || null,
    role_title: draft.role_title.trim() || null,
    greeting_template: draft.greeting_template.trim() || null,
    signoff_template: draft.signoff_template.trim() || null,
  }
}

function cosmeticErrors(draft: CosmeticDraft): Record<string, string> {
  const errors: Record<string, string> = {}
  const nameErr = validatePersonaName(draft.persona_name)
  if (nameErr) errors.persona_name = nameErr
  const roleErr = validateShortText(draft.role_title, ROLE_TITLE_MAX)
  if (roleErr) errors.role_title = roleErr
  const customErr = validateShortText(draft.pronouns_custom, PRONOUNS_CUSTOM_MAX)
  if (customErr) errors.pronouns_custom = customErr
  const avatarErr = validateAvatarUrl(draft.avatar_url)
  if (avatarErr) errors.avatar_url = avatarErr
  const greetingErr = validateTemplate(draft.greeting_template, GREETING_MAX)
  if (greetingErr) errors.greeting_template = greetingErr
  const signoffErr = validateTemplate(draft.signoff_template, SIGNOFF_MAX)
  if (signoffErr) errors.signoff_template = signoffErr
  if (draft.pronouns === 'custom' && !draft.pronouns_custom.trim()) {
    errors.pronouns_custom = 'Custom pronouns required when "Custom" is selected'
  }
  return errors
}

function cosmeticEqual(a: CosmeticDraft, b: CosmeticDraft): boolean {
  return (
    a.persona_name === b.persona_name &&
    a.pronouns === b.pronouns &&
    a.pronouns_custom === b.pronouns_custom &&
    a.avatar_url === b.avatar_url &&
    a.role_title === b.role_title &&
    a.greeting_template === b.greeting_template &&
    a.signoff_template === b.signoff_template
  )
}

// ─── Behavioural editor state ────────────────────────────────────────────────

function behavioralEqual(a: BehavioralPersona, b: BehavioralPersona): boolean {
  if (a.formality !== b.formality) return false
  if (a.emoji_policy !== b.emoji_policy) return false
  if (a.topic_enforcement !== b.topic_enforcement) return false
  if (a.voice_provider !== b.voice_provider) return false
  if (a.voice_id !== b.voice_id) return false
  if (a.voice_speed !== b.voice_speed) return false
  if (a.voice_monthly_budget_cents !== b.voice_monthly_budget_cents) return false
  if (a.restricted_topics.length !== b.restricted_topics.length) return false
  if (!a.restricted_topics.every((topic, i) => topic === b.restricted_topics[i])) return false
  // Phase 2b — language fields.
  if (a.primary_language !== b.primary_language) return false
  if (a.allowed_languages.length !== b.allowed_languages.length) return false
  if (!a.allowed_languages.every((lang, i) => lang === b.allowed_languages[i])) return false
  if (a.auto_switch_language !== b.auto_switch_language) return false
  if (a.language_switch_confidence_threshold !== b.language_switch_confidence_threshold) return false
  if (a.language_switch_min_chars !== b.language_switch_min_chars) return false
  if (a.language_switch_debounce_turns !== b.language_switch_debounce_turns) return false
  if (a.language_switch_policy !== b.language_switch_policy) return false
  if (a.unsupported_language_policy !== b.unsupported_language_policy) return false
  if (a.locale_code !== b.locale_code) return false
  if (a.cultural_calendar_enabled !== b.cultural_calendar_enabled) return false
  // Voice override map equality (key order doesn't matter).
  const aKeys = Object.keys(a.voice_id_overrides)
  const bKeys = Object.keys(b.voice_id_overrides)
  if (aKeys.length !== bKeys.length) return false
  for (const k of aKeys) {
    if (a.voice_id_overrides[k] !== b.voice_id_overrides[k]) return false
  }
  return true
}

// ─── Component ───────────────────────────────────────────────────────────────

export interface PersonaTabProps {
  agentId: string
}

export function PersonaTab({ agentId }: PersonaTabProps) {
  const queryClient = useQueryClient()

  const settingsQuery = useQuery({
    queryKey: ['agent-settings', agentId],
    queryFn: () => agentDefinitionService.getAgentSettings(agentId),
  })

  const behavioralQuery = useQuery({
    queryKey: ['agent-behavioral-persona', agentId],
    queryFn: () => agentDefinitionService.getBehavioralPersona(agentId),
  })

  const serverCosmetic = useMemo(
    () => cosmeticToDraft(settingsQuery.data?.settings?.persona ?? null),
    [settingsQuery.data?.settings?.persona],
  )
  const serverBehavioral = behavioralQuery.data?.persona ?? DEFAULT_BEHAVIORAL_PERSONA

  // Voice subsection is only meaningful for voice/multimodal agents.
  // Chat-only agents skip the picker entirely so the value still saves
  // back as the schema default but the author isn't asked to pick.
  const agentType = settingsQuery.data?.settings?.agent_type
  const showVoiceSection = agentType === 'voice' || agentType === 'multimodal'

  const [cosmetic, setCosmetic] = useState<CosmeticDraft>(serverCosmetic)
  const [behavioral, setBehavioral] = useState<BehavioralPersona>(serverBehavioral)
  const [topicInput, setTopicInput] = useState('')

  // Re-sync local state when the server data changes (initial load + after save).
  useEffect(() => {
    setCosmetic(serverCosmetic)
  }, [serverCosmetic])
  useEffect(() => {
    setBehavioral(serverBehavioral)
  }, [serverBehavioral])

  const cosmeticDirty = !cosmeticEqual(cosmetic, serverCosmetic)
  const behavioralDirty = !behavioralEqual(behavioral, serverBehavioral)

  const cosmeticValidation = cosmeticErrors(cosmetic)
  const cosmeticValid = Object.keys(cosmeticValidation).length === 0

  const cosmeticMutation = useMutation({
    mutationFn: (next: CosmeticPersona | null) =>
      agentDefinitionService.updateCosmeticPersona(agentId, next),
    onSuccess: (data) => {
      queryClient.setQueryData(['agent-settings', agentId], data)
      toast.success('Persona identity saved')
    },
    onError: (error: Error) => {
      toast.error(`Save failed: ${error.message}`)
    },
  })

  // ── Phase 2d — avatar upload ──────────────────────────────────────────
  // Pre-validates size/format client-side for fast feedback; the
  // server is the source of truth for everything else (dimensions,
  // EXIF, magic bytes). Successful upload sets cosmetic.avatar_url
  // to the server-returned URL so the next "Save identity" persists
  // it on the persona.
  const avatarFileInputRef = useRef<HTMLInputElement | null>(null)
  const [avatarUploadError, setAvatarUploadError] = useState<string | null>(null)

  const avatarUploadMutation = useMutation({
    mutationFn: (file: File) =>
      agentDefinitionService.uploadPersonaAvatar(agentId, file),
    onSuccess: (result) => {
      setAvatarUploadError(null)
      setCosmetic((current) => ({
        ...current,
        avatar_url: result.avatar_url,
      }))
      toast.success('Avatar uploaded')
    },
    onError: (err: unknown) => {
      // Map server status to user-readable copy. The wizard pattern
      // duck-types on .status to avoid loading client.ts at test time.
      const status =
        typeof err === 'object' &&
        err !== null &&
        'status' in err &&
        typeof (err as { status: unknown }).status === 'number'
          ? (err as { status: number }).status
          : 0
      const message =
        typeof err === 'object' &&
        err !== null &&
        'message' in err &&
        typeof (err as { message: unknown }).message === 'string'
          ? (err as { message: string }).message
          : ''
      if (status === 413) {
        setAvatarUploadError(
          'Image is too large. Max 2MB; pick a smaller file.',
        )
      } else if (status === 422) {
        setAvatarUploadError(message || 'Image rejected — must be JPEG/PNG/WebP, square, 256–1024px.')
      } else if (status === 401 || status === 403) {
        setAvatarUploadError('You don’t have permission to upload an avatar.')
      } else {
        setAvatarUploadError('Upload failed — try again or contact support.')
      }
    },
  })

  const uploadAvatar = async (file: File) => {
    setAvatarUploadError(null)
    // Cheap client-side checks that catch obvious problems before
    // burning a network round-trip. The server enforces the same
    // limits.
    if (file.size > 2 * 1024 * 1024) {
      setAvatarUploadError(
        'Image is too large. Max 2MB; pick a smaller file.',
      )
      return
    }
    const allowedMimes = new Set([
      'image/jpeg',
      'image/jpg',
      'image/png',
      'image/webp',
    ])
    if (!allowedMimes.has(file.type)) {
      setAvatarUploadError(
        'Unsupported format. Use JPEG, PNG, or WebP.',
      )
      return
    }
    avatarUploadMutation.mutate(file)
  }

  const behavioralMutation = useMutation({
    mutationFn: async (next: BehavioralPersona) => {
      const document = behavioralQuery.data?.document
      if (!document) {
        throw new Error('Agent document is not yet loaded')
      }
      return agentDefinitionService.updateBehavioralPersona(agentId, next, document)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-behavioral-persona', agentId] })
      queryClient.invalidateQueries({ queryKey: ['agent-document', agentId] })
      queryClient.invalidateQueries({ queryKey: ['agent-publish-review', agentId] })
      toast.success('Persona behaviour saved to draft — publish to roll out')
    },
    onError: (error: Error) => {
      toast.error(`Save failed: ${error.message}`)
    },
  })

  const handleSaveCosmetic = () => {
    if (!cosmeticValid) return
    cosmeticMutation.mutate(draftToCosmetic(cosmetic))
  }

  const handleResetCosmetic = () => setCosmetic(serverCosmetic)

  const handleSaveBehavioral = () => {
    behavioralMutation.mutate(behavioral)
  }
  const handleResetBehavioral = () => setBehavioral(serverBehavioral)

  const topicError = topicInput ? validateRestrictedTopic(topicInput) : null
  const canAddTopic =
    !!topicInput.trim() &&
    !topicError &&
    behavioral.restricted_topics.length < TOPIC_LIMIT &&
    !behavioral.restricted_topics.includes(topicInput.trim())

  const handleAddTopic = () => {
    if (!canAddTopic) return
    setBehavioral({
      ...behavioral,
      restricted_topics: [...behavioral.restricted_topics, topicInput.trim()],
    })
    setTopicInput('')
  }

  const handleRemoveTopic = (idx: number) => {
    setBehavioral({
      ...behavioral,
      restricted_topics: behavioral.restricted_topics.filter((_, i) => i !== idx),
    })
  }

  if (settingsQuery.isLoading || behavioralQuery.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading persona…
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b border-border/40 px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold">Persona</h2>
          <p className="text-sm text-muted-foreground">
            How your agent introduces itself and behaves with customers.
          </p>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-10">
        {/* ── Identity ────────────────────────────────────────────────── */}
        <section className="space-y-5" data-testid="persona-identity-section">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-base font-semibold">Identity</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Live-edit. Saves apply immediately to running conversations.
              </p>
            </div>
            <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-700">
              Live
            </Badge>
          </div>

          <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
            <Field
              id="persona-name"
              label="Persona name"
              hint="What the agent calls itself, e.g. “Maya”."
              error={cosmeticValidation.persona_name}
            >
              <Input
                id="persona-name"
                value={cosmetic.persona_name}
                onChange={(e) =>
                  setCosmetic({ ...cosmetic, persona_name: e.target.value })
                }
                placeholder="e.g. Maya"
                maxLength={PERSONA_NAME_MAX}
              />
            </Field>

            <Field
              id="persona-role"
              label="Role title"
              hint="Optional. Used in the agent's self-introduction."
              error={cosmeticValidation.role_title}
            >
              <Input
                id="persona-role"
                value={cosmetic.role_title}
                onChange={(e) =>
                  setCosmetic({ ...cosmetic, role_title: e.target.value })
                }
                placeholder="e.g. Customer Support Specialist"
                maxLength={ROLE_TITLE_MAX}
              />
            </Field>

            <Field id="persona-pronouns" label="Pronouns" hint="Optional.">
              <Select
                value={cosmetic.pronouns || 'unset'}
                onValueChange={(value) =>
                  setCosmetic({
                    ...cosmetic,
                    pronouns: value === 'unset' ? '' : (value as PersonaPronouns),
                  })
                }
              >
                <SelectTrigger id="persona-pronouns">
                  <SelectValue placeholder="Not set" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="unset">Not set</SelectItem>
                  <SelectItem value="she/her">she/her</SelectItem>
                  <SelectItem value="he/him">he/him</SelectItem>
                  <SelectItem value="they/them">they/them</SelectItem>
                  <SelectItem value="custom">Custom…</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            {cosmetic.pronouns === 'custom' && (
              <Field
                id="persona-pronouns-custom"
                label="Custom pronouns"
                error={cosmeticValidation.pronouns_custom}
              >
                <Input
                  id="persona-pronouns-custom"
                  value={cosmetic.pronouns_custom}
                  onChange={(e) =>
                    setCosmetic({ ...cosmetic, pronouns_custom: e.target.value })
                  }
                  placeholder="e.g. ze/zir"
                  maxLength={PRONOUNS_CUSTOM_MAX}
                />
              </Field>
            )}

            <Field
              id="persona-avatar"
              label="Avatar"
              hint="Upload a square JPEG/PNG/WebP (256–1024px, max 2MB) or paste an HTTPS URL."
              error={cosmeticValidation.avatar_url || avatarUploadError || undefined}
              className="md:col-span-2"
            >
              <div className="flex gap-2">
                <Input
                  id="persona-avatar"
                  value={cosmetic.avatar_url}
                  onChange={(e) =>
                    setCosmetic({ ...cosmetic, avatar_url: e.target.value })
                  }
                  placeholder="https://… or upload below"
                  maxLength={AVATAR_URL_MAX}
                  className="flex-1"
                />
                <input
                  ref={avatarFileInputRef}
                  type="file"
                  accept="image/jpeg,image/jpg,image/png,image/webp"
                  className="hidden"
                  data-testid="persona-avatar-upload-input"
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) {
                      void uploadAvatar(file)
                    }
                    // Reset so the same file can be re-selected.
                    e.target.value = ''
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => avatarFileInputRef.current?.click()}
                  disabled={avatarUploadMutation.isPending}
                  data-testid="persona-avatar-upload-button"
                >
                  {avatarUploadMutation.isPending ? 'Uploading…' : 'Upload'}
                </Button>
              </div>
            </Field>

            <Field
              id="persona-greeting"
              label="Greeting template"
              hint="Phrasing the agent uses to open. Supports $persona_name, $company_name, $role_title."
              error={cosmeticValidation.greeting_template}
              className="md:col-span-2"
            >
              <textarea
                id="persona-greeting"
                value={cosmetic.greeting_template}
                onChange={(e) =>
                  setCosmetic({ ...cosmetic, greeting_template: e.target.value })
                }
                placeholder="Hi, I'm $persona_name from $company_name — how can I help today?"
                maxLength={GREETING_MAX}
                rows={2}
                className="min-h-[60px] w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </Field>

            <Field
              id="persona-signoff"
              label="Sign-off template"
              hint="Phrasing the agent uses to close."
              error={cosmeticValidation.signoff_template}
              className="md:col-span-2"
            >
              <textarea
                id="persona-signoff"
                value={cosmetic.signoff_template}
                onChange={(e) =>
                  setCosmetic({ ...cosmetic, signoff_template: e.target.value })
                }
                placeholder="Thanks for chatting — this was $persona_name from $company_name."
                maxLength={SIGNOFF_MAX}
                rows={2}
                className="min-h-[60px] w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </Field>
          </div>

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              onClick={handleResetCosmetic}
              disabled={!cosmeticDirty || cosmeticMutation.isPending}
            >
              Reset
            </Button>
            <Button
              onClick={handleSaveCosmetic}
              disabled={!cosmeticDirty || !cosmeticValid || cosmeticMutation.isPending}
              data-testid="persona-save-cosmetic"
            >
              {cosmeticMutation.isPending ? 'Saving…' : 'Save identity'}
            </Button>
          </div>
        </section>

        <Separator />

        {/* ── Behaviour ──────────────────────────────────────────────── */}
        <section className="space-y-5" data-testid="persona-behavior-section">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-base font-semibold">Behaviour</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Versioned. Saves to the draft — publish to roll out.
              </p>
            </div>
            <Badge variant="secondary" className="bg-amber-500/10 text-amber-700">
              {behavioralDirty ? 'Pending publish' : 'Draft'}
            </Badge>
          </div>

          <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
            <Field id="persona-formality" label="Formality" hint="Tone register the agent uses.">
              <Select
                value={behavioral.formality}
                onValueChange={(value) =>
                  setBehavioral({ ...behavioral, formality: value as PersonaFormality })
                }
              >
                <SelectTrigger id="persona-formality">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="formal">Formal</SelectItem>
                  <SelectItem value="neutral">Neutral</SelectItem>
                  <SelectItem value="casual">Casual</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            <Field
              id="persona-emoji"
              label="Emoji policy"
              hint="How freely the agent uses emoji."
            >
              <Select
                value={behavioral.emoji_policy}
                onValueChange={(value) =>
                  setBehavioral({ ...behavioral, emoji_policy: value as PersonaEmojiPolicy })
                }
              >
                <SelectTrigger id="persona-emoji">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="never">Never</SelectItem>
                  <SelectItem value="sparingly">Sparingly</SelectItem>
                  <SelectItem value="encouraged">Encouraged</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>

          <div className="space-y-2">
            <div className="flex items-baseline justify-between">
              <Label>Topic enforcement</Label>
              <span className="text-xs text-muted-foreground">
                {behavioral.restricted_topics.length} / {TOPIC_LIMIT}
              </span>
            </div>

            <Field
              id="persona-topic-enforcement"
              label="Enforcement mode"
              hint={topicEnforcementHint(behavioral.topic_enforcement)}
            >
              <Select
                value={behavioral.topic_enforcement}
                onValueChange={(value) =>
                  setBehavioral({
                    ...behavioral,
                    topic_enforcement: value as TopicEnforcementPolicy,
                  })
                }
              >
                <SelectTrigger
                  id="persona-topic-enforcement"
                  data-testid="persona-topic-enforcement-select"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="off">Off</SelectItem>
                  <SelectItem value="log_only">Log only (canary)</SelectItem>
                  <SelectItem value="block_and_retry">Block &amp; retry</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                {topicEnforcementCaption(behavioral.topic_enforcement)}
              </span>
            </p>

            <div className="flex gap-2">
              <Input
                value={topicInput}
                onChange={(e) => setTopicInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && canAddTopic) {
                    e.preventDefault()
                    handleAddTopic()
                  }
                }}
                placeholder="e.g. competitor pricing"
                maxLength={TOPIC_MAX}
                aria-label="Add topic"
                data-testid="persona-topic-input"
              />
              <Button
                variant="outline"
                onClick={handleAddTopic}
                disabled={!canAddTopic}
                data-testid="persona-topic-add"
              >
                <Plus className="h-4 w-4" />
                Add
              </Button>
            </div>
            {topicError && <p className="text-xs text-destructive">{topicError}</p>}
            {behavioral.restricted_topics.length === TOPIC_LIMIT && (
              <p className="text-xs text-amber-600">Maximum of {TOPIC_LIMIT} topics.</p>
            )}

            {behavioral.restricted_topics.length > 0 && (
              <ul className="space-y-1.5 pt-1" data-testid="persona-topic-list">
                {behavioral.restricted_topics.map((topic, idx) => (
                  <li
                    key={`${topic}-${idx}`}
                    className="flex items-center justify-between rounded-md border border-border/60 bg-background px-3 py-2 text-sm"
                  >
                    <span>{topic}</span>
                    <button
                      type="button"
                      onClick={() => handleRemoveTopic(idx)}
                      className="rounded p-1 text-muted-foreground hover:text-destructive"
                      aria-label={`Remove topic ${topic}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* ── Phase 2b — Languages ──────────────────────────────────
              The Africa-first competitive moat. Authors set primary +
              allowed languages, optional auto-switch behaviour, and
              the locale code that drives default greetings. */}
          <div className="space-y-3" data-testid="persona-languages-section">
            <div>
              <Label>Languages</Label>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Which languages your agent can speak, and how it switches
                between them. Defaults match Phase 1 (English-only, no
                auto-switch) so existing agents see no change.
              </p>
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field
                id="persona-primary-language"
                label="Primary language"
                hint="The default language the agent speaks. Choose 'Auto' to let the model pick (advanced; requires Gemini Live native audio or Soniox)."
              >
                <Select
                  value={behavioral.primary_language}
                  onValueChange={(v) =>
                    setBehavioral({ ...behavioral, primary_language: v })
                  }
                >
                  <SelectTrigger id="persona-primary-language" data-testid="persona-primary-language">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">Auto (advanced)</SelectItem>
                    {LANGUAGE_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>

              <Field
                id="persona-locale"
                label="Locale"
                hint="Drives default greetings, currency, and date format."
              >
                <Select
                  value={behavioral.locale_code}
                  onValueChange={(v) =>
                    setBehavioral({ ...behavioral, locale_code: v })
                  }
                >
                  <SelectTrigger id="persona-locale" data-testid="persona-locale">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {LOCALE_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>

            {/* Allowed languages — chip-add UI matching the topics UX */}
            <div className="space-y-1.5">
              <Label>Allowed languages</Label>
              <p className="text-xs text-muted-foreground">
                The set the agent will respond in. The user's detected
                language must match one of these for the agent to switch.
              </p>
              <div className="flex flex-wrap gap-1.5" data-testid="persona-allowed-languages">
                {behavioral.allowed_languages.map((lang) => {
                  const opt = LANGUAGE_OPTIONS.find((o) => o.value === lang)
                  return (
                    <button
                      key={lang}
                      type="button"
                      onClick={() => {
                        if (behavioral.allowed_languages.length <= 1) return
                        setBehavioral({
                          ...behavioral,
                          allowed_languages: behavioral.allowed_languages.filter(
                            (l) => l !== lang,
                          ),
                        })
                      }}
                      className="inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-background px-2 py-1 text-xs hover:border-destructive/40 hover:text-destructive"
                      data-testid={`persona-allowed-language-${lang}`}
                      aria-label={`Remove ${opt?.label ?? lang}`}
                    >
                      {opt?.label ?? lang}
                      <Trash2 className="h-3 w-3" />
                    </button>
                  )
                })}
              </div>
              <Select
                value=""
                onValueChange={(lang) => {
                  if (!lang || behavioral.allowed_languages.includes(lang)) return
                  setBehavioral({
                    ...behavioral,
                    allowed_languages: [...behavioral.allowed_languages, lang],
                  })
                }}
              >
                <SelectTrigger className="h-8" data-testid="persona-add-language">
                  <SelectValue placeholder="Add a language…" />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGE_OPTIONS.filter(
                    (o) => !behavioral.allowed_languages.includes(o.value),
                  ).map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Auto-switch mode — same shape as topic_enforcement (off / log_only / on) */}
            <Field
              id="persona-auto-switch"
              label="Auto-switch language"
              hint={autoSwitchHint(behavioral.auto_switch_language)}
            >
              <Select
                value={behavioral.auto_switch_language}
                onValueChange={(v) =>
                  setBehavioral({
                    ...behavioral,
                    auto_switch_language: v as AutoSwitchMode,
                  })
                }
              >
                <SelectTrigger id="persona-auto-switch" data-testid="persona-auto-switch">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="off">Off — never switch</SelectItem>
                  <SelectItem value="log_only">Log only (canary)</SelectItem>
                  <SelectItem value="on">On — match user's language</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            {/* Stability gates + policy — only shown when auto-switch is enabled */}
            {behavioral.auto_switch_language !== 'off' && (
              <div className="space-y-3 rounded-md border border-border/40 bg-muted/20 p-3">
                <p className="text-xs font-medium text-muted-foreground">
                  Stability controls
                </p>
                <div className="space-y-1.5">
                  <Label htmlFor="persona-confidence-threshold">
                    Confidence threshold:{' '}
                    {behavioral.language_switch_confidence_threshold.toFixed(2)}
                  </Label>
                  <input
                    id="persona-confidence-threshold"
                    type="range"
                    min={0.5}
                    max={0.99}
                    step={0.01}
                    value={behavioral.language_switch_confidence_threshold}
                    onChange={(e) =>
                      setBehavioral({
                        ...behavioral,
                        language_switch_confidence_threshold: Number(e.target.value),
                      })
                    }
                    data-testid="persona-confidence-threshold"
                    className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-slate-700"
                  />
                </div>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  <Field
                    id="persona-min-chars"
                    label={`Min chars before switch: ${behavioral.language_switch_min_chars}`}
                  >
                    <input
                      id="persona-min-chars"
                      type="range"
                      min={0}
                      max={50}
                      step={1}
                      value={behavioral.language_switch_min_chars}
                      onChange={(e) =>
                        setBehavioral({
                          ...behavioral,
                          language_switch_min_chars: Number(e.target.value),
                        })
                      }
                      data-testid="persona-min-chars"
                      className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-slate-700"
                    />
                  </Field>
                  <Field
                    id="persona-debounce-turns"
                    label={`Debounce turns: ${behavioral.language_switch_debounce_turns}`}
                  >
                    <input
                      id="persona-debounce-turns"
                      type="range"
                      min={0}
                      max={5}
                      step={1}
                      value={behavioral.language_switch_debounce_turns}
                      onChange={(e) =>
                        setBehavioral({
                          ...behavioral,
                          language_switch_debounce_turns: Number(e.target.value),
                        })
                      }
                      data-testid="persona-debounce-turns"
                      className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-slate-700"
                    />
                  </Field>
                </div>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  <Field id="persona-switch-policy" label="When user switches">
                    <Select
                      value={behavioral.language_switch_policy}
                      onValueChange={(v) =>
                        setBehavioral({
                          ...behavioral,
                          language_switch_policy: v as LanguageSwitchPolicy,
                        })
                      }
                    >
                      <SelectTrigger id="persona-switch-policy" data-testid="persona-switch-policy">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="mirror_user">Mirror user (default)</SelectItem>
                        <SelectItem value="lock_to_primary">Lock to primary</SelectItem>
                        <SelectItem value="gradual_revert">Gradual revert</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field
                    id="persona-unsupported-policy"
                    label="If user speaks unsupported language"
                  >
                    <Select
                      value={behavioral.unsupported_language_policy}
                      onValueChange={(v) =>
                        setBehavioral({
                          ...behavioral,
                          unsupported_language_policy: v as UnsupportedLanguagePolicy,
                        })
                      }
                    >
                      <SelectTrigger
                        id="persona-unsupported-policy"
                        data-testid="persona-unsupported-policy"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="stay_in_primary">Stay silent (in primary)</SelectItem>
                        <SelectItem value="explain_and_offer">Explain &amp; offer (default)</SelectItem>
                        <SelectItem value="escalate_to_human">Escalate to human</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                </div>
              </div>
            )}

            {/* Cultural calendar opt-in */}
            <div className="flex items-start gap-2">
              <input
                id="persona-cultural-calendar"
                type="checkbox"
                checked={behavioral.cultural_calendar_enabled}
                onChange={(e) =>
                  setBehavioral({
                    ...behavioral,
                    cultural_calendar_enabled: e.target.checked,
                  })
                }
                data-testid="persona-cultural-calendar"
                className="mt-1"
              />
              <div className="space-y-0.5">
                <Label htmlFor="persona-cultural-calendar" className="cursor-pointer">
                  Cultural calendar greetings
                </Label>
                <p className="text-xs text-muted-foreground">
                  Inject locale-appropriate Ramadan / Christmas greetings during the
                  relevant windows. Off by default — enable explicitly per locale.
                </p>
              </div>
            </div>
          </div>

          {/* Voice — only shown for voice/multimodal agents. Reads agent_type
              from settings; chat-only agents don't see this subsection. */}
          {showVoiceSection && (
            <div className="space-y-3" data-testid="persona-voice-section">
              <div>
                <Label>Voice</Label>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  The voice your agent uses for spoken responses. Click any preview
                  to hear how it sounds.
                </p>
              </div>

              <VoicePicker
                selectedVoiceId={behavioral.voice_id}
                onSelect={(voiceId) =>
                  setBehavioral({ ...behavioral, voice_id: voiceId })
                }
                agentId={agentId}
              />

              <div className="space-y-1.5">
                <div className="flex items-baseline justify-between">
                  <Label htmlFor="persona-voice-speed">
                    Speed: {behavioral.voice_speed.toFixed(1)}×
                  </Label>
                  <span className="text-xs text-muted-foreground">0.7×–1.3×</span>
                </div>
                <input
                  id="persona-voice-speed"
                  type="range"
                  min={0.7}
                  max={1.3}
                  step={0.1}
                  value={behavioral.voice_speed}
                  onChange={(event) =>
                    setBehavioral({
                      ...behavioral,
                      voice_speed: Number(event.target.value),
                    })
                  }
                  data-testid="persona-voice-speed"
                  className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-slate-700"
                />
              </div>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              onClick={handleResetBehavioral}
              disabled={!behavioralDirty || behavioralMutation.isPending}
            >
              Reset
            </Button>
            <Button
              onClick={handleSaveBehavioral}
              disabled={!behavioralDirty || behavioralMutation.isPending}
              data-testid="persona-save-behavioral"
            >
              {behavioralMutation.isPending ? 'Saving…' : 'Save to draft'}
            </Button>
          </div>

          {behavioralDirty && (
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <ArrowRight className="h-3 w-3" />
              Changes apply after publish. Use the Publish button at the top of the
              canvas.
            </p>
          )}
        </section>
      </div>
    </div>
  )
}

interface FieldProps {
  id: string
  label: string
  hint?: string
  error?: string
  className?: string
  children: React.ReactNode
}

function Field({ id, label, hint, error, className = '', children }: FieldProps) {
  return (
    <div className={`space-y-1.5 ${className}`}>
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      {children}
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}

// Mode-specific UI copy for topic enforcement. The exact wording is part
// of the product/legal contract — this is the single place to change it.
// Tests assert each mode's caption, so changing copy = changing tests.
function topicEnforcementHint(policy: TopicEnforcementPolicy): string {
  switch (policy) {
    case 'off':
      return 'No detection runs. The model is still asked to avoid these topics in the prompt, but nothing checks the response.'
    case 'log_only':
      return 'Canary mode — detection runs and violations are logged for audit, but the response is still emitted unchanged.'
    case 'block_and_retry':
      return 'Enforced — violating responses are retried once with a stronger constraint, then deflected if the retry also violates.'
  }
}

function topicEnforcementCaption(policy: TopicEnforcementPolicy): string {
  switch (policy) {
    case 'off':
      return 'Off — guardrail is disabled.'
    case 'log_only':
      return 'Log only — observable, but does not block. Use this to vet your topic list before flipping to Block & retry.'
    case 'block_and_retry':
      return 'Block & retry — violating responses do not reach the customer.'
  }
}

function autoSwitchHint(policy: AutoSwitchMode): string {
  switch (policy) {
    case 'off':
      return 'No detection runs. Agent always responds in the primary language.'
    case 'log_only':
      return 'Canary — detection runs and would-be switches are audited, but the agent stays in the primary language.'
    case 'on':
      return 'Detection runs and the agent matches the user’s language (within Allowed languages).'
  }
}
