from __future__ import annotations

from decimal import Decimal

from .models import BillingPlan


def default_pricing_catalog() -> tuple[BillingPlan, ...]:
    return (
        BillingPlan(
            plan_id="plan_free",
            name="Free",
            slug="free",
            description="Perfect for testing and small projects.",
            price_monthly=Decimal("0.00"),
            price_yearly=Decimal("0.00"),
            max_agents=1,
            max_conversations_monthly=100,
            max_voice_minutes_monthly=60,
            max_team_members=1,
            features={
                "analytics": False,
                "webhooks": False,
                "custom_integrations": False,
                "priority_support": False,
                "sla": False,
                "api_rpm": 60,  # 1 request per second
            },
            sort_order=0,
            rate_limit_multiplier=1.0,
        ),
        BillingPlan(
            plan_id="plan_starter",
            name="Starter",
            slug="starter",
            description="For small teams getting started with AI voice and chat agents.",
            price_monthly=Decimal("49.00"),
            price_yearly=Decimal("470.00"),
            max_agents=3,
            max_conversations_monthly=1000,
            max_voice_minutes_monthly=500,
            max_team_members=3,
            features={
                "analytics": True,
                "webhooks": False,
                "custom_integrations": False,
                "priority_support": False,
                "sla": False,
                "api_rpm": 150,  # 2.5 requests per second
            },
            sort_order=1,
            rate_limit_multiplier=2.5,
        ),
        BillingPlan(
            plan_id="plan_professional",
            name="Professional",
            slug="professional",
            description="For growing businesses with advanced automation and channel needs.",
            price_monthly=Decimal("199.00"),
            price_yearly=Decimal("1910.00"),
            max_agents=10,
            max_conversations_monthly=10000,
            max_voice_minutes_monthly=5000,
            max_team_members=10,
            features={
                "analytics": True,
                "webhooks": True,
                "custom_integrations": True,
                "priority_support": True,
                "sla": False,
                "api_rpm": 300,  # 5 requests per second
            },
            sort_order=2,
            rate_limit_multiplier=5.0,
        ),
        BillingPlan(
            plan_id="plan_enterprise",
            name="Enterprise",
            slug="enterprise",
            description="Custom solutions for large organizations with billing-managed onboarding.",
            price_monthly=Decimal("999.00"),
            price_yearly=Decimal("9590.00"),
            max_agents=None,
            max_conversations_monthly=None,
            max_voice_minutes_monthly=None,
            max_team_members=None,
            features={
                "analytics": True,
                "webhooks": True,
                "custom_integrations": True,
                "priority_support": True,
                "sla": True,
                "dedicated_support": True,
                "custom_development": True,
                "api_rpm": 600,  # 10 requests per second
            },
            sort_order=3,
            rate_limit_multiplier=10.0,
        ),
    )
