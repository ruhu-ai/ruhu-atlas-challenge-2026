/**
 * Billing and Subscription Types
 */

export interface SubscriptionPlan {
  id: string
  name: string
  slug: string
  description: string
  price_monthly: string
  price_yearly: string
  max_agents: number | null
  max_conversations_monthly: number | null
  max_voice_minutes_monthly: number | null
  max_team_members: number | null
  features: Record<string, boolean | string>
  is_active: boolean
  is_public: boolean
  sort_order: number
  created_at: string
  updated_at: string
}

export interface Subscription {
  id: string
  organization_id: string
  plan_id: string
  plan: SubscriptionPlan
  status: 'active' | 'cancelled' | 'past_due' | 'trialing' | 'paused'
  billing_period: 'monthly' | 'yearly'
  current_period_start: string
  current_period_end: string
  cancel_at_period_end: boolean
  stripe_subscription_id: string | null
  stripe_customer_id: string | null
  created_at: string
  updated_at: string
}

export interface UsageMetrics {
  period_start: string
  period_end: string
  agents_created: number
  conversations_count: number
  voice_minutes_used: number
  team_members_count: number
  limits: {
    max_agents: number | null
    max_conversations_monthly: number | null
    max_voice_minutes_monthly: number | null
    max_team_members: number | null
  }
  usage_percentage: {
    agents: number
    conversations: number
    voice_minutes: number
    team_members: number
  }
}

export interface BillingTransaction {
  id: string
  organization_id: string
  subscription_id: string | null
  amount: string
  currency: string
  status: 'pending' | 'completed' | 'failed' | 'refunded'
  description: string
  stripe_invoice_id: string | null
  stripe_payment_intent_id: string | null
  created_at: string
  updated_at: string
}

export interface Invoice {
  id: string
  organization_id: string
  subscription_id: string
  amount_due: string
  amount_paid: string
  currency: string
  status: 'draft' | 'open' | 'paid' | 'void' | 'uncollectible'
  billing_period_start: string
  billing_period_end: string
  due_date: string | null
  paid_at: string | null
  invoice_pdf_url: string | null
  stripe_invoice_id: string | null
  created_at: string
  updated_at: string
}

export interface CheckoutSessionResponse {
  checkout_url: string
  session_id: string
}

export interface BillingPortalResponse {
  portal_url: string
}

export type BillingPeriod = 'monthly' | 'yearly'
