from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BillingCycle = Literal["monthly", "yearly"]
SubscriptionStatus = Literal["trialing", "active", "canceled", "past_due", "paused"]
InvoiceStatus = Literal["draft", "open", "paid", "void", "uncollectible"]
UsageResourceType = Literal["agents", "conversations", "voice_minutes", "team_members"]

_TWO_PLACES = Decimal("0.01")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def normalize_amount(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def period_end_for_cycle(start: datetime, cycle: BillingCycle) -> datetime:
    if cycle == "monthly":
        return start + timedelta(days=30)
    return start + timedelta(days=365)


class BillingPlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    plan_id: str = Field(default_factory=new_id)
    name: str
    slug: str
    description: str | None = None
    price_monthly: Decimal
    price_yearly: Decimal | None = None
    max_agents: int | None = None
    max_conversations_monthly: int | None = None
    max_voice_minutes_monthly: int | None = None
    max_team_members: int | None = None
    features: dict[str, bool | str | int | float] = Field(default_factory=dict)
    is_active: bool = True
    is_public: bool = True
    sort_order: int = 0
    rate_limit_multiplier: float = Field(default=1.0, ge=0.1, le=10.0)
    external_product_ref: str | None = None
    external_price_monthly_ref: str | None = None
    external_price_yearly_ref: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("price_monthly", "price_yearly", mode="before")
    @classmethod
    def _normalize_price(cls, value: Decimal | int | float | str | None) -> Decimal | None:
        return normalize_amount(value)

    @model_validator(mode="after")
    def _ensure_yearly_price(self) -> "BillingPlan":
        if self.price_yearly is None:
            self.price_yearly = normalize_amount(self.price_monthly * 12)
        return self


class BillingSubscription(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    subscription_id: str = Field(default_factory=new_id)
    organization_id: str
    plan_id: str
    billing_cycle: BillingCycle = "monthly"
    status: SubscriptionStatus = "active"
    current_period_start: datetime = Field(default_factory=utc_now)
    current_period_end: datetime | None = None
    trial_end: datetime | None = None
    cancel_at: datetime | None = None
    canceled_at: datetime | None = None
    external_customer_ref: str | None = None
    external_subscription_ref: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _ensure_period_end(self) -> "BillingSubscription":
        if self.current_period_end is None:
            self.current_period_end = period_end_for_cycle(self.current_period_start, self.billing_cycle)
        return self


class UsageRecord(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    usage_id: str = Field(default_factory=new_id)
    organization_id: str
    subscription_id: str
    resource_type: UsageResourceType
    quantity: int = 1
    unit_price: Decimal | None = None
    usage_key: str | None = None
    period_start: datetime
    period_end: datetime
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("unit_price", mode="before")
    @classmethod
    def _normalize_unit_price(cls, value: Decimal | int | float | str | None) -> Decimal | None:
        return normalize_amount(value)


class UsageSummary(BaseModel):
    resource_type: UsageResourceType
    current_usage: int
    limit: int | None = None
    remaining: int | None = None


class LimitCheck(BaseModel):
    resource_type: UsageResourceType
    allowed: bool
    current_usage: int
    limit: int | None = None
    remaining: int | None = None
    reason: str | None = None


class InvoiceLineItem(BaseModel):
    description: str
    quantity: int = 1
    unit_price: Decimal
    amount: Decimal

    @field_validator("unit_price", "amount", mode="before")
    @classmethod
    def _normalize_amounts(cls, value: Decimal | int | float | str) -> Decimal:
        normalized = normalize_amount(value)
        assert normalized is not None
        return normalized


class BillingInvoice(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    invoice_id: str = Field(default_factory=new_id)
    organization_id: str
    subscription_id: str
    invoice_number: str
    status: InvoiceStatus = "draft"
    subtotal: Decimal
    tax: Decimal = Decimal("0.00")
    total: Decimal
    amount_paid: Decimal = Decimal("0.00")
    period_start: datetime
    period_end: datetime
    due_date: datetime | None = None
    paid_at: datetime | None = None
    external_invoice_ref: str | None = None
    external_payment_ref: str | None = None
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("subtotal", "tax", "total", "amount_paid", mode="before")
    @classmethod
    def _normalize_money(cls, value: Decimal | int | float | str) -> Decimal:
        normalized = normalize_amount(value)
        assert normalized is not None
        return normalized
