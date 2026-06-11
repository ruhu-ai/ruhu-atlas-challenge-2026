/**
 * Settings Service
 *
 * User profile, organization, team, sessions, and SSO management.
 * All endpoints and field names match the backend exactly.
 */

import { apiClient } from '../client'
import { meToUser } from './auth.service'
import type { MeResponse } from './auth.service'
import type {
  User,
  Organization,
  OrganizationMember,
  OrganizationRole,
  AuthSession,
  EnterpriseSSOConfig,
  EnterpriseSSOConfigUpsert,
  APIKeyPublic,
  APIKeyCreated,
  ClosureStatus,
} from '@/types'

function _randomHex(bytes: number): string {
  const cryptoApi = globalThis.crypto
  if (!cryptoApi) {
    throw new Error('Web Crypto is not available in this environment')
  }
  const data = new Uint8Array(bytes)
  cryptoApi.getRandomValues(data)
  return Array.from(data, (value) => value.toString(16).padStart(2, '0')).join('')
}

async function _sha256Hex(value: string): Promise<string> {
  const cryptoApi = globalThis.crypto
  if (!cryptoApi?.subtle) {
    throw new Error('Web Crypto digest support is not available in this environment')
  }
  const digest = await cryptoApi.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('')
}

// ── Request types (mirrors backend Pydantic models) ─────────────────────────

// Mirrors UpdateSelfRequest
interface UpdateUserRequest {
  display_name?: string
  avatar_url?: string
  timezone?: string
  language?: string
  preferences?: Record<string, unknown>
}

// Mirrors UpdateOrganizationRequest
interface UpdateOrganizationRequest {
  name?: string
  domain?: string
  email?: string
  phone?: string
  icon_url?: string
  description?: string
  brand_color?: string
  settings?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

// Mirrors CreateOrganizationInvitationRequest
export interface CreateInvitationRequest {
  email: string
  role?: OrganizationRole
  is_account_owner?: boolean
}

// Mirrors OrganizationInvitationResponse
export interface Invitation {
  invitation_id: string
  email: string
  role: OrganizationRole
  is_account_owner: boolean
  invited_by_user_id: string
  created_at: string
  expires_at: string
  accepted_at: string | null
  accepted_by_user_id: string | null
  revoked_at: string | null
  revoked_by_user_id: string | null
  status: 'pending' | 'accepted' | 'revoked' | 'expired'
}

// Mirrors EmailDeliverySummary (part of CreateOrganizationInvitationResponse)
export interface EmailDeliverySummary {
  transport: 'smtp' | 'dev_outbox'
  delivery_id: string | null
  status: 'sent' | 'queued' | 'failed'
  dev_outbox_entry_id: string | null
}

export interface CreatedInvitation extends Invitation {
  delivery: EmailDeliverySummary
}

// Mirrors InviteValidateResponse
export interface InvitationDetail {
  valid: boolean
  email: string | null
  expires_at: string | null
  organization_name: string | null
  invited_by_name: string | null
  role: string | null
  is_account_owner: boolean
}

// Mirrors UpdateOrganizationMemberRequest
interface UpdateMemberRequest {
  role?: OrganizationRole
  is_account_owner?: boolean
}

class SettingsService {
  // ── Profile ───────────────────────────────────────────────────────────────

  /**
   * Update the current user's profile (PATCH /auth/me)
   */
  async updateUser(_userId: string, data: UpdateUserRequest): Promise<User> {
    const me = await apiClient.patch<MeResponse>('/auth/me', data)
    return meToUser(me)
  }

  // ── Avatar ────────────────────────────────────────────────────────────────

  /**
   * Upload a new avatar for the current user (POST /auth/me/avatar)
   * Returns the updated user with the new avatar_url set.
   */
  async uploadAvatar(file: File): Promise<User> {
    const form = new FormData()
    form.append('file', file)
    const me = await apiClient.post<MeResponse>('/auth/me/avatar', form)
    return meToUser(me)
  }

  // ── Sessions ──────────────────────────────────────────────────────────────

  /**
   * List all sessions for the current user (GET /auth/sessions)
   */
  async listSessions(): Promise<AuthSession[]> {
    return apiClient.get<AuthSession[]>('/auth/sessions')
  }

  /**
   * Revoke a specific session (DELETE /auth/sessions/{sessionId})
   */
  async revokeSession(sessionId: string): Promise<void> {
    await apiClient.delete(`/auth/sessions/${sessionId}`)
  }

  /**
   * Revoke the current session / sign out (DELETE /auth/sessions/current)
   */
  async revokeCurrentSession(): Promise<void> {
    await apiClient.delete('/auth/sessions/current')
  }

  // ── Organization ──────────────────────────────────────────────────────────

  /**
   * Get current organization profile (GET /organization)
   */
  async getOrganization(_orgId: string): Promise<Organization> {
    return apiClient.get<Organization>('/organization')
  }

  /**
   * Update current organization (PATCH /organization)
   */
  async updateOrganization(_orgId: string, data: UpdateOrganizationRequest): Promise<Organization> {
    return apiClient.patch<Organization>('/organization', data)
  }

  /**
   * Revoke all sessions for every member in the organization
   * (POST /organization/auth/revoke-sessions)
   */
  async revokeOrganizationSessions(): Promise<{ organization_id: string; auth_revoked_after: string }> {
    return apiClient.post('/organization/auth/revoke-sessions')
  }

  // ── Enterprise SSO ────────────────────────────────────────────────────────

  /**
   * Get the organization's SSO config (GET /auth/sso/config)
   * Returns null if no SSO is configured.
   */
  async getSSOConfig(): Promise<EnterpriseSSOConfig | null> {
    return apiClient.get<EnterpriseSSOConfig | null>('/auth/sso/config')
  }

  /**
   * Create or update SSO config (PUT /auth/sso/config)
   */
  async upsertSSOConfig(data: EnterpriseSSOConfigUpsert): Promise<EnterpriseSSOConfig> {
    return apiClient.put<EnterpriseSSOConfig>('/auth/sso/config', data)
  }

  /**
   * Disable / delete SSO config (DELETE /auth/sso/config)
   */
  async deleteSSOConfig(): Promise<void> {
    await apiClient.delete('/auth/sso/config')
  }

  // ── Team members ──────────────────────────────────────────────────────────

  /**
   * List organization members (GET /organization/members)
   */
  async listUsers(_skip: number = 0, _limit: number = 100): Promise<OrganizationMember[]> {
    return apiClient.get<OrganizationMember[]>('/organization/members')
  }

  /**
   * Update a member's role or account-owner flag
   * (PATCH /organization/members/{userId})
   */
  async updateMember(userId: string, data: UpdateMemberRequest): Promise<OrganizationMember> {
    return apiClient.patch<OrganizationMember>(`/organization/members/${userId}`, data)
  }

  /**
   * Remove a member from the organization (DELETE /organization/members/{userId})
   */
  async deleteUser(userId: string): Promise<void> {
    await apiClient.delete(`/organization/members/${userId}`)
  }

  /**
   * Revoke all active sessions for a specific member
   * (DELETE /organization/members/{userId}/sessions)
   */
  async revokeMemberSessions(userId: string): Promise<void> {
    await apiClient.delete(`/organization/members/${userId}/sessions`)
  }

  // ── Invitations ───────────────────────────────────────────────────────────

  /**
   * Create team invitation (POST /organization/invitations)
   */
  async createInvitation(data: CreateInvitationRequest): Promise<CreatedInvitation> {
    return apiClient.post<CreatedInvitation>('/organization/invitations', data)
  }

  /**
   * List pending invitations (GET /organization/invitations)
   */
  async listInvitations(): Promise<Invitation[]> {
    return apiClient.get<Invitation[]>('/organization/invitations')
  }

  /**
   * Validate an invitation token (GET /auth/invite/validate?token=)
   */
  async getInvitationByToken(token: string): Promise<InvitationDetail> {
    return apiClient.get<InvitationDetail>(`/auth/invite/validate?token=${encodeURIComponent(token)}`)
  }

  /**
   * Revoke (cancel) invitation (DELETE /organization/invitations/{invitationId})
   */
  async revokeInvitation(invitationId: string): Promise<void> {
    await apiClient.delete(`/organization/invitations/${invitationId}`)
  }

  // ── API Keys ──────────────────────────────────────────────────────────────

  /**
   * List all active API keys for the organization (GET /api-keys)
   */
  async listApiKeys(): Promise<APIKeyPublic[]> {
    return apiClient.get<APIKeyPublic[]>('/api-keys')
  }

  /**
   * Create a new API key (POST /api-keys)
   * The plaintext key is generated client-side and never returned by the backend.
   */
  async createApiKey(name: string): Promise<APIKeyCreated> {
    const key = `sk_live_${_randomHex(32)}`
    const key_hash = await _sha256Hex(key)
    const key_prefix = key.slice(0, 16)
    const created = await apiClient.post<APIKeyPublic>('/api-keys', {
      name,
      key_hash,
      key_prefix,
    })
    return { ...created, key }
  }

  /**
   * Revoke (soft-delete) an API key (DELETE /api-keys/{keyId})
   */
  async revokeApiKey(keyId: string): Promise<void> {
    await apiClient.delete(`/api-keys/${keyId}`)
  }

  // ── Account closure ───────────────────────────────────────────────────────

  /**
   * Initiate account closure — sends a confirmation email
   * (POST /organization/close-account)
   */
  async closeAccount(confirmOrgName: string, reason?: string): Promise<ClosureStatus> {
    return apiClient.post<ClosureStatus>('/organization/close-account', {
      confirm_org_name: confirmOrgName,
      reason,
    })
  }

  /**
   * Initiate account reactivation — sends a confirmation email
   * (POST /organization/reactivate)
   */
  async reactivateAccount(): Promise<ClosureStatus> {
    return apiClient.post<ClosureStatus>('/organization/reactivate')
  }

  /**
   * Confirm a close-account or reactivate action via the emailed token
   * (POST /organization/confirm-action)
   */
  async confirmAction(token: string): Promise<ClosureStatus> {
    return apiClient.post<ClosureStatus>('/organization/confirm-action', { token })
  }
}

export const settingsService = new SettingsService()
export type { UpdateOrganizationRequest, UpdateUserRequest }
