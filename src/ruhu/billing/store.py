from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..db_models import Base
from .models import BillingInvoice, BillingPlan, BillingSubscription, InvoiceLineItem, UsageRecord
from .sqlalchemy_models import (
    BillingInvoiceRecord,
    BillingPlanRecord,
    BillingSubscriptionRecord,
    BillingUsageRecordRecord,
    BillingWebhookEventRecord,
)


WebhookEventStatus = Literal["received", "processed", "failed"]


class BillingStore(Protocol):
    def save_plan(self, plan: BillingPlan) -> BillingPlan: ...

    def get_plan(self, plan_id: str) -> BillingPlan | None: ...

    def get_plan_by_slug(self, slug: str) -> BillingPlan | None: ...

    def list_plans(self, *, is_active: bool | None = None, is_public: bool | None = None) -> list[BillingPlan]: ...

    def save_subscription(self, subscription: BillingSubscription) -> BillingSubscription: ...

    def get_subscription(self, subscription_id: str) -> BillingSubscription | None: ...

    def get_active_subscription(self, organization_id: str) -> BillingSubscription | None: ...

    def list_subscriptions(self, organization_id: str) -> list[BillingSubscription]: ...

    def save_usage_record(self, record: UsageRecord) -> UsageRecord: ...

    def list_usage_records(
        self,
        organization_id: str,
        subscription_id: str,
        *,
        resource_type: str | None = None,
    ) -> list[UsageRecord]: ...

    def summarize_usage(
        self,
        organization_id: str,
        subscription_id: str,
    ) -> dict[str, int]: ...

    def save_invoice(self, invoice: BillingInvoice) -> BillingInvoice: ...

    def get_invoice(self, invoice_id: str, *, organization_id: str | None = None) -> BillingInvoice | None: ...

    def list_invoices(
        self,
        organization_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[BillingInvoice]: ...

    def get_subscription_by_external_ref(self, external_ref: str) -> BillingSubscription | None: ...

    def get_invoice_by_external_ref(self, external_ref: str) -> BillingInvoice | None: ...

    def claim_webhook_event(
        self,
        *,
        event_id: str,
        provider: str,
        event_type: str,
    ) -> bool:
        """Atomically register an inbound webhook event for the first time.

        Returns ``True`` if this event_id was newly recorded (caller should
        process it). Returns ``False`` if the event was already seen — the
        caller must skip processing to preserve at-most-once semantics.
        """
        ...

    def mark_webhook_event_status(
        self,
        *,
        event_id: str,
        status: WebhookEventStatus,
        error_message: str | None = None,
    ) -> None: ...


class InMemoryBillingStore:
    def __init__(self) -> None:
        self._plans: dict[str, BillingPlan] = {}
        self._subscriptions: dict[str, BillingSubscription] = {}
        self._usage_records: dict[str, UsageRecord] = {}
        self._usage_ids_by_key: dict[tuple[str, str], str] = {}
        self._invoices: dict[str, BillingInvoice] = {}
        self._webhook_events: dict[str, dict[str, object]] = {}

    def save_plan(self, plan: BillingPlan) -> BillingPlan:
        stored = plan.model_copy(deep=True)
        self._plans[stored.plan_id] = stored
        return stored.model_copy(deep=True)

    def get_plan(self, plan_id: str) -> BillingPlan | None:
        item = self._plans.get(plan_id)
        return None if item is None else item.model_copy(deep=True)

    def get_plan_by_slug(self, slug: str) -> BillingPlan | None:
        for item in self._plans.values():
            if item.slug == slug:
                return item.model_copy(deep=True)
        return None

    def list_plans(self, *, is_active: bool | None = None, is_public: bool | None = None) -> list[BillingPlan]:
        items = list(self._plans.values())
        if is_active is not None:
            items = [item for item in items if item.is_active is is_active]
        if is_public is not None:
            items = [item for item in items if item.is_public is is_public]
        items.sort(key=lambda item: (item.sort_order, item.slug))
        return [item.model_copy(deep=True) for item in items]

    def save_subscription(self, subscription: BillingSubscription) -> BillingSubscription:
        stored = subscription.model_copy(deep=True)
        self._subscriptions[stored.subscription_id] = stored
        return stored.model_copy(deep=True)

    def get_subscription(self, subscription_id: str) -> BillingSubscription | None:
        item = self._subscriptions.get(subscription_id)
        return None if item is None else item.model_copy(deep=True)

    def get_active_subscription(self, organization_id: str) -> BillingSubscription | None:
        items = [
            item
            for item in self._subscriptions.values()
            if item.organization_id == organization_id and item.status in {"active", "trialing"}
        ]
        items.sort(key=lambda item: (item.created_at, item.subscription_id), reverse=True)
        return None if not items else items[0].model_copy(deep=True)

    def list_subscriptions(self, organization_id: str) -> list[BillingSubscription]:
        items = [item for item in self._subscriptions.values() if item.organization_id == organization_id]
        items.sort(key=lambda item: (item.created_at, item.subscription_id), reverse=True)
        return [item.model_copy(deep=True) for item in items]

    def save_usage_record(self, record: UsageRecord) -> UsageRecord:
        if record.usage_key:
            existing_id = self._usage_ids_by_key.get((record.organization_id, record.usage_key))
            if existing_id:
                existing = self._usage_records[existing_id]
                return existing.model_copy(deep=True)
        stored = record.model_copy(deep=True)
        self._usage_records[stored.usage_id] = stored
        if stored.usage_key:
            self._usage_ids_by_key[(stored.organization_id, stored.usage_key)] = stored.usage_id
        return stored.model_copy(deep=True)

    def list_usage_records(
        self,
        organization_id: str,
        subscription_id: str,
        *,
        resource_type: str | None = None,
    ) -> list[UsageRecord]:
        items = [
            item
            for item in self._usage_records.values()
            if item.organization_id == organization_id and item.subscription_id == subscription_id
        ]
        if resource_type is not None:
            items = [item for item in items if item.resource_type == resource_type]
        items.sort(key=lambda item: (item.created_at, item.usage_id))
        return [item.model_copy(deep=True) for item in items]

    def summarize_usage(self, organization_id: str, subscription_id: str) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for item in self._usage_records.values():
            if item.organization_id == organization_id and item.subscription_id == subscription_id:
                totals[item.resource_type] += item.quantity
        return dict(totals)

    def save_invoice(self, invoice: BillingInvoice) -> BillingInvoice:
        stored = invoice.model_copy(deep=True)
        self._invoices[stored.invoice_id] = stored
        return stored.model_copy(deep=True)

    def get_invoice(self, invoice_id: str, *, organization_id: str | None = None) -> BillingInvoice | None:
        item = self._invoices.get(invoice_id)
        if item is None:
            return None
        if organization_id is not None and item.organization_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def list_invoices(
        self,
        organization_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[BillingInvoice]:
        items = [item for item in self._invoices.values() if item.organization_id == organization_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.created_at, item.invoice_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def get_subscription_by_external_ref(self, external_ref: str) -> BillingSubscription | None:
        for item in self._subscriptions.values():
            if item.external_subscription_ref == external_ref:
                return item.model_copy(deep=True)
        return None

    def get_invoice_by_external_ref(self, external_ref: str) -> BillingInvoice | None:
        for item in self._invoices.values():
            if item.external_invoice_ref == external_ref:
                return item.model_copy(deep=True)
        return None

    def claim_webhook_event(
        self,
        *,
        event_id: str,
        provider: str,
        event_type: str,
    ) -> bool:
        if event_id in self._webhook_events:
            return False
        self._webhook_events[event_id] = {
            "provider": provider,
            "event_type": event_type,
            "received_at": datetime.now(timezone.utc),
            "processed_at": None,
            "status": "received",
            "error_message": None,
        }
        return True

    def mark_webhook_event_status(
        self,
        *,
        event_id: str,
        status: WebhookEventStatus,
        error_message: str | None = None,
    ) -> None:
        record = self._webhook_events.get(event_id)
        if record is None:
            return
        record["status"] = status
        record["error_message"] = error_message
        if status in ("processed", "failed"):
            record["processed_at"] = datetime.now(timezone.utc)


class SQLAlchemyBillingStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        bind = self._session_factory.kw.get("bind")
        if bind is None:
            with self._session_factory() as session:
                bind = session.get_bind()
        Base.metadata.create_all(
            bind=bind,
            tables=[
                BillingPlanRecord.__table__,
                BillingSubscriptionRecord.__table__,
                BillingUsageRecordRecord.__table__,
                BillingInvoiceRecord.__table__,
                BillingWebhookEventRecord.__table__,
            ],
        )

    def save_plan(self, plan: BillingPlan) -> BillingPlan:
        with self._session_factory() as session:
            record = session.get(BillingPlanRecord, plan.plan_id)
            if record is None:
                record = BillingPlanRecord(plan_id=plan.plan_id)
                session.add(record)
            _apply_plan(record, plan)
            session.commit()
        return self.get_plan(plan.plan_id) or plan.model_copy(deep=True)

    def get_plan(self, plan_id: str) -> BillingPlan | None:
        with self._session_factory() as session:
            record = session.get(BillingPlanRecord, plan_id)
            return None if record is None else _record_to_plan(record)

    def get_plan_by_slug(self, slug: str) -> BillingPlan | None:
        statement = select(BillingPlanRecord).where(BillingPlanRecord.slug == slug)
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
            return None if record is None else _record_to_plan(record)

    def list_plans(self, *, is_active: bool | None = None, is_public: bool | None = None) -> list[BillingPlan]:
        statement = select(BillingPlanRecord)
        if is_active is not None:
            statement = statement.where(BillingPlanRecord.is_active == is_active)
        if is_public is not None:
            statement = statement.where(BillingPlanRecord.is_public == is_public)
        statement = statement.order_by(BillingPlanRecord.sort_order.asc(), BillingPlanRecord.slug.asc())
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_plan(record) for record in records]

    def save_subscription(self, subscription: BillingSubscription) -> BillingSubscription:
        with self._session_factory() as session:
            record = session.get(BillingSubscriptionRecord, subscription.subscription_id)
            if record is None:
                record = BillingSubscriptionRecord(subscription_id=subscription.subscription_id)
                session.add(record)
            _apply_subscription(record, subscription)
            session.commit()
        return self.get_subscription(subscription.subscription_id) or subscription.model_copy(deep=True)

    def get_subscription(self, subscription_id: str) -> BillingSubscription | None:
        with self._session_factory() as session:
            record = session.get(BillingSubscriptionRecord, subscription_id)
            return None if record is None else _record_to_subscription(record)

    def get_active_subscription(self, organization_id: str) -> BillingSubscription | None:
        statement = (
            select(BillingSubscriptionRecord)
            .where(
                BillingSubscriptionRecord.organization_id == organization_id,
                BillingSubscriptionRecord.status.in_(["active", "trialing"]),
            )
            .order_by(BillingSubscriptionRecord.created_at.desc(), BillingSubscriptionRecord.subscription_id.desc())
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalars().first()
            return None if record is None else _record_to_subscription(record)

    def list_subscriptions(self, organization_id: str) -> list[BillingSubscription]:
        statement = (
            select(BillingSubscriptionRecord)
            .where(BillingSubscriptionRecord.organization_id == organization_id)
            .order_by(BillingSubscriptionRecord.created_at.desc(), BillingSubscriptionRecord.subscription_id.desc())
        )
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_subscription(record) for record in records]

    def save_usage_record(self, record: UsageRecord) -> UsageRecord:
        with self._session_factory() as session:
            existing = session.get(BillingUsageRecordRecord, record.usage_id)
            if existing is None and record.usage_key:
                existing = session.execute(
                    select(BillingUsageRecordRecord).where(
                        BillingUsageRecordRecord.organization_id == record.organization_id,
                        BillingUsageRecordRecord.usage_key == record.usage_key,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return _record_to_usage_record(existing)
            if existing is None:
                existing = BillingUsageRecordRecord(usage_id=record.usage_id)
                session.add(existing)
            _apply_usage_record(existing, record)
            session.commit()
        return self.get_usage_record_by_id(record.usage_id) or (
            self.get_usage_record_by_key(record.organization_id, record.usage_key)
            if record.usage_key
            else record.model_copy(deep=True)
        )

    def get_usage_record_by_id(self, usage_id: str) -> UsageRecord | None:
        with self._session_factory() as session:
            record = session.get(BillingUsageRecordRecord, usage_id)
            return None if record is None else _record_to_usage_record(record)

    def get_usage_record_by_key(self, organization_id: str, usage_key: str | None) -> UsageRecord | None:
        if not usage_key:
            return None
        statement = select(BillingUsageRecordRecord).where(
            BillingUsageRecordRecord.organization_id == organization_id,
            BillingUsageRecordRecord.usage_key == usage_key,
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
            return None if record is None else _record_to_usage_record(record)

    def list_usage_records(
        self,
        organization_id: str,
        subscription_id: str,
        *,
        resource_type: str | None = None,
    ) -> list[UsageRecord]:
        statement = select(BillingUsageRecordRecord).where(
            BillingUsageRecordRecord.organization_id == organization_id,
            BillingUsageRecordRecord.subscription_id == subscription_id,
        )
        if resource_type is not None:
            statement = statement.where(BillingUsageRecordRecord.resource_type == resource_type)
        statement = statement.order_by(BillingUsageRecordRecord.created_at.asc(), BillingUsageRecordRecord.usage_id.asc())
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_usage_record(record) for record in records]

    def summarize_usage(self, organization_id: str, subscription_id: str) -> dict[str, int]:
        statement = (
            select(BillingUsageRecordRecord.resource_type, func.sum(BillingUsageRecordRecord.quantity))
            .where(
                BillingUsageRecordRecord.organization_id == organization_id,
                BillingUsageRecordRecord.subscription_id == subscription_id,
            )
            .group_by(BillingUsageRecordRecord.resource_type)
        )
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return {resource_type: int(total or 0) for resource_type, total in rows}

    def save_invoice(self, invoice: BillingInvoice) -> BillingInvoice:
        with self._session_factory() as session:
            record = session.get(BillingInvoiceRecord, invoice.invoice_id)
            if record is None:
                record = BillingInvoiceRecord(invoice_id=invoice.invoice_id)
                session.add(record)
            _apply_invoice(record, invoice)
            session.commit()
        return self.get_invoice(invoice.invoice_id, organization_id=invoice.organization_id) or invoice.model_copy(deep=True)

    def get_invoice(self, invoice_id: str, *, organization_id: str | None = None) -> BillingInvoice | None:
        statement = select(BillingInvoiceRecord).where(BillingInvoiceRecord.invoice_id == invoice_id)
        if organization_id is not None:
            statement = statement.where(BillingInvoiceRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
            return None if record is None else _record_to_invoice(record)

    def list_invoices(
        self,
        organization_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[BillingInvoice]:
        statement = select(BillingInvoiceRecord).where(BillingInvoiceRecord.organization_id == organization_id)
        if status is not None:
            statement = statement.where(BillingInvoiceRecord.status == status)
        statement = statement.order_by(BillingInvoiceRecord.created_at.desc(), BillingInvoiceRecord.invoice_id.desc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_invoice(record) for record in records]

    def get_subscription_by_external_ref(self, external_ref: str) -> BillingSubscription | None:
        statement = select(BillingSubscriptionRecord).where(
            BillingSubscriptionRecord.external_subscription_ref == external_ref
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
            return None if record is None else _record_to_subscription(record)

    def get_invoice_by_external_ref(self, external_ref: str) -> BillingInvoice | None:
        statement = select(BillingInvoiceRecord).where(
            BillingInvoiceRecord.external_invoice_ref == external_ref
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
            return None if record is None else _record_to_invoice(record)

    def claim_webhook_event(
        self,
        *,
        event_id: str,
        provider: str,
        event_type: str,
    ) -> bool:
        with self._session_factory() as session:
            record = BillingWebhookEventRecord(
                event_id=event_id,
                provider=provider,
                event_type=event_type,
                received_at=datetime.now(timezone.utc),
                status="received",
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True

    def mark_webhook_event_status(
        self,
        *,
        event_id: str,
        status: WebhookEventStatus,
        error_message: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            record = session.get(BillingWebhookEventRecord, event_id)
            if record is None:
                return
            record.status = status
            record.error_message = error_message
            if status in ("processed", "failed"):
                record.processed_at = datetime.now(timezone.utc)
            session.commit()


def _apply_plan(record: BillingPlanRecord, plan: BillingPlan) -> None:
    record.name = plan.name
    record.slug = plan.slug
    record.description = plan.description
    record.price_monthly = plan.price_monthly
    record.price_yearly = plan.price_yearly
    record.max_agents = plan.max_agents
    record.max_conversations_monthly = plan.max_conversations_monthly
    record.max_voice_minutes_monthly = plan.max_voice_minutes_monthly
    record.max_team_members = plan.max_team_members
    record.features_json = deepcopy(plan.features)
    record.is_active = plan.is_active
    record.is_public = plan.is_public
    record.sort_order = plan.sort_order
    record.rate_limit_multiplier = plan.rate_limit_multiplier
    record.external_product_ref = plan.external_product_ref
    record.external_price_monthly_ref = plan.external_price_monthly_ref
    record.external_price_yearly_ref = plan.external_price_yearly_ref
    record.created_at = plan.created_at
    record.updated_at = plan.updated_at


def _record_to_plan(record: BillingPlanRecord) -> BillingPlan:
    return BillingPlan.model_validate(
        {
            "plan_id": record.plan_id,
            "name": record.name,
            "slug": record.slug,
            "description": record.description,
            "price_monthly": Decimal(str(record.price_monthly)),
            "price_yearly": None if record.price_yearly is None else Decimal(str(record.price_yearly)),
            "max_agents": record.max_agents,
            "max_conversations_monthly": record.max_conversations_monthly,
            "max_voice_minutes_monthly": record.max_voice_minutes_monthly,
            "max_team_members": record.max_team_members,
            "features": dict(record.features_json or {}),
            "is_active": record.is_active,
            "is_public": record.is_public,
            "sort_order": record.sort_order,
            "rate_limit_multiplier": float(record.rate_limit_multiplier or 1.0),
            "external_product_ref": record.external_product_ref,
            "external_price_monthly_ref": record.external_price_monthly_ref,
            "external_price_yearly_ref": record.external_price_yearly_ref,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


def _apply_subscription(record: BillingSubscriptionRecord, subscription: BillingSubscription) -> None:
    record.organization_id = subscription.organization_id
    record.plan_id = subscription.plan_id
    record.billing_cycle = subscription.billing_cycle
    record.status = subscription.status
    record.current_period_start = subscription.current_period_start
    record.current_period_end = subscription.current_period_end
    record.trial_end = subscription.trial_end
    record.cancel_at = subscription.cancel_at
    record.canceled_at = subscription.canceled_at
    record.external_customer_ref = subscription.external_customer_ref
    record.external_subscription_ref = subscription.external_subscription_ref
    record.metadata_json = deepcopy(subscription.metadata)
    record.created_at = subscription.created_at
    record.updated_at = subscription.updated_at


def _record_to_subscription(record: BillingSubscriptionRecord) -> BillingSubscription:
    return BillingSubscription.model_validate(
        {
            "subscription_id": record.subscription_id,
            "organization_id": record.organization_id,
            "plan_id": record.plan_id,
            "billing_cycle": record.billing_cycle,
            "status": record.status,
            "current_period_start": record.current_period_start,
            "current_period_end": record.current_period_end,
            "trial_end": record.trial_end,
            "cancel_at": record.cancel_at,
            "canceled_at": record.canceled_at,
            "external_customer_ref": record.external_customer_ref,
            "external_subscription_ref": record.external_subscription_ref,
            "metadata": dict(record.metadata_json or {}),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


def _apply_usage_record(record_row: BillingUsageRecordRecord, record: UsageRecord) -> None:
    record_row.organization_id = record.organization_id
    record_row.subscription_id = record.subscription_id
    record_row.resource_type = record.resource_type
    record_row.quantity = record.quantity
    record_row.unit_price = record.unit_price
    record_row.usage_key = record.usage_key
    record_row.period_start = record.period_start
    record_row.period_end = record.period_end
    record_row.metadata_json = deepcopy(record.metadata)
    record_row.created_at = record.created_at


def _record_to_usage_record(record: BillingUsageRecordRecord) -> UsageRecord:
    return UsageRecord.model_validate(
        {
            "usage_id": record.usage_id,
            "organization_id": record.organization_id,
            "subscription_id": record.subscription_id,
            "resource_type": record.resource_type,
            "quantity": record.quantity,
            "unit_price": None if record.unit_price is None else Decimal(str(record.unit_price)),
            "usage_key": record.usage_key,
            "period_start": record.period_start,
            "period_end": record.period_end,
            "metadata": dict(record.metadata_json or {}),
            "created_at": record.created_at,
        }
    )


def _apply_invoice(record: BillingInvoiceRecord, invoice: BillingInvoice) -> None:
    record.organization_id = invoice.organization_id
    record.subscription_id = invoice.subscription_id
    record.invoice_number = invoice.invoice_number
    record.status = invoice.status
    record.subtotal = invoice.subtotal
    record.tax = invoice.tax
    record.total = invoice.total
    record.amount_paid = invoice.amount_paid
    record.period_start = invoice.period_start
    record.period_end = invoice.period_end
    record.due_date = invoice.due_date
    record.paid_at = invoice.paid_at
    record.external_invoice_ref = invoice.external_invoice_ref
    record.external_payment_ref = invoice.external_payment_ref
    record.line_items_json = [item.model_dump(mode="json") for item in invoice.line_items]
    record.metadata_json = deepcopy(invoice.metadata)
    record.created_at = invoice.created_at
    record.updated_at = invoice.updated_at


def _record_to_invoice(record: BillingInvoiceRecord) -> BillingInvoice:
    return BillingInvoice.model_validate(
        {
            "invoice_id": record.invoice_id,
            "organization_id": record.organization_id,
            "subscription_id": record.subscription_id,
            "invoice_number": record.invoice_number,
            "status": record.status,
            "subtotal": Decimal(str(record.subtotal)),
            "tax": Decimal(str(record.tax)),
            "total": Decimal(str(record.total)),
            "amount_paid": Decimal(str(record.amount_paid)),
            "period_start": record.period_start,
            "period_end": record.period_end,
            "due_date": record.due_date,
            "paid_at": record.paid_at,
            "external_invoice_ref": record.external_invoice_ref,
            "external_payment_ref": record.external_payment_ref,
            "line_items": [InvoiceLineItem.model_validate(item) for item in (record.line_items_json or [])],
            "metadata": dict(record.metadata_json or {}),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )
