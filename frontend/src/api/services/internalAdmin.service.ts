/**
 * Internal admin service — staff-only control-plane API.
 * All endpoints require is_superuser=true.
 */
import { apiClient } from '@/api/client'

export interface StaffInvite {
  token: string
  token_preview: string
  email: string
  invited_by?: string
  personal_note?: string
  created_at?: string
  expires_at?: string
}

export interface StaffUser {
  user_id: string
  email: string
  display_name: string | null
  is_superuser: boolean
  is_active: boolean
  last_login_at?: string | null
  created_at: string
}

export interface StaffAgent {
  id: string
  name: string
  status: string
  is_deployed: boolean
  is_widget_enabled: boolean
  atlas_enabled: boolean
  organization_id: string
  organization_name?: string
  created_at: string
}

export interface PlatformHealth {
  status: 'healthy' | 'degraded' | 'unhealthy'
  database: Record<string, unknown>
  redis: Record<string, unknown>
  livekit: Record<string, unknown>
}

interface ReauthPayload {
  actor_password: string
  reason: string
  notify_target?: boolean
}

const BASE = '/internal'

export const internalAdminService = {
  // ── Invitations ──────────────────────────────────────────────────────────
  listInvites: () =>
    apiClient.get<StaffInvite[]>(`${BASE}/platform-invitations`),

  createInvite: (email: string, personal_note: string | undefined) =>
    apiClient.post<StaffInvite>(`${BASE}/platform-invitations`, {
      email,
      personal_note: personal_note || undefined,
    }),

  revokeInvite: (token: string) =>
    apiClient.delete<{ message: string }>(`${BASE}/platform-invitations/${token}`),

  // ── Health ────────────────────────────────────────────────────────────────
  getHealth: () =>
    apiClient.get<PlatformHealth>(`${BASE}/platform/health`),

  // ── Users ─────────────────────────────────────────────────────────────────
  listUsers: (search?: string, limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (search) params.set('search', search)
    return apiClient.get<StaffUser[]>(`${BASE}/users?${params}`)
  },

  promoteUser: (userId: string, payload: ReauthPayload) =>
    apiClient.post<StaffUser>(`${BASE}/users/${userId}/promote-superuser`, payload),

  revokeUserSuperuser: (userId: string, payload: ReauthPayload) =>
    apiClient.post<StaffUser>(`${BASE}/users/${userId}/revoke-superuser`, payload),

  changeUserRole: (userId: string, new_role: string, payload: ReauthPayload) =>
    apiClient.post<StaffUser>(`${BASE}/users/${userId}/change-role`, {
      ...payload,
      new_role,
    }),

  // ── Agents ────────────────────────────────────────────────────────────────
  listAgents: (search?: string, limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (search) params.set('search', search)
    return apiClient.get<StaffAgent[]>(`${BASE}/agents?${params}`)
  },

  emergencyDisable: (
    agentId: string,
    payload: ReauthPayload & { disable_widget: boolean; disable_atlas: boolean },
  ) =>
    apiClient.post<StaffAgent>(`${BASE}/agents/${agentId}/emergency-disable`, payload),
}
