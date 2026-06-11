import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Loader2, Mail } from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { authService } from '@/api/services/auth.service'

type SignupStep = 'loading' | 'invalid' | 'ready' | 'sent'

export default function SignupPage() {
  const [searchParams] = useSearchParams()
  const [step, setStep] = useState<SignupStep>('loading')
  const [inviteToken, setInviteToken] = useState('')
  const [inviteEmail, setInviteEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loadingProvider, setLoadingProvider] = useState<'google' | 'magic' | null>(null)

  useEffect(() => {
    const tokenFromUrl = (searchParams.get('invite') || '').trim()
    if (!tokenFromUrl) {
      setStep('invalid')
      setError('This signup link is invalid or missing an invite token.')
      window.history.replaceState({}, '', '/signup')
      return
    }

    setInviteToken(tokenFromUrl)
    authService
      .validateInviteToken(tokenFromUrl)
      .then((res) => {
        if (!res.valid || !res.email) {
          setStep('invalid')
          setError('This invite token is invalid, expired, or already used.')
          return
        }
        setInviteEmail(res.email)
        setStep('ready')
      })
      .catch((e) => {
        setStep('invalid')
        setError(e instanceof Error ? e.message : 'Failed to validate invite token.')
      })
      .finally(() => {
        // Remove invite token from URL immediately after validation attempt.
        window.history.replaceState({}, '', '/signup')
      })
  }, [searchParams])

  const isBusy = loadingProvider !== null || step === 'loading'

  const title = useMemo(() => {
    if (step === 'loading') return 'Validating invitation'
    if (step === 'invalid') return 'Invite not valid'
    if (step === 'sent') return 'Check your email'
    return 'Complete your signup'
  }, [step])

  const handleGoogle = async () => {
    setError(null)
    setLoadingProvider('google')
    try {
      const url = await authService.startGoogleSignIn(inviteToken)
      window.location.assign(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start Google sign-in')
      setLoadingProvider(null)
    }
  }

  const handleMagicLink = async () => {
    setError(null)
    setLoadingProvider('magic')
    try {
      await authService.requestMagicLink(inviteEmail, inviteToken)
      setStep('sent')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to send sign-in link')
    } finally {
      setLoadingProvider(null)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-primary">Ruhu AI</h1>
          <p className="mt-2 text-sm text-muted-foreground">Invitation-only signup</p>
        </div>

        <Card>
          <CardHeader className="space-y-1">
            <CardTitle className="text-center text-2xl">{title}</CardTitle>
          </CardHeader>

          <CardContent className="space-y-4">
            {error && (
              <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}

            {step === 'loading' && (
              <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Verifying invite token...
              </div>
            )}

            {(step === 'ready' || step === 'sent') && (
              <div className="space-y-2">
                <Label htmlFor="invite-email">Invited Email</Label>
                <Input id="invite-email" type="email" value={inviteEmail} readOnly />
              </div>
            )}

            {step === 'ready' && (
              <div className="space-y-2">
                <Button
                  type="button"
                  className="w-full"
                  variant="outline"
                  onClick={handleGoogle}
                  isLoading={loadingProvider === 'google'}
                  disabled={isBusy}
                >
                  <span className="inline-flex items-center justify-center gap-3">
                    {loadingProvider !== 'google' && (
                      <svg className="h-[18px] w-[18px] shrink-0" viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
                        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                      </svg>
                    )}
                    <span>Continue with Google</span>
                  </span>
                </Button>
                <Button
                  type="button"
                  className="w-full"
                  variant="outline"
                  onClick={handleMagicLink}
                  isLoading={loadingProvider === 'magic'}
                  disabled={isBusy}
                >
                  <span className="inline-flex items-center justify-center gap-2">
                    <Mail className="h-4 w-4" />
                    Send me a sign-in link
                  </span>
                </Button>
              </div>
            )}

            {step === 'sent' && (
              <p className="text-sm text-muted-foreground">
                We sent a sign-in link to <span className="font-medium text-foreground">{inviteEmail}</span>.
                The link expires in 15 minutes.
              </p>
            )}

            {(step === 'invalid' || step === 'sent') && (
              <Link
                to="/login"
                className="inline-flex w-full items-center justify-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft className="h-3.5 w-3.5" /> Back to sign in
              </Link>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
