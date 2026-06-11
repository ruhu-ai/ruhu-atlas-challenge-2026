/**
 * Accept Invitation Page
 *
 * Public page where invited users can accept an invitation and join an organization.
 * The new Ruhu uses token-based invitations with no password — auth is via cookie session.
 */

import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Loader2, CheckCircle, XCircle } from 'lucide-react'
import { authService } from '@/api/services/auth.service'
import { useAuthStore } from '@/store/auth.store'

export default function AcceptInvitationPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { setUser } = useAuthStore()
  const inviteToken = searchParams.get('token')

  const [validating, setValidating] = useState(true)
  const [inviteEmail, setInviteEmail] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [displayName, setDisplayName] = useState('')

  // Validate the invite token on load
  useEffect(() => {
    if (!inviteToken) {
      setError('Invalid invitation link — no token found.')
      setValidating(false)
      return
    }

    authService.validateInviteToken(inviteToken)
      .then(res => {
        if (!res.valid) {
          setError('This invitation has expired or has already been used.')
        } else {
          setInviteEmail(res.email ?? null)
        }
      })
      .catch(() => setError('Failed to validate invitation. It may have expired.'))
      .finally(() => setValidating(false))
  }, [inviteToken])

  const handleAccept = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!inviteToken) return

    setIsSubmitting(true)
    setError(null)
    try {
      const user = await authService.acceptInvitation(inviteToken, displayName.trim() || undefined)
      setUser(user)
      navigate('/dashboard', { replace: true })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to accept invitation.')
      setIsSubmitting(false)
    }
  }

  if (validating) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error && !inviteEmail) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <div className="flex items-center gap-2">
              <XCircle className="h-6 w-6 text-destructive" />
              <CardTitle>Invalid Invitation</CardTitle>
            </div>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => navigate('/login')} className="w-full">
              Go to Login
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-primary">Ruhu AI</h1>
          <p className="mt-2 text-sm text-muted-foreground">You've been invited to join</p>
        </div>

        <Card>
          <CardHeader className="space-y-1">
            <CardTitle className="text-2xl text-center">Accept Invitation</CardTitle>
            {inviteEmail && (
              <CardDescription className="text-center">
                Joining as <span className="font-medium text-foreground">{inviteEmail}</span>
              </CardDescription>
            )}
          </CardHeader>
          <CardContent>
            <form onSubmit={handleAccept} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="display_name">Your Name <span className="text-muted-foreground">(optional)</span></Label>
                <Input
                  id="display_name"
                  type="text"
                  placeholder="Jane Smith"
                  value={displayName}
                  onChange={e => setDisplayName(e.target.value)}
                  autoFocus
                  disabled={isSubmitting}
                />
              </div>

              {error && (
                <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                  {error}
                </div>
              )}

              <Button type="submit" className="w-full" disabled={isSubmitting}>
                {isSubmitting ? (
                  <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Joining…</>
                ) : (
                  <><CheckCircle className="mr-2 h-4 w-4" /> Accept & Join</>
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
