"""KPI API schemas. Request/response contracts for KPI endpoints."""

from ruhu.models.kpi.requests import (
    CreateGoalRequest,
    UpdateGoalRequest,
    RecordObservationRequest,
)
from ruhu.models.kpi.responses import (
    GoalResponse,
    GoalListResponse,
    GoalExecutionResponse,
    ObservationListResponse,
)

__all__ = [
    "CreateGoalRequest",
    "UpdateGoalRequest",
    "RecordObservationRequest",
    "GoalResponse",
    "GoalListResponse",
    "GoalExecutionResponse",
    "ObservationListResponse",
]
