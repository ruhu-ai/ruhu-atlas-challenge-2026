/**
 * Phone Numbers — pure helpers shared by the page, hooks, and components.
 */

import type {
  AfricasTalkingBindingStateResponse,
  PhoneNumberBinding,
  PhoneNumberOwnershipMode,
  PhoneNumberRoute,
  PhoneNumberStatus,
} from '@/types/phone'

export type ImportPanel = 'registry' | 'telnyx' | 'africastalking'

/** Minimal mutation surface needed by presentational components. */
export type MutationLike<TVariables = void> = {
  mutate: (variables: TVariables) => void
  isPending: boolean
}

export type ManualNumberFormState = {
  e164_number: string
  display_name: string
  ownership_mode: PhoneNumberOwnershipMode
  status: PhoneNumberStatus
  metadata_note: string
}

export type NumberEditFormState = {
  display_name: string
  status: PhoneNumberStatus
}

export type RouteFormState = {
  agent_id: string
  priority: string
  purpose: string
  enabled: boolean
}

export type TelnyxImportFormState = {
  provider_resource_id: string
  phone_number: string
  display_name: string
}

export type TelnyxLookupFormState = {
  country_code: string
  phone_number_type: string
  national_destination_code: string
  locality: string
  limit: string
}

export type AfricasTalkingImportFormState = {
  phone_number: string
  provider_resource_id: string
  display_name: string
  account_username: string
  voice_callback_url: string
  events_callback_url: string
  sip_trunk_target: string
  sip_auth_required: boolean
  credentials_reference: string
  ip_whitelist_confirmed: boolean
  sip_forwarding_confirmed: boolean
  configuration_confirmed: boolean
  last_verified_at: string
  notes: string
}

// Africa + Middle East first (primary markets), then global coverage
export const TELNYX_COUNTRY_OPTIONS = [
  { code: 'NG', label: 'Nigeria' },
  { code: 'KE', label: 'Kenya' },
  { code: 'ZA', label: 'South Africa' },
  { code: 'GH', label: 'Ghana' },
  { code: 'TZ', label: 'Tanzania' },
  { code: 'UG', label: 'Uganda' },
  { code: 'RW', label: 'Rwanda' },
  { code: 'EG', label: 'Egypt' },
  { code: 'MA', label: 'Morocco' },
  { code: 'AE', label: 'United Arab Emirates' },
  { code: 'SA', label: 'Saudi Arabia' },
  { code: 'QA', label: 'Qatar' },
  { code: 'JO', label: 'Jordan' },
  { code: 'US', label: 'United States' },
  { code: 'GB', label: 'United Kingdom' },
  { code: 'CA', label: 'Canada' },
  { code: 'AU', label: 'Australia' },
  { code: 'IN', label: 'India' },
  { code: 'SG', label: 'Singapore' },
  { code: 'HK', label: 'Hong Kong' },
  { code: 'PH', label: 'Philippines' },
  { code: 'DE', label: 'Germany' },
  { code: 'FR', label: 'France' },
  { code: 'ES', label: 'Spain' },
  { code: 'IT', label: 'Italy' },
  { code: 'NL', label: 'Netherlands' },
  { code: 'SE', label: 'Sweden' },
  { code: 'NO', label: 'Norway' },
  { code: 'DK', label: 'Denmark' },
  { code: 'PL', label: 'Poland' },
  { code: 'BR', label: 'Brazil' },
  { code: 'MX', label: 'Mexico' },
]

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function asString(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const candidate = value.trim()
  return candidate || null
}

export function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return 'Not recorded'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function humanizeProvider(provider: string): string {
  return provider === 'africastalking' ? "Africa's Talking" : 'Telnyx'
}

export function humanizeManualItem(code: string): string {
  return code
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function humanizeAuditAction(action: string): string {
  return action
    .split('.')
    .map((part) => part.split('_').join(' '))
    .join(' / ')
}

export function routeLabel(route: PhoneNumberRoute): string | null {
  const label = route.metadata.label ?? route.metadata.purpose ?? route.metadata.route_source
  return asString(label)
}

export function getAfricasTalkingState(binding: PhoneNumberBinding): AfricasTalkingBindingStateResponse | null {
  const candidate = binding.transport_metadata.africastalking
  if (!isRecord(candidate)) return null
  const manualRequirements = Array.isArray(candidate.manual_requirements)
    ? candidate.manual_requirements.filter((item): item is string => typeof item === 'string')
    : []
  const recommendedActions = Array.isArray(candidate.recommended_actions)
    ? candidate.recommended_actions.filter((item): item is string => typeof item === 'string')
    : []
  const providerResourceId = asString(candidate.provider_resource_id)
  const phoneNumber = asString(candidate.phone_number)
  if (!providerResourceId || !phoneNumber) return null
  return {
    provider_resource_id: providerResourceId,
    phone_number: phoneNumber,
    account_username: asString(candidate.account_username),
    voice_callback_url: asString(candidate.voice_callback_url),
    events_callback_url: asString(candidate.events_callback_url),
    sip_trunk_target: asString(candidate.sip_trunk_target),
    sip_auth_required: asBoolean(candidate.sip_auth_required, true),
    credentials_reference: asString(candidate.credentials_reference),
    ip_whitelist_confirmed: asBoolean(candidate.ip_whitelist_confirmed),
    sip_forwarding_confirmed: asBoolean(candidate.sip_forwarding_confirmed),
    configuration_confirmed: asBoolean(candidate.configuration_confirmed),
    last_verified_at: asString(candidate.last_verified_at),
    notes: asString(candidate.notes),
    manual_requirements: manualRequirements,
    recommended_actions: recommendedActions,
  }
}

export function getTelnyxProjection(binding: PhoneNumberBinding): Record<string, unknown> | null {
  const candidate = binding.transport_metadata.telnyx
  return isRecord(candidate) ? candidate : null
}

export function toDateTimeLocalValue(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (segment: number) => String(segment).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function fromDateTimeLocalValue(value: string): string | null {
  const candidate = value.trim()
  if (!candidate) return null
  const date = new Date(candidate)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}
