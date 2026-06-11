import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const mockStartGoogleSignIn = jest.fn()
const mockRequestMagicLink = jest.fn()
const mockStartEnterpriseSSO = jest.fn()
const mockRedirectTo = jest.fn()

jest.mock('@/api/client', () => ({
  apiClient: { get: jest.fn(), post: jest.fn() },
  setApiTokenGetter: jest.fn(),
}))

jest.mock('@/utils/logger', () => ({
  apiLogger: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
  logger: { log: jest.fn(), error: jest.fn() },
  createLogger: jest.fn(() => ({ log: jest.fn(), error: jest.fn(), warn: jest.fn() })),
  reportError: jest.fn(),
}))

jest.mock('@/api/services/auth.service', () => ({
  authService: {
    startGoogleSignIn: (...args: unknown[]) => mockStartGoogleSignIn(...args),
    requestMagicLink: (...args: unknown[]) => mockRequestMagicLink(...args),
    startEnterpriseSSO: (...args: unknown[]) => mockStartEnterpriseSSO(...args),
  },
}))

jest.mock('@/utils/navigation', () => ({
  redirectTo: (...args: unknown[]) => mockRedirectTo(...args),
}))

import LoginPage from '@/pages/login'

function renderLoginPage() {
  return render(<LoginPage />)
}

describe('LoginPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders the provider-first sign-in options', () => {
    renderLoginPage()

    expect(screen.getByRole('heading', { name: 'Log in to Ruhu AI' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue with Magic Link' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue with SSO' })).toBeInTheDocument()
  })

  it('starts Google sign-in and redirects to the provider', async () => {
    mockStartGoogleSignIn.mockResolvedValue('https://accounts.google.com/o/oauth2/v2/auth')
    renderLoginPage()

    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))

    await waitFor(() => {
      expect(mockStartGoogleSignIn).toHaveBeenCalledTimes(1)
      expect(mockRedirectTo).toHaveBeenCalledWith('https://accounts.google.com/o/oauth2/v2/auth')
    })
  })

  it('opens the Magic Link step and returns to the main step', () => {
    renderLoginPage()

    fireEvent.click(screen.getByRole('button', { name: 'Continue with Magic Link' }))
    expect(screen.getByRole('heading', { name: 'Continue with Magic Link' })).toBeInTheDocument()
    expect(screen.getByLabelText('Email Address')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Back' }))

    expect(screen.getByRole('heading', { name: 'Log in to Ruhu AI' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeInTheDocument()
  })

  it('requests a magic link and shows the sent confirmation state', async () => {
    mockRequestMagicLink.mockResolvedValue(undefined)
    renderLoginPage()

    fireEvent.click(screen.getByRole('button', { name: 'Continue with Magic Link' }))
    fireEvent.change(screen.getByLabelText('Email Address'), {
      target: { value: '  test@ruhu.ai  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send sign-in link' }))

    await waitFor(() => {
      expect(mockRequestMagicLink).toHaveBeenCalledWith('test@ruhu.ai')
    })
    expect(screen.getByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    expect(screen.getByText(/test@ruhu\.ai/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resend link' })).toBeInTheDocument()
  })

  it('shows an inline error when magic link delivery fails', async () => {
    mockRequestMagicLink.mockRejectedValue(new Error('Unable to send sign-in link'))
    renderLoginPage()

    fireEvent.click(screen.getByRole('button', { name: 'Continue with Magic Link' }))
    fireEvent.change(screen.getByLabelText('Email Address'), {
      target: { value: 'test@ruhu.ai' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send sign-in link' }))

    expect(await screen.findByText('Unable to send sign-in link')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Continue with Magic Link' })).toBeInTheDocument()
  })

  it('starts enterprise SSO with a trimmed email and redirects', async () => {
    mockStartEnterpriseSSO.mockResolvedValue('https://sso.example.com/authorize')
    renderLoginPage()

    fireEvent.click(screen.getByRole('button', { name: 'Continue with SSO' }))
    fireEvent.change(screen.getByLabelText('Work Email'), {
      target: { value: '  ops@company.com  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Continue with SSO' }))

    await waitFor(() => {
      expect(mockStartEnterpriseSSO).toHaveBeenCalledWith('ops@company.com')
      expect(mockRedirectTo).toHaveBeenCalledWith('https://sso.example.com/authorize')
    })
  })
})
