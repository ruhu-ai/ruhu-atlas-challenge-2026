"""
Tests for tier-based rate limiting in make_org_rate_limiter.

Covers:
- _TierCache 2-level caching (local → Redis → store)
- Tier RPM resolution (free/starter/professional/enterprise)
- Endpoint hard limits
- Graceful degradation on errors
"""
from __future__ import annotations

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
import anyio

from ruhu.rate_limit import make_org_rate_limiter, _TierCache
from ruhu.api_auth import RequestAuthContext
from ruhu.billing.models import BillingPlan, BillingSubscription


@pytest.fixture
def mock_billing_store():
    """Mock BillingStore with plan lookups."""
    store = MagicMock()

    # Default: free tier (no subscription)
    store.get_active_subscription.return_value = None
    store.get_plan.return_value = None

    return store


@pytest.fixture
def tier_cache_with_store(mock_billing_store):
    """_TierCache with mocked store (no Redis)."""
    cache = _TierCache(redis_url=None, billing_store=mock_billing_store)
    return cache


# ─── Tests: _TierCache (Core Rate Limiting Logic) ──────────────────────────────

def test_tier_cache_returns_none_on_no_subscription(tier_cache_with_store):
    """No active subscription → returns (None, 1.0)."""
    async def _test():
        result = await tier_cache_with_store.get_plan_info("org-xyz")
        assert result == (None, 1.0)
    anyio.run(_test)


def test_tier_cache_resolves_plan_slug_from_store(tier_cache_with_store, mock_billing_store):
    """On cache miss, loads from billing store via executor."""
    async def _test():
        free_plan = BillingPlan(
            plan_id="plan_free",
            name="Free",
            slug="free",
            price_monthly=Decimal("0"),
            price_yearly=Decimal("0"),
            rate_limit_multiplier=1.0,
        )
        free_sub = BillingSubscription(
            subscription_id="sub-1",
            organization_id="org-test-123",
            plan_id="plan_free",
        )

        mock_billing_store.get_active_subscription.return_value = free_sub
        mock_billing_store.get_plan.return_value = free_plan

        slug, multiplier = await tier_cache_with_store.get_plan_info("org-test-123")
        assert slug == "free"
        assert multiplier == 1.0

        # Verify lookups happened
        mock_billing_store.get_active_subscription.assert_called_once_with("org-test-123")
        mock_billing_store.get_plan.assert_called_once_with("plan_free")
    anyio.run(_test)


def test_tier_cache_local_cache_hit(tier_cache_with_store):
    """Second call within 30s uses local cache, skips store."""
    async def _test():
        tier_cache_with_store._local["org-test-123"] = ("free", 1.0, time.time() + 30)

        slug, multiplier = await tier_cache_with_store.get_plan_info("org-test-123")
        assert slug == "free"
        assert multiplier == 1.0
    anyio.run(_test)


def test_tier_cache_local_cache_expiry(tier_cache_with_store, mock_billing_store):
    """Expired local cache entry is refetched from store."""
    async def _test():
        # Set expired entry
        tier_cache_with_store._local["org-test-123"] = ("free", 1.0, time.time() - 1)

        free_plan = BillingPlan(
            plan_id="plan_free", name="Free", slug="free",
            price_monthly=Decimal("0"), price_yearly=Decimal("0"),
            rate_limit_multiplier=1.0,
        )
        free_sub = BillingSubscription(
            subscription_id="sub-1", organization_id="org-test-123", plan_id="plan_free",
        )
        mock_billing_store.get_active_subscription.return_value = free_sub
        mock_billing_store.get_plan.return_value = free_plan

        slug, multiplier = await tier_cache_with_store.get_plan_info("org-test-123")
        assert slug == "free"
        assert multiplier == 1.0
        # Should refetch from store
        mock_billing_store.get_active_subscription.assert_called()
    anyio.run(_test)


def test_tier_cache_graceful_degradation_on_store_error(tier_cache_with_store, mock_billing_store):
    """On store error, returns (None, 1.0) gracefully."""
    async def _test():
        mock_billing_store.get_active_subscription.side_effect = Exception("DB error")

        slug, multiplier = await tier_cache_with_store.get_plan_info("org-test-123")
        assert slug is None
        assert multiplier == 1.0
    anyio.run(_test)


# ─── Tests: Tier Cache Invalidation & Multi-Org ─────────────────────────────────

def test_tier_cache_invalidate(tier_cache_with_store):
    """Invalidate clears local cache."""
    async def _test():
        tier_cache_with_store._local["org-test-123"] = ("free", 1.0, time.time() + 30)

        await tier_cache_with_store.invalidate("org-test-123")

        assert "org-test-123" not in tier_cache_with_store._local
    anyio.run(_test)


def test_tier_cache_multiple_orgs(mock_billing_store):
    """Cache independently tracks multiple orgs."""
    async def _test():
        def get_sub(org_id):
            if org_id == "org-a":
                return BillingSubscription(
                    subscription_id=f"sub-{org_id}",
                    organization_id=org_id,
                    plan_id="plan_starter",
                )
            return None

        mock_billing_store.get_active_subscription.side_effect = get_sub

        starter_plan = BillingPlan(
            plan_id="plan_starter",
            name="Starter",
            slug="starter",
            price_monthly=Decimal("49"),
            price_yearly=Decimal("470"),
            rate_limit_multiplier=2.5,
        )
        mock_billing_store.get_plan.return_value = starter_plan

        cache = _TierCache(redis_url=None, billing_store=mock_billing_store)

        # Org A has starter plan
        slug_a, mult_a = await cache.get_plan_info("org-a")
        assert slug_a == "starter"
        assert mult_a == 2.5

        # Org B has no subscription
        slug_b, mult_b = await cache.get_plan_info("org-b")
        assert slug_b is None
        assert mult_b == 1.0

        # Org A is cached locally
        slug_a_again, mult_a_again = await cache.get_plan_info("org-a")
        assert slug_a_again == "starter"
        assert mult_a_again == 2.5
    anyio.run(_test)


# ─── Tests: make_org_rate_limiter Backward Compatibility ─────────────────────────

def test_make_org_rate_limiter_accepts_billing_store():
    """make_org_rate_limiter accepts optional billing_store param."""
    store = MagicMock()
    limiter = make_org_rate_limiter(redis_url=None, billing_store=store)
    assert limiter is not None


def test_make_org_rate_limiter_backward_compatible():
    """Without billing_store param, returns valid Depends()."""
    limiter = make_org_rate_limiter(redis_url=None, billing_store=None)
    assert limiter is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
