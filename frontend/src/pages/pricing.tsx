/**
 * Pricing Page
 *
 * Displays subscription plans with pricing cards and allows users to subscribe.
 */

import { useState } from 'react'
import { toast } from 'sonner'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Check, Zap, Building2, Rocket, Shield } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { billingService } from '@/api/services/billing.service'
import { Button } from '@/components/atoms/button'
import { Card } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import type { SubscriptionPlan, BillingPeriod } from '@/types/billing'
import { DashboardLayout } from '@/layouts/dashboard-layout'

const planIcons: Record<string, LucideIcon> = {
  free: Zap,
  starter: Rocket,
  professional: Building2,
  enterprise: Shield,
}

const planColors: Record<string, string> = {
  free: 'text-gray-500',
  starter: 'text-blue-500',
  professional: 'text-purple-500',
  enterprise: 'text-amber-500',
}

function PricingPage() {
  const navigate = useNavigate()
  const [billingPeriod, setBillingPeriod] = useState<BillingPeriod>('monthly')
  const [loadingPlan, setLoadingPlan] = useState<string | null>(null)

  // Fetch subscription plans
  const { data: plans, isLoading } = useQuery({
    queryKey: ['billing-plans'],
    queryFn: () => billingService.getPlans(),
  })

  // Fetch current subscription
  const { data: currentSubscription } = useQuery({
    queryKey: ['current-subscription'],
    queryFn: () => billingService.getCurrentSubscription(),
  })

  const handleSubscribe = async (plan: SubscriptionPlan) => {
    if (plan.slug === 'free') {
      // Free plan - just show message or redirect to signup
      return
    }

    setLoadingPlan(plan.id)
    try {
      const { checkout_url } = await billingService.createCheckoutSession(
        plan.slug,
        billingPeriod
      )
      // Validate and redirect to Stripe Checkout
      const checkoutUrlObj = new URL(checkout_url)
      if (!checkoutUrlObj.hostname.endsWith('.stripe.com') && !checkoutUrlObj.hostname.endsWith('.stripe.dev')) {
        throw new Error('Invalid checkout URL')
      }
      window.location.href = checkout_url
    } catch (error: any) {
      console.error('Failed to create checkout session:', error)
      toast.error(error.message || 'Failed to start checkout process')
      setLoadingPlan(null)
    }
  }

  const formatPrice = (plan: SubscriptionPlan) => {
    const price = billingPeriod === 'monthly' ? plan.price_monthly : plan.price_yearly
    const numPrice = parseFloat(price)

    if (numPrice === 0) {
      return 'Free'
    }

    if (billingPeriod === 'yearly') {
      const monthlyEquivalent = numPrice / 12
      return (
        <>
          <span className="text-4xl font-bold">${monthlyEquivalent.toFixed(0)}</span>
          <span className="text-gray-500 dark:text-gray-400">/mo</span>
          <div className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            billed ${numPrice.toFixed(0)} yearly
          </div>
        </>
      )
    }

    return (
      <>
        <span className="text-4xl font-bold">${numPrice.toFixed(0)}</span>
        <span className="text-gray-500 dark:text-gray-400">/mo</span>
      </>
    )
  }

  const formatLimit = (value: number | null) => {
    if (value === null) return 'Unlimited'
    return value.toLocaleString()
  }

  const isCurrentPlan = (plan: SubscriptionPlan) => {
    return currentSubscription?.plan_id === plan.id
  }

  const getFeaturesList = (plan: SubscriptionPlan) => {
    const coreFeatures = [
      { label: 'AI Agents', value: formatLimit(plan.max_agents) },
      { label: 'Conversations/month', value: formatLimit(plan.max_conversations_monthly) },
      { label: 'Voice Minutes/month', value: formatLimit(plan.max_voice_minutes_monthly) },
      { label: 'Team Members', value: formatLimit(plan.max_team_members) },
    ]

    const additionalFeatures = Object.entries(plan.features)
      .filter(([_, enabled]) => enabled === true)
      .map(([key]) => {
        // Convert snake_case to Title Case
        const label = key
          .split('_')
          .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
          .join(' ')
        return { label, value: true }
      })

    return { coreFeatures, additionalFeatures }
  }

  if (isLoading) {
    return (
      <DashboardLayout>
        <div className="flex items-center justify-center h-96">
          <div className="text-gray-500">Loading plans...</div>
        </div>
      </DashboardLayout>
    )
  }

  const sortedPlans = [...(plans || [])].sort((a, b) => a.sort_order - b.sort_order)

  return (
    <DashboardLayout>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold mb-4">Choose Your Plan</h1>
          <p className="text-xl text-gray-600 dark:text-gray-400 mb-8">
            Scale your AI voice agents with the perfect plan for your needs
          </p>

          {/* Billing Period Toggle */}
          <div className="flex justify-center">
            <Tabs
              value={billingPeriod}
              onValueChange={(value) => setBillingPeriod(value as BillingPeriod)}
            >
              <TabsList>
                <TabsTrigger value="monthly">Monthly</TabsTrigger>
                <TabsTrigger value="yearly">
                  Yearly
                  <Badge variant="default" className="ml-2">
                    Save 20%
                  </Badge>
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>

        {/* Pricing Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-12">
          {sortedPlans.map((plan) => {
            const Icon = planIcons[plan.slug] || Zap
            const { coreFeatures, additionalFeatures } = getFeaturesList(plan)
            const isCurrent = isCurrentPlan(plan)
            const isPopular = plan.slug === 'professional'

            return (
              <Card
                key={plan.id}
                className={`relative p-6 ${
                  isPopular
                    ? 'border-2 border-purple-500 shadow-lg'
                    : 'border border-gray-200 dark:border-gray-700'
                }`}
              >
                {isPopular && (
                  <div className="absolute -top-4 left-1/2 -translate-x-1/2">
                    <Badge variant="default" className="bg-purple-500">
                      Most Popular
                    </Badge>
                  </div>
                )}

                {/* Plan Icon & Name */}
                <div className="flex items-center gap-3 mb-4">
                  <Icon className={`w-8 h-8 ${planColors[plan.slug] || 'text-gray-500'}`} />
                  <div>
                    <h3 className="text-xl font-bold">{plan.name}</h3>
                    {isCurrent && (
                      <Badge variant="outline" className="mt-1">
                        Current Plan
                      </Badge>
                    )}
                  </div>
                </div>

                {/* Description */}
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
                  {plan.description}
                </p>

                {/* Price */}
                <div className="mb-6">{formatPrice(plan)}</div>

                {/* CTA Button */}
                <Button
                  className="w-full mb-6"
                  variant={isPopular ? 'primary' : 'outline'}
                  onClick={() => handleSubscribe(plan)}
                  disabled={isCurrent || loadingPlan === plan.id}
                >
                  {loadingPlan === plan.id
                    ? 'Loading...'
                    : isCurrent
                    ? 'Current Plan'
                    : plan.slug === 'free'
                    ? 'Get Started'
                    : 'Subscribe'}
                </Button>

                {/* Core Features */}
                <div className="space-y-3 mb-4">
                  {coreFeatures.map((feature, idx) => (
                    <div key={idx} className="flex items-start gap-2">
                      <Check className="w-5 h-5 text-green-500 shrink-0 mt-0.5" />
                      <div className="text-sm">
                        <span className="font-medium">{feature.value}</span>{' '}
                        <span className="text-gray-600 dark:text-gray-400">{feature.label}</span>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Additional Features */}
                {additionalFeatures.length > 0 && (
                  <div className="pt-4 border-t border-gray-200 dark:border-gray-700">
                    <div className="space-y-2">
                      {additionalFeatures.map((feature, idx) => (
                        <div key={idx} className="flex items-start gap-2">
                          <Check className="w-4 h-4 text-green-500 shrink-0 mt-0.5" />
                          <span className="text-sm text-gray-600 dark:text-gray-400">
                            {feature.label}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            )
          })}
        </div>

        {/* FAQ Section */}
        <div className="max-w-3xl mx-auto mt-16">
          <h2 className="text-2xl font-bold text-center mb-8">Frequently Asked Questions</h2>
          <div className="space-y-6">
            <div>
              <h3 className="font-semibold mb-2">Can I change plans anytime?</h3>
              <p className="text-gray-600 dark:text-gray-400">
                Yes! You can upgrade or downgrade your plan at any time. Changes take effect
                immediately, and we'll prorate the difference.
              </p>
            </div>
            <div>
              <h3 className="font-semibold mb-2">What payment methods do you accept?</h3>
              <p className="text-gray-600 dark:text-gray-400">
                We accept all major credit cards (Visa, MasterCard, American Express) via Stripe.
              </p>
            </div>
            <div>
              <h3 className="font-semibold mb-2">What happens if I exceed my limits?</h3>
              <p className="text-gray-600 dark:text-gray-400">
                You'll receive a notification when approaching your limits. You can upgrade your plan
                anytime to continue service without interruption.
              </p>
            </div>
            <div>
              <h3 className="font-semibold mb-2">Is there a free trial?</h3>
              <p className="text-gray-600 dark:text-gray-400">
                The Free plan allows you to test our platform with limited features. No credit card
                required!
              </p>
            </div>
          </div>
        </div>

        {/* Manage Subscription Link */}
        {currentSubscription && (
          <div className="text-center mt-12">
            <Button
              variant="outline"
              onClick={() => navigate('/settings/billing')}
            >
              Manage My Subscription
            </Button>
          </div>
        )}
      </div>
    </DashboardLayout>
  )
}

export default PricingPage
