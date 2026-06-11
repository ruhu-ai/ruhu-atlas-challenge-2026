from __future__ import annotations

from datetime import timedelta
from dataclasses import dataclass

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ruhu.browser_tasks import (
    APIConnectionBrowserCredentialResolver,
    APIConnectionBrowserCredentialValidator,
    BrowserCredentialRef,
    BrowserTaskPack,
    BrowserTaskPackAccessPolicy,
    BrowserTaskPackApprovalPolicy,
    BrowserCredentialRequirement,
    BrowserTaskPackExecutionPolicy,
    BrowserTaskPackRegistry,
    BrowserTaskPackRetryPolicy,
    BrowserWorkerError,
    BrowserWorkerProgress,
    BrowserWorkerRequest,
    BrowserWorkerResult,
)
from ruhu.browser_tasks.sqlalchemy_models import (
    BrowserApprovalRecord,
    BrowserTaskPackAccessRecord,
    BrowserTaskEventRecord,
    BrowserTaskRecord,
)
from ruhu.browser_tasks.service import BrowserTaskService
from ruhu.browser_tasks.store import InMemoryBrowserTaskStore, SQLAlchemyBrowserTaskStore
from ruhu.db_models import APIConnectionRecord
from ruhu.tools.cipher import FernetCipher
from ruhu.tools.management import APIConnectionStore


@dataclass(slots=True)
class FakeConnectionRecord:
    connection_id: str
    organization_id: str
    provider: str
    auth_type: str
    status: str = "active"


class FakeConnectionStore:
    def __init__(self, *records: FakeConnectionRecord) -> None:
        self._records = {record.connection_id: record for record in records}
        self.credentials: dict[str, dict] = {}

    def get(self, connection_id: str) -> FakeConnectionRecord | None:
        return self._records.get(connection_id)

    def decrypt_credentials_from_record(
        self,
        record: FakeConnectionRecord,
        *,
        actor_id: str | None,
        actor_type: str,
        purpose: str,
    ) -> dict:
        self.last_decrypt = {
            "connection_id": record.connection_id,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "purpose": purpose,
        }
        return self.credentials.get(record.connection_id, {})


def test_browser_task_service_tracks_approval_and_events() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())

    created = service.create_task(
        conversation_id="conv_1",
        organization_id="org_1",
        title="Check invoice status",
        summary="Open the billing portal and verify the latest invoice.",
        requires_approval=True,
        approval_prompt="Approve secure browser access for invoice lookup.",
    )

    assert created.task.state == "awaiting_approval"
    assert created.approval is not None
    assert created.approval.state == "pending"
    assert created.recent_events[-1].event_type == "browser.awaiting_approval"

    approved = service.approve(
        approval_id=created.approval.approval_id,
        organization_id="org_1",
    )
    assert approved.task.state == "queued"
    assert approved.approval is not None
    assert approved.approval.state == "approved"

    running = service.record_progress(
        task_id=approved.task.task_id,
        organization_id="org_1",
        event_type="browser.navigating",
        message="Opening billing portal.",
        state="running",
    )
    assert running.task.state == "running"

    completed = service.complete_task(
        task_id=approved.task.task_id,
        organization_id="org_1",
        result={"invoice_id": "inv_123"},
        message="Invoice located.",
    )
    assert completed.task.state == "completed"
    assert completed.task.result["invoice_id"] == "inv_123"
    assert completed.recent_events[-1].event_type == "browser.completed"
    assert [event.event_sequence for event in completed.recent_events] == [1, 2, 3, 4]


def test_browser_task_service_lists_recent_tasks_for_operator_inbox() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())
    service.create_task(
        conversation_id="conv_inbox_1",
        organization_id="org_1",
        title="Queued task",
    )
    service.create_task(
        conversation_id="conv_inbox_2",
        organization_id="org_1",
        title="Approval task",
        requires_approval=True,
    )
    service.create_task(
        conversation_id="conv_inbox_3",
        organization_id="org_2",
        title="Other org task",
    )

    pending = service.list_recent_tasks(
        organization_id="org_1",
        approval_state="pending",
    )

    assert len(pending) == 1
    assert pending[0].task.title == "Approval task"

    inbox = service.list_recent_tasks(organization_id="org_1", limit=10)
    assert [item.task.title for item in inbox] == ["Approval task", "Queued task"]


def test_browser_task_service_rejects_progress_after_terminal_state() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())
    created = service.create_task(
        conversation_id="conv_2",
        organization_id="org_1",
        title="Check invoice status",
    )
    completed = service.complete_task(
        task_id=created.task.task_id,
        organization_id="org_1",
        result={"ok": True},
    )

    try:
        service.record_progress(
            task_id=completed.task.task_id,
            organization_id="org_1",
            event_type="browser.navigating",
            message="Opening billing portal.",
            state="running",
        )
    except ValueError as exc:
        assert str(exc) == "browser task is already completed"
    else:
        raise AssertionError("expected terminal progress to be rejected")


def test_browser_task_service_expires_stale_approval() -> None:
    store = InMemoryBrowserTaskStore()
    service = BrowserTaskService(store)
    created = service.create_task(
        conversation_id="conv_3",
        organization_id="org_1",
        title="Check invoice status",
        requires_approval=True,
        approval_ttl_seconds=60,
    )
    assert created.approval is not None
    expired_approval = created.approval.model_copy(
        update={"expires_at": created.approval.requested_at - timedelta(seconds=1)}
    )
    store.save_approval(expired_approval)

    try:
        service.approve(
            approval_id=expired_approval.approval_id,
            organization_id="org_1",
        )
    except ValueError as exc:
        assert str(exc) == "approval expired"
    else:
        raise AssertionError("expected expired approval to be rejected")

    snapshot = service.get_snapshot(created.task.task_id, organization_id="org_1")
    assert snapshot.task.state == "failed"
    assert snapshot.task.approval_state == "expired"
    assert snapshot.approval is not None
    assert snapshot.approval.state == "expired"
    assert snapshot.recent_events[-1].event_type == "browser.approval_expired"


def test_browser_task_service_persists_task_pack_execution_fields() -> None:
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_status",
                version="1.0.0",
                display_name="Payment status",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
                credentials=[
                    BrowserCredentialRequirement(
                        kind="oauth",
                        name="merchant_connection",
                    )
                ],
                performs_write=True,
                approval_policy=BrowserTaskPackApprovalPolicy(
                    approval_required=True,
                    approval_kinds=["change_confirmation"],
                    approval_ttl_seconds=120,
                ),
            )
        ]
    )
    service = BrowserTaskService(InMemoryBrowserTaskStore(), task_pack_registry=registry)

    created = service.create_task(
        conversation_id="conv_4",
        organization_id="org_1",
        title="Check payment status",
        task_pack_id="payment_status",
        input_payload={"payment_id": "pay_123"},
        credential_refs={"merchant_connection": "connection:conn_123"},
    )

    assert created.task.task_pack_id == "payment_status"
    assert created.task.task_pack_version == "1.0.0"
    assert created.task.start_url == "https://portal.example.com/payments"
    assert created.task.input_payload == {"payment_id": "pay_123"}
    assert created.task.credential_refs == {"merchant_connection": "connection:conn_123"}
    assert created.task.state == "awaiting_approval"
    assert created.approval is not None
    assert created.approval.kind == "change_confirmation"
    assert created.approval.context["task_pack_id"] == "payment_status"
    assert created.approval.context["task_pack_display_name"] == "Payment status"
    assert created.approval.context["allowed_domains"] == ["portal.example.com"]
    assert created.approval.context["performs_write"] is True
    assert created.approval.context["approval_kind"] == "change_confirmation"
    assert created.approval.context["credential_refs"] == [
        {
            "name": "merchant_connection",
            "kind": "oauth",
            "provider": None,
            "auth_type": None,
            "ref_label": "connection:conn_123",
        }
    ]

    try:
        service.create_task(
            conversation_id="conv_4",
            organization_id="org_1",
            title="Open wrong site",
            task_pack_id="payment_status",
            start_url="https://evil.example/payments",
        )
    except ValueError as exc:
        assert str(exc) == "start_url must match the browser task pack allowed domains"
    else:
        raise AssertionError("expected start_url outside the task pack to be rejected")


def test_sqlalchemy_browser_task_store_round_trips_task_pack_fields() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    BrowserTaskRecord.metadata.create_all(
        engine,
        tables=[
            BrowserTaskRecord.__table__,
            BrowserApprovalRecord.__table__,
            BrowserTaskEventRecord.__table__,
            BrowserTaskPackAccessRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                credentials=[
                    BrowserCredentialRequirement(
                        kind="session",
                        name="merchant_session",
                    )
                ],
            )
        ]
    )
    service = BrowserTaskService(SQLAlchemyBrowserTaskStore(session_factory), task_pack_registry=registry)

    created = service.create_task(
        conversation_id="conv_sql",
        organization_id="org_1",
        title="Lookup payment",
        task_pack_id="payment_lookup",
        start_url="https://portal.example.com/payments",
        input_payload={"payment_id": "pay_123"},
        credential_refs={"merchant_session": "connection:conn_sql"},
    )
    snapshot = service.get_snapshot(created.task.task_id, organization_id="org_1")

    assert snapshot.task.task_pack_id == "payment_lookup"
    assert snapshot.task.task_pack_version == "1.0.0"
    assert snapshot.task.start_url == "https://portal.example.com/payments"
    assert snapshot.task.input_payload == {"payment_id": "pay_123"}
    assert snapshot.task.credential_refs == {"merchant_session": "connection:conn_sql"}

    claimed = service.claim_next_task(worker_id="worker_sql", organization_id="org_1")
    assert claimed is not None
    assert claimed.task.task_id == created.task.task_id
    assert claimed.task.state == "running"
    assert claimed.task.lease_owner == "worker_sql"
    assert claimed.task.attempt_count == 1


def test_browser_task_service_validates_real_api_connection_record() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    BrowserTaskRecord.metadata.create_all(
        engine,
        tables=[
            BrowserTaskRecord.__table__,
            BrowserApprovalRecord.__table__,
            BrowserTaskEventRecord.__table__,
            BrowserTaskPackAccessRecord.__table__,
            APIConnectionRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    connection_store = APIConnectionStore(
        session_factory,
        blob_cipher=FernetCipher(primary=Fernet.generate_key().decode()),
    )
    connection = connection_store.create(
        organization_id="org_1",
        display_name="Merchant",
        provider="merchant",
        auth_type="oauth2",
    )
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
                credentials=[
                    BrowserCredentialRequirement(
                        kind="oauth",
                        name="merchant_connection",
                        provider="merchant",
                    )
                ],
            )
        ]
    )
    service = BrowserTaskService(
        SQLAlchemyBrowserTaskStore(session_factory),
        task_pack_registry=registry,
        credential_validator=APIConnectionBrowserCredentialValidator(connection_store),
    )

    created = service.create_task(
        conversation_id="conv_real_connection",
        organization_id="org_1",
        title="Lookup payment",
        task_pack_id="payment_lookup",
        credential_refs={"merchant_connection": f"connection:{connection.connection_id}"},
    )

    assert created.task.credential_refs == {"merchant_connection": f"connection:{connection.connection_id}"}


def test_browser_task_worker_claim_renew_release_flow() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())
    created = service.create_task(
        conversation_id="conv_worker",
        organization_id="org_1",
        title="Lookup payment",
    )

    claimed = service.claim_next_task(
        worker_id="worker_1",
        organization_id="org_1",
        lease_seconds=30,
    )
    assert claimed is not None
    assert claimed.task.task_id == created.task.task_id
    assert claimed.task.state == "running"
    assert claimed.task.lease_owner == "worker_1"
    assert claimed.task.lease_expires_at is not None
    assert claimed.task.attempt_count == 1
    assert claimed.recent_events[-1].event_type == "browser.worker_claimed"

    assert service.claim_next_task(worker_id="worker_2", organization_id="org_1") is None

    renewed = service.renew_task_lease(
        task_id=claimed.task.task_id,
        worker_id="worker_1",
        organization_id="org_1",
        lease_seconds=60,
    )
    assert renewed.task.lease_owner == "worker_1"

    try:
        service.renew_task_lease(
            task_id=claimed.task.task_id,
            worker_id="worker_2",
            organization_id="org_1",
        )
    except ValueError as exc:
        assert str(exc) == "browser task lease is not held by this worker"
    else:
        raise AssertionError("expected wrong worker lease renewal to be rejected")

    released = service.release_task_lease(
        task_id=claimed.task.task_id,
        worker_id="worker_1",
        organization_id="org_1",
    )
    assert released.task.state == "queued"
    assert released.task.lease_owner is None
    assert released.task.lease_expires_at is None
    assert released.recent_events[-1].event_type == "browser.worker_released"


def test_browser_task_worker_can_reclaim_expired_lease() -> None:
    store = InMemoryBrowserTaskStore()
    service = BrowserTaskService(store)
    created = service.create_task(
        conversation_id="conv_reclaim",
        organization_id="org_1",
        title="Lookup payment",
    )
    claimed = service.claim_next_task(
        worker_id="worker_1",
        organization_id="org_1",
        lease_seconds=30,
    )
    assert claimed is not None
    expired_task = claimed.task.model_copy(
        update={"lease_expires_at": claimed.task.updated_at - timedelta(seconds=1)}
    )
    store.save_task(expired_task)

    reclaimed = service.claim_next_task(worker_id="worker_2", organization_id="org_1")
    assert reclaimed is not None
    assert reclaimed.task.task_id == created.task.task_id
    assert reclaimed.task.lease_owner == "worker_2"
    assert reclaimed.task.attempt_count == 2


def test_browser_task_service_builds_worker_request_for_lease_holder() -> None:
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
                credentials=[
                    BrowserCredentialRequirement(
                        kind="oauth",
                        name="merchant_connection",
                    )
                ],
            )
        ]
    )
    service = BrowserTaskService(InMemoryBrowserTaskStore(), task_pack_registry=registry)
    created = service.create_task(
        conversation_id="conv_worker_request",
        organization_id="org_1",
        title="Lookup payment",
        task_pack_id="payment_lookup",
        input_payload={"payment_id": "pay_123"},
        credential_refs={"merchant_connection": "connection:conn_123"},
    )
    claimed = service.claim_next_task(worker_id="worker_1", organization_id="org_1")
    assert claimed is not None

    request = service.build_worker_request(
        task_id=created.task.task_id,
        worker_id="worker_1",
        organization_id="org_1",
    )
    assert request.task_id == created.task.task_id
    assert request.pack_id == "payment_lookup"
    assert request.start_url == "https://portal.example.com/payments"
    assert request.input == {"payment_id": "pay_123"}
    assert request.policy.allowed_domains == ["portal.example.com"]
    assert len(request.credentials) == 1
    assert request.credentials[0].name == "merchant_connection"
    assert request.credentials[0].kind == "oauth"
    assert request.credentials[0].secret_ref == "connection:conn_123"

    try:
        service.build_worker_request(
            task_id=created.task.task_id,
            worker_id="worker_2",
            organization_id="org_1",
        )
    except ValueError as exc:
        assert str(exc) == "browser task lease is not held by this worker"
    else:
        raise AssertionError("expected worker request to require the lease holder")


def test_browser_task_service_validates_task_pack_credential_refs() -> None:
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
                credentials=[
                    BrowserCredentialRequirement(
                        kind="oauth",
                        name="merchant_connection",
                    )
                ],
            )
        ]
    )
    service = BrowserTaskService(InMemoryBrowserTaskStore(), task_pack_registry=registry)

    try:
        service.create_task(
            conversation_id="conv_missing_credential",
            organization_id="org_1",
            title="Lookup payment",
            task_pack_id="payment_lookup",
        )
    except ValueError as exc:
        assert str(exc) == "missing required credential refs for task pack: merchant_connection"
    else:
        raise AssertionError("expected missing required credential refs to be rejected")

    try:
        service.create_task(
            conversation_id="conv_unknown_credential",
            organization_id="org_1",
            title="Lookup payment",
            task_pack_id="payment_lookup",
            credential_refs={"merchant_connection": "connection:conn_123", "extra": "connection:extra"},
        )
    except ValueError as exc:
        assert str(exc) == "unknown credential refs for task pack: extra"
    else:
        raise AssertionError("expected unknown credential refs to be rejected")


def test_browser_task_service_enforces_task_pack_access_policy() -> None:
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
            )
        ]
    )
    service = BrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=registry,
        task_pack_access_policy=BrowserTaskPackAccessPolicy(
            agent_allowed_pack_ids={("org_1", "agent_1"): {"other_pack"}}
        ),
    )

    try:
        service.create_task(
            conversation_id="conv_pack_policy",
            organization_id="org_1",
            agent_id="agent_1",
            title="Lookup payment",
            task_pack_id="payment_lookup",
        )
    except ValueError as exc:
        assert str(exc) == "browser task pack is not enabled for this agent: payment_lookup"
    else:
        raise AssertionError("expected task-pack access policy to reject the request")


def test_browser_task_service_validates_task_pack_input_schema() -> None:
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
                input_schema={
                    "type": "object",
                    "properties": {"payment_id": {"type": "string"}},
                    "required": ["payment_id"],
                    "additionalProperties": False,
                },
            )
        ]
    )
    service = BrowserTaskService(InMemoryBrowserTaskStore(), task_pack_registry=registry)

    try:
        service.create_task(
            conversation_id="conv_invalid_input",
            organization_id="org_1",
            title="Lookup payment",
            task_pack_id="payment_lookup",
            input_payload={"payment_id": 123},
        )
    except ValueError as exc:
        assert "browser task input does not match task pack schema" in str(exc)
    else:
        raise AssertionError("expected invalid task-pack input to be rejected")


def test_browser_task_service_validates_worker_result_schema() -> None:
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
                result_schema={
                    "type": "object",
                    "properties": {"payment_status": {"type": "string"}},
                    "required": ["payment_status"],
                    "additionalProperties": False,
                },
            )
        ]
    )
    service = BrowserTaskService(InMemoryBrowserTaskStore(), task_pack_registry=registry)
    created = service.create_task(
        conversation_id="conv_invalid_result",
        organization_id="org_1",
        title="Lookup payment",
        task_pack_id="payment_lookup",
    )
    claimed = service.claim_next_task(worker_id="worker_1", organization_id="org_1")
    assert claimed is not None

    try:
        service.apply_worker_result(
            worker_id="worker_1",
            organization_id="org_1",
            result=BrowserWorkerResult(
                task_id=created.task.task_id,
                success=True,
                output={"payment_status": 404},
            ),
        )
    except ValueError as exc:
        assert "browser worker result does not match task pack schema" in str(exc)
    else:
        raise AssertionError("expected invalid worker result to be rejected")


def test_browser_task_service_validates_connection_refs_against_connection_store() -> None:
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
                credentials=[
                    BrowserCredentialRequirement(
                        kind="oauth",
                        name="merchant_connection",
                        provider="merchant",
                    )
                ],
            )
        ]
    )
    service = BrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=registry,
        credential_validator=APIConnectionBrowserCredentialValidator(
            FakeConnectionStore(
                FakeConnectionRecord(
                    connection_id="conn_active",
                    organization_id="org_1",
                    provider="merchant",
                    auth_type="oauth2",
                ),
                FakeConnectionRecord(
                    connection_id="conn_other_org",
                    organization_id="org_2",
                    provider="merchant",
                    auth_type="oauth2",
                ),
                FakeConnectionRecord(
                    connection_id="conn_wrong_provider",
                    organization_id="org_1",
                    provider="other",
                    auth_type="oauth2",
                ),
                FakeConnectionRecord(
                    connection_id="conn_inactive",
                    organization_id="org_1",
                    provider="merchant",
                    auth_type="oauth2",
                    status="disabled",
                ),
            )
        ),
    )

    created = service.create_task(
        conversation_id="conv_connection_valid",
        organization_id="org_1",
        title="Lookup payment",
        task_pack_id="payment_lookup",
        credential_refs={"merchant_connection": "connection:conn_active"},
    )
    assert created.task.credential_refs == {"merchant_connection": "connection:conn_active"}

    for secret_ref, expected in [
        ("conn_active", "credential ref merchant_connection must use connection:<connection_id>"),
        ("connection:missing", "credential ref merchant_connection references unknown connection"),
        (
            "connection:conn_other_org",
            "credential ref merchant_connection references a connection outside this organization",
        ),
        ("connection:conn_wrong_provider", "credential ref merchant_connection references the wrong provider"),
        ("connection:conn_inactive", "credential ref merchant_connection references an inactive connection"),
    ]:
        try:
            service.create_task(
                conversation_id="conv_connection_invalid",
                organization_id="org_1",
                title="Lookup payment",
                task_pack_id="payment_lookup",
                credential_refs={"merchant_connection": secret_ref},
            )
        except ValueError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError(f"expected {secret_ref} to be rejected")


def test_api_connection_browser_credential_resolver_returns_scoped_session_state() -> None:
    store = FakeConnectionStore(
        FakeConnectionRecord(
            connection_id="conn_session",
            organization_id="org_1",
            provider="merchant",
            auth_type="browser_session",
        )
    )
    storage_state = {"cookies": [{"name": "session", "value": "redacted"}], "origins": []}
    store.credentials["conn_session"] = {"playwright_storage_state": storage_state}
    resolver = APIConnectionBrowserCredentialResolver(store, actor_id="worker_1")
    request = BrowserWorkerRequest.from_task_pack(
        request_id="req_1",
        task_id="task_1",
        organization_id="org_1",
        conversation_id="conv_1",
        title="Lookup payment",
        pack=BrowserTaskPack(
            pack_id="payment_lookup",
            version="1.0.0",
            display_name="Payment lookup",
            allowed_domains=["portal.example.com"],
            start_url="https://portal.example.com/payments",
        ),
    )

    resolved = resolver.resolve(
        request=request,
        credential=BrowserCredentialRef(
            name="merchant_session",
            kind="session",
            secret_ref="connection:conn_session",
        ),
    )

    assert resolved.kind == "session"
    assert resolved.storage_state == storage_state
    assert store.last_decrypt == {
        "connection_id": "conn_session",
        "actor_id": "worker_1",
        "actor_type": "tool_runtime",
        "purpose": "browser_task_session",
    }


def test_api_connection_browser_credential_resolver_rejects_raw_oauth_credentials() -> None:
    store = FakeConnectionStore(
        FakeConnectionRecord(
            connection_id="conn_oauth",
            organization_id="org_1",
            provider="merchant",
            auth_type="oauth2",
        )
    )
    resolver = APIConnectionBrowserCredentialResolver(store)
    request = BrowserWorkerRequest.from_task_pack(
        request_id="req_1",
        task_id="task_1",
        organization_id="org_1",
        conversation_id="conv_1",
        title="Lookup payment",
        pack=BrowserTaskPack(
            pack_id="payment_lookup",
            version="1.0.0",
            display_name="Payment lookup",
            allowed_domains=["portal.example.com"],
            start_url="https://portal.example.com/payments",
        ),
    )

    try:
        resolver.resolve(
            request=request,
            credential=BrowserCredentialRef(
                name="merchant_connection",
                kind="oauth",
                secret_ref="connection:conn_oauth",
            ),
        )
    except ValueError as exc:
        assert str(exc) == "browser worker accepts only session credentials"
    else:
        raise AssertionError("expected raw oauth credential refs to be rejected")


def test_browser_task_service_records_worker_progress_idempotently() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())
    created = service.create_task(
        conversation_id="conv_worker_progress",
        organization_id="org_1",
        title="Lookup payment",
    )
    claimed = service.claim_next_task(worker_id="worker_1", organization_id="org_1")
    assert claimed is not None

    progress = BrowserWorkerProgress(
        task_id=created.task.task_id,
        event_sequence=1,
        phase="navigating",
        message="Opening payment portal.",
        metadata={"worker_id": "spoofed", "url": "https://portal.example.com"},
    )
    first = service.record_worker_progress(
        worker_id="worker_1",
        progress=progress,
        organization_id="org_1",
    )
    second = service.record_worker_progress(
        worker_id="worker_1",
        progress=progress,
        organization_id="org_1",
    )

    assert first.task.state == "running"
    assert [event.event_type for event in second.recent_events].count("browser.worker_navigating") == 1
    progress_event = next(event for event in second.recent_events if event.event_type == "browser.worker_navigating")
    assert progress_event.metadata["worker_id"] == "worker_1"
    assert progress_event.metadata["url"] == "https://portal.example.com"

    try:
        service.record_worker_progress(
            worker_id="worker_2",
            progress=BrowserWorkerProgress(
                task_id=created.task.task_id,
                event_sequence=2,
                phase="acting",
                message="Trying to act from the wrong worker.",
            ),
            organization_id="org_1",
        )
    except ValueError as exc:
        assert str(exc) == "browser task lease is not held by this worker"
    else:
        raise AssertionError("expected wrong worker progress to be rejected")


def test_browser_task_service_applies_successful_worker_result() -> None:
    service = BrowserTaskService(InMemoryBrowserTaskStore())
    created = service.create_task(
        conversation_id="conv_worker_result",
        organization_id="org_1",
        title="Lookup payment",
    )
    claimed = service.claim_next_task(worker_id="worker_1", organization_id="org_1")
    assert claimed is not None

    completed = service.apply_worker_result(
        worker_id="worker_1",
        organization_id="org_1",
        result=BrowserWorkerResult(
            task_id=created.task.task_id,
            success=True,
            summary="Payment found.",
            output={"payment_status": "settled"},
        ),
    )

    assert completed.task.state == "completed"
    assert completed.task.lease_owner is None
    assert completed.task.lease_expires_at is None
    assert completed.task.result["summary"] == "Payment found."
    assert completed.task.result["payment_status"] == "settled"
    assert completed.recent_events[-1].event_type == "browser.completed"


def test_browser_task_service_requeues_retryable_worker_failure_until_attempts_exhausted() -> None:
    registry = BrowserTaskPackRegistry(
        [
            BrowserTaskPack(
                pack_id="payment_lookup",
                version="1.0.0",
                display_name="Payment lookup",
                allowed_domains=["portal.example.com"],
                start_url="https://portal.example.com/payments",
                execution_policy=BrowserTaskPackExecutionPolicy(
                    retry_policy=BrowserTaskPackRetryPolicy(max_attempts=2)
                ),
            )
        ]
    )
    service = BrowserTaskService(InMemoryBrowserTaskStore(), task_pack_registry=registry)
    created = service.create_task(
        conversation_id="conv_worker_retry",
        organization_id="org_1",
        title="Lookup payment",
        task_pack_id="payment_lookup",
    )
    first_claim = service.claim_next_task(worker_id="worker_1", organization_id="org_1")
    assert first_claim is not None

    retry = service.apply_worker_result(
        worker_id="worker_1",
        organization_id="org_1",
        result=BrowserWorkerResult(
            task_id=created.task.task_id,
            success=False,
            error=BrowserWorkerError(kind="network", message="Network timeout.", retryable=True),
        ),
    )
    assert retry.task.state == "queued"
    assert retry.task.lease_owner is None
    assert retry.task.error == "Network timeout."
    assert retry.recent_events[-1].event_type == "browser.worker_retry"

    second_claim = service.claim_next_task(worker_id="worker_2", organization_id="org_1")
    assert second_claim is not None
    failed = service.apply_worker_result(
        worker_id="worker_2",
        organization_id="org_1",
        result=BrowserWorkerResult(
            task_id=created.task.task_id,
            success=False,
            error=BrowserWorkerError(kind="network", message="Network timeout again.", retryable=True),
        ),
    )
    assert failed.task.state == "failed"
    assert failed.task.error == "Network timeout again."
    assert failed.recent_events[-1].event_type == "browser.failed"
