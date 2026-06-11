"""Ruhu state-native runtime."""

import importlib

from .auth import (
    AccessTokenClaims,
    AuthError,
    AuthService,
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthorizationError,
    IssuedSession,
    JWTCodec,
    TokenExpiredError,
)
from .gemma_local import GemmaLocalInterpreter, GemmaLocalRuntimeError, build_gemma_local_interpreter
from .heuristics import KeywordInterpreter, interpreter_by_name
from .interpreters import AgentInterpreterRouter, LazyInterpreter, build_interpreter_router, build_named_interpreter
from .identity import (
    AuthSession,
    IdentityStore,
    InMemoryIdentityStore,
    Organization,
    OrganizationMembership,
    User,
)
from .identity_sqlalchemy import SQLAlchemyIdentityStore
from .interpreter import SemanticInterpreter
from .kernel import ConversationKernel
from .loader import load_agent_document, load_agent_document_source, load_transcript
from .livekit_adapter import (
    LiveKitAdapterConfig,
    LiveKitAgentsUnavailableError,
    LiveKitControlPlaneClient,
    LiveKitDispatchClient,
    LiveKitDispatchResult,
    LiveKitPhoneAdapter,
    LiveKitTokenIssuer,
    LiveKitVoiceTransportGrant,
    LiveKitWorkerDispatchContext,
    RuhuLiveKitAgentWorker,
    load_livekit_agents_sdk,
    load_livekit_api_sdk,
)
from .livekit_worker import build_livekit_agent_server_app, build_livekit_worker
from .registry import FileAgentRegistry, SQLAlchemyAgentRegistry
from .rules import (
    PendingRuleConfirmation,
    RuleBinding,
    RuleBindingScope,
    RuleDecision,
    RuleDefinition,
    RuleEngine,
    RuleEvaluationContext,
    RuleLibrary,
    RuleMatch,
    RuleProgram,
    RuleStageDecision,
    RuleTrace,
    RuntimeRulesTrace,
    dump_rule_program,
    load_rule_program,
    starter_rule_program,
)
from .rules_resolver import RuleProgramResolutionInput, RuleProgramResolver, SQLAlchemyRuleProgramResolver
from .runtime_config import RuntimeSettings
from .stores import (
    ConversationStore,
    InMemoryConversationStore,
    InMemoryTraceStore,
    SQLAlchemyConversationStore,
    SQLAlchemyTraceStore,
    TraceStore,
)
from .tenant import TenantIdentityRepository, TenantIdentityRepositoryFactory, TenantScope

__all__ = [
    "ConversationKernel",
    "ConversationStore",
    "TraceStore",
    "InMemoryConversationStore",
    "InMemoryTraceStore",
    "SQLAlchemyConversationStore",
    "SQLAlchemyTraceStore",
    "SemanticInterpreter",
    "KeywordInterpreter",
    "User",
    "Organization",
    "OrganizationMembership",
    "AuthSession",
    "IdentityStore",
    "InMemoryIdentityStore",
    "SQLAlchemyIdentityStore",
    "AccessTokenClaims",
    "AuthError",
    "AuthenticationError",
    "AuthorizationError",
    "TokenExpiredError",
    "AuthenticatedPrincipal",
    "IssuedSession",
    "JWTCodec",
    "AuthService",
    "GemmaLocalInterpreter",
    "GemmaLocalRuntimeError",
    "build_gemma_local_interpreter",
    "LazyInterpreter",
    "AgentInterpreterRouter",
    "build_named_interpreter",
    "build_interpreter_router",
    "LiveKitAdapterConfig",
    "LiveKitPhoneAdapter",
    "LiveKitAgentsUnavailableError",
    "LiveKitTokenIssuer",
    "LiveKitVoiceTransportGrant",
    "LiveKitControlPlaneClient",
    "LiveKitDispatchClient",
    "LiveKitDispatchResult",
    "LiveKitWorkerDispatchContext",
    "RuhuLiveKitAgentWorker",
    "build_livekit_agent_server_app",
    "build_livekit_worker",
    "load_livekit_agents_sdk",
    "load_livekit_api_sdk",
    "FileAgentRegistry",
    "SQLAlchemyAgentRegistry",
    "interpreter_by_name",
    "load_agent_document",
    "load_agent_document_source",
    "load_transcript",
    "RuleBinding",
    "RuleBindingScope",
    "RuleDecision",
    "RuleDefinition",
    "RuleEngine",
    "RuleEvaluationContext",
    "RuleLibrary",
    "RuleMatch",
    "PendingRuleConfirmation",
    "RuleProgram",
    "RuleStageDecision",
    "RuleTrace",
    "RuntimeRulesTrace",
    "load_rule_program",
    "dump_rule_program",
    "starter_rule_program",
    "RuleProgramResolver",
    "RuleProgramResolutionInput",
    "SQLAlchemyRuleProgramResolver",
    "RuntimeSettings",
    "TenantScope",
    "TenantIdentityRepository",
    "TenantIdentityRepositoryFactory",
]

__all__.extend(
    [
        "build_default_app",
        "create_app",
        "AuthContextMiddleware",
        "AuthContextResolver",
        "RequestAuthContext",
        "extract_bearer_token",
        "get_request_auth_context",
        "require_authenticated_context",
        "PersistentAuthRuntime",
        "build_persistent_auth_runtime",
        "build_sqlalchemy_auth_runtime",
    ]
)

_LAZY_ATTRS = {
    "build_default_app": (".api", "build_default_app"),
    "create_app": (".api", "create_app"),
    "AuthContextMiddleware": (".api_auth", "AuthContextMiddleware"),
    "AuthContextResolver": (".api_auth", "AuthContextResolver"),
    "RequestAuthContext": (".api_auth", "RequestAuthContext"),
    "extract_bearer_token": (".api_auth", "extract_bearer_token"),
    "get_request_auth_context": (".api_auth", "get_request_auth_context"),
    "require_authenticated_context": (".api_auth", "require_authenticated_context"),
    "PersistentAuthRuntime": (".auth_runtime", "PersistentAuthRuntime"),
    "build_persistent_auth_runtime": (".auth_runtime", "build_persistent_auth_runtime"),
    "build_sqlalchemy_auth_runtime": (".auth_runtime", "build_sqlalchemy_auth_runtime"),
}


def __getattr__(name: str):
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
