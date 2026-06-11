"""
Billing API — REST endpoints for subscription management, Stripe checkout, and webhooks.

Supports three billing modes (configured via RUHU_STRIPE_BILLING_MODE):
- 'mock'  : Development mode — no real charges, subscriptions created directly
- 'test'  : Stripe test mode (sk_test_... keys)
- 'live'  : Production mode  (sk_live_... keys)

Reference: https://docs.stripe.com/billing/subscriptions/build-subscriptions
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .api_auth import require_authenticated_context
from .billing.models import (
    BillingInvoice,
    BillingPlan,
    BillingSubscription,
    InvoiceLineItem,
    UsageSummary,
    new_id,
    utc_now,
)
from .billing.service import BillingService
from .billing.store import BillingStore
from .email_transport import EmailMessage, EmailSender

logger = logging.getLogger(__name__)

# Initialize Stripe client lazily — avoids import errors when stripe isn't installed
_stripe_client = None
_stripe_webhook_cls = None


def _init_stripe(secret_key: str, billing_mode: str) -> None:
    global _stripe_client, _stripe_webhook_cls
    if billing_mode == "mock":
        return
    try:
        from stripe import StripeClient, Webhook  # type: ignore[import]

        _stripe_client = StripeClient(secret_key)
        _stripe_webhook_cls = Webhook
        logger.info(
            "stripe_client_initialized mode=%s key_prefix=%s",
            billing_mode,
            secret_key[:7] if secret_key else None,
        )
    except ImportError:
        logger.warning("stripe SDK not installed — install with: pip install 'stripe>=14.2.0'")


# ---------------------------------------------------------------------------
# Serialisers — map internal field names → frontend field names exactly
# ---------------------------------------------------------------------------

def _serialize_plan(plan: BillingPlan) -> dict[str, Any]:
    return {
        "id": plan.plan_id,
        "name": plan.name,
        "slug": plan.slug,
        "description": plan.description or "",
        "price_monthly": str(plan.price_monthly),
        "price_yearly": str(plan.price_yearly),
        "max_agents": plan.max_agents,
        "max_conversations_monthly": plan.max_conversations_monthly,
        "max_voice_minutes_monthly": plan.max_voice_minutes_monthly,
        "max_team_members": plan.max_team_members,
        "features": dict(plan.features),
        "is_active": plan.is_active,
        "is_public": plan.is_public,
        "sort_order": plan.sort_order,
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
    }


def _serialize_subscription(sub: BillingSubscription, plan: BillingPlan) -> dict[str, Any]:
    # Backend uses "canceled" (US spelling); frontend contract uses "cancelled" (British)
    status_str = "cancelled" if sub.status == "canceled" else sub.status
    return {
        "id": sub.subscription_id,
        "organization_id": sub.organization_id,
        "plan_id": sub.plan_id,
        "plan": _serialize_plan(plan),
        "status": status_str,
        "billing_period": sub.billing_cycle,
        "current_period_start": sub.current_period_start.isoformat(),
        "current_period_end": (
            sub.current_period_end.isoformat() if sub.current_period_end else None
        ),
        "cancel_at_period_end": sub.cancel_at is not None,
        "stripe_subscription_id": sub.external_subscription_ref,
        "stripe_customer_id": sub.external_customer_ref,
        "created_at": sub.created_at.isoformat(),
        "updated_at": sub.updated_at.isoformat(),
    }


_INVOICE_STATUS_TO_TX_STATUS: dict[str, str] = {
    "paid": "completed",
    "draft": "pending",
    "open": "pending",
    "void": "refunded",
    "uncollectible": "failed",
}


def _serialize_invoice(invoice: BillingInvoice) -> dict[str, Any]:
    """Matches frontend Invoice type exactly."""
    meta = invoice.metadata if isinstance(invoice.metadata, dict) else {}
    invoice_pdf_url = meta.get("stripe_invoice_pdf") or meta.get("pdf_url")
    return {
        "id": invoice.invoice_id,
        "organization_id": invoice.organization_id,
        "subscription_id": invoice.subscription_id,
        "amount_due": str(invoice.total),
        "amount_paid": str(invoice.amount_paid),
        "currency": str(meta.get("currency", "usd")),
        "status": invoice.status,
        "billing_period_start": invoice.period_start.isoformat(),
        "billing_period_end": invoice.period_end.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "invoice_pdf_url": invoice_pdf_url,
        "stripe_invoice_id": invoice.external_invoice_ref,
        "created_at": invoice.created_at.isoformat(),
        "updated_at": invoice.updated_at.isoformat(),
    }


def _serialize_transaction(invoice: BillingInvoice) -> dict[str, Any]:
    """Transaction projection derived from invoices — matches frontend BillingTransaction type."""
    tx_status = _INVOICE_STATUS_TO_TX_STATUS.get(invoice.status, "pending")
    return {
        "id": invoice.invoice_id,
        "organization_id": invoice.organization_id,
        "subscription_id": invoice.subscription_id,
        "amount": str(invoice.total),
        "currency": str((invoice.metadata or {}).get("currency", "usd")),
        "status": tx_status,
        "description": f"Invoice {invoice.invoice_number}",
        "stripe_invoice_id": invoice.external_invoice_ref,
        "stripe_payment_intent_id": invoice.external_payment_ref,
        "created_at": invoice.created_at.isoformat(),
        "updated_at": invoice.updated_at.isoformat(),
    }


def _serialize_usage_metrics(
    summaries: list[UsageSummary],
    subscription: BillingSubscription,
    plan: BillingPlan,
    agent_count: int = 0,
    team_member_count: int = 1,
) -> dict[str, Any]:
    by_type = {s.resource_type: s for s in summaries}

    def _usage(key: str) -> int:
        return by_type[key].current_usage if key in by_type else 0

    def _pct(used: int, limit: int | None) -> int:
        if limit is None or limit == 0:
            return 0
        return int(min(used / limit * 100, 100))

    conversations = _usage("conversations")
    voice_minutes = _usage("voice_minutes")

    return {
        "period_start": subscription.current_period_start.isoformat(),
        "period_end": (
            subscription.current_period_end.isoformat()
            if subscription.current_period_end
            else None
        ),
        "agents_created": agent_count,
        "conversations_count": conversations,
        "voice_minutes_used": voice_minutes,
        "team_members_count": team_member_count,
        "limits": {
            "max_agents": plan.max_agents,
            "max_conversations_monthly": plan.max_conversations_monthly,
            "max_voice_minutes_monthly": plan.max_voice_minutes_monthly,
            "max_team_members": plan.max_team_members,
        },
        "usage_percentage": {
            "agents": _pct(agent_count, plan.max_agents),
            "conversations": _pct(conversations, plan.max_conversations_monthly),
            "voice_minutes": _pct(voice_minutes, plan.max_voice_minutes_monthly),
            "team_members": _pct(team_member_count, plan.max_team_members),
        },
    }


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CheckoutRequest(BaseModel):
    plan_slug: str = Field(..., description="Plan slug to subscribe to")
    billing_period: str = Field(default="monthly", description="monthly or yearly")
    success_url: Optional[str] = Field(default=None, description="Override success redirect URL")
    cancel_url: Optional[str] = Field(default=None, description="Override cancel redirect URL")


class PortalRequest(BaseModel):
    return_url: Optional[str] = Field(default=None, description="URL to return to after portal")


# ---------------------------------------------------------------------------
# Stripe helpers
# ---------------------------------------------------------------------------

def _get_or_create_stripe_customer(
    stripe_client: Any,
    *,
    user_email: str,
    user_display_name: str | None,
    organization_id: str,
    user_id: str,
    existing_customer_id: str | None,
) -> str:
    """Return existing Stripe customer ID or create a new one."""
    if existing_customer_id:
        return existing_customer_id

    try:
        from stripe import StripeError  # type: ignore[import]
        customer = stripe_client.customers.create(params={
            "email": user_email,
            "name": user_display_name or user_email,
            "metadata": {
                "organization_id": organization_id,
                "user_id": user_id,
            },
        })
        logger.info(
            "stripe_customer_created customer_id=%s organization_id=%s",
            customer.id,
            organization_id,
        )
        return customer.id
    except Exception as exc:
        logger.error(
            "stripe_customer_creation_failed organization_id=%s error=%s",
            organization_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create Stripe customer: {exc}",
        ) from exc


def _create_stripe_checkout_session(
    stripe_client: Any,
    billing_mode: str,
    *,
    plan: BillingPlan,
    billing_period: str,
    customer_id: str,
    organization_id: str,
    success_url: str,
    cancel_url: str,
) -> dict[str, Any]:
    price_id = (
        plan.external_price_yearly_ref
        if billing_period == "yearly" and plan.external_price_yearly_ref
        else plan.external_price_monthly_ref
    )
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Plan '{plan.slug}' has no Stripe price configured for {billing_period} billing",
        )

    try:
        checkout_session = stripe_client.checkout.sessions.create(params={
            "customer": customer_id,
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "organization_id": organization_id,
                "plan_id": plan.plan_id,
                "billing_period": billing_period,
            },
            "subscription_data": {
                "metadata": {
                    "organization_id": organization_id,
                    "plan_id": plan.plan_id,
                },
            },
            "allow_promotion_codes": True,
            "billing_address_collection": "auto",
            "automatic_tax": {"enabled": False},
            "customer_update": {"address": "auto", "name": "auto"},
        })
        logger.info(
            "stripe_checkout_created session_id=%s organization_id=%s plan=%s period=%s",
            checkout_session.id,
            organization_id,
            plan.slug,
            billing_period,
        )
        return {
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id,
        }
    except Exception as exc:
        logger.error(
            "stripe_checkout_failed organization_id=%s error=%s",
            organization_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create Stripe checkout session: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Webhook event handlers
# ---------------------------------------------------------------------------

def _handle_checkout_completed(
    checkout_session: Any,
    billing_store: BillingStore,
) -> None:
    """Handle checkout.session.completed — create/update local subscription record."""
    metadata = checkout_session.metadata or {}
    organization_id = metadata.get("organization_id")
    plan_id = metadata.get("plan_id")
    billing_period = metadata.get("billing_period", "monthly")

    if not organization_id or not plan_id:
        logger.warning(
            "stripe_checkout_missing_metadata session_id=%s",
            checkout_session.id,
        )
        return

    stripe_subscription_id = checkout_session.subscription
    stripe_customer_id = checkout_session.customer

    # Fetch full Stripe subscription for period dates
    if _stripe_client is None:
        return
    stripe_sub = _stripe_client.subscriptions.retrieve(stripe_subscription_id)

    period_start = datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc)
    period_end = datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc)
    trial_end = (
        datetime.fromtimestamp(stripe_sub.trial_end, tz=timezone.utc)
        if stripe_sub.trial_end
        else None
    )

    existing = billing_store.get_active_subscription(organization_id)
    now = utc_now()

    if existing:
        updated = existing.model_copy(
            update={
                "plan_id": plan_id,
                "billing_cycle": billing_period,
                "status": stripe_sub.status,
                "current_period_start": period_start,
                "current_period_end": period_end,
                "trial_end": trial_end,
                "external_customer_ref": stripe_customer_id,
                "external_subscription_ref": stripe_subscription_id,
                "updated_at": now,
            }
        )
        billing_store.save_subscription(updated)
    else:
        new_sub = BillingSubscription(
            organization_id=organization_id,
            plan_id=plan_id,
            billing_cycle=billing_period,
            status=stripe_sub.status,
            current_period_start=period_start,
            current_period_end=period_end,
            trial_end=trial_end,
            external_customer_ref=stripe_customer_id,
            external_subscription_ref=stripe_subscription_id,
            created_at=now,
            updated_at=now,
        )
        billing_store.save_subscription(new_sub)

    logger.info(
        "stripe_checkout_subscription_synced organization_id=%s stripe_sub=%s plan=%s",
        organization_id,
        stripe_subscription_id,
        plan_id,
    )


def _find_subscription_by_stripe_id(
    billing_store: BillingStore,
    stripe_subscription_id: str,
) -> BillingSubscription | None:
    """Scan subscriptions to find one matching the Stripe subscription ID.

    SQLAlchemyBillingStore doesn't expose a direct lookup by external_subscription_ref,
    so we use the store's save_subscription after updates — but for lookup we need to
    access the underlying data. We use a small convention: the store can look it up
    if it has the method, otherwise we raise and let callers handle gracefully.
    """
    if hasattr(billing_store, "get_subscription_by_external_ref"):
        return billing_store.get_subscription_by_external_ref(stripe_subscription_id)  # type: ignore[attr-defined]
    # Fallback: not ideal, but webhook payloads include organization_id in metadata
    return None


def _find_invoice_by_stripe_id(
    billing_store: BillingStore,
    stripe_invoice_id: str,
) -> BillingInvoice | None:
    if hasattr(billing_store, "get_invoice_by_external_ref"):
        return billing_store.get_invoice_by_external_ref(stripe_invoice_id)  # type: ignore[attr-defined]
    return None


def _handle_subscription_updated(
    stripe_sub: Any,
    billing_store: BillingStore,
) -> None:
    """Handle customer.subscription.updated — sync status and period dates."""
    subscription = _find_subscription_by_stripe_id(billing_store, stripe_sub.id)
    if subscription is None:
        logger.warning("stripe_subscription_not_found stripe_sub=%s", stripe_sub.id)
        return

    now = utc_now()
    cancel_at = (
        datetime.fromtimestamp(stripe_sub.cancel_at, tz=timezone.utc)
        if stripe_sub.cancel_at
        else None
    )
    canceled_at = (
        datetime.fromtimestamp(stripe_sub.canceled_at, tz=timezone.utc)
        if stripe_sub.canceled_at
        else None
    )
    updated = subscription.model_copy(
        update={
            "status": stripe_sub.status,
            "current_period_start": datetime.fromtimestamp(
                stripe_sub.current_period_start, tz=timezone.utc
            ),
            "current_period_end": datetime.fromtimestamp(
                stripe_sub.current_period_end, tz=timezone.utc
            ),
            "cancel_at": cancel_at,
            "canceled_at": canceled_at,
            "updated_at": now,
        }
    )
    billing_store.save_subscription(updated)
    logger.info(
        "stripe_subscription_updated stripe_sub=%s status=%s",
        stripe_sub.id,
        stripe_sub.status,
    )


def _handle_subscription_deleted(
    stripe_sub: Any,
    billing_store: BillingStore,
) -> None:
    """Handle customer.subscription.deleted — mark subscription as canceled."""
    subscription = _find_subscription_by_stripe_id(billing_store, stripe_sub.id)
    if subscription is None:
        logger.warning("stripe_subscription_not_found_for_deletion stripe_sub=%s", stripe_sub.id)
        return

    updated = subscription.model_copy(
        update={
            "status": "canceled",
            "canceled_at": utc_now(),
            "updated_at": utc_now(),
        }
    )
    billing_store.save_subscription(updated)
    logger.info(
        "stripe_subscription_canceled stripe_sub=%s organization_id=%s",
        stripe_sub.id,
        subscription.organization_id,
    )


def _handle_invoice_paid(
    stripe_invoice: Any,
    billing_store: BillingStore,
) -> None:
    """Handle invoice.paid — create or update local invoice record."""
    stripe_subscription_id = stripe_invoice.subscription
    if not stripe_subscription_id:
        return  # One-time payment, not subscription-based

    subscription = _find_subscription_by_stripe_id(billing_store, stripe_subscription_id)
    if subscription is None:
        logger.warning(
            "stripe_invoice_subscription_not_found invoice=%s stripe_sub=%s",
            stripe_invoice.id,
            stripe_subscription_id,
        )
        return

    now = utc_now()
    stripe_invoice_id = stripe_invoice.id
    paid_at = now

    # Check for existing local invoice
    existing_invoice = _find_invoice_by_stripe_id(billing_store, stripe_invoice_id)

    if existing_invoice:
        updated = existing_invoice.model_copy(
            update={
                "status": "paid",
                "amount_paid": Decimal(str(stripe_invoice.amount_paid)) / 100,
                "paid_at": paid_at,
                "metadata": {
                    **existing_invoice.metadata,
                    "stripe_hosted_invoice_url": stripe_invoice.hosted_invoice_url,
                    "stripe_invoice_pdf": stripe_invoice.invoice_pdf,
                    "currency": stripe_invoice.currency or "usd",
                },
                "updated_at": now,
            }
        )
        billing_store.save_invoice(updated)
    else:
        # Build line items from Stripe invoice
        line_items: list[InvoiceLineItem] = []
        if stripe_invoice.lines and stripe_invoice.lines.data:
            for line in stripe_invoice.lines.data:
                line_items.append(
                    InvoiceLineItem(
                        description=line.description or "Subscription",
                        quantity=line.quantity or 1,
                        unit_price=Decimal(str(line.amount or 0)) / 100,
                        amount=Decimal(str(line.amount or 0)) / 100,
                    )
                )

        invoice_count = len(billing_store.list_invoices(subscription.organization_id, limit=10000))
        invoice_number = (
            stripe_invoice.number
            or f"INV-{subscription.organization_id[:8].upper()}-{invoice_count + 1:06d}"
        )

        new_invoice = BillingInvoice(
            organization_id=subscription.organization_id,
            subscription_id=subscription.subscription_id,
            invoice_number=invoice_number,
            status="paid",
            subtotal=Decimal(str(stripe_invoice.subtotal or 0)) / 100,
            tax=Decimal(str(stripe_invoice.tax or 0)) / 100,
            total=Decimal(str(stripe_invoice.total or 0)) / 100,
            amount_paid=Decimal(str(stripe_invoice.amount_paid or 0)) / 100,
            period_start=datetime.fromtimestamp(stripe_invoice.period_start, tz=timezone.utc),
            period_end=datetime.fromtimestamp(stripe_invoice.period_end, tz=timezone.utc),
            due_date=(
                datetime.fromtimestamp(stripe_invoice.due_date, tz=timezone.utc)
                if stripe_invoice.due_date
                else None
            ),
            paid_at=paid_at,
            external_invoice_ref=stripe_invoice_id,
            external_payment_ref=stripe_invoice.payment_intent,
            line_items=line_items,
            metadata={
                "stripe_hosted_invoice_url": stripe_invoice.hosted_invoice_url,
                "stripe_invoice_pdf": stripe_invoice.invoice_pdf,
                "currency": stripe_invoice.currency or "usd",
            },
            created_at=now,
            updated_at=now,
        )
        billing_store.save_invoice(new_invoice)

    logger.info(
        "stripe_invoice_paid stripe_invoice=%s organization_id=%s amount=%s",
        stripe_invoice_id,
        subscription.organization_id,
        stripe_invoice.amount_paid,
    )


def _handle_invoice_payment_failed(
    stripe_invoice: Any,
    billing_store: BillingStore,
    email_sender: EmailSender | None,
    identity_store: Any | None,
    frontend_url: str,
) -> None:
    """Handle invoice.payment_failed — mark subscription past_due, notify admins."""
    stripe_subscription_id = stripe_invoice.subscription
    if not stripe_subscription_id:
        return

    subscription = _find_subscription_by_stripe_id(billing_store, stripe_subscription_id)
    if subscription is None:
        return

    updated = subscription.model_copy(
        update={"status": "past_due", "updated_at": utc_now()}
    )
    billing_store.save_subscription(updated)

    attempt_count = stripe_invoice.attempt_count or 0
    amount_due = stripe_invoice.amount_due or 0
    logger.warning(
        "stripe_invoice_payment_failed invoice=%s organization_id=%s attempt=%s",
        stripe_invoice.id,
        subscription.organization_id,
        attempt_count,
    )

    if email_sender and identity_store:
        _notify_admins_payment_failed(
            email_sender=email_sender,
            identity_store=identity_store,
            organization_id=subscription.organization_id,
            invoice_id=stripe_invoice.id,
            amount_due=amount_due,
            attempt_count=attempt_count,
            frontend_url=frontend_url,
        )


def _notify_admins_payment_failed(
    *,
    email_sender: EmailSender,
    identity_store: Any,
    organization_id: str,
    invoice_id: str,
    amount_due: int,
    attempt_count: int,
    frontend_url: str,
) -> None:
    """Send payment failure email to organization admins and owners."""
    try:
        members = identity_store.list_organization_members(organization_id)
    except Exception as exc:
        logger.error("failed to fetch org members for payment notification: %s", exc)
        return

    admin_members = [
        m for m in members
        if m.membership.role in {"admin", "owner"}
    ]
    if not admin_members:
        logger.warning(
            "payment_failure_no_admins organization_id=%s",
            organization_id,
        )
        return

    amount_display = f"${amount_due / 100:.2f}" if amount_due else "your subscription amount"
    billing_url = f"{frontend_url}/settings/billing"
    subject = "Payment failed for your organization — action required"

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
    .alert {{ background: #FEF2F2; border: 1px solid #FECACA; border-radius: 8px; padding: 16px; margin: 20px 0; }}
    .alert h3 {{ color: #DC2626; margin: 0 0 8px 0; }}
    .button {{ display: inline-block; padding: 14px 28px; background-color: #4F46E5; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: 600; }}
    .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb; font-size: 14px; color: #6b7280; text-align: center; }}
  </style>
</head>
<body>
  <h1>Ruhu AI</h1>
  <div class="alert">
    <h3>Payment Failed</h3>
    <p>We were unable to process a payment of <strong>{amount_display}</strong> for your organization.</p>
    <p>This was attempt <strong>{attempt_count}</strong>. Please update your payment method to avoid service interruption.</p>
  </div>
  <p style="text-align: center; margin: 30px 0;">
    <a href="{billing_url}" class="button">Update Payment Method</a>
  </p>
  <div class="footer">
    <p>If you believe this is an error, please contact support.</p>
    <p>&copy; 2026 Ruhu AI. All rights reserved.</p>
  </div>
</body>
</html>"""

    text_content = (
        f"Payment failed for your organization.\n\n"
        f"We were unable to process a payment of {amount_display}. "
        f"This was attempt {attempt_count}.\n\n"
        f"Please update your payment method at: {billing_url}\n\n"
        f"If you believe this is an error, please contact support."
    )

    for member in admin_members:
        user = member.user
        if not user.email:
            continue
        try:
            email_sender.send(
                EmailMessage(
                    to_email=user.email,
                    subject=subject,
                    html_content=html_content,
                    text_content=text_content,
                )
            )
            logger.info(
                "payment_failure_notification_sent to=%s organization_id=%s invoice=%s",
                user.email,
                organization_id,
                invoice_id,
            )
        except Exception as exc:
            logger.error(
                "payment_failure_notification_error to=%s error=%s",
                user.email,
                exc,
            )


# ---------------------------------------------------------------------------
# Router installer
# ---------------------------------------------------------------------------

def install_billing_router(
    app: FastAPI,
    *,
    billing_service: BillingService,
    billing_store: BillingStore,
    stripe_secret_key: str | None = None,
    stripe_webhook_secret: str | None = None,
    billing_mode: str = "mock",
    frontend_url: str | None = None,
    email_sender: EmailSender | None = None,
    identity_store: Any | None = None,
    rate_limiter=None,
) -> None:
    """Mount all /billing/* routes onto ``app``."""

    use_stripe = billing_mode != "mock" and stripe_secret_key is not None
    _frontend_url = frontend_url or "http://localhost:3000"

    if use_stripe and stripe_secret_key:
        _init_stripe(stripe_secret_key, billing_mode)

    router = APIRouter(
        prefix="/billing",
        tags=["billing"],
        dependencies=[rate_limiter] if rate_limiter else [],
    )

    def _org_context(request: Request):
        ctx = require_authenticated_context(request)
        p = ctx.principal
        if p is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return ctx

    def _org_id(request: Request) -> str:
        return _org_context(request).principal.organization.organization_id

    # ------------------------------------------------------------------
    # GET /billing/plans  — public, no auth
    # ------------------------------------------------------------------

    @router.get("/plans")
    def list_plans() -> list[dict]:
        return [_serialize_plan(p) for p in billing_service.list_public_plans()]

    # ------------------------------------------------------------------
    # GET /billing/plans/{slug}  — public, no auth
    # ------------------------------------------------------------------

    @router.get("/plans/{slug}")
    def get_plan(slug: str) -> dict:
        plan = billing_service.get_plan_by_slug(slug)
        if plan is None:
            raise HTTPException(status_code=404, detail=f"plan '{slug}' not found")
        return _serialize_plan(plan)

    # ------------------------------------------------------------------
    # GET /billing/subscription
    # ------------------------------------------------------------------

    @router.get("/subscription")
    def get_subscription(request: Request) -> dict:
        org_id = _org_id(request)
        sub = billing_service.get_current_subscription(org_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="No active subscription found")
        plan = billing_service.get_plan(sub.plan_id)
        if plan is None:
            raise HTTPException(status_code=500, detail="Subscription plan not found")
        return _serialize_subscription(sub, plan)

    # ------------------------------------------------------------------
    # POST /billing/subscription/cancel
    # ------------------------------------------------------------------

    @router.post("/subscription/cancel")
    def cancel_subscription(request: Request) -> dict:
        org_id = _org_id(request)
        sub = billing_service.get_current_subscription(org_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="No active subscription found")

        if use_stripe and sub.external_subscription_ref and _stripe_client:
            try:
                _stripe_client.subscriptions.update(
                    sub.external_subscription_ref,
                    params={"cancel_at_period_end": True},
                )
            except Exception as exc:
                logger.warning("Stripe cancel_at_period_end failed: %s", exc)

        updated = billing_service.cancel_subscription(
            subscription_id=sub.subscription_id,
            at_period_end=True,
        )
        plan = billing_service.get_plan(updated.plan_id)
        if plan is None:
            raise HTTPException(status_code=500, detail="Subscription plan not found")
        return _serialize_subscription(updated, plan)

    # ------------------------------------------------------------------
    # POST /billing/subscription/resume
    # ------------------------------------------------------------------

    @router.post("/subscription/resume")
    def resume_subscription(request: Request) -> dict:
        org_id = _org_id(request)
        sub = billing_service.get_current_subscription(org_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="No active subscription found")

        if use_stripe and sub.external_subscription_ref and _stripe_client:
            try:
                _stripe_client.subscriptions.update(
                    sub.external_subscription_ref,
                    params={"cancel_at_period_end": False},
                )
            except Exception as exc:
                logger.warning("Stripe resume failed: %s", exc)

        updated = billing_service.resume_subscription(sub.subscription_id)
        plan = billing_service.get_plan(updated.plan_id)
        if plan is None:
            raise HTTPException(status_code=500, detail="Subscription plan not found")
        return _serialize_subscription(updated, plan)

    # ------------------------------------------------------------------
    # GET /billing/usage/metrics
    # ------------------------------------------------------------------

    @router.get("/usage/metrics")
    def get_usage_metrics(request: Request) -> dict:
        org_id = _org_id(request)
        sub = billing_service.get_current_subscription(org_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="No active subscription found")
        plan = billing_service.get_plan(sub.plan_id)
        if plan is None:
            raise HTTPException(status_code=500, detail="Subscription plan not found")
        summaries = billing_service.get_usage_summary(
            organization_id=org_id,
            subscription_id=sub.subscription_id,
        )
        # Count agents and team members from identity store if available
        agent_count = 0
        team_member_count = 1
        if identity_store is not None:
            try:
                members = identity_store.list_organization_members(org_id)
                team_member_count = max(1, len(members))
            except Exception:
                pass
        return _serialize_usage_metrics(
            summaries,
            sub,
            plan,
            agent_count=agent_count,
            team_member_count=team_member_count,
        )

    # ------------------------------------------------------------------
    # GET /billing/transactions
    # ------------------------------------------------------------------

    @router.get("/transactions")
    def list_transactions(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[dict]:
        org_id = _org_id(request)
        invoices = billing_service.list_invoices(organization_id=org_id, limit=limit)
        return [_serialize_transaction(inv) for inv in invoices]

    # ------------------------------------------------------------------
    # GET /billing/invoices
    # ------------------------------------------------------------------

    @router.get("/invoices")
    def list_invoices(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[dict]:
        org_id = _org_id(request)
        invoices = billing_service.list_invoices(organization_id=org_id, limit=limit)
        return [_serialize_invoice(inv) for inv in invoices]

    # ------------------------------------------------------------------
    # GET /billing/invoices/{invoice_id}
    # ------------------------------------------------------------------

    @router.get("/invoices/{invoice_id}")
    def get_invoice(request: Request, invoice_id: str) -> dict:
        org_id = _org_id(request)
        invoice = billing_service.get_invoice(organization_id=org_id, invoice_id=invoice_id)
        if invoice is None:
            raise HTTPException(status_code=404, detail="invoice not found")
        return _serialize_invoice(invoice)

    # ------------------------------------------------------------------
    # POST /billing/checkout
    # ------------------------------------------------------------------

    @router.post("/checkout")
    def create_checkout_session(request: Request, body: CheckoutRequest) -> dict:
        ctx = _org_context(request)
        org_id = ctx.principal.organization.organization_id
        billing_cycle = body.billing_period if body.billing_period in ("monthly", "yearly") else "monthly"

        plan = billing_service.get_plan_by_slug(body.plan_slug)
        if plan is None:
            raise HTTPException(status_code=404, detail=f"plan '{body.plan_slug}' not found")

        if use_stripe and _stripe_client:
            existing_sub = billing_service.get_current_subscription(org_id)
            existing_customer_id = (
                existing_sub.external_customer_ref
                if existing_sub and existing_sub.external_customer_ref
                else None
            )
            user = ctx.principal.user
            customer_id = _get_or_create_stripe_customer(
                _stripe_client,
                user_email=user.email or "",
                user_display_name=getattr(user, "display_name", None) or getattr(user, "full_name", None),
                organization_id=org_id,
                user_id=user.user_id,
                existing_customer_id=existing_customer_id,
            )
            success_url = body.success_url or (
                f"{_frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
            )
            cancel_url = body.cancel_url or f"{_frontend_url}/settings/billing"
            return _create_stripe_checkout_session(
                _stripe_client,
                billing_mode,
                plan=plan,
                billing_period=billing_cycle,
                customer_id=customer_id,
                organization_id=org_id,
                success_url=success_url,
                cancel_url=cancel_url,
            )

        # Mock mode — create subscription directly
        existing = billing_service.get_current_subscription(org_id)
        if existing:
            sub = billing_service.change_plan(
                subscription_id=existing.subscription_id,
                new_plan_id=plan.plan_id,
            )
        else:
            sub = billing_service.create_subscription(
                organization_id=org_id,
                plan_id=plan.plan_id,
                billing_cycle=billing_cycle,
            )
        logger.info(
            "checkout_mock_mode organization_id=%s plan=%s",
            org_id,
            body.plan_slug,
        )
        return {
            "checkout_url": f"{_frontend_url}/dashboard?subscription_created=true&plan={body.plan_slug}",
            "session_id": sub.subscription_id,
        }

    # ------------------------------------------------------------------
    # POST /billing/portal
    # ------------------------------------------------------------------

    @router.post("/portal")
    def create_billing_portal(request: Request, body: PortalRequest) -> dict:
        org_id = _org_id(request)
        return_url = body.return_url or f"{_frontend_url}/settings/billing"

        if use_stripe and _stripe_client:
            sub = billing_service.get_current_subscription(org_id)
            customer_id = sub.external_customer_ref if sub else None

            if not customer_id:
                raise HTTPException(
                    status_code=422,
                    detail="No Stripe customer found for this organization. Complete a checkout first.",
                )

            try:
                portal_session = _stripe_client.billing_portal.sessions.create(params={
                    "customer": customer_id,
                    "return_url": return_url,
                })
                logger.info(
                    "stripe_portal_created organization_id=%s customer=%s",
                    org_id,
                    customer_id,
                )
                return {"portal_url": portal_session.url}
            except Exception as exc:
                logger.error(
                    "stripe_portal_failed organization_id=%s error=%s",
                    org_id,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create billing portal: {exc}",
                ) from exc

        # Mock mode
        return {"portal_url": return_url}

    app.include_router(router)

    # ------------------------------------------------------------------
    # POST /billing/webhooks/stripe  — unauthenticated, Stripe-signed
    # ------------------------------------------------------------------
    # Registered directly on app (not the /billing router) to keep
    # the path explicit and avoid confusion with the /billing prefix.

    @app.post("/billing/webhooks/stripe", include_in_schema=False)
    async def stripe_webhook_handler(
        request: Request,
        stripe_signature: str = Header(None, alias="stripe-signature"),
    ) -> dict:
        payload = await request.body()

        logger.info(
            "stripe_webhook_received has_signature=%s body_len=%d",
            stripe_signature is not None,
            len(payload),
        )

        if not use_stripe or _stripe_webhook_cls is None:
            logger.info("stripe_webhook_mock_mode — skipped processing")
            return {"status": "success", "mode": "mock"}

        if not stripe_webhook_secret:
            logger.error("stripe_webhook_secret_missing")
            raise HTTPException(status_code=500, detail="Webhook secret not configured")

        try:
            from stripe import SignatureVerificationError  # type: ignore[import]
            event = _stripe_webhook_cls.construct_event(
                payload.decode("utf-8"),
                stripe_signature,
                stripe_webhook_secret,
            )
        except ValueError as exc:
            logger.error("stripe_webhook_invalid_payload: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid payload") from exc
        except Exception as exc:
            logger.error("stripe_webhook_invalid_signature: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid signature") from exc

        event_type: str = event.type
        event_data = event.data.object
        event_id: str = event.id

        # Idempotency check — Stripe explicitly retries undelivered webhooks
        # (network errors, non-2xx responses, timeouts). Without this check
        # we would double-process invoice.paid → double-credit revenue.
        is_new_event = billing_store.claim_webhook_event(
            event_id=event_id,
            provider="stripe",
            event_type=event_type,
        )
        if not is_new_event:
            logger.info(
                "stripe_webhook_duplicate_skipped event_type=%s event_id=%s",
                event_type,
                event_id,
            )
            return {"status": "success", "event_type": event_type, "deduplicated": True}

        logger.info(
            "stripe_webhook_processing event_type=%s event_id=%s",
            event_type,
            event_id,
        )

        try:
            if event_type == "checkout.session.completed":
                _handle_checkout_completed(event_data, billing_store)

            elif event_type == "customer.subscription.created":
                # Usually handled by checkout.session.completed
                # Log for subscriptions created via API or Billing Portal
                logger.info(
                    "stripe_subscription_created_webhook stripe_sub=%s",
                    event_data.id,
                )

            elif event_type == "customer.subscription.updated":
                _handle_subscription_updated(event_data, billing_store)

            elif event_type == "customer.subscription.deleted":
                _handle_subscription_deleted(event_data, billing_store)

            elif event_type == "invoice.paid":
                _handle_invoice_paid(event_data, billing_store)

            elif event_type == "invoice.payment_failed":
                _handle_invoice_payment_failed(
                    event_data,
                    billing_store,
                    email_sender=email_sender,
                    identity_store=identity_store,
                    frontend_url=_frontend_url,
                )

            else:
                logger.info("stripe_webhook_unhandled_event event_type=%s", event_type)

            billing_store.mark_webhook_event_status(
                event_id=event_id, status="processed"
            )
            return {"status": "success", "event_type": event_type}

        except Exception as exc:
            logger.error(
                "stripe_webhook_handler_error event_type=%s error=%s",
                event_type,
                exc,
                exc_info=True,
            )
            billing_store.mark_webhook_event_status(
                event_id=event_id, status="failed", error_message=str(exc)
            )
            # Return 200 to prevent Stripe from retrying non-recoverable errors
            return {"status": "error", "event_type": event_type, "error": str(exc)}
