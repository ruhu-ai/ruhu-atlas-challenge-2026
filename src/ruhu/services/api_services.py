"""``ApiServices`` — the API host's service bundle (RP-3.1 step 18 / RP-3.2 finale).

Everything ``ruhu.api.build_default_app`` constructs *beyond* the
:class:`ruhu.composition.ComposedRuntime` lands here: API-facing runtimes
(KPI, intent-tags), persistence-backed services (ticketing, provider costs,
phone numbers, journeys, simulation/evaluation), the agent-template store,
the email sender, and the auth-runtime products.  ``create_app(runtime,
services, *, settings, ...)`` consumes the bundle; the old 26-parameter
``create_app`` signature is retired.

Every field defaults to ``None`` so direct ``create_app`` callers (tests,
the OpenAPI export) can construct a sparse bundle — ``create_app`` keeps the
same in-memory fallbacks (``InMemoryNotificationStore``,
``InMemorySimulationFixtureStore``, locally-built journey/evaluation
runtimes, ...) it always had for omitted services.

All type imports are ``TYPE_CHECKING``-only: this module is imported by
``ruhu.api`` before most of the runtime packages, and a frozen dataclass
needs no runtime annotations (PEP 563 — ``from __future__ import
annotations``).  ``template_store`` is typed ``Any`` because
``SQLAlchemyAgentTemplateStore`` is defined in ``ruhu.api`` itself — a real
import either way (runtime or TYPE_CHECKING) would create the api ↔
services cycle this package exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from ..api_auth import AuthContextResolver
    from ..auth import AuthService
    from ..email_transport import EmailSender
    from ..identity import IdentityStore
    from ..analytics_tagging import IntentTagsRuntime
    from ..journeys import (
        JourneyRuntime,
        JourneyService,
        SQLAlchemyJourneyDefinitionStore,
        SQLAlchemyJourneyInstanceStore,
        SQLAlchemyJourneyRuntimeJobStore,
    )
    from ..kpi import KPIRuntime
    from ..notifications.store import (
        InMemoryNotificationStore,
        SQLAlchemyNotificationStore,
    )
    from ..phone_number_audit import PhoneNumberAuditService
    from ..phone_number_operations import PhoneNumberOperationsService
    from ..phone_number_registry import PhoneNumberRegistryService
    from ..provider_costs import SQLAlchemyProviderCostStore
    from ..simulation_eval import (
        EvaluationRunStore,
        EvaluationRuntime,
        EvaluationService,
        SimulationFixtureStore,
    )
    from ..tenant import TenantIdentityRepositoryFactory
    from ..ticket_system import TicketSystemService


@dataclass(frozen=True)
class ApiServices:
    """Frozen bundle of API-process services threaded into ``create_app``."""

    # API-facing runtimes
    kpi_runtime: KPIRuntime | None = None
    intent_tags_runtime: IntentTagsRuntime | None = None

    # Notification + ticketing + provider costs
    notification_store: InMemoryNotificationStore | SQLAlchemyNotificationStore | None = None
    ticket_system_service: TicketSystemService | None = None
    provider_cost_store: SQLAlchemyProviderCostStore | None = None

    # Phone-number registry surface
    phone_number_registry: PhoneNumberRegistryService | None = None
    phone_number_audit_service: PhoneNumberAuditService | None = None
    phone_number_operations_service: PhoneNumberOperationsService | None = None

    # Journeys
    journey_definition_store: SQLAlchemyJourneyDefinitionStore | None = None
    journey_instance_store: SQLAlchemyJourneyInstanceStore | None = None
    journey_runtime_job_store: SQLAlchemyJourneyRuntimeJobStore | None = None
    journey_service: JourneyService | None = None
    journey_runtime: JourneyRuntime | None = None

    # Simulation fixtures + evaluation
    simulation_fixture_store: SimulationFixtureStore | None = None
    evaluation_run_store: EvaluationRunStore | None = None
    evaluation_service: EvaluationService | None = None
    evaluation_runtime: EvaluationRuntime | None = None

    # Agent templates — ruhu.api.SQLAlchemyAgentTemplateStore (see module
    # docstring for why this is ``Any``).
    template_store: Any | None = None

    # Email
    email_sender: EmailSender | None = None

    # Auth-runtime products
    auth_resolver: AuthContextResolver | None = None
    auth_service: AuthService | None = None
    identity_store: IdentityStore | None = None
    tenant_identity_repositories: TenantIdentityRepositoryFactory | None = None
    auth_session_factory: sessionmaker | None = None
