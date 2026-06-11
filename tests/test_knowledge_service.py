from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from ruhu.knowledge import (
    HostedEmbeddingProvider,
    InMemoryKnowledgeVectorIndex,
    InMemoryKnowledgeStore,
    KnowledgeIngestError,
    KnowledgeService,
    detect_file_kind,
    extract_knowledge_file,
    supported_document_extensions,
)


def _build_docx_bytes(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body>"
                f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
                "</w:body>"
                "</w:document>"
            ),
        )
    return buffer.getvalue()


def test_extractors_support_structured_textual_and_docx_inputs() -> None:
    json_doc = extract_knowledge_file(filename="plans.json", file_bytes=b'{"plan":"starter","channels":["voice","chat"]}')
    csv_doc = extract_knowledge_file(filename="leads.csv", file_bytes=b"name,email\nAda,ada@example.com\n")
    html_doc = extract_knowledge_file(filename="page.html", file_bytes=b"<html><body><h1>FAQ</h1><p>Voice agents.</p></body></html>")
    xml_doc = extract_knowledge_file(filename="feed.xml", file_bytes=b"<root><item>Automation</item><item>Integrations</item></root>")
    docx_doc = extract_knowledge_file(filename="guide.docx", file_bytes=_build_docx_bytes("Workflow automation playbook"))

    assert detect_file_kind("guide.docx") == "docx"
    assert ".pdf" in supported_document_extensions()
    assert '"plan": "starter"' in json_doc.content
    assert "Ada, ada@example.com" in csv_doc.content
    assert "FAQ" in html_doc.content
    assert "Automation" in xml_doc.content
    assert "Workflow automation playbook" in docx_doc.content


def test_pdf_extraction_reports_clear_failure_without_valid_reader_input() -> None:
    with pytest.raises(ValueError):
        extract_knowledge_file(filename="notes.pdf", file_bytes=b"%PDF-1.4\nnot-a-real-pdf")


def test_knowledge_service_rejects_large_files_with_context() -> None:
    service = KnowledgeService(InMemoryKnowledgeStore(), max_file_bytes=8)

    with pytest.raises(KnowledgeIngestError) as exc_info:
        service.ingest_file(
            organization_id="org-guard",
            filename="manual.txt",
            file_bytes=b"this is larger than eight bytes",
            status="published",
        )

    assert exc_info.value.code == "file_too_large"
    assert exc_info.value.details["filename"] == "manual.txt"
    assert exc_info.value.details["max_file_bytes"] == 8


def test_hosted_embedding_provider_reads_openai_compatible_payload(monkeypatch) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {"index": 1, "embedding": [0.0, 3.0, 4.0, 0.0]},
                    {"index": 0, "embedding": [3.0, 4.0, 0.0, 0.0]},
                ]
            }

    def _fake_post(self, url, json, headers):
        assert url == "https://embeddings.example/embeddings"
        assert json["model"] == "demo-embedding"
        assert json["input"] == ["first", "second"]
        assert headers["Authorization"] == "Bearer secret-key"
        return _Response()

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    provider = HostedEmbeddingProvider(
        base_url="https://embeddings.example",
        model="demo-embedding",
        api_key="secret-key",
        dimensions=4,
    )

    vectors = provider.embed_documents(["first", "second"])

    assert len(vectors) == 2
    assert vectors[0] == pytest.approx([0.6, 0.8, 0.0, 0.0])
    assert vectors[1] == pytest.approx([0.0, 0.6, 0.8, 0.0])
    provider.close()


def test_knowledge_service_rejects_documents_that_expand_past_chunk_guardrail() -> None:
    service = KnowledgeService(
        InMemoryKnowledgeStore(),
        max_chunks_per_document=2,
        chunk_max_words=2,
        chunk_overlap_words=0,
    )

    with pytest.raises(KnowledgeIngestError) as exc_info:
        service.upsert_document(
            organization_id="org-chunks",
            title="Large handbook",
            content="one two three four five six",
            status="published",
        )

    assert exc_info.value.code == "too_many_chunks"
    assert exc_info.value.details["chunk_count"] == 3
    assert exc_info.value.details["max_chunks_per_document"] == 2


def test_knowledge_service_seeds_searches_and_scopes_documents() -> None:
    service = KnowledgeService(InMemoryKnowledgeStore())
    seeded = service.seed_documents(
        organization_id="org-1",
        path=Path(__file__).resolve().parent / "_fixtures" / "data" / "knowledge" / "sales.json",
    )

    duplicate_seed = service.seed_documents(
        organization_id="org-1",
        path=Path(__file__).resolve().parent / "_fixtures" / "data" / "knowledge" / "sales.json",
    )

    assert len(seeded) == 3
    assert [item.document_id for item in duplicate_seed] == [item.document_id for item in seeded]

    lookup = service.lookup(organization_id="org-1", query="How does workflow automation work?")
    assert lookup.sources[0].title == "Workflow builder and integrations"
    assert "visual workflow builder" in lookup.message

    other = service.upsert_document(
        organization_id="org-2",
        title="Private operating runbook",
        content="Internal escalation rules.",
        status="published",
    )
    assert service.lookup(organization_id="org-1", query="Internal escalation rules").hits == []
    assert service.get_document(organization_id="org-2", document_id=other.document_id).title == "Private operating runbook"


def test_lookup_message_skips_template_boilerplate_for_sample_docs() -> None:
    service = KnowledgeService(InMemoryKnowledgeStore())
    service.ingest_file(
        organization_id="org-sample",
        filename="sample.md",
        file_bytes=(
            b"# Ruhu - Knowledge Base for Sales Agents\n\n"
            b"Sample knowledge document for the Sales Agent template. Cover the "
            b"common product and pricing questions a prospect might ask. Edit "
            b"freely and replace the **PLACEHOLDER** sections before shipping.\n\n"
            b"---\n\n"
            b"## What is Ruhu?\n\n"
            b"Ruhu is a production-grade conversational AI runtime for building "
            b"workflow-driven agents across web chat, voice, and telephony."
        ),
        status="published",
    )

    lookup = service.lookup(organization_id="org-sample", query="Ruhu product overview")

    assert lookup.message == (
        "Ruhu is a production-grade conversational AI runtime for building "
        "workflow-driven agents across web chat, voice, and telephony."
    )


def test_knowledge_service_ingests_files_and_honors_publish_archive_states() -> None:
    service = KnowledgeService(InMemoryKnowledgeStore())
    document = service.ingest_file(
        organization_id="org-3",
        filename="faq.md",
        file_bytes=b"# FAQ\n\nRuhu supports phone, web chat, and WhatsApp channels.",
        status="draft",
        tags=["faq", "channels"],
    )

    assert service.lookup(organization_id="org-3", query="What channels are supported?").hits == []

    published = service.publish_document(organization_id="org-3", document_id=document.document_id)
    assert published.status == "published"
    assert service.lookup(organization_id="org-3", query="What channels are supported?").hits[0].document_id == document.document_id

    archived = service.archive_document(organization_id="org-3", document_id=document.document_id)
    assert archived.status == "archived"
    assert service.lookup(organization_id="org-3", query="What channels are supported?").hits == []


def test_knowledge_service_indexes_embeddings_and_supports_semantic_search() -> None:
    vector_index = InMemoryKnowledgeVectorIndex()
    service = KnowledgeService(InMemoryKnowledgeStore(), vector_index=vector_index)
    seeded = service.seed_documents(
        organization_id="org-4",
        path=Path(__file__).resolve().parent / "_fixtures" / "data" / "knowledge" / "sales.json",
    )

    indexed = service.index_organization_embeddings(organization_id="org-4")
    assert indexed
    assert all(item.sync_status == "indexed" for item in indexed)

    pricing_doc = next(item for item in seeded if item.title == "Pricing and plans")
    pricing_embeddings = service.list_chunk_embeddings(
        organization_id="org-4",
        document_id=pricing_doc.document_id,
    )
    assert pricing_embeddings
    assert all(item.model_key == "hashing-v1" for item in pricing_embeddings)

    semantic_hits = service.semantic_search(
        organization_id="org-4",
        query="What budget options exist for enterprise rollout?",
    )
    assert semantic_hits[0].title == "Pricing and plans"
    assert semantic_hits[0].retrieval_mode == "semantic"


def test_hybrid_lookup_falls_back_to_lexical_when_index_is_incomplete() -> None:
    service = KnowledgeService(InMemoryKnowledgeStore(), vector_index=InMemoryKnowledgeVectorIndex())
    indexed_doc = service.upsert_document(
        organization_id="org-5",
        title="Pricing guide",
        content="Ruhu pricing adapts to usage and enterprise rollout needs.",
        status="published",
        tags=["pricing"],
    )
    unindexed_doc = service.upsert_document(
        organization_id="org-5",
        title="Escalation protocol",
        content="Escalate billing disputes to the senior support queue.",
        status="published",
        tags=["support"],
    )
    service.index_document_embeddings(
        organization_id="org-5",
        document_id=indexed_doc.document_id,
    )

    lookup = service.lookup(
        organization_id="org-5",
        query="senior support queue",
    )
    assert lookup.sources[0].document_id == unindexed_doc.document_id
    assert lookup.hits[0].retrieval_mode == "lexical"
