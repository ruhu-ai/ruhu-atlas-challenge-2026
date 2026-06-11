export type PhoneProvider = 'africastalking' | 'telnyx'
export type PhoneNumberStatus = 'draft' | 'active' | 'suspended' | 'archived'
export type PhoneNumberOwnershipMode = 'imported' | 'provider_managed'
export type PhoneBindingChannel = 'phone' | 'sms' | 'whatsapp'
export type PhoneBindingVerificationStatus = 'unverified' | 'pending' | 'verified' | 'manual_required' | 'failed'
export type PhoneBindingHealthStatus = 'unknown' | 'healthy' | 'degraded' | 'misconfigured' | 'disabled'

export interface PhoneRegistryNumber {
  phone_number_id: string
  organization_id: string
  e164_number: string
  display_name: string | null
  country_code: string | null
  status: PhoneNumberStatus
  ownership_mode: PhoneNumberOwnershipMode
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface PhoneNumberBinding {
  binding_id: string
  phone_number_id: string
  organization_id: string
  channel: PhoneBindingChannel
  provider: PhoneProvider
  provider_resource_id: string | null
  capabilities: string[]
  verification_status: PhoneBindingVerificationStatus
  health_status: PhoneBindingHealthStatus
  is_active: boolean
  transport_metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface PhoneNumberRoute {
  route_id: string
  phone_number_id: string
  organization_id: string
  channel: PhoneBindingChannel
  agent_id: string
  priority: number
  enabled: boolean
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface PhoneNumberDetail {
  number: PhoneRegistryNumber
  bindings: PhoneNumberBinding[]
  routes: PhoneNumberRoute[]
}

export interface PhoneNumberCreateRequest {
  e164_number: string
  display_name?: string | null
  ownership_mode?: PhoneNumberOwnershipMode
  status?: PhoneNumberStatus
  metadata?: Record<string, unknown>
}

export interface PhoneNumberUpdateRequest {
  display_name?: string | null
  ownership_mode?: PhoneNumberOwnershipMode
  status?: PhoneNumberStatus
  metadata?: Record<string, unknown>
}

export interface PhoneNumberRouteCreateRequest {
  channel?: PhoneBindingChannel
  agent_id: string
  priority?: number
  enabled?: boolean
  metadata?: Record<string, unknown>
}

export interface PhoneNumberRouteUpdateRequest {
  agent_id?: string
  priority?: number
  enabled?: boolean
  metadata?: Record<string, unknown>
}

export interface TelnyxAvailableNumber {
  phone_number: string
  country_code: string | null
  phone_number_type: string | null
  locality: string | null
  region: string | null
  features: string[]
  monthly_cost: string | null
  upfront_cost: string | null
  currency: string | null
  quickship: boolean | null
  reservable: boolean | null
}

export interface TelnyxPhoneNumberResponse {
  provider_resource_id: string
  phone_number: string
  country_code: string | null
  status: string | null
  phone_number_type: string | null
  connection_id: string | null
  connection_name: string | null
  customer_reference: string | null
  messaging_profile_id: string | null
  messaging_profile_name: string | null
  billing_group_id: string | null
  emergency_enabled: boolean | null
  emergency_status: string | null
  call_forwarding_enabled: boolean | null
  inbound_call_screening: string | null
  hd_voice_enabled: boolean | null
  source_type: string | null
  purchased_at: string | null
  created_at: string | null
  updated_at: string | null
  tags: string[]
}

export interface TelnyxVoiceSettingsResponse {
  provider_resource_id: string
  connection_id: string | null
  customer_reference: string | null
  translated_number: string | null
  usage_payment_method: string | null
  inbound_call_screening: string | null
  tech_prefix_enabled: boolean | null
  call_forwarding_enabled: boolean | null
  forwards_to: string | null
  forwarding_type: string | null
  emergency_enabled: boolean | null
  emergency_status: string | null
  media_features: Record<string, unknown>
}

export interface TelnyxPhoneNumberImportRequest {
  phone_number_id?: string | null
  provider_resource_id?: string | null
  phone_number?: string | null
  display_name?: string | null
  metadata?: Record<string, unknown>
  channel?: PhoneBindingChannel
}

export interface TelnyxBindingSyncResponse {
  number: PhoneRegistryNumber
  binding: PhoneNumberBinding
  detail: PhoneNumberDetail
  provider_number: TelnyxPhoneNumberResponse
  voice_settings: TelnyxVoiceSettingsResponse | null
  created_number: boolean
  created_binding: boolean
}

export interface AfricasTalkingPhoneNumberImportRequest {
  phone_number_id?: string | null
  phone_number: string
  provider_resource_id?: string | null
  display_name?: string | null
  metadata?: Record<string, unknown>
  channel?: PhoneBindingChannel
  account_username?: string | null
  voice_callback_url?: string | null
  events_callback_url?: string | null
  sip_trunk_target?: string | null
  sip_auth_required?: boolean
  credentials_reference?: string | null
  ip_whitelist_confirmed?: boolean
  sip_forwarding_confirmed?: boolean
  configuration_confirmed?: boolean
  last_verified_at?: string | null
  notes?: string | null
}

export interface AfricasTalkingBindingSyncRequest {
  provider_resource_id?: string | null
  account_username?: string | null
  voice_callback_url?: string | null
  events_callback_url?: string | null
  sip_trunk_target?: string | null
  sip_auth_required?: boolean | null
  credentials_reference?: string | null
  ip_whitelist_confirmed?: boolean | null
  sip_forwarding_confirmed?: boolean | null
  configuration_confirmed?: boolean | null
  last_verified_at?: string | null
  notes?: string | null
}

export interface AfricasTalkingBindingStateResponse {
  provider_resource_id: string
  phone_number: string
  account_username: string | null
  voice_callback_url: string | null
  events_callback_url: string | null
  sip_trunk_target: string | null
  sip_auth_required: boolean
  credentials_reference: string | null
  ip_whitelist_confirmed: boolean
  sip_forwarding_confirmed: boolean
  configuration_confirmed: boolean
  last_verified_at: string | null
  notes: string | null
  manual_requirements: string[]
  recommended_actions: string[]
}

export interface AfricasTalkingBindingSyncResponse {
  number: PhoneRegistryNumber
  binding: PhoneNumberBinding
  detail: PhoneNumberDetail
  provider_binding: AfricasTalkingBindingStateResponse
  created_number: boolean
  created_binding: boolean
}

export interface PhoneAuditEvent {
  audit_event_id: string
  organization_id: string
  phone_number_id: string | null
  actor_type: string
  actor_user_id: string | null
  action: string
  resource_type: string
  resource_id: string | null
  summary: string
  payload: Record<string, unknown>
  ip_address: string | null
  user_agent: string | null
  created_at: string
}

export interface PhoneBindingReconciliationRequest {
  provider?: string | null
  phone_number_id?: string | null
  binding_id?: string | null
  limit?: number
}

export interface PhoneBindingReconciliationResult {
  phone_number_id: string
  binding_id: string
  provider: string
  operation_status: string
  previous_verification_status: PhoneBindingVerificationStatus
  previous_health_status: PhoneBindingHealthStatus
  verification_status: PhoneBindingVerificationStatus
  health_status: PhoneBindingHealthStatus
  changed: boolean
  notification_emitted: boolean
  error: string | null
  reconciled_at: string
}

export interface PhoneBindingReconciliationResponse {
  organization_id: string
  processed_count: number
  changed_count: number
  failed_count: number
  results: PhoneBindingReconciliationResult[]
}
