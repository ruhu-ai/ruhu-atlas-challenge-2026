from __future__ import annotations

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db_models import Base


class BillingPlanRecord(Base):
    __tablename__ = "billing_plans"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_billing_plans_slug"),
    )

    plan_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_monthly: Mapped[object] = mapped_column(Numeric(12, 2), nullable=False)
    price_yearly: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    max_agents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_conversations_monthly: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_voice_minutes_monthly: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_team_members: Mapped[int | None] = mapped_column(Integer, nullable=True)
    features_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit_multiplier: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    external_product_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_price_monthly_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_price_yearly_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingSubscriptionRecord(Base):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        UniqueConstraint("external_subscription_ref", name="uq_billing_subscriptions_external_subscription"),
    )

    subscription_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    billing_cycle: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_period_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    trial_end: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_customer_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_subscription_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingUsageRecordRecord(Base):
    __tablename__ = "billing_usage_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "usage_key", name="uq_billing_usage_records_org_usage_key"),
    )

    usage_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subscription_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    usage_key: Mapped[str | None] = mapped_column(String(191), nullable=True, index=True)
    period_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingInvoiceRecord(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (
        UniqueConstraint("organization_id", "invoice_number", name="uq_billing_invoices_org_invoice_number"),
        UniqueConstraint("external_invoice_ref", name="uq_billing_invoices_external_invoice"),
    )

    invoice_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subscription_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    invoice_number: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subtotal: Mapped[object] = mapped_column(Numeric(12, 2), nullable=False)
    tax: Mapped[object] = mapped_column(Numeric(12, 2), nullable=False)
    total: Mapped[object] = mapped_column(Numeric(12, 2), nullable=False)
    amount_paid: Mapped[object] = mapped_column(Numeric(12, 2), nullable=False)
    period_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    due_date: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_invoice_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_payment_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    line_items_json: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingWebhookEventRecord(Base):
    """Idempotency log for inbound provider webhooks (Stripe today; extensible).

    Prevents double-processing when a provider redelivers the same event.
    We INSERT the event_id at the start of handling; a conflict on the
    primary key means we have seen this event before and can skip it.
    """

    __tablename__ = "billing_webhook_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="stripe", index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
