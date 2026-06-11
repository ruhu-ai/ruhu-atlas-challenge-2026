/**
 * Main App Component
 *
 * Configures routing, providers, and global app structure.
 * Uses React.lazy for code splitting — each page is loaded on demand.
 */

import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import { queryClient } from '@/lib/query-client'
import { useAuthStore } from '@/store/auth.store'
import { useThemeSync } from '@/store/ui.store'
import { AppErrorBoundary } from '@/components/common/AppErrorBoundary'
import { PageErrorBoundary } from '@/components/common/PageErrorBoundary'
import { PageLoadingSkeleton } from '@/components/common/LazyLoadWrapper'

// Lazy-loaded pages — each becomes a separate chunk
const LoginPage = lazy(() => import('@/pages/login'))
const SignupPage = lazy(() => import('@/pages/signup'))
const MagicLinkCallbackPage = lazy(() => import('@/pages/magic-link-callback'))
const AuthCallbackPage = lazy(() => import('@/pages/auth-callback'))
const ConfirmActionPage = lazy(() => import('@/pages/confirm-action'))
const TermsPage = lazy(() => import('@/pages/terms'))
const PrivacyPage = lazy(() => import('@/pages/privacy'))
const AcceptInvitationPage = lazy(() => import('@/pages/accept-invitation'))
const IntegrationsOAuthCallbackPage = lazy(() => import('@/pages/integrations-oauth-callback'))

const DashboardPage = lazy(() => import('@/pages/dashboard'))
const AgentsPage = lazy(() => import('@/pages/agents'))
const AgentCanvasPage = lazy(() => import('@/pages/agent-canvas'))
const AgentSetupPage = lazy(() => import('@/pages/agent-setup'))
const WidgetSettingsPage = lazy(() => import('@/pages/widget-settings'))
const AgentAnalysisPage = lazy(() => import('@/pages/agent-analysis'))
const CallsPage = lazy(() => import('@/pages/calls'))
const BrowserTasksPage = lazy(() => import('@/pages/browser-tasks'))
const PhoneNumbersPage = lazy(() => import('@/pages/phone-numbers'))
const KnowledgeBasePage = lazy(() => import('@/pages/knowledge-base'))
const AnalyticsPage = lazy(() => import('@/pages/analytics'))
const InsightsPage = lazy(() => import('@/pages/insights-enhanced'))
const EvaluationPage = lazy(() => import('@/pages/testing'))
const SettingsPage = lazy(() => import('@/pages/settings'))
const PricingPage = lazy(() => import('@/pages/pricing'))
const BillingSettingsPage = lazy(() => import('@/pages/billing-settings'))
const IntentsTagsPage = lazy(() => import('@/pages/intents-tags'))
const KPIGoalsPage = lazy(() => import('@/pages/kpi-goals'))
const JourneysPage = lazy(() => import('@/pages/journeys'))
const RulesPage = lazy(() => import('@/pages/rules'))
const ToolsPage = lazy(() => import('@/pages/tools'))
const AuditPage = lazy(() => import('@/pages/audit'))
const TicketsPage = lazy(() => import('@/pages/tickets'))
const StaffPortalPage = lazy(() => import('@/pages/staff'))
const TemplatesPage = lazy(() => import('@/pages/templates'))

// Runs inside ProtectedRoute — by this point setUser() has been called with
// fresh server data, so is_superuser reflects the current DB value.
function SuperuserGuard({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore()
  if (!user?.is_superuser) {
    return <Navigate to="/dashboard" replace />
  }
  return <PageErrorBoundary>{children}</PageErrorBoundary>
}

// Staff Route — requires is_superuser in addition to normal auth
function StaffRoute({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <SuperuserGuard>{children}</SuperuserGuard>
    </ProtectedRoute>
  )
}

// Protected Route — waits for initAuth() to complete, then reads auth state from store.
// No additional API call: initAuth() already validated the session via /auth/me.
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isInitialized, isAuthenticated } = useAuthStore()

  if (!isInitialized) return <PageLoadingSkeleton />
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <PageErrorBoundary>{children}</PageErrorBoundary>
}

function App() {
  useThemeSync()

  return (
    <AppErrorBoundary>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Suspense fallback={<PageLoadingSkeleton />}>
          <Routes>
            {/* Public Routes */}
            <Route path="/login" element={<LoginPage />} />
            <Route path="/signup" element={<SignupPage />} />
            <Route path="/register" element={<Navigate to="/login" replace />} />
            <Route path="/forgot-password" element={<Navigate to="/login" replace />} />
            <Route path="/auth/callback" element={<AuthCallbackPage />} />
            <Route path="/auth/magic-link" element={<MagicLinkCallbackPage />} />
            <Route path="/terms" element={<TermsPage />} />
            <Route path="/privacy" element={<PrivacyPage />} />
            <Route path="/accept-invitation" element={<AcceptInvitationPage />} />
            <Route path="/confirm-action" element={<ConfirmActionPage />} />
            <Route path="/integrations/oauth/callback" element={<IntegrationsOAuthCallbackPage />} />

            {/* Protected Routes */}
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <DashboardPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/agents"
              element={
                <ProtectedRoute>
                  <AgentsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/agents/:id/canvas"
              element={
                <ProtectedRoute>
                  <AgentCanvasPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/agents/:id/setup"
              element={
                <ProtectedRoute>
                  <AgentSetupPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/agents/:id/widget"
              element={
                <ProtectedRoute>
                  <WidgetSettingsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/agents/:id/analysis"
              element={
                <ProtectedRoute>
                  <AgentAnalysisPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/calls"
              element={
                <ProtectedRoute>
                  <CallsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/browser-tasks"
              element={
                <ProtectedRoute>
                  <BrowserTasksPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/operations/phone-numbers"
              element={
                <ProtectedRoute>
                  <PhoneNumbersPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/knowledge-base"
              element={
                <ProtectedRoute>
                  <KnowledgeBasePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/tickets"
              element={
                <ProtectedRoute>
                  <TicketsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/analytics"
              element={
                <ProtectedRoute>
                  <AnalyticsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/insights"
              element={
                <ProtectedRoute>
                  <InsightsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/intents-tags"
              element={
                <ProtectedRoute>
                  <IntentsTagsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/kpi-goals"
              element={
                <ProtectedRoute>
                  <KPIGoalsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/kpi-goals/:goalId"
              element={
                <ProtectedRoute>
                  <KPIGoalsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/journeys"
              element={
                <ProtectedRoute>
                  <JourneysPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/journeys/:journeyId"
              element={
                <ProtectedRoute>
                  <JourneysPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/rules"
              element={
                <ProtectedRoute>
                  <RulesPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/tools"
              element={
                <ProtectedRoute>
                  <ToolsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/evaluation"
              element={
                <ProtectedRoute>
                  <EvaluationPage />
                </ProtectedRoute>
              }
            />
            <Route path="/testing" element={<Navigate to="/evaluation" replace />} />
            <Route
              path="/audit"
              element={
                <ProtectedRoute>
                  <AuditPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings"
              element={
                <ProtectedRoute>
                  <SettingsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings/:tab"
              element={
                <ProtectedRoute>
                  <SettingsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/pricing"
              element={
                <ProtectedRoute>
                  <PricingPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings/billing"
              element={
                <ProtectedRoute>
                  <BillingSettingsPage />
                </ProtectedRoute>
              }
            />

            <Route
              path="/templates"
              element={
                <ProtectedRoute>
                  <TemplatesPage />
                </ProtectedRoute>
              }
            />

            {/* Staff Portal — requires is_superuser */}
            <Route
              path="/staff"
              element={
                <StaffRoute>
                  <Navigate to="/staff/invitations" replace />
                </StaffRoute>
              }
            />
            <Route
              path="/staff/:section"
              element={
                <StaffRoute>
                  <StaffPortalPage />
                </StaffRoute>
              }
            />

            {/* Default Route */}
            <Route path="/" element={<Navigate to="/dashboard" replace />} />

            {/* 404 Catch-all */}
            <Route
              path="*"
              element={
                <div className="flex min-h-screen items-center justify-center bg-background p-6">
                  <div className="max-w-md text-center space-y-4">
                    <h1 className="text-6xl font-bold text-muted-foreground">404</h1>
                    <h2 className="text-xl font-semibold text-foreground">Page not found</h2>
                    <p className="text-muted-foreground">
                      The page you're looking for doesn't exist or has been moved.
                    </p>
                    <a
                      href="/dashboard"
                      className="inline-block rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
                    >
                      Go to Dashboard
                    </a>
                  </div>
                </div>
              }
            />
          </Routes>
        </Suspense>
      </BrowserRouter>

      {/* React Query Devtools (only in development) */}
      {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
    </AppErrorBoundary>
  )
}

export default App
