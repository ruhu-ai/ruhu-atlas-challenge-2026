from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ruhu.knowledge import KnowledgeService

from .authorizer import DefaultToolAuthorizer
from .catalog import CompositeToolCatalogResolver
from .compiler import ToolSpecCompiler
from .db_catalog import SQLAlchemyCatalogResolver
from .executors.builtin import BuiltinExecutor
from .executors.code import CodeExecutor
from .executors.composite import CompositeExecutor
from .executors.http import HttpExecutor
from .executors.mcp import MCPExecutor
from .integration_runtime import ToolIntegrationRuntime
from .integration_store import SQLAlchemyToolIntegrationJobStore
from .management import APIConnectionStore, CredentialCipher
from .pii import TieredPiiScanner
from .registry import ToolRegistry
from .runtime import ToolRuntime
from .specs import ToolAnnotations, ToolSpec
from .store import SQLAlchemyToolInvocationStore
from .types import ToolCall

logger = logging.getLogger(__name__)

# Type alias for the callback that resolves knowledge_base_ids for an agent.
# Signature: (agent_id, organization_id, step_id) -> list[str] | None
KnowledgeBaseIdsResolver = Callable[[str | None, str | None, str | None], Sequence[str] | None]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ProductionToolBackend:
    knowledge_service: KnowledgeService
    default_knowledge_organization_id: str | None = None
    knowledge_base_ids_resolver: KnowledgeBaseIdsResolver | None = None

    def knowledge_lookup(
        self,
        query: str,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        step_id: str | None = None,
        mode: str = "standard",
    ) -> dict[str, object]:
        # Resolve per-step document scope.  If the step has linked
        # knowledge_base_ids, only those documents are searched.  Otherwise
        # fall back to agent-level knowledge_base_ids. Otherwise all
        # published documents in the organization are searched.
        document_ids: Sequence[str] | None = None
        if self.knowledge_base_ids_resolver is not None and agent_id:
            try:
                resolved = self.knowledge_base_ids_resolver(agent_id, organization_id, step_id)
                if resolved:
                    document_ids = list(resolved)
            except Exception:
                logger.warning(
                    "knowledge_base_ids_resolver failed for agent %s, searching all docs",
                    agent_id,
                    exc_info=True,
                )
        resolved_organization_id = organization_id or self.default_knowledge_organization_id
        if resolved_organization_id is None:
            # No tenant resolved → no knowledge base to search.  Return a
            # graceful empty result rather than raising (keeps the tool call
            # status=success, matching previous sentinel-fallback behaviour).
            # Log loud enough to catch unconfigured dev installs — silent
            # empty results were confusing during the enterprise migration.
            logger.warning(
                "knowledge_lookup.no_tenant_scope",
                extra={
                    "agent_id": agent_id,
                    "hint": (
                        "Pass organization_id explicitly, or configure "
                        "ProductionToolBackend(default_knowledge_organization_id=...). "
                        "For a local demo set RUHU_DEV_BOOTSTRAP_ORGANIZATION_ID + "
                        "RUHU_KNOWLEDGE_DEFAULT_ORGANIZATION_ID + RUHU_KNOWLEDGE_SEED_PATH + "
                        "RUHU_KNOWLEDGE_AUTO_SEED=true."
                    ),
                },
            )
            return {
                "message": (
                    "No knowledge base is scoped to this conversation. "
                    "See server logs (knowledge_lookup.no_tenant_scope) for setup hints."
                ),
                "context_block": None,
                "facts": {},
                "top_source": None,
                "top_hit": None,
                "sources": [],
                "hits": [],
                "retrieval_mode": mode,
                "retrieval_queries": [],
                "retrieval_steps": [],
            }
        result = self.knowledge_service.lookup(
            organization_id=resolved_organization_id,
            query=query,
            document_ids=document_ids,
            mode="deep" if str(mode).lower() == "deep" else "standard",
        )
        top_source = result.sources[0] if result.sources else None
        top_hit = result.hits[0] if result.hits else None
        evaluation = getattr(result, "evaluation", None)
        return {
            "message": result.message,
            "context_block": result.context_block,
            "evaluation": (
                None if evaluation is None else evaluation.model_dump(mode="json")
            ),
            "facts": {
                "last_knowledge_query": query,
                "last_knowledge_document_id": None if top_source is None else top_source.document_id,
                "last_knowledge_hit_count": len(result.hits),
                "knowledge_retrieval_mode": None if top_hit is None else top_hit.retrieval_mode,
                "knowledge_lookup_mode": result.lookup_mode,
                "knowledge_lookup_grade": (
                    None if evaluation is None else evaluation.grade
                ),
            },
            "retrieval_mode": result.lookup_mode,
            "retrieval_queries": list(result.retrieval_queries),
            "retrieval_steps": [step.model_dump(mode="json") for step in result.retrieval_steps],
            "top_source": None if top_source is None else top_source.model_dump(mode="json"),
            "top_hit": None if top_hit is None else top_hit.model_dump(mode="json"),
            "sources": [source.model_dump(mode="json") for source in result.sources],
            "hits": [hit.model_dump(mode="json") for hit in result.hits],
        }


def production_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            ref="knowledge.lookup",
            kind="builtin",
            display_name="Knowledge Lookup",
            description="Look up grounded product, workflow, integration, and pricing answers from curated knowledge.",
            purpose="Retrieve grounded facts from curated knowledge before answering product, workflow, or pricing questions.",
            when_to_use=[
                "Use when the user asks for product, workflow, pricing, or integration information that should come from curated knowledge.",
            ],
            when_not_to_use=[
                "Do not use for account-specific actions, record updates, or questions that require fresh external system state.",
            ],
            input_examples=[
                {
                    "name": "pricing_question",
                    "description": "Looks up pricing context before composing a grounded answer.",
                    "args": {"query": "How does Ruhu pricing work for WhatsApp support?"},
                }
            ],
            failure_modes=[
                {
                    "kind": "transient_upstream_error",
                    "description": "Knowledge retrieval backend is temporarily unavailable or timing out.",
                    "retryable": True,
                },
                {
                    "kind": "permanent_upstream_error",
                    "description": "No tenant-scoped knowledge base is configured for the conversation.",
                    "retryable": False,
                },
            ],
            output_validation_mode="strict",
            annotations=ToolAnnotations(read_only=True, side_effect_free=True, idempotent=True),
            timeout_ms=1200,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Question or topic to search for."},
                    "mode": {
                        "type": "string",
                        "enum": ["standard", "deep"],
                        "description": "Use 'deep' for harder multi-part or exploratory questions.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "context_block": {"type": ["string", "null"]},
                    "evaluation": {"type": ["object", "null"]},
                    "facts": {"type": "object"},
                    "retrieval_mode": {"type": "string", "enum": ["standard", "deep"]},
                    "retrieval_queries": {"type": "array"},
                    "retrieval_steps": {"type": "array"},
                    "top_source": {"type": ["object", "null"]},
                    "top_hit": {"type": ["object", "null"]},
                    "sources": {"type": "array"},
                    "hits": {"type": "array"},
                },
                "additionalProperties": True,
            },
        ),
    ]


def build_production_tool_runtime(
    *,
    session_factory: sessionmaker[Session],
    knowledge_service: KnowledgeService,
    default_knowledge_organization_id: str | None = None,
    cipher: CredentialCipher | None = None,
    knowledge_base_ids_resolver: KnowledgeBaseIdsResolver | None = None,
    connection_store: "APIConnectionStore | None" = None,
    tiered_pii_scanner: TieredPiiScanner | None = None,
    oauth_manager: "Any | None" = None,
    oauth_get_credentials: "Any | None" = None,
) -> tuple[ToolRuntime, ProductionToolBackend]:
    """``connection_store``: the phase-1 ``APIConnectionStore`` wrapping the
    AEAD cipher + audit router.  When supplied, the ``ToolSpecCompiler``
    routes OAuth2 decrypts through it so every compile-time credential read
    emits an audit event.  Optional for backward-compatibility with existing
    test harnesses that construct a runtime without wiring the store.

    ``tiered_pii_scanner``: optional TieredPiiScanner for PII scanning and
    redaction at tool invocation time (args before execution, output after).
    """
    backend = ProductionToolBackend(
        knowledge_service=knowledge_service,
        default_knowledge_organization_id=default_knowledge_organization_id,
        knowledge_base_ids_resolver=knowledge_base_ids_resolver,
    )
    registry = ToolRegistry(production_tool_specs())
    executor = BuiltinExecutor(
        {
            "knowledge.lookup": lambda call, _spec: backend.knowledge_lookup(
                str(call.args.get("query") or ""),
                organization_id=call.caller.tenant_id,
                agent_id=call.caller.agent_id,
                step_id=call.caller.step_id,
                mode=str(call.args.get("mode") or "standard"),
            ),
        }
    )
    # Customer-defined tools (HTTP endpoints, OAuth integrations) live in the
    # DB and are resolved per-request.  The CompositeToolCatalogResolver chains
    # additional resolvers in the future without changing this call site.
    catalog_resolver = CompositeToolCatalogResolver(
        SQLAlchemyCatalogResolver(
            session_factory,
            ToolSpecCompiler(cipher=cipher, connection_store=connection_store),
        )
    )
    invocation_store = SQLAlchemyToolInvocationStore(session_factory, pii_scanner=tiered_pii_scanner)
    # Composite + Code executors both need a back-reference to the runtime
    # to invoke sub-callables. Build them after construction with a shared
    # closure provider; the holder gets populated below.
    runtime_holder: dict[str, ToolRuntime] = {}
    composite_executor = CompositeExecutor(runtime_provider=lambda: runtime_holder["runtime"])
    code_executor = CodeExecutor(runtime_provider=lambda: runtime_holder["runtime"])

    # ``on_unauthorized`` for the HTTP executor: bridges a 401 into a
    # synchronous OAuth refresh. Wired only when an ``oauth_manager`` is
    # supplied — non-OAuth deployments (early dev, single-tenant API-key
    # only) get the no-op behaviour of the executor's default.
    on_unauthorized = None
    if oauth_manager is not None:
        def on_unauthorized(request_config: dict[str, Any]) -> dict[str, str] | None:
            connection_id = request_config.get("connection_id")
            organization_id = request_config.get("organization_id")
            if not connection_id or not organization_id:
                return None
            new_tokens = oauth_manager.force_refresh_sync(
                connection_id=str(connection_id),
                organization_id=str(organization_id),
                get_credentials=oauth_get_credentials,
            )
            access_token = (new_tokens or {}).get("access_token")
            if not access_token:
                return None
            return {"Authorization": f"Bearer {access_token}"}

    runtime = ToolRuntime(
        registry,
        authorizer=DefaultToolAuthorizer(),
        store=invocation_store,
        executors={
            "builtin": executor,
            "http": HttpExecutor(on_unauthorized=on_unauthorized),
            "mcp": MCPExecutor(),
            "code": code_executor,
            "composite": composite_executor,
        },
        catalog_resolver=catalog_resolver,
        tiered_pii_scanner=tiered_pii_scanner,
        integration_runtime=ToolIntegrationRuntime(
            job_store=SQLAlchemyToolIntegrationJobStore(session_factory),
            invocation_store=invocation_store,
        ),
    )
    runtime_holder["runtime"] = runtime
    return runtime, backend
