/**
 * Confirm Action Page
 *
 * Handles email-confirmed destructive operations: account closure and reactivation.
 * The user clicks an emailed link → lands here → reviews → clicks Confirm.
 * The signed token in the URL proves identity and intent; the button press
 * is the final human decision.
 */

import { useEffect, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { AlertTriangle, RotateCcw, Loader2, CheckCircle2, XCircle } from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/atoms/card'
import { settingsService } from '@/api/services/settings.service'

type PageState = 'loading' | 'ready' | 'confirming' | 'success' | 'error'
type Action = 'close_account' | 'reactivate'

/** Decode JWT payload without verifying signature — for display only. */
function decodeTokenPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(payload))
  } catch {
    return null
  }
}

export default function ConfirmActionPage() {
  const [searchParams] = useSearchParams()
  const [pageState, setPageState] = useState<PageState>('loading')
  const [action, setAction] = useState<Action | null>(null)
  const [orgName, setOrgName] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [successMessage, setSuccessMessage] = useState('')

  const token = searchParams.get('token') || ''

  useEffect(() => {
    if (!token) {
      setErrorMessage('No confirmation token found. This link may be invalid or expired.')
      setPageState('error')
      return
    }

    const payload = decodeTokenPayload(token)
    if (!payload) {
      setErrorMessage('This confirmation link is malformed. Please request a new one.')
      setPageState('error')
      return
    }

    const act = payload['action'] as string
    if (act !== 'close_account' && act !== 'reactivate') {
      setErrorMessage('Unknown action in confirmation link.')
      setPageState('error')
      return
    }

    setAction(act as Action)
    setOrgName((payload['org_name'] as string) || 'your organisation')
    setPageState('ready')
  }, [token])

  const handleConfirm = async () => {
    setPageState('confirming')
    try {
      const result = await settingsService.confirmAction(token)
      const msg =
        action === 'close_account'
          ? `Account closure scheduled. All data for ${orgName} will be permanently deleted in 30 days.`
          : `Reactivation confirmed. Scheduled deletion for ${orgName} has been cancelled.`
      setSuccessMessage(result.message || msg)
      setPageState('success')
    } catch (err: unknown) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to confirm. The link may have expired.'
      )
      setPageState('error')
    }
  }

  const isCloseAccount = action === 'close_account'

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-primary">Ruhu AI</h1>
        </div>

        <Card>
          <CardHeader className="space-y-1">
            {pageState === 'loading' && (
              <CardTitle className="flex items-center justify-center gap-2 text-xl">
                <Loader2 className="h-5 w-5 animate-spin" />
                Loading…
              </CardTitle>
            )}

            {pageState === 'ready' && (
              <>
                <CardTitle
                  className={`flex items-center gap-2 text-xl ${isCloseAccount ? 'text-destructive' : ''}`}
                >
                  {isCloseAccount ? (
                    <AlertTriangle className="h-5 w-5" />
                  ) : (
                    <RotateCcw className="h-5 w-5 text-primary" />
                  )}
                  {isCloseAccount ? 'Confirm account closure' : 'Confirm reactivation'}
                </CardTitle>
                <CardDescription>
                  {isCloseAccount
                    ? `This will schedule ${orgName} for permanent deletion in 30 days. All agents, users, API keys, and data will be deleted. You can reactivate within the grace period.`
                    : `This will cancel the scheduled deletion and restore ${orgName} to full access.`}
                </CardDescription>
              </>
            )}

            {(pageState === 'confirming') && (
              <CardTitle className="flex items-center justify-center gap-2 text-xl">
                <Loader2 className="h-5 w-5 animate-spin" />
                Confirming…
              </CardTitle>
            )}

            {pageState === 'success' && (
              <CardTitle className="flex items-center gap-2 text-xl text-green-600">
                <CheckCircle2 className="h-5 w-5" />
                {isCloseAccount ? 'Closure scheduled' : 'Account reactivated'}
              </CardTitle>
            )}

            {pageState === 'error' && (
              <CardTitle className="flex items-center gap-2 text-xl text-destructive">
                <XCircle className="h-5 w-5" />
                Confirmation failed
              </CardTitle>
            )}
          </CardHeader>

          <CardContent className="space-y-4">
            {pageState === 'ready' && (
              <Button
                className="w-full"
                variant={isCloseAccount ? 'destructive' : 'primary'}
                onClick={handleConfirm}
              >
                {isCloseAccount ? 'Yes, schedule deletion' : 'Yes, reactivate account'}
              </Button>
            )}

            {pageState === 'success' && (
              <p className="text-sm text-muted-foreground">{successMessage}</p>
            )}

            {pageState === 'error' && (
              <p className="text-sm text-destructive">{errorMessage}</p>
            )}

            {(pageState === 'success' || pageState === 'error') && (
              <Link
                to="/dashboard"
                className="block text-center text-sm text-muted-foreground hover:text-foreground"
              >
                Return to app
              </Link>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
