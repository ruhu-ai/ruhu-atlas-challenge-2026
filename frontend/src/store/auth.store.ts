/**
 * Authentication Store (Zustand)
 *
 * Auth is entirely cookie-based (httpOnly access_token + refresh_token cookies set
 * by the backend). There is no in-memory JWT — all API requests rely on
 * `credentials: 'include'` in the fetch client.
 *
 * Boot sequence:
 *   1. initAuth() fires on module load → calls GET /auth/me with the cookie.
 *   2. On success:  isAuthenticated=true, user=<me>, isInitialized=true.
 *   3. On failure:  isAuthenticated=false,  user=null,  isInitialized=true.
 *   4. ProtectedRoute reads isAuthenticated from the store (no extra API call).
 *
 * Mid-session expiry:
 *   Any 401 response dispatches the 'unauthorized' window event, which triggers
 *   logout + redirect to /login.
 */

import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { User } from '@/types'
import { authService } from '@/api/services/auth.service'
import { cancelAllRequests } from '@/api/client'
import { queryClient } from '@/lib/query-client'

// Proactive refresh: fire at 75% of the 1-hour access token TTL (45 min).
const REFRESH_INTERVAL_MS = 45 * 60 * 1000
let _refreshIntervalId: ReturnType<typeof setInterval> | null = null

function startRefreshInterval() {
  stopRefreshInterval()
  _refreshIntervalId = setInterval(async () => {
    try {
      await authService.refresh()
    } catch {
      // Refresh failed — the 401 handler in client.ts will catch the next API
      // call and fire the unauthorized event if the session is truly gone.
    }
  }, REFRESH_INTERVAL_MS)
}

function stopRefreshInterval() {
  if (_refreshIntervalId !== null) {
    clearInterval(_refreshIntervalId)
    _refreshIntervalId = null
  }
}

const PUBLIC_AUTH_ROUTES = new Set([
  '/login',
  '/signup',
  '/register',
  '/forgot-password',
  '/auth/callback',
  '/auth/magic-link',
  '/accept-invitation',
  '/confirm-action',
  '/terms',
  '/privacy',
  '/integrations/oauth/callback',
])

export function isPublicAuthRoute(pathname: string): boolean {
  return PUBLIC_AUTH_ROUTES.has(pathname)
}

interface AuthState {
  user: User | null
  isAuthenticated: boolean
  isInitialized: boolean
  isLoading: boolean
  isLoggingOut: boolean
  error: string | null

  completeOAuthLogin: (code: string, state: string) => Promise<void>
  logout: () => Promise<void>
  setUser: (user: User) => void
  clearError: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isAuthenticated: false,
      isInitialized: false,
      isLoading: false,
      isLoggingOut: false,
      error: null,

      completeOAuthLogin: async (code, state) => {
        set({ isLoading: true, error: null })
        try {
          const user = await authService.completeOAuthSignIn(code, state)
          set({ user, isAuthenticated: true, isLoading: false })
          startRefreshInterval()
        } catch (error) {
          set({ error: error instanceof Error ? error.message : 'OAuth login failed', isLoading: false })
          throw error
        }
      },

      logout: async () => {
        if (useAuthStore.getState().isLoggingOut) return
        stopRefreshInterval()
        set({ isLoggingOut: true })
        try {
          await Promise.race([
            authService.logout(),
            new Promise<never>((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000)),
          ])
        } catch (err) {
          console.error('Logout API call failed:', err)
        } finally {
          cancelAllRequests()
          queryClient.clear()
          set({ user: null, isAuthenticated: false, isLoggingOut: false, error: null })
        }
      },

      setUser: (user) => set({ user, isAuthenticated: true }),
      clearError: () => set({ error: null }),
    }),
    {
      name: 'auth-storage',
      storage: createJSONStorage(() => localStorage),
      // Only persist the user profile for instant UI hydration on revisit.
      // isAuthenticated is NOT persisted — initAuth() always re-validates via
      // /auth/me on load, so the persisted user is only used for optimistic display.
      partialize: (state) => ({ user: state.user }),
    }
  )
)

// On every page load, verify the session cookie against the server.
// Sets isInitialized=true regardless of outcome so ProtectedRoute can render.
if (typeof window !== 'undefined') {
  const initAuth = async () => {
    try {
      const user = await authService.getCurrentUser()
      useAuthStore.setState({ user, isAuthenticated: true, isInitialized: true })
      startRefreshInterval()
    } catch {
      // Access token expired — try silent refresh before redirecting to login.
      try {
        const user = await authService.refresh()
        useAuthStore.setState({ user, isAuthenticated: true, isInitialized: true })
        startRefreshInterval()
      } catch {
        useAuthStore.setState({ user: null, isAuthenticated: false, isInitialized: true })
        if (!isPublicAuthRoute(window.location.pathname)) {
          window.location.replace('/login')
        }
      }
    }
  }
  void initAuth()
}

// 401 mid-session → log out and redirect.
if (typeof window !== 'undefined') {
  let handling = false
  window.addEventListener('unauthorized', async () => {
    if (handling) return
    const state = useAuthStore.getState()
    if (!state.isInitialized || !state.isAuthenticated) return
    handling = true
    try {
      await state.logout()
    } finally {
      window.location.replace('/login')
      setTimeout(() => { handling = false }, 1000)
    }
  })
}
