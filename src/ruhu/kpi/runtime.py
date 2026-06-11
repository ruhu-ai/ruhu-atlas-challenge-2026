from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from ..registry import SQLAlchemyAgentRegistry
from .analysis import SQLAlchemyKPIInsightAnalyzer
from .execution import KPIExecutionAdapterRegistry, TemplateValidationAdapter
from .measurement import SQLAlchemyKPIMeasurementService
from .read_models import GoalDetailReadModel, GoalSummaryReadModel
from .service import KPIReadService, KPIService
from .store import SQLAlchemyKPIStore


@dataclass(slots=True)
class KPIRuntime:
    store: SQLAlchemyKPIStore
    service: KPIService
    read_service: KPIReadService
    measurement_service: SQLAlchemyKPIMeasurementService
    insight_analyzer: SQLAlchemyKPIInsightAnalyzer
    execution_registry: KPIExecutionAdapterRegistry


def build_kpi_runtime(
    *,
    session_factory: sessionmaker[Session],
    agent_registry: SQLAlchemyAgentRegistry,
) -> KPIRuntime:
    store = SQLAlchemyKPIStore(session_factory)
    execution_registry = KPIExecutionAdapterRegistry(adapters=[TemplateValidationAdapter()])
    service = KPIService(store, execution_registry=execution_registry)
    read_service = KPIReadService(store)
    measurement_service = SQLAlchemyKPIMeasurementService(
        session_factory=session_factory,
        agent_registry=agent_registry,
        kpi_service=service,
    )
    insight_analyzer = SQLAlchemyKPIInsightAnalyzer(
        session_factory=session_factory,
        measurement_service=measurement_service,
        kpi_service=service,
    )
    return KPIRuntime(
        store=store,
        service=service,
        read_service=read_service,
        measurement_service=measurement_service,
        insight_analyzer=insight_analyzer,
        execution_registry=execution_registry,
    )
