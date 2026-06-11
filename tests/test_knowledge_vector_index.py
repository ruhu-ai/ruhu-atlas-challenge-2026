from __future__ import annotations

import os

import pytest

from ruhu.knowledge import (
    HashingEmbeddingProvider,
    IndexedKnowledgeChunk,
    WeaviateKnowledgeVectorIndex,
    run_weaviate_smoke_check,
)


class _FakeTenantCollection:
    def __init__(self) -> None:
        self.data = self
        self.insert_attempts = 0

    def insert(self, **kwargs) -> None:
        self.insert_attempts += 1
        if self.insert_attempts == 1:
            raise RuntimeError("temporary insert failure")

    def delete_many(self, **kwargs):
        class _Result:
            successful = 1

        return _Result()


class _FakeCollectionHandle:
    def __init__(self, tenant_collection: _FakeTenantCollection) -> None:
        self._tenant_collection = tenant_collection

    def with_tenant(self, organization_id: str) -> _FakeTenantCollection:
        assert organization_id == "org-index"
        return self._tenant_collection


class _FakeCollections:
    def __init__(self, tenant_collection: _FakeTenantCollection) -> None:
        self._tenant_collection = tenant_collection

    def exists(self, collection_name: str) -> bool:
        assert collection_name == "KnowledgeChunk"
        return True

    def create(self, **kwargs) -> None:  # pragma: no cover - should not be called when exists() is True
        raise AssertionError("create should not be called")

    def get(self, collection_name: str) -> _FakeCollectionHandle:
        assert collection_name == "KnowledgeChunk"
        return _FakeCollectionHandle(self._tenant_collection)


class _FakeClient:
    def __init__(self, tenant_collection: _FakeTenantCollection) -> None:
        self.collections = _FakeCollections(tenant_collection)

    def is_ready(self) -> bool:
        return True

    def close(self) -> None:
        return None


def test_weaviate_index_retries_transient_insert_failures_and_records_success() -> None:
    tenant_collection = _FakeTenantCollection()
    index = WeaviateKnowledgeVectorIndex(max_retries=1, sleep_fn=lambda _: None)
    index._client = _FakeClient(tenant_collection)

    refs = index.upsert_chunks(
        [
            IndexedKnowledgeChunk(
                chunk_id="chunk-1",
                document_id="doc-1",
                organization_id="org-index",
                model_key="hashing-v1",
                title="Workflow guide",
                summary="Guide",
                category="ops",
                tags=["workflow"],
                content="workflow automation",
                search_text="workflow automation",
                vector=[1.0, 0.0],
            )
        ]
    )

    assert refs["chunk-1"]
    assert tenant_collection.insert_attempts == 2
    diagnostics = index.diagnostics()
    assert diagnostics is not None
    assert diagnostics.last_error is None
    assert diagnostics.last_operation == "upsert_chunks"
    assert diagnostics.last_successful_write_at is not None


@pytest.mark.skipif(
    os.getenv("RUHU_RUN_WEAVIATE_SMOKE") != "1",
    reason="set RUHU_RUN_WEAVIATE_SMOKE=1 to run the live Weaviate smoke check",
)
def test_live_weaviate_smoke_check() -> None:
    provider = HashingEmbeddingProvider()
    index = WeaviateKnowledgeVectorIndex(
        host=os.getenv("RUHU_KNOWLEDGE_WEAVIATE_HOST", "localhost"),
        port=int(os.getenv("RUHU_KNOWLEDGE_WEAVIATE_PORT", "8080")),
        grpc_port=int(os.getenv("RUHU_KNOWLEDGE_WEAVIATE_GRPC_PORT", "50051")),
        collection_name=os.getenv("RUHU_KNOWLEDGE_WEAVIATE_COLLECTION", "KnowledgeChunk"),
    )

    payload = run_weaviate_smoke_check(
        index=index,
        organization_id=os.getenv("RUHU_KNOWLEDGE_SMOKE_ORGANIZATION_ID", "smoke-test"),
        model_key=provider.model_key,
        query="workflow automation smoke check",
        vector=provider.embed_query("workflow automation smoke check"),
    )

    index.close()
    assert payload["ok"] is True
