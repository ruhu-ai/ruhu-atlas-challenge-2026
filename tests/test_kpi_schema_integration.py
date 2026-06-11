"""Integration tests for KPI schema layers.

Tests the full flow:
Request Schema → Domain Model → DB Model → Response Schema
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from ruhu.domain.kpi import GoalDefinition as GoalDefinitionDomain
from ruhu.models.kpi import CreateGoalRequest, GoalResponse
from ruhu.db_sqlmodel import GoalDefinition as GoalDefinitionRecord


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TestKPIDomainModel:
    """Test KPI domain model (business logic)."""

    def test_create_goal_definition(self):
        """Create a valid goal definition."""
        goal = GoalDefinitionDomain(
            organization_id="org_123",
            kind="agent",
            name="First-Call Resolution",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            baseline_value=72.0,
        )

        assert goal.definition_id is not None
        assert goal.organization_id == "org_123"
        assert goal.name == "First-Call Resolution"
        assert goal.status == "draft"

    def test_goal_validator_metric_key(self):
        """Metric key must be alphanumeric with dots."""
        with pytest.raises(ValueError):
            GoalDefinitionDomain(
                organization_id="org_123",
                kind="agent",
                name="Test",
                metric_key="invalid@key",  # Invalid characters
                metric_direction="higher_is_better",
                metric_unit="percent",
                target_value=85.0,
            )

    def test_goal_validator_target_positive(self):
        """Target value must be positive."""
        with pytest.raises(ValueError):
            GoalDefinitionDomain(
                organization_id="org_123",
                kind="agent",
                name="Test",
                metric_key="conversation.score",
                metric_direction="higher_is_better",
                metric_unit="percent",
                target_value=0,  # Must be > 0
            )

    def test_is_on_track_higher_is_better(self):
        """Test on-track check for higher-is-better goals."""
        goal = GoalDefinitionDomain(
            organization_id="org_123",
            kind="agent",
            name="Test",
            metric_key="conversation.score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=100.0,
        )

        # 90% of target or better → on track
        assert goal.is_on_track(90.0) is True
        assert goal.is_on_track(100.0) is True
        assert goal.is_on_track(120.0) is True

        # Below 90% of target → not on track
        assert goal.is_on_track(89.0) is False
        assert goal.is_on_track(50.0) is False

    def test_is_on_track_lower_is_better(self):
        """Test on-track check for lower-is-better goals."""
        goal = GoalDefinitionDomain(
            organization_id="org_123",
            kind="agent",
            name="Test",
            metric_key="conversation.cost",
            metric_direction="lower_is_better",
            metric_unit="usd",
            target_value=10.0,
        )

        # 110% of target or better (lower) → on track
        assert goal.is_on_track(11.0) is True
        assert goal.is_on_track(10.0) is True
        assert goal.is_on_track(5.0) is True

        # Above 110% of target (worse) → not on track
        assert goal.is_on_track(11.1) is False
        assert goal.is_on_track(20.0) is False


class TestKPIRequestSchema:
    """Test KPI request schemas (API input contracts)."""

    def test_create_goal_request_valid(self):
        """Valid request passes Pydantic validation."""
        req = CreateGoalRequest(
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            baseline_value=72.0,
        )

        assert req.name == "FCR"
        assert req.target_value == 85.0
        assert req.kind == "custom"  # Default

    def test_create_goal_request_coerces_types(self):
        """Request schema coerces types (HTTP boundary behavior)."""
        # String target_value is coerced to float
        req = CreateGoalRequest(
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value="85",  # String, should be coerced to float
        )

        assert isinstance(req.target_value, float)
        assert req.target_value == 85.0

    def test_create_goal_request_invalid_empty_name(self):
        """Empty name fails validation."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            CreateGoalRequest(
                name="",  # Empty
                metric_key="conversation.fcr_score",
                metric_direction="higher_is_better",
                metric_unit="percent",
                target_value=85.0,
            )

    def test_create_goal_request_invalid_metric_direction(self):
        """Invalid metric direction fails validation."""
        with pytest.raises(Exception):
            CreateGoalRequest(
                name="FCR",
                metric_key="conversation.fcr_score",
                metric_direction="unknown",  # Invalid enum
                metric_unit="percent",
                target_value=85.0,
            )


class TestKPIResponseSchema:
    """Test KPI response schemas (API output contracts)."""

    def test_goal_response_basic(self):
        """Create a goal response."""
        response = GoalResponse(
            definition_id="goal_123",
            organization_id="org_456",
            kind="agent",
            name="FCR",
            description="First-call resolution",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            baseline_value=72.0,
            status="active",
            tags=["important"],
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        assert response.definition_id == "goal_123"
        assert response.name == "FCR"
        assert response.metric_key == "conversation.fcr_score"

    def test_goal_response_computed_is_on_track(self):
        """Computed field: is_on_track."""
        response_on_track = GoalResponse(
            definition_id="goal_123",
            organization_id="org_456",
            kind="agent",
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=100.0,
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
            current_value=95.0,  # 95% of target → on track
            progress_pct=95.0,
        )

        assert response_on_track.is_on_track is True

        response_off_track = GoalResponse(
            definition_id="goal_456",
            organization_id="org_456",
            kind="agent",
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=100.0,
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
            current_value=80.0,  # 80% of target → not on track
            progress_pct=80.0,
        )

        assert response_off_track.is_on_track is False

    def test_goal_response_computed_days_active(self):
        """Computed field: days_active."""
        now = utcnow()
        response = GoalResponse(
            definition_id="goal_123",
            organization_id="org_456",
            kind="agent",
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            status="active",
            created_at=now,
            updated_at=now,
        )

        # Just created → 0 days active
        assert response.days_active == 0

        # Draft status → days_active is None
        response_draft = GoalResponse(
            definition_id="goal_456",
            organization_id="org_456",
            kind="agent",
            name="Draft Goal",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            status="draft",
            created_at=now,
            updated_at=now,
        )

        assert response_draft.days_active is None


class TestKPILayerConversions:
    """Test conversions between layers."""

    def test_request_to_domain(self):
        """Convert request to domain model."""
        req = CreateGoalRequest(
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            baseline_value=72.0,
            kind="agent",
        )

        # Simulate conversion (as done in kpi_api_production.py)
        domain = GoalDefinitionDomain(
            organization_id="org_123",
            kind=req.kind,  # type: ignore
            name=req.name,
            description=req.description,
            metric_key=req.metric_key,
            metric_direction=req.metric_direction,  # type: ignore
            metric_unit=req.metric_unit,  # type: ignore
            target_value=req.target_value,
            baseline_value=req.baseline_value,
            tags=req.tags,
        )

        assert domain.name == req.name
        assert domain.target_value == req.target_value
        assert domain.organization_id == "org_123"

    def test_domain_to_db(self):
        """Convert domain model to DB record."""
        domain = GoalDefinitionDomain(
            organization_id="org_123",
            kind="agent",
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
        )

        # Simulate conversion
        record = GoalDefinitionRecord(
            definition_id=domain.definition_id,
            organization_id=domain.organization_id,
            kind=domain.kind,
            name=domain.name,
            metric_key=domain.metric_key,
            metric_direction=domain.metric_direction,
            metric_unit=domain.metric_unit,
            target_value=domain.target_value,
            baseline_value=domain.baseline_value,
            status=domain.status,
            tags=domain.tags,
        )

        assert record.name == domain.name
        assert record.definition_id == domain.definition_id

    def test_db_to_domain(self):
        """Convert DB record back to domain model."""
        record = GoalDefinitionRecord(
            definition_id="goal_123",
            organization_id="org_456",
            kind="agent",
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            baseline_value=72.0,
            status="active",
        )

        # Simulate conversion
        domain = GoalDefinitionDomain(
            definition_id=record.definition_id,
            organization_id=record.organization_id,
            kind=record.kind,  # type: ignore
            name=record.name,
            metric_key=record.metric_key,
            metric_direction=record.metric_direction,  # type: ignore
            metric_unit=record.metric_unit,  # type: ignore
            target_value=record.target_value,
            baseline_value=record.baseline_value,
            status=record.status,  # type: ignore
            tags=record.tags or [],
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

        assert domain.name == record.name
        assert domain.organization_id == record.organization_id

    def test_domain_to_response(self):
        """Convert domain to response (add computed fields)."""
        domain = GoalDefinitionDomain(
            organization_id="org_123",
            kind="agent",
            name="FCR",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=100.0,
            baseline_value=72.0,
            status="active",
        )

        # Simulate conversion with computed fields. Mirror
        # kpi_api_production.domain_to_response: metric_key is required on
        # GoalResponse and must always propagate from the domain model.
        response = GoalResponse(
            definition_id=domain.definition_id,
            organization_id=domain.organization_id,
            kind=domain.kind,
            name=domain.name,
            metric_key=domain.metric_key,
            metric_direction=domain.metric_direction,
            metric_unit=domain.metric_unit,
            target_value=domain.target_value,
            baseline_value=domain.baseline_value,
            status=domain.status,
            tags=domain.tags,
            created_at=domain.created_at,
            updated_at=domain.updated_at,
            # Add computed fields from projection
            current_value=95.0,
            progress_pct=95.0,
            trend="up",
        )

        assert response.name == domain.name
        assert response.current_value == 95.0
        assert response.is_on_track is True


class TestKPIEndToEnd:
    """End-to-end schema tests."""

    def test_full_flow_request_to_response(self):
        """Full flow: Request → Domain → DB → Response."""
        # Step 1: Client sends request
        req = CreateGoalRequest(
            name="First-Call Resolution",
            description="Resolve issues on first contact",
            metric_key="conversation.fcr_score",
            metric_direction="higher_is_better",
            metric_unit="percent",
            target_value=85.0,
            baseline_value=72.0,
            kind="agent",
        )

        # Step 2: Request → Domain
        org_id = "org_acme"
        domain = GoalDefinitionDomain(
            organization_id=org_id,
            kind=req.kind,  # type: ignore
            name=req.name,
            description=req.description,
            metric_key=req.metric_key,
            metric_direction=req.metric_direction,  # type: ignore
            metric_unit=req.metric_unit,  # type: ignore
            target_value=req.target_value,
            baseline_value=req.baseline_value,
            tags=req.tags,
        )

        assert domain.name == "First-Call Resolution"

        # Step 3: Domain → DB
        db_record = GoalDefinitionRecord(
            definition_id=domain.definition_id,
            organization_id=domain.organization_id,
            kind=domain.kind,
            name=domain.name,
            description=domain.description,
            metric_key=domain.metric_key,
            metric_direction=domain.metric_direction,
            metric_unit=domain.metric_unit,
            target_value=domain.target_value,
            baseline_value=domain.baseline_value,
            status=domain.status,
            tags=domain.tags,
        )

        assert db_record.name == domain.name

        # Step 4: DB → Domain (simulate DB read)
        read_domain = GoalDefinitionDomain(
            definition_id=db_record.definition_id,
            organization_id=db_record.organization_id,
            kind=db_record.kind,  # type: ignore
            name=db_record.name,
            metric_key=db_record.metric_key,
            metric_direction=db_record.metric_direction,  # type: ignore
            metric_unit=db_record.metric_unit,  # type: ignore
            target_value=db_record.target_value,
            baseline_value=db_record.baseline_value,
            status=db_record.status,  # type: ignore
            created_at=db_record.created_at,
            updated_at=db_record.updated_at,
            tags=db_record.tags or [],
        )

        # Step 5: Domain → Response
        response = GoalResponse(
            definition_id=read_domain.definition_id,
            organization_id=read_domain.organization_id,
            kind=read_domain.kind,
            name=read_domain.name,
            description=read_domain.description,
            metric_key=read_domain.metric_key,
            metric_direction=read_domain.metric_direction,
            metric_unit=read_domain.metric_unit,
            target_value=read_domain.target_value,
            baseline_value=read_domain.baseline_value,
            status=read_domain.status,
            tags=read_domain.tags,
            created_at=read_domain.created_at,
            updated_at=read_domain.updated_at,
        )

        # Verify end-to-end
        assert response.name == "First-Call Resolution"
        assert response.target_value == 85.0
        assert response.organization_id == "org_acme"
