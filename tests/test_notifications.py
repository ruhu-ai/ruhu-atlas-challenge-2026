from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI

from ruhu.api_auth import AuthContextMiddleware, AuthContextResolver
from ruhu.auth import AuthService, JWTCodec
from ruhu.db import build_session_factory
from ruhu.identity import InMemoryIdentityStore, Organization, OrganizationMembership, User
from ruhu.notifications_api import install_notifications_router
from ruhu.notifications.models import NotificationCreate, NotificationRecord
from ruhu.notifications.service import emit_notification, _resolve_expiry_hours
from ruhu.notifications.store import InMemoryNotificationStore, SQLAlchemyNotificationStore

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_store() -> InMemoryNotificationStore:
    return InMemoryNotificationStore()


def _build_notifications_api_app() -> tuple[FastAPI, InMemoryNotificationStore, AuthService]:
    identity_store = InMemoryIdentityStore()
    identity_store.save_organization(
        Organization(
            organization_id="org-1",
            slug="org-1",
            name="Org 1",
        )
    )
    identity_store.save_user(User(user_id="user-1", email="user-1@example.com"))
    identity_store.save_user(User(user_id="user-2", email="user-2@example.com"))
    identity_store.add_organization_membership(
        OrganizationMembership(user_id="user-1", organization_id="org-1", role="admin")
    )
    identity_store.add_organization_membership(
        OrganizationMembership(user_id="user-2", organization_id="org-1", role="developer")
    )
    auth_service = AuthService(
        identity_store=identity_store,
        jwt_codec=JWTCodec(secret=TEST_HS256_SECRET),
    )
    notification_store = InMemoryNotificationStore()
    app = FastAPI()
    app.add_middleware(AuthContextMiddleware, resolver=AuthContextResolver(auth_service=auth_service))
    install_notifications_router(app, notification_store=notification_store)
    return app, notification_store, auth_service


def _base_create(
    *,
    org: str = "org-1",
    user: str | None = "user-1",
    category: str = "eval.run_completed_pass",
    urgency: str = "fyi",
    level: str = "info",
    expires_after_hours: int | None = 24,
) -> NotificationCreate:
    return NotificationCreate(
        organization_id=org,
        user_id=user,
        category=category,
        level=level,
        urgency=urgency,
        title="Test notification",
        message=None,
        url=None,
        url_label=None,
        source_type=None,
        source_id=None,
        payload={},
        expires_after_hours=expires_after_hours,
    )


# ---------------------------------------------------------------------------
# Expiry policy unit tests
# ---------------------------------------------------------------------------

class TestResolveExpiryHours:
    def test_explicit_override_wins(self) -> None:
        assert _resolve_expiry_hours("billing.invoice_paid", "fyi", 5) == 5

    def test_billing_category_never_expires(self) -> None:
        assert _resolve_expiry_hours("billing.invoice_paid", "soon", None) is None

    def test_auth_category_default_720h(self) -> None:
        assert _resolve_expiry_hours("auth.role_changed", "soon", None) == 720

    def test_fyi_caps_auth_to_24h(self) -> None:
        # auth. prefix → 720h, but fyi caps to 24h
        assert _resolve_expiry_hours("auth.role_changed", "fyi", None) == 24

    def test_default_category_72h(self) -> None:
        assert _resolve_expiry_hours("something.unknown", "soon", None) == 72

    def test_fyi_on_default_caps_to_24h(self) -> None:
        assert _resolve_expiry_hours("something.unknown", "fyi", None) == 24

    def test_eval_category_72h(self) -> None:
        assert _resolve_expiry_hours("eval.run_completed_pass", "soon", None) == 72

    def test_knowledge_indexed_24h(self) -> None:
        assert _resolve_expiry_hours("knowledge.document_indexed", "soon", None) == 24

    def test_billing_none_ignores_urgency(self) -> None:
        # even with fyi, billing.* never expires
        assert _resolve_expiry_hours("billing.overdue", "fyi", None) is None


# ---------------------------------------------------------------------------
# InMemoryNotificationStore — visibility rules
# ---------------------------------------------------------------------------

class TestInMemoryVisibility:
    def test_user_sees_own_notifications(self) -> None:
        store = _make_store()
        store.create(_base_create(org="org-1", user="user-1"))
        records = store.list_for_user("org-1", "user-1", limit=10)
        assert len(records) == 1

    def test_user_does_not_see_other_user_notifications(self) -> None:
        store = _make_store()
        store.create(_base_create(org="org-1", user="user-2"))
        records = store.list_for_user("org-1", "user-1", limit=10)
        assert len(records) == 0

    def test_org_broadcast_visible_to_all_members(self) -> None:
        store = _make_store()
        store.create(_base_create(org="org-1", user=None))  # broadcast
        records = store.list_for_user("org-1", "user-1", limit=10)
        assert len(records) == 1

    def test_different_org_not_visible(self) -> None:
        store = _make_store()
        store.create(_base_create(org="org-2", user="user-1"))
        records = store.list_for_user("org-1", "user-1", limit=10)
        assert len(records) == 0

    def test_dismissed_not_visible(self) -> None:
        store = _make_store()
        record = store.create(_base_create(org="org-1", user="user-1"))
        store.dismiss(record.notification_id, "org-1", "user-1")
        records = store.list_for_user("org-1", "user-1", limit=10)
        assert len(records) == 0

    def test_expired_not_visible(self) -> None:
        store = _make_store()
        spec = _base_create(org="org-1", user="user-1", expires_after_hours=None)
        # Manually inject with past expiry
        spec_with_past_expiry = spec.model_copy(update={"expires_after_hours": None})
        record = store.create(spec_with_past_expiry)
        # Patch the record's expires_at to the past
        idx = next(i for i, r in enumerate(store._records) if r.notification_id == record.notification_id)
        store._records[idx] = store._records[idx].model_copy(
            update={"expires_at": _utcnow() - timedelta(hours=1)}
        )
        records = store.list_for_user("org-1", "user-1", limit=10)
        assert len(records) == 0

    def test_limit_respected(self) -> None:
        store = _make_store()
        for _ in range(10):
            store.create(_base_create(org="org-1", user="user-1"))
        records = store.list_for_user("org-1", "user-1", limit=3)
        assert len(records) == 3

    def test_unread_only_filter(self) -> None:
        store = _make_store()
        r1 = store.create(_base_create(org="org-1", user="user-1"))
        store.create(_base_create(org="org-1", user="user-1"))
        store.mark_read(r1.notification_id, "org-1", "user-1")
        unread = store.list_for_user("org-1", "user-1", limit=10, unread_only=True)
        assert len(unread) == 1
        assert unread[0].read_at is None


# ---------------------------------------------------------------------------
# InMemoryNotificationStore — mutation operations
# ---------------------------------------------------------------------------

class TestInMemoryMutations:
    def test_mark_read_returns_true_once(self) -> None:
        store = _make_store()
        record = store.create(_base_create())
        assert store.mark_read(record.notification_id, "org-1", "user-1") is True

    def test_mark_read_already_read_returns_false(self) -> None:
        store = _make_store()
        record = store.create(_base_create())
        store.mark_read(record.notification_id, "org-1", "user-1")
        assert store.mark_read(record.notification_id, "org-1", "user-1") is False

    def test_mark_read_other_users_private_notification_returns_false(self) -> None:
        store = _make_store()
        record = store.create(_base_create(org="org-1", user="user-2"))
        assert store.mark_read(record.notification_id, "org-1", "user-1") is False

    def test_mark_all_read_returns_count(self) -> None:
        store = _make_store()
        store.create(_base_create(org="org-1", user="user-1"))
        store.create(_base_create(org="org-1", user="user-1"))
        count = store.mark_all_read("org-1", "user-1")
        assert count == 2

    def test_mark_all_read_only_affects_target_user(self) -> None:
        store = _make_store()
        store.create(_base_create(org="org-1", user="user-1"))
        store.create(_base_create(org="org-1", user="user-2"))
        store.mark_all_read("org-1", "user-1")
        # user-2's notification still unread
        assert store.count_unread("org-1", "user-2") == 1

    def test_count_unread(self) -> None:
        store = _make_store()
        r = store.create(_base_create(org="org-1", user="user-1"))
        store.create(_base_create(org="org-1", user="user-1"))
        assert store.count_unread("org-1", "user-1") == 2
        store.mark_read(r.notification_id, "org-1", "user-1")
        assert store.count_unread("org-1", "user-1") == 1

    def test_dismiss_returns_true(self) -> None:
        store = _make_store()
        record = store.create(_base_create())
        assert store.dismiss(record.notification_id, "org-1", "user-1") is True

    def test_dismiss_already_dismissed_returns_false(self) -> None:
        store = _make_store()
        record = store.create(_base_create())
        store.dismiss(record.notification_id, "org-1", "user-1")
        assert store.dismiss(record.notification_id, "org-1", "user-1") is False

    def test_dismiss_other_users_private_notification_returns_false(self) -> None:
        store = _make_store()
        record = store.create(_base_create(org="org-1", user="user-2"))
        assert store.dismiss(record.notification_id, "org-1", "user-1") is False

    def test_dismiss_wrong_org_returns_false(self) -> None:
        store = _make_store()
        record = store.create(_base_create(org="org-1"))
        assert store.dismiss(record.notification_id, "org-other", "user-1") is False


# ---------------------------------------------------------------------------
# emit_notification — error swallowing
# ---------------------------------------------------------------------------

class TestEmitNotification:
    def test_emits_to_store(self) -> None:
        store = _make_store()
        emit_notification(
            store,
            organization_id="org-1",
            category="agent.published",
            title="My agent published",
            user_id="user-1",
        )
        records = store.list_for_user("org-1", "user-1", limit=10)
        assert len(records) == 1
        assert records[0].category == "agent.published"

    def test_does_not_raise_on_store_failure(self) -> None:
        class BrokenStore(InMemoryNotificationStore):
            def create(self, spec: NotificationCreate) -> NotificationRecord:
                raise RuntimeError("DB is down")

        # Should not propagate the exception
        emit_notification(BrokenStore(), organization_id="org-1", category="test", title="T")

    def test_default_expiry_applied(self) -> None:
        store = _make_store()
        emit_notification(
            store,
            organization_id="org-1",
            category="billing.invoice_paid",
            title="Invoice paid",
            urgency="soon",
        )
        records = store._records
        assert len(records) == 1
        # billing.* → never expires
        assert records[0].expires_at is None


# ---------------------------------------------------------------------------
# SQLAlchemy store — round-trip smoke test (requires postgres)
# ---------------------------------------------------------------------------

def test_sqlalchemy_notification_store_round_trip(postgres_database_url_factory) -> None:
    from ruhu.db import run_migrations

    db_url = postgres_database_url_factory()
    run_migrations(db_url)
    session_factory = build_session_factory(db_url)
    store = SQLAlchemyNotificationStore(session_factory)

    spec = _base_create(org="org-sql", user="user-sql", expires_after_hours=48)
    record = store.create(spec)
    assert record.notification_id
    assert record.organization_id == "org-sql"
    assert record.user_id == "user-sql"
    assert record.expires_at is not None

    # list
    records = store.list_for_user("org-sql", "user-sql", limit=10)
    assert len(records) == 1

    # unread count
    assert store.count_unread("org-sql", "user-sql") == 1

    # mark read
    assert store.mark_read(record.notification_id, "org-sql", "user-sql") is True
    assert store.count_unread("org-sql", "user-sql") == 0
    assert store.mark_read(record.notification_id, "org-sql", "user-sql") is False

    # dismiss
    spec2 = _base_create(org="org-sql", user="user-sql")
    record2 = store.create(spec2)
    assert store.dismiss(record2.notification_id, "org-sql", "user-sql") is True
    visible = store.list_for_user("org-sql", "user-sql", limit=10)
    assert all(r.notification_id != record2.notification_id for r in visible)
    assert store.dismiss(record2.notification_id, "org-sql", "user-sql") is False


def test_notifications_api_rejects_private_notification_mutations_from_other_users() -> None:
    async def run() -> None:
        app, store, auth_service = _build_notifications_api_app()
        target = store.create(_base_create(org="org-1", user="user-2"))
        issued = auth_service.issue_browser_session(user_id="user-1", organization_id="org-1")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.headers["Authorization"] = f"Bearer {issued.access_token}"

            mark_read = await client.post(
                "/notifications/mark-read",
                json={"notification_id": target.notification_id},
            )
            assert mark_read.status_code == 200
            assert mark_read.json() == {"marked": 0}

            dismiss = await client.post(f"/notifications/{target.notification_id}/dismiss")
            assert dismiss.status_code == 200
            assert dismiss.json() == {"dismissed": False}

        visible = store.list_for_user("org-1", "user-2", limit=10)
        assert visible[0].read_at is None
        assert visible[0].dismissed_at is None

    asyncio.run(run())
