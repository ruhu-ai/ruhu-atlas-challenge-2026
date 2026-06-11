/**
 * Login Page
 *
 * Enterprise sign-in: Google, Magic Link, SSO.
 * Magic Link and SSO show an inline email step within the same card.
 */

import { useState } from 'react'
import { Mail, Key, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { authService } from '@/api/services/auth.service'
import { redirectTo } from '@/utils/navigation'

function GoogleGIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 533.5 544.3" aria-hidden="true" {...props}>
      <path fill="#4285F4" d="M533.5 278.4c0-18.5-1.5-37.1-4.7-55.3H272.1v104.8h147.7c-6.1 33.8-25.4 63.7-53.9 82.7v68h87c51-47 80.6-116.3 80.6-200.2z" />
      <path fill="#34A853" d="M272.1 544.3c73.5 0 135.5-24.1 180.6-65.4l-87-68c-24.2 16.5-55.3 25.9-93.6 25.9-71.9 0-132.8-48.6-154.6-113.9H27.9v71.1c46.3 92 139.2 150.3 244.2 150.3z" />
      <path fill="#FBBC04" d="M117.5 322.9c-11.4-33.8-11.4-70.4 0-104.2V147.6H27.9c-38.7 77.3-38.7 171.8 0 249.1l89.6-73.8z" />
      <path fill="#EA4335" d="M272.1 107.7c40.4-.6 79.5 14.8 109.3 43.1l81.5-81.5C405.2 24.9 339.4-.2 272.1 0 167.1 0 74.2 58.3 27.9 150.3l89.6 71.1C139.3 156.3 200.2 107.7 272.1 107.7z" />
    </svg>
  )
}

type Step =
  | { kind: 'main' }
  | { kind: 'magic-link-email' }
  | { kind: 'magic-link-sent'; email: string }
  | { kind: 'sso-email' }

export default function LoginPage() {
  const [step, setStep] = useState<Step>({ kind: 'main' })
  const [email, setEmail] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [loadingProvider, setLoadingProvider] = useState<'google' | 'magic' | 'sso' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const clearError = () => setError(null)
  const back = () => { setStep({ kind: 'main' }); clearError() }
  const anyLoading = isLoading || loadingProvider !== null

  const handleGoogle = async () => {
    clearError()
    setLoadingProvider('google')
    try {
      const url = await authService.startGoogleSignIn()
      redirectTo(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start Google sign-in')
      setLoadingProvider(null)
    }
  }

  const handleMagicLinkSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    clearError()
    if (!email.trim()) return
    setIsLoading(true)
    try {
      await authService.requestMagicLink(email.trim())
      setStep({ kind: 'magic-link-sent', email: email.trim() })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to send sign-in link')
    } finally {
      setIsLoading(false)
    }
  }

  const handleSSOSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    clearError()
    if (!email.trim()) return
    setLoadingProvider('sso')
    try {
      const url = await authService.startEnterpriseSSO(email.trim())
      redirectTo(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start SSO sign-in')
      setLoadingProvider(null)
    }
  }

  const cardTitle =
    step.kind === 'sso-email' ? 'Continue with SSO'
    : step.kind === 'magic-link-email' ? 'Continue with Magic Link'
    : step.kind === 'magic-link-sent' ? 'Check your email'
    : 'Log in to Ruhu AI'

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-primary">Ruhu AI</h1>
          <p className="mt-2 text-sm text-muted-foreground">Conversation Agent Platform</p>
        </div>

        <Card>
          <CardHeader className="space-y-1">
            <CardTitle className="text-2xl text-center">{cardTitle}</CardTitle>
          </CardHeader>

          <CardContent>
            {/* Error */}
            {error && (
              <div className="mb-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}

            {/* ── Main: three buttons ── */}
            {step.kind === 'main' && (
              <div className="space-y-2">
                <Button
                  type="button"
                  className="w-full"
                  variant="outline"
                  onClick={handleGoogle}
                  disabled={anyLoading}
                  isLoading={loadingProvider === 'google'}
                >
                  <span className="inline-flex items-center justify-center gap-3">
                    <span className="inline-flex h-5 w-5 items-center justify-center">
                      <GoogleGIcon className="h-[18px] w-[18px] shrink-0" />
                    </span>
                    <span>Continue with Google</span>
                  </span>
                </Button>

                <Button
                  type="button"
                  className="w-full"
                  variant="outline"
                  onClick={() => { clearError(); setEmail(''); setStep({ kind: 'magic-link-email' }) }}
                  disabled={anyLoading}
                >
                  <span className="inline-flex items-center justify-center gap-3">
                    <span className="inline-flex h-5 w-5 items-center justify-center">
                      <Mail className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                    </span>
                    <span>Continue with Magic Link</span>
                  </span>
                </Button>

                <Button
                  type="button"
                  className="w-full"
                  variant="outline"
                  onClick={() => { clearError(); setEmail(''); setStep({ kind: 'sso-email' }) }}
                  disabled={anyLoading}
                  isLoading={loadingProvider === 'sso'}
                >
                  <span className="inline-flex items-center justify-center gap-3">
                    <span className="inline-flex h-5 w-5 items-center justify-center">
                      <Key className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                    </span>
                    <span>Continue with SSO</span>
                  </span>
                </Button>
              </div>
            )}

            {/* ── Magic Link: email input ── */}
            {step.kind === 'magic-link-email' && (
              <form onSubmit={handleMagicLinkSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="ml-email">Email Address</Label>
                  <Input
                    id="ml-email"
                    type="email"
                    placeholder="you@company.com"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    required
                    autoFocus
                    autoComplete="email"
                  />
                </div>
                <Button
                  type="submit"
                  className="w-full"
                  disabled={!email.trim() || anyLoading}
                  isLoading={isLoading}
                >
                  Send sign-in link
                </Button>
                <button
                  type="button"
                  onClick={back}
                  className="flex w-full items-center justify-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
                >
                  <ArrowLeft className="h-3.5 w-3.5" /> Back
                </button>
              </form>
            )}

            {/* ── Magic Link: sent confirmation ── */}
            {step.kind === 'magic-link-sent' && (
              <div className="space-y-4 text-center">
                <p className="text-sm text-muted-foreground">
                  We sent a sign-in link to{' '}
                  <span className="font-medium text-foreground">{step.email}</span>.
                  Check your inbox and click the link to sign in.
                </p>
                <p className="text-xs text-muted-foreground">The link expires in 15 minutes.</p>
                <Button
                  type="button"
                  variant="outline"
                  className="w-full"
                  onClick={() => setStep({ kind: 'magic-link-email' })}
                >
                  Resend link
                </Button>
                <button
                  onClick={back}
                  className="flex w-full items-center justify-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
                >
                  <ArrowLeft className="h-3.5 w-3.5" /> Back to sign in
                </button>
              </div>
            )}

            {/* ── SSO: email input ── */}
            {step.kind === 'sso-email' && (
              <form onSubmit={handleSSOSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="sso-email">Work Email</Label>
                  <Input
                    id="sso-email"
                    type="email"
                    placeholder="you@company.com"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    required
                    autoFocus
                    autoComplete="email"
                  />
                </div>
                <Button
                  type="submit"
                  className="w-full"
                  disabled={!email.trim() || anyLoading}
                  isLoading={loadingProvider === 'sso'}
                >
                  Continue with SSO
                </Button>
                <button
                  type="button"
                  onClick={back}
                  className="flex w-full items-center justify-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
                >
                  <ArrowLeft className="h-3.5 w-3.5" /> Back
                </button>
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
