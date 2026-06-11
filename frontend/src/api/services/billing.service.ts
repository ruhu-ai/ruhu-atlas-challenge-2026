/**
 * Billing Service
 *
 * Handles billing, subscriptions, and payment operations.
 */

import { apiClient } from '../client'
import type {
  SubscriptionPlan,
  Subscription,
  UsageMetrics,
  BillingTransaction,
  Invoice,
  CheckoutSessionResponse,
  BillingPortalResponse,
  BillingPeriod,
} from '@/types/billing'

class BillingService {
  private readonly basePath = '/billing'

  /**
   * Get all available subscription plans
   */
  async getPlans(): Promise<SubscriptionPlan[]> {
    return apiClient.get<SubscriptionPlan[]>(`${this.basePath}/plans`)
  }

  /**
   * Get a specific plan by slug
   */
  async getPlan(slug: string): Promise<SubscriptionPlan> {
    return apiClient.get<SubscriptionPlan>(`${this.basePath}/plans/${slug}`)
  }

  /**
   * Get current organization's subscription
   */
  async getCurrentSubscription(): Promise<Subscription | null> {
    try {
      return await apiClient.get<Subscription>(`${this.basePath}/subscription`)
    } catch (error: any) {
      // Return null if no subscription found (404)
      if (error.message?.includes('No active subscription') || error.message?.includes('404')) {
        return null
      }
      throw error
    }
  }

  /**
   * Create a checkout session for subscribing to a plan
   */
  async createCheckoutSession(
    planSlug: string,
    billingPeriod: BillingPeriod
  ): Promise<CheckoutSessionResponse> {
    return apiClient.post<CheckoutSessionResponse>(`${this.basePath}/checkout`, {
      plan_slug: planSlug,
      billing_period: billingPeriod,
    })
  }

  /**
   * Get billing portal URL for managing subscription
   */
  async getBillingPortal(): Promise<BillingPortalResponse> {
    return apiClient.post<BillingPortalResponse>(`${this.basePath}/portal`, {})
  }

  /**
   * Cancel subscription at period end
   */
  async cancelSubscription(): Promise<Subscription> {
    return apiClient.post<Subscription>(`${this.basePath}/subscription/cancel`, {})
  }

  /**
   * Resume a cancelled subscription
   */
  async resumeSubscription(): Promise<Subscription> {
    return apiClient.post<Subscription>(`${this.basePath}/subscription/resume`, {})
  }

  /**
   * Get current usage metrics
   */
  async getUsageMetrics(): Promise<UsageMetrics> {
    return apiClient.get<UsageMetrics>(`${this.basePath}/usage/metrics`)
  }

  /**
   * Get billing transactions
   */
  async getTransactions(limit: number = 50): Promise<BillingTransaction[]> {
    return apiClient.get<BillingTransaction[]>(`${this.basePath}/transactions`, {
      params: { limit: limit.toString() },
    })
  }

  /**
   * Get invoices
   */
  async getInvoices(limit: number = 50): Promise<Invoice[]> {
    return apiClient.get<Invoice[]>(`${this.basePath}/invoices`, {
      params: { limit: limit.toString() },
    })
  }

  /**
   * Get a specific invoice
   */
  async getInvoice(invoiceId: string): Promise<Invoice> {
    return apiClient.get<Invoice>(`${this.basePath}/invoices/${invoiceId}`)
  }

  /**
   * Download invoice PDF
   */
  async downloadInvoice(invoiceId: string): Promise<string> {
    const invoice = await this.getInvoice(invoiceId)
    if (!invoice.invoice_pdf_url) {
      throw new Error('Invoice PDF not available')
    }
    return invoice.invoice_pdf_url
  }
}

export const billingService = new BillingService()
