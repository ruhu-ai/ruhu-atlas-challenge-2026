from __future__ import annotations

from datetime import datetime, timezone

from ruhu.db import build_session_factory
from ruhu.tools.store import InMemoryToolInvocationStore, SQLAlchemyToolInvocationStore
from ruhu.tools.types import ToolCaller, ToolInvocation


def _invocation(
    invocation_id: str = "inv-1",
    *,
    conversation_id: str = "conv-1",
    tenant_id: str | None = None,
) -> ToolInvocation:
    now = datetime.now(timezone.utc)
    return ToolInvocation(
        invocation_id=invocation_id,
        tool_ref="knowledge.lookup",
        executor_kind="builtin",
        status="completed",
        caller=ToolCaller(
            channel="web_chat",
            conversation_id=conversation_id,
            tenant_id=tenant_id,
        ),
        args={"query": "pricing"},
        output={"answer": "Pricing starts at ..."},
        created_at=now,
        updated_at=now,
    )


def test_in_memory_store_round_trip() -> None:
    store = InMemoryToolInvocationStore()
    invocation = _invocation(tenant_id="org-1")

    store.save(invocation)

    loaded = store.load(invocation.invocation_id, organization_id="org-1")
    assert loaded is not None
    assert loaded.output["answer"] == "Pricing starts at ..."
    assert store.load(invocation.invocation_id, organization_id="org-2") is None


def test_in_memory_store_scopes_by_organization() -> None:
    store = InMemoryToolInvocationStore()
    store.save(_invocation("inv-org-1", tenant_id="org-1"))
    store.save(_invocation("inv-org-2", tenant_id="org-2"))

    assert [item.invocation_id for item in store.all(organization_id="org-1")] == ["inv-org-1"]
    assert [item.invocation_id for item in store.by_conversation("conv-1", organization_id="org-2")] == ["inv-org-2"]


def test_sqlalchemy_store_round_trip(postgres_database_url_factory) -> None:
    store = SQLAlchemyToolInvocationStore(
        build_session_factory(postgres_database_url_factory())
    )
    invocation = _invocation("inv-2", tenant_id="org-1")

    store.save(invocation)

    loaded = store.load("inv-2", organization_id="org-1")
    assert loaded is not None
    assert loaded.caller.conversation_id == "conv-1"
    assert store.by_conversation("conv-1", organization_id="org-1")[0].invocation_id == "inv-2"
    assert store.load("inv-2", organization_id="org-2") is None


def test_sqlalchemy_store_scopes_by_organization(postgres_database_url_factory) -> None:
    store = SQLAlchemyToolInvocationStore(
        build_session_factory(postgres_database_url_factory())
    )
    store.save(_invocation("inv-org-1", tenant_id="org-1"))
    store.save(_invocation("inv-org-2", tenant_id="org-2"))

    assert [item.invocation_id for item in store.all(organization_id="org-1")] == ["inv-org-1"]
    assert [item.invocation_id for item in store.by_conversation("conv-1", organization_id="org-2")] == ["inv-org-2"]
