from __future__ import annotations

import asyncio
from pathlib import Path
import time

import httpx
import ruhu.knowledge_api as knowledge_api

from ruhu.api import create_app
from ruhu.composition import build_minimal_runtime
from ruhu.knowledge import (
    InMemoryKnowledgeStore,
    InMemoryKnowledgeVectorIndex,
    KnowledgeRuntime,
    KnowledgeService,
)
from ruhu.kernel import ConversationKernel
from ruhu.registry import FileAgentRegistry
from ruhu.tools.production import ProductionToolBackend


def _build_knowledge_app() -> tuple[object, KnowledgeRuntime]:
    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    knowledge_runtime = KnowledgeRuntime(
        service=KnowledgeService(InMemoryKnowledgeStore()),
        default_organization_id="public",
        seed_path=Path(__file__).resolve().parent / "_fixtures" / "data" / "knowledge" / "sales.json",
        auto_seed=True,
        auto_reindex_on_startup=False,
    )
    knowledge_runtime.startup()
    app = create_app(
        build_minimal_runtime(
            kernel=ConversationKernel(),
            agent_registry=FileAgentRegistry(agent_root_path),
            knowledge_runtime=knowledge_runtime,
            tool_backend=ProductionToolBackend(
                knowledge_service=knowledge_runtime.service,
                default_knowledge_organization_id=knowledge_runtime.default_organization_id,
            ),
        )
    )
    return app, knowledge_runtime


def test_create_app_exposes_read_only_knowledge_routes() -> None:
    async def run() -> None:
        app, knowledge_runtime = _build_knowledge_app()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            documents = await client.get("/knowledge/documents")
            assert documents.status_code == 200
            assert len(documents.json()) == 3

            search = await client.get(
                "/knowledge/search",
                params={"query": "How does workflow automation work?"},
            )
            assert search.status_code == 200
            payload = search.json()
            assert payload["sources"][0]["title"] == "Workflow builder and integrations"

            status = await client.get("/knowledge/status")
            assert status.status_code == 200
            status_payload = status.json()
            assert status_payload["organization"]["document_count"] == 3
            assert status_payload["vector_index"] is None
            assert status_payload["guardrails"]["max_file_bytes"] == 5 * 1024 * 1024
            assert status_payload["index_health"]["chunk_count"] >= 1
            assert status_payload["index_health"]["lagging_chunk_count"] >= 1
            assert status_payload["index_health"]["failed_chunk_count"] == 0

        backend = app.state.tool_backend
        assert backend.knowledge_service is app.state.knowledge_runtime.service
        lookup = backend.knowledge_lookup("pricing plans", organization_id=None)
        assert lookup["sources"][0]["title"] == "Pricing and plans"
        assert lookup["facts"]["last_knowledge_hit_count"] >= 1
        assert isinstance(lookup["context_block"], str) and "Question: pricing plans" in lookup["context_block"]
        assert lookup["retrieval_mode"] == "standard"

        deep_lookup = backend.knowledge_lookup(
            "How do workflows and integrations work for support teams?",
            organization_id=None,
            mode="deep",
        )
        assert deep_lookup["retrieval_mode"] == "deep"
        assert len(deep_lookup["retrieval_queries"]) >= 1
        assert isinstance(deep_lookup["retrieval_steps"], list)

        knowledge_runtime.shutdown()

    asyncio.run(run())


def test_create_app_supports_knowledge_document_upload() -> None:
    async def run() -> None:
        app, knowledge_runtime = _build_knowledge_app()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/knowledge/documents/upload",
                data={
                    "title": "Notes",
                    "status": "published",
                    "tags": '["sales","faq"]',
                },
                files={"file": ("notes.txt", b"hello multipart world", "text/plain")},
            )
            assert response.status_code == 201
            payload = response.json()
            assert payload["title"] == "Notes"
            assert payload["source_kind"] == "file"
            assert payload["source_ref"] == "notes.txt"
            assert payload["tags"] == ["sales", "faq"]

            stored = await client.get(f"/knowledge/documents/{payload['document_id']}")
            assert stored.status_code == 200
            assert "hello multipart world" in stored.json()["content"]

        knowledge_runtime.shutdown()

    asyncio.run(run())


def test_create_app_returns_503_for_upload_when_multipart_support_is_unavailable(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(knowledge_api, "_multipart_support_available", lambda: False)
        app, knowledge_runtime = _build_knowledge_app()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            documents = await client.get("/knowledge/documents")
            assert documents.status_code == 200

            response = await client.post("/knowledge/documents/upload")
            assert response.status_code == 503
            assert response.json()["detail"] == "knowledge upload requires python-multipart to be installed"

        knowledge_runtime.shutdown()

    asyncio.run(run())


def test_knowledge_runtime_tracks_background_reindex_jobs() -> None:
    runtime = KnowledgeRuntime(
        service=KnowledgeService(
            InMemoryKnowledgeStore(),
            vector_index=InMemoryKnowledgeVectorIndex(),
        ),
        default_organization_id="org-runtime",
        seed_path=Path(__file__).resolve().parent / "_fixtures" / "data" / "knowledge" / "sales.json",
        auto_seed=True,
        auto_reindex_on_startup=False,
    )
    runtime.startup()

    initial_status = runtime.status()
    assert initial_status.organization.document_count == 3
    assert initial_status.organization.embedding_count == 0

    job = runtime.schedule_organization_reindex()
    completed = None
    for _ in range(50):
        current = runtime.get_job(job.job_id)
        if current is not None and current.status in {"completed", "failed"}:
            completed = current
            break
        time.sleep(0.02)

    assert completed is not None
    assert completed.status == "completed"
    assert completed.indexed_embeddings > 0

    status = runtime.status()
    assert status.completed_jobs >= 1
    assert status.organization.embedding_count > 0
    assert status.organization.indexed_embedding_count > 0
    assert status.vector_index_available is True
    assert status.index_health.lagging_chunk_count == 0
    assert status.index_health.failed_chunk_count == 0
    assert status.index_health.last_successful_indexed_at is not None
    assert status.index_health.index_lag_seconds == 0.0

    runtime.shutdown()


def test_knowledge_runtime_can_restart_after_shutdown() -> None:
    runtime = KnowledgeRuntime(
        service=KnowledgeService(
            InMemoryKnowledgeStore(),
            vector_index=InMemoryKnowledgeVectorIndex(),
        ),
        default_organization_id="org-runtime",
        seed_path=Path(__file__).resolve().parent / "_fixtures" / "data" / "knowledge" / "sales.json",
        auto_seed=True,
        auto_reindex_on_startup=False,
    )
    runtime.startup()
    runtime.shutdown()

    runtime.startup()
    job = runtime.schedule_organization_reindex()
    completed = runtime.wait_for_job(job.job_id, timeout_seconds=5)

    assert completed.status == "completed"
    assert completed.indexed_embeddings > 0

    runtime.shutdown()
