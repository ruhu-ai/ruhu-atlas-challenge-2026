from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from ruhu.attachments.runtime import AttachmentRuntime
from ruhu.attachments.service import AttachmentService
from ruhu.attachments.store import InMemoryAttachmentStore
from ruhu.browser_tasks.service import BrowserTaskService
from ruhu.browser_tasks.store import InMemoryBrowserTaskStore
from ruhu.browser_tasks.task_packs import (
    BrowserCredentialRequirement,
    BrowserTaskPack,
    BrowserTaskPackApprovalPolicy,
    BrowserTaskPackRegistry,
)
from ruhu.public_widget import widget_embed_script
from ruhu.schemas import ConversationState
from ruhu.schemas import PendingActionState, RepairContext
from ruhu.widget_projection_api import install_widget_projection_router


def _conversation(conversation_id: str) -> ConversationState:
    return ConversationState(
        conversation_id=conversation_id,
        organization_id=None,
        agent_id="sales_agent",
        agent_version_id="version_1",
        mode="live",
        step_id="discover",
        updated_at=datetime.now(timezone.utc),
    )


def test_widget_projection_routes_support_attachments_browser_tasks_and_artifacts() -> None:
    async def run() -> None:
        conversation = _conversation("conv_widget_1")
        conversation.control_state.pending_action = PendingActionState(
            action_id="pending-1",
            action_type="booking_lookup",
            status="running",
            tool_ref="calendar.lookup",
            action_label="Checking calendar",
        )
        conversation.control_state.active_repair = RepairContext(
            repair_kind="interrupt_acknowledged",
            summary="Stopped the previous booking attempt.",
        )
        attachment_runtime = AttachmentRuntime(
            service=AttachmentService(InMemoryAttachmentStore(), max_file_bytes=1024 * 1024),
            max_workers=1,
        )
        browser_task_service = BrowserTaskService(InMemoryBrowserTaskStore())
        class _FakeEvents:
            def __init__(self) -> None:
                self._items: list[SimpleNamespace] = []

            def append(self, **kwargs):
                item = SimpleNamespace(
                    family=kwargs["family"],
                    name=kwargs["name"],
                    payload=kwargs.get("payload", {}),
                    created_at=datetime.now(timezone.utc),
                )
                self._items.append(item)
                return SimpleNamespace(event_id=f"evt-{len(self._items)}")

            def replay(self, *, conversation_id: str, after_sequence=None, after_event_id=None):
                return list(self._items)

        class _FakeSessions:
            def list_by_conversation(self, conversation_id: str):
                if conversation_id != conversation.conversation_id:
                    return []
                now = datetime.now(timezone.utc)
                return [
                    SimpleNamespace(
                        surface="voice",
                        status="active",
                        transport_metadata={
                            "voice_interaction_policy": {
                                "step_id": "discover",
                                "endpointing_ms": 650,
                                "soft_timeout_ms": 800,
                                "turn_eagerness": "normal",
                                "interruptibility_policy": "interruptible_except_policy",
                            }
                        },
                        last_seen_at=now,
                        updated_at=now,
                        created_at=now,
                    )
                ]

        fake_control_plane = SimpleNamespace(events=_FakeEvents(), sessions=_FakeSessions())
        fake_control_plane.events.append(
            conversation_id=conversation.conversation_id,
            organization_id=conversation.organization_id,
            family="voice",
            name="assistant_speaking_started",
            payload={"channel": "browser", "observed_at": datetime.now(timezone.utc)},
        )
        app = FastAPI()
        install_widget_projection_router(
            app,
            attachment_runtime=attachment_runtime,
            browser_task_service=browser_task_service,
            realtime_control_plane=fake_control_plane,
            load_conversation=lambda conversation_id: (
                conversation if conversation_id == conversation.conversation_id else None
            ),
            list_pending_tool_invocations=lambda conversation_id: [],
            authorize_conversation_request=lambda _request, _conversation: None,
        )

        script = widget_embed_script()
        assert "EventSource" in script
        assert "uploadAttachmentWithProgress" in script
        assert "/browser-tasks" in script
        assert "public_widget_download_url" in script
        assert "/events" in script
        assert "interaction_status" in script
        assert "voiceActivityLabel" in script

        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                upload = await client.post(
                    f"/public/widget/sessions/{conversation.conversation_id}/attachments",
                    params={"filename": "../notes.txt"},
                    content=b"Attachment evidence for the widget projection.",
                    headers={"Content-Type": "text/plain"},
                )
                assert upload.status_code == 200
                attachment_payload = upload.json()
                attachment_id = attachment_payload["attachment"]["attachment_id"]

                attachment_runtime.service.process_attachment(attachment_id=attachment_id, organization_id=None)

                created_task = browser_task_service.create_task(
                    conversation_id=conversation.conversation_id,
                    organization_id=None,
                    title="Check billing portal",
                    summary="Requires approval before secure browser access.",
                    credential_refs={"billing_portal": "connection:conn_secret_123456"},
                    requires_approval=True,
                    approval_prompt="Approve secure billing lookup.",
                )
                approval_id = created_task.approval.approval_id
                task_id = created_task.task.task_id

                approved = await client.post(
                    f"/public/widget/sessions/{conversation.conversation_id}/browser-tasks/{task_id}/approvals/{approval_id}/approve",
                    json={},
                )
                assert approved.status_code == 200
                approved_payload = approved.json()
                assert approved_payload["state"] == "queued"
                assert "credential_refs" not in approved_payload
                assert "lease_owner" not in approved_payload
                assert "recent_events" not in approved_payload

                artifact = attachment_runtime.service.create_artifact(
                    conversation_id=conversation.conversation_id,
                    organization_id=None,
                    filename="result.txt",
                    content_type="text/plain",
                    content_bytes=b"artifact bytes",
                    kind="result_bundle",
                    task_id=task_id,
                )
                browser_task_service.attach_artifact(
                    task_id=task_id,
                    organization_id=None,
                    artifact={
                        "artifact_id": artifact.artifact_id,
                        "filename": artifact.filename,
                        "public_widget_download_url": (
                            f"/public/widget/sessions/{conversation.conversation_id}/artifacts/{artifact.artifact_id}/download"
                        ),
                    },
                )
                browser_task_service.complete_task(
                    task_id=task_id,
                    organization_id=None,
                    result={
                        "summary": "Invoice located.",
                        "artifacts": [
                            {
                                "artifact_id": artifact.artifact_id,
                                "filename": artifact.filename,
                                "public_widget_download_url": (
                                    f"/public/widget/sessions/{conversation.conversation_id}/artifacts/{artifact.artifact_id}/download"
                                ),
                            }
                        ],
                    },
                )

                attachments = await client.get(f"/public/widget/sessions/{conversation.conversation_id}/attachments")
                assert attachments.status_code == 200
                assert attachments.json()[0]["attachment"]["filename"] == "notes.txt"
                assert attachments.json()[0]["attachment"]["extraction_status"] == "ready"

                browser_tasks = await client.get(f"/public/widget/sessions/{conversation.conversation_id}/browser-tasks")
                assert browser_tasks.status_code == 200
                browser_task_payload = browser_tasks.json()[0]
                assert browser_task_payload["task_id"] == task_id
                assert browser_task_payload["state"] == "completed"
                assert browser_task_payload["latest_progress"] == "Browser task completed."
                assert browser_task_payload["artifacts"][0]["artifact_id"] == artifact.artifact_id
                assert "credential_refs" not in browser_task_payload
                assert "result" not in browser_task_payload
                assert "recent_events" not in browser_task_payload

                projection = await client.get(f"/public/widget/sessions/{conversation.conversation_id}/projection")
                assert projection.status_code == 200
                projection_payload = projection.json()
                assert projection_payload["attachments"]
                assert projection_payload["browser_tasks"][0]["artifacts"]
                assert "credential_refs" not in projection_payload["browser_tasks"][0]
                assert projection_payload["snapshot_id"]
                assert projection_payload["interaction_status"]
                assert projection_payload["interaction_status"][0]["item_type"] == "activity"
                assert projection_payload["voice_activity"]["name"] == "assistant_speaking_started"
                assert isinstance(projection_payload["voice_activity"]["payload"]["observed_at"], str)
                assert projection_payload["voice_interaction_policy"]["endpointing_ms"] == 650
                assert (
                    projection_payload["voice_interaction_policy"]["interruptibility_policy"]
                    == "interruptible_except_policy"
                )

                download = await client.get(
                    f"/public/widget/sessions/{conversation.conversation_id}/attachments/{attachment_id}/download"
                )
                assert download.status_code == 200
                assert download.headers["x-ruhu-attachment-id"] == attachment_id
                assert download.content == b"Attachment evidence for the widget projection."

                artifact_download = await client.get(
                    f"/public/widget/sessions/{conversation.conversation_id}/artifacts/{artifact.artifact_id}/download"
                )
                assert artifact_download.status_code == 200
                assert artifact_download.headers["x-ruhu-artifact-id"] == artifact.artifact_id
                assert artifact_download.content == b"artifact bytes"
        finally:
            attachment_runtime.shutdown()

    asyncio.run(run())


def test_widget_approval_route_rejects_mismatched_conversation_before_mutating_task() -> None:
    async def run() -> None:
        primary = _conversation("conv_widget_primary")
        secondary = _conversation("conv_widget_secondary")
        browser_task_service = BrowserTaskService(InMemoryBrowserTaskStore())
        app = FastAPI()
        install_widget_projection_router(
            app,
            attachment_runtime=None,
            browser_task_service=browser_task_service,
            realtime_control_plane=None,
            load_conversation=lambda conversation_id: {
                primary.conversation_id: primary,
                secondary.conversation_id: secondary,
            }.get(conversation_id),
            list_pending_tool_invocations=lambda conversation_id: [],
            authorize_conversation_request=lambda _request, _conversation: None,
        )

        created_task = browser_task_service.create_task(
            conversation_id=secondary.conversation_id,
            organization_id=None,
            title="Check billing portal",
            summary="Requires approval before secure browser access.",
            requires_approval=True,
            approval_prompt="Approve secure billing lookup.",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            denied = await client.post(
                (
                    f"/public/widget/sessions/{primary.conversation_id}/browser-tasks/"
                    f"{created_task.task.task_id}/approvals/{created_task.approval.approval_id}/approve"
                ),
                json={},
            )
            assert denied.status_code == 404

        unchanged = browser_task_service.get_snapshot(created_task.task.task_id, organization_id=None)
        assert unchanged.task.conversation_id == secondary.conversation_id
        assert unchanged.task.state == "awaiting_approval"
        assert unchanged.task.approval_state == "pending"
        assert unchanged.approval is not None
        assert unchanged.approval.state == "pending"

    asyncio.run(run())


def test_widget_browser_task_projection_shows_safe_approval_context_only() -> None:
    async def run() -> None:
        conversation = _conversation("conv_widget_browser_security")
        registry = BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="billing_portal_lookup",
                    version="1.0.0",
                    display_name="Billing portal lookup",
                    allowed_domains=["billing.example.com"],
                    start_url="https://billing.example.com/accounts",
                    credentials=[
                        BrowserCredentialRequirement(
                            name="portal_session",
                            kind="session",
                            provider="billing",
                            auth_type="cookie",
                        )
                    ],
                    approval_policy=BrowserTaskPackApprovalPolicy(
                        approval_required=True,
                        approval_kinds=["change_confirmation"],
                        approval_ttl_seconds=300,
                    ),
                    performs_write=True,
                )
            ]
        )
        browser_task_service = BrowserTaskService(
            InMemoryBrowserTaskStore(),
            task_pack_registry=registry,
        )
        app = FastAPI()
        install_widget_projection_router(
            app,
            attachment_runtime=None,
            browser_task_service=browser_task_service,
            realtime_control_plane=None,
            load_conversation=lambda conversation_id: (
                conversation if conversation_id == conversation.conversation_id else None
            ),
            list_pending_tool_invocations=lambda conversation_id: [],
            authorize_conversation_request=lambda _request, _conversation: None,
        )
        created_task = browser_task_service.create_task(
            conversation_id=conversation.conversation_id,
            organization_id=None,
            title="Check billing portal",
            task_pack_id="billing_portal_lookup",
            credential_refs={
                "portal_session": "connection:conn_super_secret_customer_session_1234567890"
            },
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            listed = await client.get(
                f"/public/widget/sessions/{conversation.conversation_id}/browser-tasks"
            )
            assert listed.status_code == 200
            payload = listed.json()[0]
            encoded = jsonable_for_assertion(payload)
            assert "conn_super_secret_customer_session_1234567890" not in encoded
            assert "credential_refs" not in encoded
            assert "lease_owner" not in encoded
            assert "result" not in encoded
            assert "recent_events" not in encoded
            assert payload["task_pack_label"] == "Billing portal lookup"
            assert payload["domain_label"] == "billing.example.com"
            assert payload["approval"]["approval_kind"] == "change_confirmation"
            assert payload["approval"]["performs_write"] is True
            assert payload["approval"]["credential_labels"] == [
                "portal_session: connection:conn_s...7890"
            ]
            assert payload["approval"]["expires_at"]

            denied = await client.post(
                (
                    f"/public/widget/sessions/{conversation.conversation_id}/browser-tasks/"
                    f"{created_task.task.task_id}/approvals/{created_task.approval.approval_id}/deny"
                ),
                json={"reason": "customer declined"},
            )
            assert denied.status_code == 200
            assert denied.json()["state"] == "cancelled"
            assert denied.json()["approval_state"] == "denied"

    asyncio.run(run())


def jsonable_for_assertion(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True)


def test_widget_attachment_upload_rejects_payloads_above_runtime_limit() -> None:
    async def run() -> None:
        conversation = _conversation("conv_widget_limit")
        attachment_runtime = AttachmentRuntime(
            service=AttachmentService(InMemoryAttachmentStore(), max_file_bytes=8),
            max_workers=1,
        )
        app = FastAPI()
        install_widget_projection_router(
            app,
            attachment_runtime=attachment_runtime,
            browser_task_service=None,
            realtime_control_plane=None,
            load_conversation=lambda conversation_id: (
                conversation if conversation_id == conversation.conversation_id else None
            ),
            list_pending_tool_invocations=lambda conversation_id: [],
            authorize_conversation_request=lambda _request, _conversation: None,
        )

        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    f"/public/widget/sessions/{conversation.conversation_id}/attachments",
                    params={"filename": "notes.txt"},
                    content=b"123456789",
                    headers={"Content-Type": "text/plain"},
                )
                assert response.status_code == 413
                assert response.json()["detail"] == "attachment exceeds limit of 8 bytes"
                assert (
                    attachment_runtime.service.list_conversation_attachments(
                        conversation_id=conversation.conversation_id,
                        organization_id=None,
                    )
                    == []
                )
        finally:
            attachment_runtime.shutdown()

    asyncio.run(run())


def test_widget_projection_snapshot_reports_degraded_components_when_loaders_raise() -> None:
    async def run() -> None:
        conversation = _conversation("conv_widget_degraded")

        class _ExplodingAttachmentService:
            def list_conversation_attachments(self, *_, **__):
                raise RuntimeError("attachment backend offline")

        class _ExplodingBrowserTasks:
            def list_conversation_tasks(self, *_, **__):
                raise RuntimeError("browser tasks offline")

        def _pending_raises(_conversation_id: str):
            raise RuntimeError("pending lookup offline")

        attachment_runtime = SimpleNamespace(service=_ExplodingAttachmentService())
        app = FastAPI()
        install_widget_projection_router(
            app,
            attachment_runtime=attachment_runtime,
            browser_task_service=_ExplodingBrowserTasks(),
            realtime_control_plane=None,
            load_conversation=lambda conversation_id: (
                conversation if conversation_id == conversation.conversation_id else None
            ),
            list_pending_tool_invocations=_pending_raises,
            authorize_conversation_request=lambda _request, _conversation: None,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            projection = await client.get(
                f"/public/widget/sessions/{conversation.conversation_id}/projection"
            )
        assert projection.status_code == 200
        payload = projection.json()
        assert payload["attachments"] == []
        assert payload["browser_tasks"] == []
        assert payload["pending_tool_invocations"] == []
        assert sorted(payload["degraded_components"]) == [
            "attachments",
            "browser_tasks",
            "pending_tool_invocations",
        ]

    asyncio.run(run())
