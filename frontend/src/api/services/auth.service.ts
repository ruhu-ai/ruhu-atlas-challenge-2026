/**
 * Authentication Service
 *
 * Thin layer over the Ruhu backend auth endpoints.
 * Services return backend types directly — the only transformation is the
 * structural merge of MeResponse.user + MeResponse.organization into a
 * single User object (no field renaming).
 */

import { apiClient } from '../client'
import type { User } from '@/types'

// ── Backend response shapes (mirrors Pydantic models) ────────────────────────

export interface MeResponse {
  user: {
    user_id: string
    email: string
    display_name: string | null
    avatar_url: string | null
    timezone: string
    language: string
    preferences: Record<string, unknown>
    is_superuser: boolean
  }
  organization: {
    organization_id: string
    slug: string
    name: string
    domain: string | null
    icon_url: string | null
    role: string
    is_account_owner: boolean
  }
  session_id: string
  expires_at: string
}

interface OAuthStartResponse {
  authorization_url: string
}

export interface InviteValidateResponse {
  valid: boolean
  email?: string
  expires_at?: string
}

// ── Structural merge — one place, no field renaming ──────────────────────────

export function meToUser(me: MeResponse): User {
  return { ...me.user, organization: me.organization }
}

// ── Service ──────────────────────────────────────────────────────────────────

class AuthService {
  async logout(): Promise<void> {
    await apiClient.post('/auth/logout', {})
  }

  async getCurrentUser(): Promise<User> {
    return meToUser(await apiClient.get<MeResponse>('/auth/me'))
  }

  async refresh(): Promise<User> {
    return meToUser(await apiClient.post<MeResponse>('/auth/refresh', {}))
  }

  async startGoogleSignIn(inviteToken?: string): Promise<string> {
    const payload: Record<string, string> = {
      redirect_uri: `${window.location.origin}/auth/callback`,
    }
    if (inviteToken) payload.invite_token = inviteToken
    const res = await apiClient.post<OAuthStartResponse>('/auth/oauth/google/start', payload)
    return res.authorization_url
  }

  async startEnterpriseSSO(email: string): Promise<string> {
    const res = await apiClient.post<OAuthStartResponse>('/auth/oauth/sso/start', {
      email,
      redirect_uri: `${window.location.origin}/auth/callback`,
    })
    return res.authorization_url
  }

  async completeOAuthSignIn(code: string, state: string): Promise<User> {
    return meToUser(await apiClient.post<MeResponse>('/auth/oauth/callback', {
      code,
      state,
      redirect_uri: `${window.location.origin}/auth/callback`,
    }))
  }

  async requestMagicLink(email: string, inviteToken?: string): Promise<void> {
    const payload: Record<string, string> = { email }
    if (inviteToken) payload.invite_token = inviteToken
    await apiClient.post('/auth/magic-link/request', payload)
  }

  async verifyMagicLink(token: string): Promise<User> {
    return meToUser(await apiClient.post<MeResponse>('/auth/magic-link/verify', { token }))
  }

  async challengeDemoLogin(email: string, password: string): Promise<User> {
    return meToUser(await apiClient.post<MeResponse>('/auth/challenge-demo/login', {
      email,
      password,
    }))
  }

  async validateInviteToken(token: string): Promise<InviteValidateResponse> {
    return apiClient.get<InviteValidateResponse>(
      `/auth/invite/validate?${new URLSearchParams({ token })}`
    )
  }

  async acceptInvitation(invitationToken: string, displayName?: string): Promise<User> {
    return meToUser(await apiClient.post<MeResponse>('/auth/invitations/accept', {
      invitation_token: invitationToken,
      display_name: displayName ?? null,
    }))
  }
}

export const authService = new AuthService()
