/**
 * Billing Settings Page
 *
 * Manages subscription, usage, invoices, and payment methods.
 */

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  CreditCard,
  FileText,
  TrendingUp,
  AlertCircle,
  ExternalLink,
  CheckCircle,
  XCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import { billingService } from '@/api/services/billing.service'
import { Button } from '@/components/atoms/button'
import { Card } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { Separator } from '@/components/atoms/separator'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/atoms/tabs'
import { UsageWidget } from '@/components/molecules/usage-widget'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import type { Subscription, Invoice } from '@/types/billing'

function BillingSettingsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState('overview')

  // Fetch current subscription
  const { data: subscription, isLoading: loadingSubscription } = useQuery({
    queryKey: ['current-subscription'],
    queryFn: () => billingService.getCurrentSubscription(),
  })

  // Fetch usage metrics
  const { data: usage, isLoading: loadingUsage } = useQuery({
    queryKey: ['usage-metrics'],
    queryFn: () => billingService.getUsageMetrics(),
    enabled: !!subscription,
  })

  // Fetch invoices
  const { data: invoices } = useQuery({
    queryKey: ['invoices'],
    queryFn: () => billingService.getInvoices(10),
    enabled: !!subscription,
  })

  // Cancel subscription mutation
  const cancelMutation = useMutation({
    mutationFn: () => billingService.cancelSubscription(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['current-subscription'] })
      toast.success('Subscription will be cancelled at the end of the billing period')
    },
    onError: (error: any) => {
      toast.error(error.message || 'Failed to cancel subscription')
    },
  })

  // Resume subscription mutation
  const resumeMutation = useMutation({
    mutationFn: () => billingService.resumeSubscription(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['current-subscription'] })
      toast.success('Subscription resumed successfully')
    },
    onError: (error: any) => {
      toast.error(error.message || 'Failed to resume subscription')
    },
  })

  const handleManageBilling = async () => {
    try {
      const { portal_url } = await billingService.getBillingPortal()
      // Allow Stripe portal URLs (production) and same-origin relative paths (mock/dev)
      const isRelative = portal_url.startsWith('/')
      if (!isRelative) {
        const billingUrl = new URL(portal_url)
        if (!billingUrl.hostname.endsWith('.stripe.com') && !billingUrl.hostname.endsWith('.stripe.dev')) {
          throw new Error('Invalid billing portal URL')
        }
      }
      if (isRelative) {
        navigate(portal_url)
      } else {
        window.location.href = portal_url
      }
    } catch (error: any) {
      toast.error(error.message || 'Failed to open billing portal')
    }
  }

  const handleCancelSubscription = () => {
    if (
      window.confirm(
        'Are you sure you want to cancel? You will retain access until the end of your billing period.'
      )
    ) {
      cancelMutation.mutate()
    }
  }

  const handleResumeSubscription = () => {
    resumeMutation.mutate()
  }

  const handleUpgrade = () => {
    navigate('/pricing')
  }

  const formatDate = (dateStr: string | null | undefined) => {
    if (!dateStr) return 'N/A'
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  }

  const getStatusBadge = (status: Subscription['status']) => {
    const statusConfig: Record<
      Subscription['status'],
      { label: string; variant: 'default' | 'destructive' | 'outline' }
    > = {
      active: { label: 'Active', variant: 'default' },
      cancelled: { label: 'Cancelled', variant: 'destructive' },
      past_due: { label: 'Past Due', variant: 'destructive' },
      trialing: { label: 'Trial', variant: 'outline' },
      paused: { label: 'Paused', variant: 'outline' },
    }

    const config = statusConfig[status] || { label: status, variant: 'outline' }
    return <Badge variant={config.variant}>{config.label}</Badge>
  }

  const getInvoiceStatusIcon = (status: Invoice['status']) => {
    if (status === 'paid') return <CheckCircle className="w-5 h-5 text-green-500" />
    if (status === 'void' || status === 'uncollectible')
      return <XCircle className="w-5 h-5 text-red-500" />
    return <AlertCircle className="w-5 h-5 text-amber-500" />
  }

  if (loadingSubscription) {
    return (
      <DashboardLayout>
        <div className="flex items-center justify-center h-96">
          <div className="text-gray-500">Loading billing information...</div>
        </div>
      </DashboardLayout>
    )
  }

  if (!subscription) {
    return (
      <DashboardLayout>
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <Card className="p-8 text-center">
            <h2 className="text-2xl font-bold mb-4">No Active Subscription</h2>
            <p className="text-gray-600 dark:text-gray-400 mb-6">
              You're currently on the free plan. Upgrade to unlock more features and higher limits.
            </p>
            <Button onClick={() => navigate('/pricing')}>View Plans</Button>
          </Card>
        </div>
      </DashboardLayout>
    )
  }

  return (
    <DashboardLayout>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold mb-2">Billing & Subscription</h1>
          <p className="text-gray-600 dark:text-gray-400">
            Manage your subscription, usage, and payment methods
          </p>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="mb-6">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="usage">Usage</TabsTrigger>
            <TabsTrigger value="invoices">Invoices</TabsTrigger>
          </TabsList>

          {/* Overview Tab */}
          <TabsContent value="overview" className="space-y-6">
            {/* Current Plan Card */}
            <Card className="p-6">
              <div className="flex items-start justify-between mb-6">
                <div>
                  <h2 className="text-xl font-semibold mb-2">Current Plan</h2>
                  <div className="flex items-center gap-3">
                    <span className="text-2xl font-bold">{subscription.plan.name}</span>
                    {getStatusBadge(subscription.status)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-3xl font-bold">
                    ${parseFloat(subscription.plan.price_monthly).toFixed(0)}
                  </div>
                  <div className="text-sm text-gray-500">
                    /{subscription.billing_period === 'monthly' ? 'month' : 'year'}
                  </div>
                </div>
              </div>

              <p className="text-gray-600 dark:text-gray-400 mb-6">
                {subscription.plan.description}
              </p>

              <Separator className="mb-6" />

              <div className="grid grid-cols-2 gap-4 mb-6">
                <div>
                  <div className="text-sm text-gray-500 mb-1">Billing Period</div>
                  <div className="font-medium capitalize">{subscription.billing_period}</div>
                </div>
                <div>
                  <div className="text-sm text-gray-500 mb-1">Next Billing Date</div>
                  <div className="font-medium">{formatDate(subscription.current_period_end)}</div>
                </div>
              </div>

              {subscription.cancel_at_period_end && (
                <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-4 mb-6">
                  <div className="flex items-start gap-3">
                    <AlertCircle className="w-5 h-5 text-amber-600 dark:text-amber-500 shrink-0 mt-0.5" />
                    <div>
                      <p className="font-medium text-amber-900 dark:text-amber-100">
                        Subscription Scheduled for Cancellation
                      </p>
                      <p className="text-sm text-amber-800 dark:text-amber-200 mt-1">
                        Your subscription will end on {formatDate(subscription.current_period_end)}.
                        You can resume anytime before then.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              <div className="flex gap-3">
                <Button onClick={handleUpgrade} variant="primary">
                  <TrendingUp className="w-4 h-4 mr-2" />
                  Upgrade Plan
                </Button>
                <Button onClick={handleManageBilling} variant="outline">
                  <CreditCard className="w-4 h-4 mr-2" />
                  Manage Payment Methods
                </Button>
                {subscription.cancel_at_period_end ? (
                  <Button
                    onClick={handleResumeSubscription}
                    variant="outline"
                    disabled={resumeMutation.isPending}
                  >
                    Resume Subscription
                  </Button>
                ) : (
                  <Button
                    onClick={handleCancelSubscription}
                    variant="outline"
                    disabled={cancelMutation.isPending}
                  >
                    Cancel Subscription
                  </Button>
                )}
              </div>
            </Card>

            {/* Usage Summary */}
            {usage && <UsageWidget usage={usage} />}
          </TabsContent>

          {/* Usage Tab */}
          <TabsContent value="usage">
            {loadingUsage ? (
              <div className="flex items-center justify-center h-64">
                <div className="text-gray-500">Loading usage data...</div>
              </div>
            ) : usage ? (
              <UsageWidget usage={usage} />
            ) : (
              <Card className="p-8 text-center">
                <p className="text-gray-500">No usage data available</p>
              </Card>
            )}
          </TabsContent>

          {/* Invoices Tab */}
          <TabsContent value="invoices">
            <Card className="p-6">
              <h2 className="text-xl font-semibold mb-6">Invoices</h2>

              {invoices && invoices.length > 0 ? (
                <div className="space-y-4">
                  {invoices.map((invoice) => (
                    <div
                      key={invoice.id}
                      className="flex items-center justify-between p-4 border border-gray-200 dark:border-gray-700 rounded-lg"
                    >
                      <div className="flex items-center gap-4">
                        {getInvoiceStatusIcon(invoice.status)}
                        <div>
                          <div className="font-medium">
                            {formatDate(invoice.billing_period_start)} -{' '}
                            {formatDate(invoice.billing_period_end)}
                          </div>
                          <div className="text-sm text-gray-500">
                            Due: {formatDate(invoice.due_date)}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-4">
                        <div className="text-right">
                          <div className="font-semibold">
                            ${parseFloat(invoice.amount_due).toFixed(2)}
                          </div>
                          <Badge variant="outline" className="mt-1">
                            {invoice.status}
                          </Badge>
                        </div>

                        {invoice.invoice_pdf_url && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => window.open(invoice.invoice_pdf_url!, '_blank', 'noopener,noreferrer')}
                          >
                            <FileText className="w-4 h-4 mr-2" />
                            Download
                            <ExternalLink className="w-3 h-3 ml-1" />
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-gray-500">No invoices yet</div>
              )}
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  )
}

export default BillingSettingsPage
