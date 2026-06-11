"""Tests for agent-scoped knowledge lookup.

Verifies that when an agent has linked knowledge_base_ids, the knowledge.lookup
tool only searches those documents (not all org docs).
"""
from __future__ import annotations

from ruhu.tools.production import ProductionToolBackend


class _StubKnowledgeService:
    """Captures the document_ids passed to lookup() for assertions."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.captured_document_ids: list[str] | None | str = "not_called"
        self.captured_organization_id: str | None = None

    def lookup(self, *, organization_id: str, query: str, document_ids=None, limit: int = 3, mode: str = "standard"):
        self.captured_document_ids = list(document_ids) if document_ids is not None else None
        self.captured_organization_id = organization_id
        return self.result


class _StubLookupResult:
    message = "stub"
    sources: list = []
    hits: list = []
    context_block = ""
    lookup_mode = "standard"
    retrieval_queries: list = []
    retrieval_steps: list = []


class TestKnowledgeAgentScope:
    def test_lookup_without_resolver_uses_no_document_filter(self):
        service = _StubKnowledgeService(_StubLookupResult())
        backend = ProductionToolBackend(knowledge_service=service)  # type: ignore[arg-type]
        backend.knowledge_lookup("test query", organization_id="org-1", agent_id="agent-a")
        # No resolver configured → document_ids should be None (search all docs)
        assert service.captured_document_ids is None
        assert service.captured_organization_id == "org-1"

    def test_lookup_with_resolver_passes_document_ids(self):
        service = _StubKnowledgeService(_StubLookupResult())

        def resolver(agent_id, organization_id, state_id):
            if agent_id == "agent-a":
                return ["doc_1", "doc_2"]
            return None

        backend = ProductionToolBackend(
            knowledge_service=service,  # type: ignore[arg-type]
            knowledge_base_ids_resolver=resolver,
        )
        backend.knowledge_lookup("test", organization_id="org-1", agent_id="agent-a")
        assert service.captured_document_ids == ["doc_1", "doc_2"]

    def test_lookup_resolver_returning_empty_list_falls_back_to_all_docs(self):
        service = _StubKnowledgeService(_StubLookupResult())

        def resolver(agent_id, organization_id, state_id):
            return []  # Empty list means "no specific docs linked"

        backend = ProductionToolBackend(
            knowledge_service=service,  # type: ignore[arg-type]
            knowledge_base_ids_resolver=resolver,
        )
        backend.knowledge_lookup("test", organization_id="org-1", agent_id="agent-a")
        # Empty list → document_ids stays None (search all docs, don't filter to nothing)
        assert service.captured_document_ids is None

    def test_lookup_resolver_returning_none_falls_back_to_all_docs(self):
        service = _StubKnowledgeService(_StubLookupResult())

        def resolver(agent_id, organization_id, state_id):
            return None

        backend = ProductionToolBackend(
            knowledge_service=service,  # type: ignore[arg-type]
            knowledge_base_ids_resolver=resolver,
        )
        backend.knowledge_lookup("test", organization_id="org-1", agent_id="agent-a")
        assert service.captured_document_ids is None

    def test_lookup_resolver_exception_falls_back_to_all_docs(self):
        service = _StubKnowledgeService(_StubLookupResult())

        def resolver(agent_id, organization_id, state_id):
            raise RuntimeError("registry unavailable")

        backend = ProductionToolBackend(
            knowledge_service=service,  # type: ignore[arg-type]
            knowledge_base_ids_resolver=resolver,
        )
        # Should not raise — resolver failure is non-fatal
        backend.knowledge_lookup("test", organization_id="org-1", agent_id="agent-a")
        assert service.captured_document_ids is None

    def test_lookup_without_agent_id_skips_resolver(self):
        service = _StubKnowledgeService(_StubLookupResult())
        resolver_called = False

        def resolver(agent_id, organization_id, state_id):
            nonlocal resolver_called
            resolver_called = True
            return ["doc_1"]

        backend = ProductionToolBackend(
            knowledge_service=service,  # type: ignore[arg-type]
            knowledge_base_ids_resolver=resolver,
        )
        # No agent_id → resolver should not be called
        backend.knowledge_lookup("test", organization_id="org-1", agent_id=None)
        assert not resolver_called
        assert service.captured_document_ids is None

    def test_lookup_different_agents_get_different_scopes(self):
        service = _StubKnowledgeService(_StubLookupResult())

        agent_docs = {
            "sales_agent": ["doc_product", "doc_pricing"],
            "support_agent": ["doc_troubleshooting", "doc_faq"],
        }

        def resolver(agent_id, organization_id, state_id):
            return agent_docs.get(agent_id)

        backend = ProductionToolBackend(
            knowledge_service=service,  # type: ignore[arg-type]
            knowledge_base_ids_resolver=resolver,
        )

        backend.knowledge_lookup("test", organization_id="org-1", agent_id="sales_agent")
        assert service.captured_document_ids == ["doc_product", "doc_pricing"]

        backend.knowledge_lookup("test", organization_id="org-1", agent_id="support_agent")
        assert service.captured_document_ids == ["doc_troubleshooting", "doc_faq"]
