from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Sequence

from .catalog import default_pricing_catalog
from .models import (
    BillingInvoice,
    BillingPlan,
    BillingSubscription,
    LimitCheck,
    InvoiceLineItem,
    UsageRecord,
    UsageResourceType,
    UsageSummary,
    normalize_amount,
    period_end_for_cycle,
    utc_now,
)
from .store import BillingStore

_RESOURCE_TYPES: tuple[UsageResourceType, ...] = ("agents", "conversations", "voice_minutes", "team_members")


class BillingService:
    def __init__(self, store: BillingStore) -> None:
        self._store = store

    def seed_pricing_catalog(self, plans: Sequence[BillingPlan] | None = None) -> list[BillingPlan]:
        seeded: list[BillingPlan] = []
        for default_plan in plans or default_pricing_catalog():
            existing = self._store.get_plan(default_plan.plan_id) or self._store.get_plan_by_slug(default_plan.slug)
            if existing is None:
                candidate = default_plan.model_copy(update={"updated_at": utc_now()})
            else:
                candidate = existing.model_copy(
                    update={
                        "name": default_plan.name,
                        "slug": default_plan.slug,
                        "description": default_plan.description,
                        "price_monthly": default_plan.price_monthly,
                        "price_yearly": default_plan.price_yearly,
                        "max_agents": default_plan.max_agents,
                        "max_conversations_monthly": default_plan.max_conversations_monthly,
                        "max_voice_minutes_monthly": default_plan.max_voice_minutes_monthly,
                        "max_team_members": default_plan.max_team_members,
                        "features": dict(default_plan.features),
                        "is_active": default_plan.is_active,
                        "is_public": default_plan.is_public,
                        "sort_order": default_plan.sort_order,
                        "updated_at": utc_now(),
                    }
                )
            seeded.append(self._store.save_plan(candidate))
        return sorted(seeded, key=lambda plan: (plan.sort_order, plan.slug))

    def list_public_plans(self) -> list[BillingPlan]:
        return self._store.list_plans(is_active=True, is_public=True)

    def get_plan(self, plan_id: str) -> BillingPlan | None:
        return self._store.get_plan(plan_id)

    def get_plan_by_slug(self, slug: str) -> BillingPlan | None:
        return self._store.get_plan_by_slug(slug)

    def create_subscription(
        self,
        *,
        organization_id: str,
        plan_id: str,
        billing_cycle: str = "monthly",
        trial_days: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> BillingSubscription:
        existing = self._store.get_active_subscription(organization_id)
        if existing is not None:
            raise ValueError(f"organization {organization_id} already has an active subscription")
        self._require_plan(plan_id)
        if billing_cycle not in {"monthly", "yearly"}:
            raise ValueError(f"unsupported billing cycle: {billing_cycle}")

        now = utc_now()
        status = "trialing" if trial_days else "active"
        subscription = BillingSubscription(
            organization_id=organization_id,
            plan_id=plan_id,
            billing_cycle=billing_cycle,
            status=status,
            current_period_start=now,
            current_period_end=period_end_for_cycle(now, billing_cycle),
            trial_end=None if not trial_days else now + timedelta(days=trial_days),
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        return self._store.save_subscription(subscription)

    def get_subscription(self, subscription_id: str) -> BillingSubscription | None:
        return self._store.get_subscription(subscription_id)

    def get_current_subscription(self, organization_id: str) -> BillingSubscription | None:
        return self._store.get_active_subscription(organization_id)

    def list_subscriptions(self, organization_id: str) -> list[BillingSubscription]:
        return self._store.list_subscriptions(organization_id)

    def cancel_subscription(
        self,
        *,
        subscription_id: str,
        at_period_end: bool = True,
    ) -> BillingSubscription:
        subscription = self._require_subscription(subscription_id)
        now = utc_now()
        updates = {
            "updated_at": now,
        }
        if at_period_end:
            updates["cancel_at"] = subscription.current_period_end
        else:
            updates["status"] = "canceled"
            updates["cancel_at"] = now
            updates["canceled_at"] = now
        return self._store.save_subscription(subscription.model_copy(update=updates))

    def resume_subscription(self, subscription_id: str) -> BillingSubscription:
        subscription = self._require_subscription(subscription_id)
        resumed = subscription.model_copy(
            update={
                "status": "active",
                "cancel_at": None,
                "canceled_at": None,
                "updated_at": utc_now(),
            }
        )
        return self._store.save_subscription(resumed)

    def change_plan(self, *, subscription_id: str, new_plan_id: str) -> BillingSubscription:
        subscription = self._require_subscription(subscription_id)
        self._require_plan(new_plan_id)
        return self._store.save_subscription(
            subscription.model_copy(update={"plan_id": new_plan_id, "updated_at": utc_now()})
        )

    def record_usage(
        self,
        *,
        organization_id: str,
        subscription_id: str,
        resource_type: UsageResourceType,
        quantity: int = 1,
        unit_price: Decimal | int | float | str | None = None,
        usage_key: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> UsageRecord:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        subscription = self._require_subscription(subscription_id)
        if subscription.organization_id != organization_id:
            raise ValueError("subscription does not belong to organization")
        record = UsageRecord(
            organization_id=organization_id,
            subscription_id=subscription_id,
            resource_type=resource_type,
            quantity=quantity,
            unit_price=normalize_amount(unit_price),
            usage_key=usage_key,
            period_start=subscription.current_period_start,
            period_end=subscription.current_period_end,
            metadata=dict(metadata or {}),
        )
        return self._store.save_usage_record(record)

    def get_usage_summary(self, *, organization_id: str, subscription_id: str) -> list[UsageSummary]:
        subscription = self._require_subscription(subscription_id)
        if subscription.organization_id != organization_id:
            raise ValueError("subscription does not belong to organization")
        plan = self._require_plan(subscription.plan_id)
        totals = self._store.summarize_usage(organization_id, subscription_id)
        limits = {
            "agents": plan.max_agents,
            "conversations": plan.max_conversations_monthly,
            "voice_minutes": plan.max_voice_minutes_monthly,
            "team_members": plan.max_team_members,
        }
        return [
            UsageSummary(
                resource_type=resource_type,
                current_usage=totals.get(resource_type, 0),
                limit=limits[resource_type],
                remaining=None if limits[resource_type] is None else limits[resource_type] - totals.get(resource_type, 0),
            )
            for resource_type in _RESOURCE_TYPES
        ]

    def check_limit(
        self,
        *,
        organization_id: str,
        resource_type: UsageResourceType,
        requested_quantity: int = 1,
    ) -> LimitCheck:
        subscription = self._store.get_active_subscription(organization_id)
        if subscription is None:
            return LimitCheck(
                resource_type=resource_type,
                allowed=False,
                current_usage=0,
                limit=0,
                remaining=0,
                reason="no_active_subscription",
            )
        self._require_plan(subscription.plan_id)
        summary = {item.resource_type: item for item in self.get_usage_summary(organization_id=organization_id, subscription_id=subscription.subscription_id)}
        current = summary[resource_type]
        if current.limit is None:
            return LimitCheck(
                resource_type=resource_type,
                allowed=True,
                current_usage=current.current_usage,
                limit=None,
                remaining=None,
            )
        remaining = current.limit - current.current_usage
        return LimitCheck(
            resource_type=resource_type,
            allowed=remaining >= requested_quantity,
            current_usage=current.current_usage,
            limit=current.limit,
            remaining=remaining,
            reason=None if remaining >= requested_quantity else "limit_exceeded",
        )

    def generate_invoice(self, *, organization_id: str, subscription_id: str) -> BillingInvoice:
        subscription = self._require_subscription(subscription_id)
        if subscription.organization_id != organization_id:
            raise ValueError("subscription does not belong to organization")
        plan = self._require_plan(subscription.plan_id)
        subtotal = plan.price_yearly if subscription.billing_cycle == "yearly" else plan.price_monthly
        invoice_count = len(self._store.list_invoices(organization_id, limit=10000))
        invoice_number = f"INV-{organization_id[:8].upper()}-{invoice_count + 1:06d}"
        line_item = InvoiceLineItem(
            description=f"{plan.name} plan ({subscription.billing_cycle})",
            quantity=1,
            unit_price=subtotal,
            amount=subtotal,
        )
        invoice = BillingInvoice(
            organization_id=organization_id,
            subscription_id=subscription_id,
            invoice_number=invoice_number,
            status="draft",
            subtotal=subtotal,
            tax=Decimal("0.00"),
            total=subtotal,
            amount_paid=Decimal("0.00"),
            period_start=subscription.current_period_start,
            period_end=subscription.current_period_end,
            due_date=subscription.current_period_end + timedelta(days=7),
            line_items=[line_item],
        )
        return self._store.save_invoice(invoice)

    def get_invoice(self, *, organization_id: str, invoice_id: str) -> BillingInvoice | None:
        return self._store.get_invoice(invoice_id, organization_id=organization_id)

    def mark_invoice_paid(self, *, organization_id: str, invoice_id: str) -> BillingInvoice:
        invoice = self._store.get_invoice(invoice_id, organization_id=organization_id)
        if invoice is None:
            raise ValueError(f"invoice {invoice_id} not found")
        paid_at = utc_now()
        updated = invoice.model_copy(
            update={
                "status": "paid",
                "amount_paid": invoice.total,
                "paid_at": paid_at,
                "updated_at": paid_at,
            }
        )
        return self._store.save_invoice(updated)

    def list_invoices(self, *, organization_id: str, status: str | None = None, limit: int = 100) -> list[BillingInvoice]:
        return self._store.list_invoices(organization_id, status=status, limit=limit)

    def _require_plan(self, plan_id: str) -> BillingPlan:
        plan = self._store.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"plan {plan_id} not found")
        return plan

    def _require_subscription(self, subscription_id: str) -> BillingSubscription:
        subscription = self._store.get_subscription(subscription_id)
        if subscription is None:
            raise ValueError(f"subscription {subscription_id} not found")
        return subscription
