import pytest
from pydantic import ValidationError

from ruhu.browser_tasks import (
    BrowserTaskPack,
    BrowserTaskPackAccessPolicy,
    BrowserTaskPackApprovalPolicy,
    BrowserTaskPackExecutionPolicy,
    BrowserTaskPackRegistry,
    BrowserWorkerProgress,
    BrowserWorkerRequest,
    BrowserWorkerResult,
    builtin_browser_task_packs,
    is_url_allowed,
    load_browser_task_pack_file,
    load_browser_task_pack_registry,
)


def test_task_pack_normalizes_allowed_domains_and_allows_subdomains() -> None:
    pack = BrowserTaskPack(
        pack_id="vendor_status_lookup",
        version="1.0.0",
        display_name="Vendor status lookup",
        allowed_domains=["https://Portal.Example.com/", "portal.example.com"],
        start_url="https://app.portal.example.com/dashboard",
    )

    assert pack.allowed_domains == ["portal.example.com"]
    assert is_url_allowed("https://app.portal.example.com/tasks", pack.allowed_domains)
    assert not is_url_allowed("https://example.net/tasks", pack.allowed_domains)


def test_task_pack_rejects_unbounded_or_path_scoped_domains() -> None:
    with pytest.raises(ValidationError):
        BrowserTaskPack(
            pack_id="unsafe",
            version="1.0.0",
            display_name="Unsafe",
            allowed_domains=["*.example.com"],
        )

    with pytest.raises(ValidationError):
        BrowserTaskPack(
            pack_id="unsafe",
            version="1.0.0",
            display_name="Unsafe",
            allowed_domains=["https://example.com/login"],
        )


def test_task_pack_rejects_invalid_json_schemas() -> None:
    with pytest.raises(ValidationError):
        BrowserTaskPack(
            pack_id="bad_schema",
            version="1.0.0",
            display_name="Bad schema",
            allowed_domains=["example.com"],
            input_schema={"type": "object", "properties": {"status": {"type": "not-a-json-schema-type"}}},
        )


def test_task_pack_write_actions_require_change_confirmation() -> None:
    with pytest.raises(ValidationError):
        BrowserTaskPack(
            pack_id="submit_form",
            version="1.0.0",
            display_name="Submit form",
            allowed_domains=["example.com"],
            performs_write=True,
            approval_policy=BrowserTaskPackApprovalPolicy(
                approval_required=True,
                approval_kinds=["generic_access"],
            ),
        )

    pack = BrowserTaskPack(
        pack_id="submit_form",
        version="1.0.0",
        display_name="Submit form",
        allowed_domains=["example.com"],
        performs_write=True,
        approval_policy=BrowserTaskPackApprovalPolicy(
            approval_required=True,
            approval_kinds=["change_confirmation"],
        ),
    )

    assert pack.approval_policy.approval_kinds == ["change_confirmation"]


def test_task_pack_registry_rejects_duplicate_versions_and_selects_latest() -> None:
    v1 = BrowserTaskPack(
        pack_id="track_transfer",
        version="1.0.0",
        display_name="Track transfer",
        allowed_domains=["bank.example"],
    )
    v2 = BrowserTaskPack(
        pack_id="track_transfer",
        version="1.1.0",
        display_name="Track transfer",
        allowed_domains=["bank.example"],
    )
    registry = BrowserTaskPackRegistry([v1, v2])

    assert registry.get("track_transfer") == v2
    assert registry.get("track_transfer", "1.0.0") == v1
    with pytest.raises(ValueError):
        registry.register(v1)


def test_task_pack_access_policy_restricts_global_org_and_agent_packs() -> None:
    policy = BrowserTaskPackAccessPolicy(
        allowed_pack_ids={"invoice_lookup", "ticket_status_lookup"},
        org_allowed_pack_ids={
            "org_1": {"invoice_lookup", "ticket_status_lookup"},
            "org_2": {"invoice_lookup"},
        },
        agent_allowed_pack_ids={("org_1", "agent_1"): {"ticket_status_lookup"}},
    )

    policy.assert_allowed(pack_id="invoice_lookup", organization_id="org_1", agent_id=None)

    with pytest.raises(ValueError, match="not enabled: order_status_lookup"):
        policy.assert_allowed(pack_id="order_status_lookup", organization_id="org_1", agent_id=None)

    with pytest.raises(ValueError, match="organization"):
        policy.assert_allowed(pack_id="ticket_status_lookup", organization_id="org_2", agent_id=None)

    with pytest.raises(ValueError, match="agent"):
        policy.assert_allowed(pack_id="invoice_lookup", organization_id="org_1", agent_id="agent_1")


def test_builtin_browser_task_packs_are_bounded_and_session_scoped() -> None:
    packs = builtin_browser_task_packs()
    by_id = {pack.pack_id: pack for pack in packs}

    assert {"invoice_lookup", "order_status_lookup", "ticket_status_lookup", "appointment_reschedule"} <= set(by_id)
    assert all(pack.allowed_domains for pack in packs)
    assert all(credential.kind == "session" for pack in packs for credential in pack.credentials)
    assert by_id["appointment_reschedule"].performs_write is True
    assert by_id["appointment_reschedule"].approval_policy.approval_kinds == ["change_confirmation"]


def test_load_browser_task_pack_registry_includes_builtin_packs_by_default() -> None:
    registry = load_browser_task_pack_registry(None)

    assert registry.get("invoice_lookup").display_name == "Invoice lookup"
    assert registry.get("appointment_reschedule").approval_policy.approval_required is True


def test_worker_request_is_built_from_pack_policy_and_checks_start_url() -> None:
    pack = BrowserTaskPack(
        pack_id="download_statement",
        version="1.0.0",
        display_name="Download statement",
        allowed_domains=["bank.example"],
        start_url="https://bank.example/accounts",
        execution_policy=BrowserTaskPackExecutionPolicy(
            max_execution_seconds=90,
            max_steps=25,
            allow_downloads=True,
        ),
    )

    request = BrowserWorkerRequest.from_task_pack(
        request_id="req_1",
        task_id="task_1",
        organization_id="org_1",
        conversation_id="conv_1",
        pack=pack,
        title="Download the current account statement",
    )

    assert request.start_url == "https://bank.example/accounts"
    assert request.policy.max_execution_seconds == 90
    assert request.policy.max_steps == 25
    assert request.policy.allow_downloads is True

    with pytest.raises(ValueError):
        BrowserWorkerRequest.from_task_pack(
            request_id="req_2",
            task_id="task_2",
            conversation_id="conv_1",
            pack=pack,
            title="Open unrelated site",
            start_url="https://evil.example/accounts",
        )

    with pytest.raises(ValidationError):
        BrowserWorkerRequest(
            request_id="req_3",
            task_id="task_3",
            conversation_id="conv_1",
            pack_id=pack.pack_id,
            pack_version=pack.version,
            title="Bypass policy",
            start_url="https://evil.example/accounts",
            policy=request.policy,
        )


def test_worker_progress_requires_monotonic_sequence_value() -> None:
    with pytest.raises(ValidationError):
        BrowserWorkerProgress(
            task_id="task_1",
            event_sequence=0,
            phase="navigating",
            message="Opening the target site.",
        )

    progress = BrowserWorkerProgress(
        task_id="task_1",
        event_sequence=1,
        phase="navigating",
        message="Opening the target site.",
    )

    assert progress.event_sequence == 1


def test_worker_result_requires_error_for_failure() -> None:
    with pytest.raises(ValidationError):
        BrowserWorkerResult(task_id="task_1", success=False)


def test_load_browser_task_pack_file_accepts_single_json_pack(tmp_path) -> None:
    path = tmp_path / "lookup-order.json"
    path.write_text(
        """
        {
          "pack_id": "lookup_order",
          "version": "1.0.0",
          "display_name": "Lookup order",
          "allowed_domains": ["merchant.example"],
          "start_url": "https://merchant.example/orders"
        }
        """,
        encoding="utf-8",
    )

    packs = load_browser_task_pack_file(path)

    assert len(packs) == 1
    assert packs[0].pack_id == "lookup_order"
    assert packs[0].allowed_domains == ["merchant.example"]


def test_load_browser_task_pack_registry_accepts_directory_and_rejects_duplicates(tmp_path) -> None:
    first = tmp_path / "lookup-order.yaml"
    first.write_text(
        """
        browser_task_packs:
          - pack_id: lookup_order
            version: 1.0.0
            display_name: Lookup order
            allowed_domains:
              - merchant.example
            start_url: https://merchant.example/orders
        """,
        encoding="utf-8",
    )
    registry = load_browser_task_pack_registry(tmp_path)
    assert registry.get("lookup_order").start_url == "https://merchant.example/orders"

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        """
        {
          "pack_id": "lookup_order",
          "version": "1.0.0",
          "display_name": "Duplicate lookup order",
          "allowed_domains": ["merchant.example"]
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already registered"):
        load_browser_task_pack_registry(tmp_path)


def test_load_browser_task_pack_registry_rejects_unsafe_pack_at_startup(tmp_path) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(
        """
        {
          "pack_id": "unsafe",
          "version": "1.0.0",
          "display_name": "Unsafe",
          "allowed_domains": ["*.example.com"]
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_browser_task_pack_registry(path)
