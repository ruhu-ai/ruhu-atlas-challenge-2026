from __future__ import annotations

from decimal import Decimal

import pytest

from ruhu.billing import BillingService, InMemoryBillingStore


def test_billing_service_seeds_default_pricing_catalog_idempotently() -> None:
    service = BillingService(InMemoryBillingStore())

    first = service.seed_pricing_catalog()
    second = service.seed_pricing_catalog()

    assert [plan.slug for plan in first] == ["free", "starter", "professional", "enterprise"]
    assert [plan.plan_id for plan in second] == [plan.plan_id for plan in first]
    assert [plan.slug for plan in service.list_public_plans()] == ["free", "starter", "professional", "enterprise"]


def test_billing_service_handles_subscription_usage_and_invoice_lifecycle() -> None:
    service = BillingService(InMemoryBillingStore())
    service.seed_pricing_catalog()
    starter = service.get_plan_by_slug("starter")
    assert starter is not None

    subscription = service.create_subscription(
        organization_id="org-1",
        plan_id=starter.plan_id,
        billing_cycle="monthly",
    )

    first_usage = service.record_usage(
        organization_id="org-1",
        subscription_id=subscription.subscription_id,
        resource_type="conversations",
        quantity=25,
        usage_key="conv-batch-1",
    )
    duplicate_usage = service.record_usage(
        organization_id="org-1",
        subscription_id=subscription.subscription_id,
        resource_type="conversations",
        quantity=25,
        usage_key="conv-batch-1",
    )
    service.record_usage(
        organization_id="org-1",
        subscription_id=subscription.subscription_id,
        resource_type="voice_minutes",
        quantity=40,
    )

    assert duplicate_usage.usage_id == first_usage.usage_id

    summaries = {
        summary.resource_type: summary
        for summary in service.get_usage_summary(
            organization_id="org-1",
            subscription_id=subscription.subscription_id,
        )
    }
    assert summaries["conversations"].current_usage == 25
    assert summaries["conversations"].limit == 1000
    assert summaries["voice_minutes"].current_usage == 40

    assert service.check_limit(
        organization_id="org-1",
        resource_type="conversations",
        requested_quantity=900,
    ).allowed is True
    assert service.check_limit(
        organization_id="org-1",
        resource_type="conversations",
        requested_quantity=976,
    ).allowed is False

    invoice = service.generate_invoice(
        organization_id="org-1",
        subscription_id=subscription.subscription_id,
    )
    assert invoice.invoice_number.startswith("INV-ORG-1-")
    assert invoice.total == Decimal("49.00")

    paid_invoice = service.mark_invoice_paid(
        organization_id="org-1",
        invoice_id=invoice.invoice_id,
    )
    assert paid_invoice.status == "paid"
    assert paid_invoice.amount_paid == Decimal("49.00")


def test_billing_usage_idempotency_is_scoped_to_organization() -> None:
    service = BillingService(InMemoryBillingStore())
    service.seed_pricing_catalog()
    starter = service.get_plan_by_slug("starter")
    assert starter is not None

    org_one_subscription = service.create_subscription(
        organization_id="org-1",
        plan_id=starter.plan_id,
        billing_cycle="monthly",
    )
    org_two_subscription = service.create_subscription(
        organization_id="org-2",
        plan_id=starter.plan_id,
        billing_cycle="monthly",
    )

    first = service.record_usage(
        organization_id="org-1",
        subscription_id=org_one_subscription.subscription_id,
        resource_type="conversations",
        quantity=25,
        usage_key="shared-batch",
    )
    duplicate = service.record_usage(
        organization_id="org-1",
        subscription_id=org_one_subscription.subscription_id,
        resource_type="conversations",
        quantity=25,
        usage_key="shared-batch",
    )
    other_org = service.record_usage(
        organization_id="org-2",
        subscription_id=org_two_subscription.subscription_id,
        resource_type="conversations",
        quantity=40,
        usage_key="shared-batch",
    )

    assert duplicate.usage_id == first.usage_id
    assert other_org.usage_id != first.usage_id

    summaries = {
        "org-1": service.get_usage_summary(
            organization_id="org-1",
            subscription_id=org_one_subscription.subscription_id,
        ),
        "org-2": service.get_usage_summary(
            organization_id="org-2",
            subscription_id=org_two_subscription.subscription_id,
        ),
    }
    assert next(item for item in summaries["org-1"] if item.resource_type == "conversations").current_usage == 25
    assert next(item for item in summaries["org-2"] if item.resource_type == "conversations").current_usage == 40


def test_billing_service_cancels_resumes_and_changes_plan() -> None:
    service = BillingService(InMemoryBillingStore())
    service.seed_pricing_catalog()
    free_plan = service.get_plan_by_slug("free")
    professional_plan = service.get_plan_by_slug("professional")
    assert free_plan is not None
    assert professional_plan is not None

    subscription = service.create_subscription(
        organization_id="org-2",
        plan_id=free_plan.plan_id,
    )
    scheduled_cancel = service.cancel_subscription(
        subscription_id=subscription.subscription_id,
        at_period_end=True,
    )
    assert scheduled_cancel.cancel_at == subscription.current_period_end
    assert scheduled_cancel.status == "active"

    resumed = service.resume_subscription(subscription.subscription_id)
    assert resumed.cancel_at is None
    assert resumed.status == "active"

    changed = service.change_plan(
        subscription_id=subscription.subscription_id,
        new_plan_id=professional_plan.plan_id,
    )
    assert changed.plan_id == professional_plan.plan_id

    with pytest.raises(ValueError):
        service.create_subscription(
            organization_id="org-2",
            plan_id=professional_plan.plan_id,
        )
