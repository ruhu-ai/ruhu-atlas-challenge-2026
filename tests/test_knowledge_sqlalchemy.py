from __future__ import annotations

from pathlib import Path

from ruhu.db import build_session_factory
from ruhu.knowledge import InMemoryKnowledgeVectorIndex, KnowledgeService, SQLAlchemyKnowledgeStore


def test_sqlalchemy_knowledge_store_round_trips_seeded_and_file_documents(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyKnowledgeStore(session_factory)
    service = KnowledgeService(store, vector_index=InMemoryKnowledgeVectorIndex())

    seeded = service.seed_documents(
        organization_id="org-sql",
        path=Path(__file__).resolve().parent / "_fixtures" / "data" / "knowledge" / "sales.json",
    )
    assert [item.title for item in seeded] == [
        "Pricing and plans",
        "Ruhu product overview",
        "Workflow builder and integrations",
    ]

    manual = service.ingest_file(
        organization_id="org-sql",
        filename="integrations.json",
        file_bytes=b'{"channels":["voice","whatsapp"],"mode":"shared-state"}',
        status="published",
        category="integrations",
        tags=["shared-state", "voice"],
    )
    assert service.get_document(organization_id="org-sql", document_id=manual.document_id).media_type == "application/json"

    hits = service.search(organization_id="org-sql", query="pricing and plans", limit=2)
    assert hits[0].title == "Pricing and plans"

    lookup = service.lookup(organization_id="org-sql", query="shared state voice integrations")
    assert lookup.sources[0].document_id == manual.document_id

    chunks = service.list_chunks(organization_id="org-sql", document_id=manual.document_id)
    assert len(chunks) >= 1

    embeddings = service.index_document_embeddings(
        organization_id="org-sql",
        document_id=manual.document_id,
    )
    assert embeddings
    assert embeddings[0].sync_status == "indexed"
    assert service.list_chunk_embeddings(
        organization_id="org-sql",
        document_id=manual.document_id,
    )[0].model_key == "hashing-v1"
