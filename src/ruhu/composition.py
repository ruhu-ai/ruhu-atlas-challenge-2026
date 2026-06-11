"""RP-3.2 composition root.

Pure construction — no FastAPI, no app.state, no eager seeding/startup side
effects. Both ``ruhu.api.build_default_app`` and ``ruhu.worker`` compose
their runtimes from the builders here; seeding (pricing catalog, agent
templates, agent bootstrap) and startup hooks (async engine init, knowledge
startup, worker starts) remain the caller's responsibility.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .attachments import AttachmentRuntime, build_attachment_runtime
from .billing.service import BillingService
from .billing.store import SQLAlchemyBillingStore
from .browser_tasks import (
    APIConnectionBrowserCredentialValidator,
    BrowserTaskPackAccessPolicy,
    BrowserTaskService,
    load_browser_task_pack_registry,
)
from .browser_tasks.store import SQLAlchemyBrowserTaskStore
from .browser_tasks.tool import BrowserTaskCreateToolHandler, browser_task_create_tool_spec
from .classifier_strategy import (
    LoRAEligibility,
    StrategyAwareInterpreter,
)
from .db import build_session_factory, resolve_database_url
from .interpreter import SemanticInterpreter
from .interpreters import build_interpreter_router
from .jobs import SQLAlchemyJobStore
from .kernel import ConversationKernel
from .knowledge import KnowledgeRuntime, build_knowledge_runtime
from .realtime import (
    KernelRealtimeBridge,
    RealtimeControlPlane,
    SQLAlchemyRealtimeEventStore,
    SQLAlchemyRealtimeIdempotencyStore,
    SQLAlchemyRealtimeOutboxStore,
    SQLAlchemyRealtimeSessionStore,
)
from .registry import SQLAlchemyAgentRegistry
from .rules_store import RulesRuntime, build_rules_runtime
from .runtime_config import RuntimeSettings
from .api_models import AgentSettings
from .stores import (
    SQLAlchemyConversationStore,
    SQLAlchemyTraceStore,
    SQLAlchemyTurnLogStore,
)
from .tools.pii import TieredPiiScanner
from .tools.production import build_production_tool_runtime

logger = logging.getLogger(__name__)


def resolve_gemini_api_key(settings: RuntimeSettings) -> str | None:
    """Compute the Gemini API key once; shared by both the field extractor
    (kernel) and the vision producers (attachment service)."""
    return (
        settings.knowledge_embedding_api_key
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    )


def _enforce_secret_boundary_policy(settings: RuntimeSettings) -> None:
    provider_secret = (settings.provider_shared_secret or "").strip()
    internal_secret = (settings.internal_api_secret or "").strip()
    if provider_secret and internal_secret and hmac.compare_digest(provider_secret, internal_secret):
        raise ValueError("internal_api_secret must be distinct from provider_shared_secret")


def _build_strategy_aware_interpreter(
    *,
    agent_registry: SQLAlchemyAgentRegistry,
    session_factory: Any,
    settings: RuntimeSettings,
    prefill_interpreter: SemanticInterpreter | None,
    model_path: str | Path,
) -> StrategyAwareInterpreter:
    """Construct the Phase A strategy dispatcher.

    Wires:
    - ``settings_resolver``: agent_id → ``AgentSettings``, sourced from the
      live ``agent_registrations`` table so settings changes take effect on
      the next turn.
    - ``main_llm_classifier``: ``VertexGeminiClassifierBackend`` built from
      ``RuntimeSettings.vertex_project`` (if available).
    - ``prefill_interpreter``: passed in (the existing ``gemma_local`` /
      vLLM router).
    - ``lora_eligibility_resolver``: queries ``classifier.registry.resolve_lora``
      under a fresh session per turn. ``None`` → not eligible (the runtime
      rejects ``strategy = "prefill"`` until promotion lands).
    """
    from .classifier.factory import ClassifierBackendConfig, build_classifier
    from .classifier.registry import resolve_lora as _resolve_lora_for_dispatch

    def _settings_resolver(agent_id: str) -> AgentSettings | None:
        try:
            registration = agent_registry.get_agent_registration(agent_id)
        except KeyError:
            return None
        payload = registration.settings.get("agent_settings") if isinstance(
            registration.settings, dict
        ) else None
        if not isinstance(payload, dict):
            return None
        try:
            return AgentSettings.model_validate(payload)
        except Exception:
            return None

    def _lora_eligibility_resolver(agent_id: str, step_id: str) -> LoRAEligibility:
        try:
            with session_factory.begin() as session:
                lora_name = _resolve_lora_for_dispatch(
                    session,
                    agent_id=agent_id,
                    step_id=step_id,
                    organization_id=None,
                )
        except Exception as exc:
            return LoRAEligibility(
                available=False,
                reason=f"lora_lookup_error:{type(exc).__name__}",
            )
        if lora_name is None:
            return LoRAEligibility(
                available=False,
                reason="no_production_lora",
            )
        return LoRAEligibility(available=True, lora_name=lora_name)

    main_llm_classifier = None
    main_llm_model_name = None
    vertex_project = (
        os.getenv("RUHU_VERTEX_AI_PROJECT")
        or os.getenv("VERTEX_AI_PROJECT")
        or ""
    ).strip() or None
    if vertex_project:
        try:
            main_llm_classifier = build_classifier(
                ClassifierBackendConfig(
                    kind="vertex_gemini",
                    vertex_project=vertex_project,
                    vertex_location=(
                        os.getenv("RUHU_VERTEX_AI_LOCATION")
                        or os.getenv("VERTEX_AI_LOCATION")
                        or "europe-west2"
                    ),
                    model=os.getenv("RUHU_CLASSIFIER_MAIN_LLM_MODEL"),
                    # 8s is too aggressive for Gemini Flash cold-start
                    # (observed ~60% success rate at 8s; >95% at 20s).
                    # Operators tighten via env when latency budget matters
                    # more than recall.
                    timeout_ms=int(
                        os.getenv("RUHU_CLASSIFIER_MAIN_LLM_TIMEOUT_MS") or "20000"
                    ),
                )
            )
            main_llm_model_name = (
                os.getenv("RUHU_CLASSIFIER_MAIN_LLM_MODEL")
                or "gemini-3-flash-preview"
            )
        except Exception:
            logger.warning(
                "could not build main_llm classifier backend; "
                "agents on strategy=main_llm will emit classifier_unavailable",
                exc_info=True,
            )

    return StrategyAwareInterpreter(
        settings_resolver=_settings_resolver,
        main_llm_classifier=main_llm_classifier,
        main_llm_model_name=main_llm_model_name,
        prefill_interpreter=prefill_interpreter,
        lora_eligibility_resolver=_lora_eligibility_resolver,
    )


@dataclass(frozen=True)
class DataLayer:
    """Stores, ciphers, and persistence-backed runtimes.

    Construction only: ``billing_service`` is NOT seeded,
    ``knowledge_runtime.startup()`` is NOT called, the agent registry is NOT
    bootstrapped, and ``live_eval_runtime`` is NOT started — callers own all
    of those side effects.
    """

    settings: RuntimeSettings
    database_url: str
    session_factory: Any
    pii_scanner: TieredPiiScanner | None
    conversation_store: SQLAlchemyConversationStore
    trace_store: Any  # SQLAlchemyTraceStore, possibly live-eval instrumented
    turn_log_store: SQLAlchemyTurnLogStore
    jobs_store: SQLAlchemyJobStore
    billing_store: SQLAlchemyBillingStore
    billing_service: BillingService
    live_eval_runtime: Any | None
    agent_registry: SQLAlchemyAgentRegistry
    legacy_cipher: Any | None
    blob_cipher: Any
    connection_store: Any
    knowledge_runtime: KnowledgeRuntime
    attachment_runtime: AttachmentRuntime
    realtime_control_plane: RealtimeControlPlane
    rules_runtime: RulesRuntime


def build_data_layer(
    *,
    settings: RuntimeSettings,
    database_url: str | None = None,
    session_factory: Any | None = None,
    audit_router: Any | None = None,
) -> DataLayer:
    """Construct the data layer. Pure construction — zero side effects."""
    if database_url is None:
        database_url = resolve_database_url(database_url=settings.database_url)
    if session_factory is None:
        session_factory = build_session_factory(
            database_url,
            pool_size=settings.sync_db_pool_size,
            max_overflow=settings.sync_db_max_overflow,
            pool_recycle=settings.sync_db_pool_recycle,
            pool_timeout=settings.sync_db_pool_timeout,
            statement_timeout_ms=settings.sync_db_statement_timeout_ms,
        )
    runtime_session_factory = session_factory
    knowledge_runtime = build_knowledge_runtime(
        session_factory=runtime_session_factory,
        runtime_settings=settings,
        # Enterprise posture: no default seed path.  Callers must opt in via
        # RUHU_KNOWLEDGE_SEED_PATH + RUHU_KNOWLEDGE_AUTO_SEED=true if they
        # want to seed knowledge at startup.
    )
    _gemini_api_key = resolve_gemini_api_key(settings)
    attachment_runtime = build_attachment_runtime(
        session_factory=runtime_session_factory,
        runtime_settings=settings,
        gemini_api_key=_gemini_api_key,
    )
    # COORD: pii-pipeline — construct tiered scanner if PII is enabled
    pii_scanner: TieredPiiScanner | None = None
    if settings.pii_global_enabled:
        pii_scanner = TieredPiiScanner.from_settings(settings, audit_router=None)
    conversation_store = SQLAlchemyConversationStore(runtime_session_factory, pii_scanner=pii_scanner)
    trace_store = SQLAlchemyTraceStore(runtime_session_factory, pii_scanner=pii_scanner)
    turn_log_store = SQLAlchemyTurnLogStore(runtime_session_factory)
    jobs_store = SQLAlchemyJobStore(runtime_session_factory)

    # Continuous (live) evaluation — opt-in via RUHU_LIVE_EVAL_ENABLED.
    # When enabled, every kernel trace_store.append() also fans out to a
    # background worker that scores a sampled fraction of turns. The
    # wrapper is non-invasive: persistence happens first, scoring is a
    # best-effort downstream step that never affects the kernel's view of
    # the trace write. See ``ruhu.live_eval`` for the data plane and
    # ``LiveEvalRuntime.start()`` / ``.stop()`` invocations in _lifespan.
    # Build billing_store early so live_eval (next) can use it for tier-
    # aware sampling.  Other consumers below still see the same instance.
    _billing_store_for_live_eval = SQLAlchemyBillingStore(runtime_session_factory)

    managed_live_eval_runtime = None
    if settings.live_eval_enabled:
        from .live_eval import (
            InstrumentedTraceStore as _InstrumentedTraceStore,
            LiveEvalRuntime as _LiveEvalRuntime,
        )
        managed_live_eval_runtime = _LiveEvalRuntime.from_settings(
            session_factory=runtime_session_factory,
            sample_rate=settings.live_eval_sample_rate,
            per_tier_rate=settings.live_eval_sample_rate_by_tier,
            billing_store=_billing_store_for_live_eval,
        )
        # Wrap the trace store in place so the kernel's downstream callers
        # see the same Protocol-typed object — no api.py-wide refactor.
        trace_store = _InstrumentedTraceStore(  # type: ignore[assignment]
            inner=trace_store,
            worker=managed_live_eval_runtime.worker,
        )
    _default_app_cipher = None
    if settings.tool_credentials_encryption_key:
        from .tools.management import CredentialCipher as _CredentialCipher
        _default_app_cipher = _CredentialCipher(settings.tool_credentials_encryption_key)

    # Build the phase-1 AEAD cipher + APIConnectionStore early so the tool
    # runtime's compiler can route OAuth2 decrypts through the audited path
    # at compile time.  The audit_router may be late-bound by the caller
    # (``shared_connection_store.set_audit_router`` in api.py) or passed in
    # directly (worker processes).
    from .tools.cipher import FernetCipher as _BlobFernetCipher
    from .tools.management import APIConnectionStore as _APIConnectionStore
    try:
        _shared_blob_cipher = _BlobFernetCipher.from_env()
    except ValueError:
        from cryptography.fernet import Fernet as _DevFernet
        _shared_blob_cipher = _BlobFernetCipher(primary=_DevFernet.generate_key().decode())
    shared_connection_store = _APIConnectionStore(
        runtime_session_factory,
        blob_cipher=_shared_blob_cipher,
        legacy_cipher=_default_app_cipher,
        audit_router=audit_router,
    )

    agent_registry = SQLAlchemyAgentRegistry(runtime_session_factory)
    rules_runtime = build_rules_runtime(runtime_session_factory)
    # Reuse the early instance built for live_eval (single object → fewer
    # PG sessions opened during seed_pricing_catalog).
    billing_store = _billing_store_for_live_eval
    billing_service = BillingService(billing_store)
    realtime_control_plane = RealtimeControlPlane(
        sessions=SQLAlchemyRealtimeSessionStore(runtime_session_factory),
        events=SQLAlchemyRealtimeEventStore(
            runtime_session_factory,
            enable_pg_notify=bool(os.getenv("RUHU_PG_DIRECT_URL", "")),
        ),
        idempotency=SQLAlchemyRealtimeIdempotencyStore(runtime_session_factory),
        outbox=SQLAlchemyRealtimeOutboxStore(runtime_session_factory),
    )
    return DataLayer(
        settings=settings,
        database_url=database_url,
        session_factory=runtime_session_factory,
        pii_scanner=pii_scanner,
        conversation_store=conversation_store,
        trace_store=trace_store,
        turn_log_store=turn_log_store,
        jobs_store=jobs_store,
        billing_store=billing_store,
        billing_service=billing_service,
        live_eval_runtime=managed_live_eval_runtime,
        agent_registry=agent_registry,
        legacy_cipher=_default_app_cipher,
        blob_cipher=_shared_blob_cipher,
        connection_store=shared_connection_store,
        knowledge_runtime=knowledge_runtime,
        attachment_runtime=attachment_runtime,
        realtime_control_plane=realtime_control_plane,
        rules_runtime=rules_runtime,
    )


@dataclass(frozen=True)
class LlmLayer:
    """Interpreter dispatch + capture extractors + response-generator chain.

    The layer closes over ``data.agent_registry`` and ``data.session_factory``
    (settings and LoRA lookups happen per turn), so it is constructed strictly
    after the data layer.
    """

    interpreter: SemanticInterpreter
    field_extractor: Any | None
    fact_pipeline: Any
    response_generator: Any | None


def build_llm_layer(
    *,
    settings: RuntimeSettings,
    data: DataLayer,
    interpreter_name: str | None = None,
    agent_interpreters: dict[str, str] | None = None,
    model_path: str | Path | None = None,
    company_name_lookup: Callable[[str], str | None] | None = None,
) -> LlmLayer:
    """Construct the LLM layer. Pure construction — zero side effects."""
    resolved_interpreter_name = (
        interpreter_name if interpreter_name is not None else settings.interpreter_name
    )
    resolved_agent_interpreters = (
        agent_interpreters if agent_interpreters is not None else settings.agent_interpreters
    )
    resolved_model_path = (
        Path(model_path) if model_path is not None else settings.classifier_model_path
    )
    runtime_session_factory = data.session_factory
    agent_registry = data.agent_registry
    # Wire optional LLM field extractor for attachment capture.
    _gemini_api_key = resolve_gemini_api_key(settings)
    _field_extractor = None
    from .capture import build_default_fact_pipeline
    from .capture.audit import SqlAuditWriter

    _conversation_field_extractor = None
    if _gemini_api_key:
        from .attachments.field_extractor import GeminiFieldExtractor
        from .capture.llm_extractor import ConversationGeminiExtractor

        _field_extractor = GeminiFieldExtractor(api_key=_gemini_api_key)
        _conversation_field_extractor = ConversationGeminiExtractor(api_key=_gemini_api_key)
    _fact_pipeline = build_default_fact_pipeline(
        _conversation_field_extractor,
        audit_writer=SqlAuditWriter(runtime_session_factory),
    )

    # Per-agent classifier dispatch. ``StrategyAwareInterpreter`` reads each
    # agent's ``llm_config.classifier.strategy`` per turn and routes to off /
    # main_llm (Vertex Gemini Flash) / prefill (the existing gemma_local
    # stack, gated on a production LoRA). The legacy interpreter router is
    # constructed only as the prefill backend.
    _prefill_interpreter = build_interpreter_router(
        default_interpreter_name=resolved_interpreter_name,
        agent_interpreters=resolved_agent_interpreters,
        model_path=resolved_model_path,
    )
    _kernel_interpreter = _build_strategy_aware_interpreter(
        agent_registry=agent_registry,
        session_factory=runtime_session_factory,
        settings=settings,
        prefill_interpreter=_prefill_interpreter,
        model_path=resolved_model_path,
    )

    # Phase 2c — wrap the response generator with topic enforcement.
    # Kill-switch: ``RUHU_TOPIC_ENFORCEMENT_ENABLED=false`` skips wrapping
    # entirely (incident-response). Default true. The wrapper is a strict
    # passthrough when an agent has no restricted_topics or policy=off, so
    # leaving it on globally has zero observable cost for agents that
    # haven't configured topic enforcement.
    _topic_enforcement_enabled = (
        os.getenv("RUHU_TOPIC_ENFORCEMENT_ENABLED", "true").strip().lower()
        not in {"false", "0", "no", "off"}
    )
    _kernel_response_generator = None
    if _topic_enforcement_enabled:
        from .topic_enforcement import (
            TopicEnforcingResponseGenerator,
            TopicSettings,
        )
        from .persona import TopicEnforcementPolicy
        from .response_generation import build_response_generator_from_env

        _inner_generator = build_response_generator_from_env()
        if _inner_generator is not None:
            def _topic_settings_lookup(
                agent_id: str,
                organization_id: str | None,
            ) -> TopicSettings | None:
                """Read BehavioralPersona from the published agent document.
                Falls back to None on any error (decorator treats None as
                policy=off, so a registry hiccup never blocks a turn)."""
                try:
                    registration = agent_registry.get_agent_registration(
                        agent_id, organization_id=organization_id,
                    )
                    published_id = registration.current_published_version_id
                    if published_id is None:
                        return None
                    snapshot = agent_registry.get_version_snapshot(
                        agent_id,
                        version_id=published_id,
                        organization_id=organization_id,
                    )
                    if snapshot.agent_document is None:
                        return None
                    persona = snapshot.agent_document.behavioral_persona()
                    if persona is None:
                        return None
                    return TopicSettings(
                        policy=persona.topic_enforcement,
                        topics=tuple(persona.restricted_topics),
                    )
                except Exception:
                    return None

            # Phase 2b — language routing decorator. Wraps the raw
            # generator BEFORE topic enforcement so the chain is:
            # TopicEnforcing → LanguageRouting → Inner. Topic
            # enforcement post-checks the rendered text regardless of
            # which language landed; language routing pre-mutates the
            # persona block before render. Same env kill-switch as
            # 2c (operators can disable both independently).
            _language_routing_enabled = (
                os.getenv("RUHU_LANGUAGE_ROUTING_ENABLED", "true").strip().lower()
                not in {"false", "0", "no", "off"}
            )
            _wrapped_for_language = _inner_generator
            if _language_routing_enabled:
                from .language_routing import (
                    LanguageRoutingResponseGenerator,
                    LanguageRoutingSettings,
                )
                from .language_detection import build_language_detector_from_env

                # Cache the text detector at the wrap level — the
                # detector is stateless after init (FastText loads the
                # model lazily), so per-render construction would be a
                # waste.
                _text_detector_singleton = build_language_detector_from_env()

                def _language_settings_lookup(
                    agent_id: str,
                    organization_id: str | None,
                ) -> LanguageRoutingSettings | None:
                    """Read full persona for language routing.

                    Cosmetic side comes from AgentSettings.persona;
                    behavioural from AgentDocument.metadata.persona.
                    Both can be None — the decorator handles that.
                    """
                    try:
                        from .api_models import AgentSettings
                        registration = agent_registry.get_agent_registration(
                            agent_id, organization_id=organization_id,
                        )
                        cosmetic = None
                        behavioral = None
                        company_name = None
                        # Cosmetic — live PATCH lives on the registration.
                        try:
                            settings_dict = registration.agent_settings or {}
                            agent_settings = AgentSettings.model_validate(settings_dict)
                            cosmetic = agent_settings.persona
                        except Exception:
                            cosmetic = None
                        # Behavioural — versioned, on the published doc.
                        published_id = registration.current_published_version_id
                        if published_id is not None:
                            snapshot = agent_registry.get_version_snapshot(
                                agent_id,
                                version_id=published_id,
                                organization_id=organization_id,
                            )
                            if snapshot.agent_document is not None:
                                behavioral = snapshot.agent_document.behavioral_persona()
                        # Company name (best-effort). H1: the injected lookup
                        # replaces the old `_widget_config(agent_id)` call,
                        # which was a latent NameError swallowed by this
                        # except — callers that pass no lookup keep the de
                        # facto behavior (always None).
                        try:
                            company_name = (
                                company_name_lookup(agent_id)
                                if company_name_lookup is not None
                                else None
                            )
                        except Exception:
                            company_name = None
                        return LanguageRoutingSettings(
                            cosmetic=cosmetic,
                            behavioral=behavioral,
                            company_name=company_name,
                        )
                    except Exception:
                        return None

                def _detect_text_language(
                    text: str,
                ) -> tuple[str, float] | None:
                    result = _text_detector_singleton.detect(text)
                    return None if result is None else (
                        result.language, result.confidence,
                    )

                _wrapped_for_language = LanguageRoutingResponseGenerator(
                    inner=_inner_generator,
                    settings_lookup=_language_settings_lookup,
                    text_detector=_detect_text_language,
                )

            _kernel_response_generator = TopicEnforcingResponseGenerator(
                inner=_wrapped_for_language,
                settings_lookup=_topic_settings_lookup,
            )

    return LlmLayer(
        interpreter=_kernel_interpreter,
        field_extractor=_field_extractor,
        fact_pipeline=_fact_pipeline,
        response_generator=_kernel_response_generator,
    )


@dataclass(frozen=True)
class ComposedRuntime:
    """Everything a kernel host needs, fully constructed and side-effect free.

    ``builtin_tool_refs`` is snapshotted BEFORE ``browser_task.create`` is
    registered (H4) — api.py's template seeding consumes exactly that set.
    """

    data: DataLayer
    llm: LlmLayer
    tool_runtime: Any
    tool_backend: Any
    builtin_tool_refs: set[str]
    browser_task_service: BrowserTaskService
    kernel: ConversationKernel


def build_kernel(
    *,
    settings: RuntimeSettings,
    data: DataLayer,
    llm: LlmLayer,
) -> ComposedRuntime:
    """Construct the tool runtime, browser-task service, and kernel."""
    # H5: the knowledge-base resolver closure late-binds the agent registry —
    # the data layer (and therefore the registry) exists before this point.
    agent_registry = data.agent_registry

    def _resolve_knowledge_base_ids(
        agent_id: str | None,
        organization_id: str | None,
        step_id: str | None = None,
    ) -> list[str] | None:
        """Resolve agent-scoped knowledge base ids for the step-native runtime."""
        if not agent_id:
            return None
        try:
            registration = agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
            payload = registration.settings.get("agent_settings")
            if isinstance(payload, dict):
                kb_ids = payload.get("knowledge_base_ids")
                if isinstance(kb_ids, list) and kb_ids:
                    return [str(kid) for kid in kb_ids if kid]
        except Exception:
            pass
        return None

    tool_runtime, tool_backend = build_production_tool_runtime(
        session_factory=data.session_factory,
        knowledge_service=data.knowledge_runtime.service,
        default_knowledge_organization_id=data.knowledge_runtime.default_organization_id,
        cipher=data.legacy_cipher,
        knowledge_base_ids_resolver=_resolve_knowledge_base_ids,
        connection_store=data.connection_store,
        tiered_pii_scanner=data.pii_scanner,  # COORD: pii-pipeline
    )
    # H4: snapshot the built-in refs before browser_task.create registration.
    builtin_tool_refs = {spec.ref for spec in tool_runtime.list_specs()}
    browser_task_pack_registry = load_browser_task_pack_registry(settings.browser_task_pack_path)
    browser_task_service = BrowserTaskService(
        SQLAlchemyBrowserTaskStore(data.session_factory),
        task_pack_registry=browser_task_pack_registry,
        credential_validator=APIConnectionBrowserCredentialValidator(data.connection_store),
        task_pack_access_policy=(
            BrowserTaskPackAccessPolicy(allowed_pack_ids=set(settings.browser_task_allowed_packs))
            if settings.browser_task_allowed_packs
            else None
        ),
    )
    tool_runtime.register_spec(browser_task_create_tool_spec())
    builtin_executor = tool_runtime.get_executor("builtin")
    if builtin_executor is not None and hasattr(builtin_executor, "register"):
        builtin_executor.register(
            "browser_task.create",
            BrowserTaskCreateToolHandler(browser_task_service),
        )
    kernel = ConversationKernel(
        conversation_store=data.conversation_store,
        trace_store=data.trace_store,
        turn_log_store=data.turn_log_store,
        interpreter=llm.interpreter,
        tool_runtime=tool_runtime,
        realtime_bridge=KernelRealtimeBridge(data.realtime_control_plane),
        rule_engine=data.rules_runtime.engine,
        rule_program_resolver=data.rules_runtime.resolver,
        field_extractor=llm.field_extractor,
        fact_pipeline=llm.fact_pipeline,
        # Phase 2c: pre-wrapped with topic enforcement when configured (see
        # build_llm_layer). When None, kernel falls back to its internal
        # ``build_response_generator_from_env()`` exactly as before.
        response_generator=llm.response_generator,
    )
    return ComposedRuntime(
        data=data,
        llm=llm,
        tool_runtime=tool_runtime,
        tool_backend=tool_backend,
        builtin_tool_refs=builtin_tool_refs,
        browser_task_service=browser_task_service,
        kernel=kernel,
    )


def build_minimal_runtime(
    *,
    kernel: ConversationKernel,
    agent_registry: Any,
    knowledge_runtime: KnowledgeRuntime | None = None,
    tool_backend: Any | None = None,
    settings: RuntimeSettings | None = None,
) -> ComposedRuntime:
    """In-memory ``ComposedRuntime`` for hosts that bypass ``build_runtime``.

    RP-3.1 step 18: direct ``create_app`` callers (tests, the OpenAPI export)
    construct their own kernel/registry and need no database, LLM layer, or
    tool backend.  This wraps those pieces in sparse ``DataLayer``/``LlmLayer``
    shells — every field ``create_app`` reads from the runtime that the caller
    did not supply is ``None``, which is exactly what those callers passed to
    the old keyword-per-dependency signature.
    """
    data = DataLayer(
        settings=settings,  # type: ignore[arg-type]
        database_url=None,  # type: ignore[arg-type]
        session_factory=None,
        pii_scanner=None,
        conversation_store=kernel.conversation_store,  # type: ignore[arg-type]
        trace_store=kernel.trace_store,
        turn_log_store=kernel.turn_log_store,  # type: ignore[arg-type]
        jobs_store=None,  # type: ignore[arg-type]
        billing_store=None,  # type: ignore[arg-type]
        billing_service=None,  # type: ignore[arg-type]
        live_eval_runtime=None,
        agent_registry=agent_registry,
        legacy_cipher=None,
        blob_cipher=None,
        connection_store=None,
        knowledge_runtime=knowledge_runtime,  # type: ignore[arg-type]
        attachment_runtime=None,  # type: ignore[arg-type]
        realtime_control_plane=None,  # type: ignore[arg-type]
        rules_runtime=None,  # type: ignore[arg-type]
    )
    llm = LlmLayer(
        interpreter=None,  # type: ignore[arg-type]
        field_extractor=None,
        fact_pipeline=None,
        response_generator=None,
    )
    return ComposedRuntime(
        data=data,
        llm=llm,
        tool_runtime=kernel.tool_runtime,
        tool_backend=tool_backend,
        builtin_tool_refs=(
            set()
            if kernel.tool_runtime is None
            else {spec.ref for spec in kernel.tool_runtime.list_specs()}
        ),
        browser_task_service=None,  # type: ignore[arg-type]
        kernel=kernel,
    )


def build_runtime(
    *,
    settings: RuntimeSettings | None = None,
    database_url: str | None = None,
    session_factory: Any | None = None,
    agent_seed_root: str | Path | None = None,
    bootstrap_organization_id: str | None = None,
    interpreter_name: str | None = None,
    agent_interpreters: dict[str, str] | None = None,
    model_path: str | Path | None = None,
    audit_router: Any | None = None,
    company_name_lookup: Callable[[str], str | None] | None = None,
) -> ComposedRuntime:
    """Compose data → llm → kernel.

    The only side effect is the opt-in agent bootstrap (``agent_seed_root``;
    worker processes pass None — H3). Template/pricing seeding, async-engine
    init (H9), knowledge startup, and worker starts all stay with the caller.
    """
    settings = settings or RuntimeSettings.from_env()
    _enforce_secret_boundary_policy(settings)
    if database_url is None:
        database_url = resolve_database_url(database_url=settings.database_url)
    resolved_interpreter_name = (
        interpreter_name if interpreter_name is not None else settings.interpreter_name
    )
    resolved_agent_interpreters = (
        agent_interpreters if agent_interpreters is not None else settings.agent_interpreters
    )
    resolved_model_path = (
        Path(model_path) if model_path is not None else settings.classifier_model_path
    )
    # H10: bake the resolved non-auth fields into the settings carried on the
    # data layer, so api.py's create_app `replace(...)` keeps producing
    # identical values when sourcing them from ``rt.data.settings``.
    settings = replace(
        settings,
        database_url=database_url,
        interpreter_name=resolved_interpreter_name,
        agent_interpreters=resolved_agent_interpreters,
        classifier_model_path=resolved_model_path,
    )
    data = build_data_layer(
        settings=settings,
        database_url=database_url,
        session_factory=session_factory,
        audit_router=audit_router,
    )
    llm = build_llm_layer(
        settings=settings,
        data=data,
        company_name_lookup=company_name_lookup,
    )
    runtime = build_kernel(settings=settings, data=data, llm=llm)
    if agent_seed_root is not None:
        data.agent_registry.bootstrap_from_directory(
            Path(agent_seed_root), organization_id=bootstrap_organization_id
        )
    return runtime
