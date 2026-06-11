/**
 * Magic Link Callback Page
 *
 * Handles /auth/magic-link?token=xxx — exchanges the token for a session.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { Loader2, AlertCircle } from 'lucide-react'
import { useAuthStore } from '@/store/auth.store'
import { authService } from '@/api/services/auth.service'

export default function MagicLinkCallbackPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { setUser } = useAuthStore()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = searchParams.get('token')
    if (!token) {
      setError('Invalid sign-in link. Please request a new one.')
      return
    }

    authService.verifyMagicLink(token)
      .then(user => {
        setUser(user)
        navigate('/dashboard', { replace: true })
      })
      .catch(e => {
        setError(e instanceof Error ? e.message : 'Invalid or expired sign-in link.')
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="flex w-full max-w-sm flex-col items-center gap-4 text-center">
        {!error ? (
          <>
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Signing you in…</p>
          </>
        ) : (
          <>
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
              <AlertCircle className="h-6 w-6 text-destructive" />
            </div>
            <p className="text-sm font-medium text-foreground">{error}</p>
            <Link to="/login" className="text-sm text-primary hover:underline">
              Back to sign in
            </Link>
          </>
        )}
      </div>
    </div>
  )
}
