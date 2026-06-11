from __future__ import annotations

from decimal import Decimal

from ruhu.billing import BillingService, SQLAlchemyBillingStore
from ruhu.db import build_session_factory


def test_sqlalchemy_billing_store_round_trips_seed_catalog_and_domain_records(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyBillingStore(session_factory)
    service = BillingService(store)

    seeded = service.seed_pricing_catalog()
    assert [plan.slug for plan in seeded] == ["free", "starter", "professional", "enterprise"]

    professional = service.get_plan_by_slug("professional")
    assert professional is not None
    assert professional.price_monthly == Decimal("199.00")

    subscription = service.create_subscription(
        organization_id="org-sql",
        plan_id=professional.plan_id,
        billing_cycle="yearly",
    )
    assert service.get_current_subscription("org-sql").subscription_id == subscription.subscription_id

    service.record_usage(
        organization_id="org-sql",
        subscription_id=subscription.subscription_id,
        resource_type="conversations",
        quantity=120,
        usage_key="seeded-conversations",
    )
    service.record_usage(
        organization_id="org-sql",
        subscription_id=subscription.subscription_id,
        resource_type="voice_minutes",
        quantity=300,
    )

    summaries = {
        summary.resource_type: summary
        for summary in service.get_usage_summary(
            organization_id="org-sql",
            subscription_id=subscription.subscription_id,
        )
    }
    assert summaries["conversations"].current_usage == 120
    assert summaries["voice_minutes"].current_usage == 300
    assert summaries["team_members"].limit == 10

    invoice = service.generate_invoice(
        organization_id="org-sql",
        subscription_id=subscription.subscription_id,
    )
    assert invoice.total == Decimal("1910.00")
    assert service.get_invoice(
        organization_id="org-sql",
        invoice_id=invoice.invoice_id,
    ).invoice_number == invoice.invoice_number

    invoices = service.list_invoices(organization_id="org-sql")
    assert len(invoices) == 1
    assert invoices[0].invoice_id == invoice.invoice_id


def test_sqlalchemy_billing_usage_idempotency_is_scoped_to_organization(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    service = BillingService(SQLAlchemyBillingStore(session_factory))
    service.seed_pricing_catalog()
    starter = service.get_plan_by_slug("starter")
    assert starter is not None

    org_one_subscription = service.create_subscription(
        organization_id="org-sql-1",
        plan_id=starter.plan_id,
        billing_cycle="monthly",
    )
    org_two_subscription = service.create_subscription(
        organization_id="org-sql-2",
        plan_id=starter.plan_id,
        billing_cycle="monthly",
    )

    first = service.record_usage(
        organization_id="org-sql-1",
        subscription_id=org_one_subscription.subscription_id,
        resource_type="conversations",
        quantity=10,
        usage_key="shared-batch",
    )
    duplicate = service.record_usage(
        organization_id="org-sql-1",
        subscription_id=org_one_subscription.subscription_id,
        resource_type="conversations",
        quantity=10,
        usage_key="shared-batch",
    )
    other_org = service.record_usage(
        organization_id="org-sql-2",
        subscription_id=org_two_subscription.subscription_id,
        resource_type="conversations",
        quantity=15,
        usage_key="shared-batch",
    )

    assert duplicate.usage_id == first.usage_id
    assert other_org.usage_id != first.usage_id
    assert next(
        item
        for item in service.get_usage_summary(
            organization_id="org-sql-1",
            subscription_id=org_one_subscription.subscription_id,
        )
        if item.resource_type == "conversations"
    ).current_usage == 10
    assert next(
        item
        for item in service.get_usage_summary(
            organization_id="org-sql-2",
            subscription_id=org_two_subscription.subscription_id,
        )
        if item.resource_type == "conversations"
    ).current_usage == 15
