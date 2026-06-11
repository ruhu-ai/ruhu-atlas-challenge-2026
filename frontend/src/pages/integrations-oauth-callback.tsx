/**
 * OAuth Callback Page
 *
 * Handles OAuth redirect from integration providers.
 * Sends the authorization code back to the parent window via postMessage.
 *
 * Security considerations:
 * - postMessage target origin is strictly set to window.location.origin
 * - No sensitive data is exposed in error messages
 * - Validates that this page was opened as a popup
 */

import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Loader2, CheckCircle, XCircle, AlertTriangle } from 'lucide-react'

type CallbackStatus = 'processing' | 'success' | 'error' | 'no_opener'

export default function IntegrationsOAuthCallbackPage() {
  const [searchParams] = useSearchParams()
  const [status, setStatus] = useState<CallbackStatus>('processing')
  const [message, setMessage] = useState('')

  useEffect(() => {
    // Check if this was opened as a popup
    if (!window.opener) {
      setStatus('no_opener')
      setMessage('This page should be opened as a popup. Please try connecting again from the Integrations page.')
      return
    }

    const code = searchParams.get('code')
    const state = searchParams.get('state')
    const error = searchParams.get('error')
    const errorDescription = searchParams.get('error_description')

    // Handle error response from OAuth provider
    if (error) {
      setStatus('error')
      // Use generic message to avoid leaking sensitive error details
      setMessage(
        error === 'access_denied'
          ? 'Authorization was denied. Please try again.'
          : 'Authorization failed. Please try again.'
      )

      // Send error to parent window (same origin only)
      window.opener.postMessage(
        {
          type: 'oauth_callback',
          error: error,
          error_description: error === 'access_denied' ? 'User denied access' : 'Authorization failed',
        },
        window.location.origin
      )
      return
    }

    // Validate required parameters
    if (!code || !state) {
      setStatus('error')
      setMessage('Invalid callback parameters. Please try again.')

      window.opener.postMessage(
        {
          type: 'oauth_callback',
          error: 'invalid_request',
          error_description: 'Missing authorization parameters',
        },
        window.location.origin
      )
      return
    }

    // Success - send code to parent
    setStatus('success')
    setMessage('Authorization successful!')

    window.opener.postMessage(
      {
        type: 'oauth_callback',
        code,
        state,
      },
      window.location.origin
    )

    // Auto-close popup after brief delay
    const closeTimeout = setTimeout(() => {
      window.close()
    }, 1500)

    return () => clearTimeout(closeTimeout)
  }, [searchParams])

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="text-center p-8 max-w-md">
        {status === 'processing' && (
          <>
            <Loader2 className="h-12 w-12 animate-spin text-primary mx-auto mb-4" />
            <h1 className="text-xl font-semibold mb-2">Processing Authorization</h1>
            <p className="text-muted-foreground">Please wait...</p>
          </>
        )}

        {status === 'success' && (
          <>
            <CheckCircle className="h-12 w-12 text-emerald-500 mx-auto mb-4" />
            <h1 className="text-xl font-semibold mb-2">Connected!</h1>
            <p className="text-muted-foreground">{message}</p>
            <p className="text-sm text-muted-foreground mt-4">
              This window will close automatically...
            </p>
          </>
        )}

        {status === 'error' && (
          <>
            <XCircle className="h-12 w-12 text-destructive mx-auto mb-4" />
            <h1 className="text-xl font-semibold mb-2">Authorization Failed</h1>
            <p className="text-muted-foreground">{message}</p>
            <button
              onClick={() => window.close()}
              className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
            >
              Close Window
            </button>
          </>
        )}

        {status === 'no_opener' && (
          <>
            <AlertTriangle className="h-12 w-12 text-amber-500 mx-auto mb-4" />
            <h1 className="text-xl font-semibold mb-2">Invalid Access</h1>
            <p className="text-muted-foreground">{message}</p>
            <a
              href="/"
              className="mt-4 inline-block px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
            >
              Go to Dashboard
            </a>
          </>
        )}
      </div>
    </div>
  )
}
