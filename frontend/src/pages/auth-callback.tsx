/**
 * Auth OAuth/OIDC Callback Page
 *
 * Handles Google/OIDC redirect for user authentication.
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Loader2, XCircle } from 'lucide-react'
import { useAuthStore } from '@/store/auth.store'

export default function AuthCallbackPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { completeOAuthLogin } = useAuthStore()
  const [error, setError] = useState<string | null>(null)
  const hasProcessed = useRef(false)

  useEffect(() => {
    if (hasProcessed.current) {
      return
    }
    hasProcessed.current = true

    const code = searchParams.get('code')
    const state = searchParams.get('state')
    const providerError = searchParams.get('error')
    const providerErrorDescription = searchParams.get('error_description')

    if (providerError) {
      setError(
        providerErrorDescription ||
          (providerError === 'access_denied'
            ? 'Authorization was denied.'
            : 'Authentication failed.')
      )
      return
    }

    if (!code || !state) {
      setError('Missing authentication response parameters.')
      return
    }

    completeOAuthLogin(code, state)
      .then(() => {
        navigate('/dashboard', { replace: true })
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Authentication failed')
      })
  }, [completeOAuthLogin, navigate, searchParams])

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
        <div className="w-full max-w-md rounded-lg border p-6 text-center">
          <XCircle className="mx-auto mb-4 h-12 w-12 text-destructive" />
          <h1 className="mb-2 text-xl font-semibold">Sign-in Failed</h1>
          <p className="mb-6 text-sm text-muted-foreground">{error}</p>
          <Link to="/login" className="inline-flex items-center rounded-md bg-primary px-4 py-2 text-primary-foreground hover:bg-primary/90">
            Back to Login
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-md rounded-lg border p-6 text-center">
        <Loader2 className="mx-auto mb-4 h-12 w-12 animate-spin text-primary" />
        <h1 className="mb-2 text-xl font-semibold">Completing Sign-in</h1>
        <p className="text-sm text-muted-foreground">Please wait...</p>
      </div>
    </div>
  )
}
