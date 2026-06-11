from __future__ import annotations

import functools
import logging
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .atlas_readiness_models import (
    AtlasReadinessCase,
    AtlasReadinessCaseSet,
    AtlasReadinessEvent,
    AtlasReadinessReport,
    AtlasReadinessRun,
    AtlasReadinessRunRequest,
    AtlasReadinessRunState,
    AtlasReadinessRunTerminal,
    AtlasReadinessScore,
    AtlasReadinessTrace,
    AtlasProviderInvocationMetadata,
    AtlasVoiceArtifact,
    new_atlas_readiness_event_id,
)
from .atlas_readiness_privacy import AtlasReadinessPrivacyScrubber
from .atlas_store import _advisory_lock_key
from .db_models import (
    AtlasReadinessCaseRecord,
    AtlasReadinessCaseSetRecord,
    AtlasReadinessApplyLockRecord,
    AtlasReadinessEventRecord,
    AtlasModelInvocationRecord,
    AtlasReadinessReportRecord,
    AtlasReadinessRunRecord,
    AtlasReadinessScoreRecord,
    AtlasReadinessTraceSnapshotRecord,
    AtlasVoiceArtifactRecord,
)

logger = logging.getLogger(__name__)

_TERMINAL_READINESS_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


class AtlasSystemScope:
    """Sentinel type: the caller deliberately requests cross-tenant access.

    F17 (docs/atlas/Atlas-Review-Remediation-Plan.md): ``organization_id`` is
    required on every scoped store method. Passing ``None`` — previously
    silently unscoped — is now a hard error; deliberate global access (system
    sweeps, internal jobs) must say so explicitly with ``ATLAS_SYSTEM_SCOPE``,
    so "this caller forgot" and "this caller intends global" are
    distinguishable in code review and at runtime.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return "ATLAS_SYSTEM_SCOPE"


ATLAS_SYSTEM_SCOPE = AtlasSystemScope()
_ORG_SCOPE_MISSING = object()


def _org_scoped(fn):
    """Enforce the required-organization_id contract on a store method.

    The wrapped method keeps its internal ``organization_id: str | None``
    semantics (``None`` → no tenant filter); this wrapper guarantees that the
    only way to reach ``None`` is the explicit ``ATLAS_SYSTEM_SCOPE`` sentinel.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, organization_id=_ORG_SCOPE_MISSING, **kwargs):
        if organization_id is _ORG_SCOPE_MISSING or organization_id is None:
            raise ValueError(
                f"{fn.__name__} requires organization_id; pass ATLAS_SYSTEM_SCOPE "
                "for deliberate cross-tenant access"
            )
        resolved = None if isinstance(organization_id, AtlasSystemScope) else organization_id
        return fn(self, *args, organization_id=resolved, **kwargs)

    return wrapper


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AtlasReadinessStore(Protocol):
    def create_run(self, run: AtlasReadinessRun) -> AtlasReadinessRun: ...
    def get_run(self, run_id: str, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessRun | None: ...
    def list_runs(
        self,
        *,
        organization_id: str | AtlasSystemScope,
        agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AtlasReadinessRun], int, bool]: ...
    def update_run(
        self,
        run_id: str,
        *,
        organization_id: str | AtlasSystemScope,
        state: AtlasReadinessRunState | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
        atlas_session_id: str | None = None,
        case_set_id: str | None = None,
        document_hash: str | None = None,
        policy_hash: str | None = None,
        provider_config_hash: str | None = None,
        blocker_codes: list[str] | None = None,
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> AtlasReadinessRun: ...
    def append_event(self, event: AtlasReadinessEvent, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessEvent: ...
    def list_events(
        self,
        run_id: str,
        *,
        organization_id: str | AtlasSystemScope,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[AtlasReadinessEvent], int, bool]: ...
    def save_case_set(
        self,
        case_set: AtlasReadinessCaseSet,
        *,
        run_id: str | None = None,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasReadinessCaseSet: ...
    def get_case_set(
        self,
        case_set_id: str,
        *,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasReadinessCaseSet | None: ...
    def save_trace(self, run_id: str, trace: AtlasReadinessTrace, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessTrace: ...
    def save_score(self, run_id: str, score: AtlasReadinessScore, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessScore: ...
    def save_report(
        self,
        report: AtlasReadinessReport,
        *,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasReadinessReport: ...
    def save_provider_invocation(
        self,
        run_id: str,
        metadata: AtlasProviderInvocationMetadata,
        *,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasProviderInvocationMetadata: ...
    def save_voice_artifact(
        self,
        artifact: AtlasVoiceArtifact,
        *,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasVoiceArtifact: ...
    def acquire_apply_lock(
        self,
        run_id: str,
        *,
        agent_id: str,
        draft_version_id: str,
        expires_at: datetime,
        organization_id: str | AtlasSystemScope,
    ) -> None: ...
    def release_apply_lock(self, run_id: str, *, organization_id: str | AtlasSystemScope) -> None: ...
    def get_report(
        self,
        run_id: str,
        *,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasReadinessReport | None: ...
    def latest_report_for_agent(
        self, agent_id: str, *, organization_id: str | AtlasSystemScope
    ) -> AtlasReadinessReport | None: ...
    def has_active_apply_lock(
        self, agent_id: str, draft_version_id: str, *, organization_id: str | AtlasSystemScope
    ) -> bool: ...
    def list_runs_in_states(
        self,
        states: list[str],
        *,
        updated_before: datetime | None = None,
        organization_id: str | AtlasSystemScope,
        limit: int = 200,
    ) -> list[AtlasReadinessRun]: ...


def _run_from_record(record: AtlasReadinessRunRecord) -> AtlasReadinessRun:
    return AtlasReadinessRun(
        run_id=record.run_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        agent_version_id=record.agent_version_id,
        atlas_session_id=record.atlas_session_id,
        scope=record.scope,  # type: ignore[arg-type]
        state=record.state,  # type: ignore[arg-type]
        provider_policy=record.provider_policy,  # type: ignore[arg-type]
        case_set_id=record.case_set_id,
        document_hash=record.document_hash,
        policy_hash=record.policy_hash,
        provider_config_hash=record.provider_config_hash,
        request=AtlasReadinessRunRequest.model_validate(record.request_json or {}),
        created_by_user_id=record.created_by_user_id,
        blocker_codes=list(record.blocker_codes_json or []),
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )


def _event_from_record(record: AtlasReadinessEventRecord) -> AtlasReadinessEvent:
    return AtlasReadinessEvent(
        event_id=record.event_id,
        run_id=record.run_id,
        sequence_number=record.sequence_number,
        type=record.event_type,
        payload=dict(record.payload_json or {}),
        created_at=record.created_at,
    )


def _case_set_from_record(record: AtlasReadinessCaseSetRecord) -> AtlasReadinessCaseSet:
    return AtlasReadinessCaseSet(
        case_set_id=record.case_set_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        seed=record.seed,
        provider_policy=record.provider_policy,  # type: ignore[arg-type]
        cases=[AtlasReadinessCase.model_validate(item) for item in (record.cases_json or [])],
        created_at=record.created_at,
    )


class SQLAlchemyAtlasReadinessStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._privacy = AtlasReadinessPrivacyScrubber()

    def _scrub_json(self, payload):
        return self._privacy.scrub(payload)

    def _next_event_sequence(self, session: Session, run_id: str) -> int:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            session.execute(select(func.pg_advisory_xact_lock(_advisory_lock_key(f"atlas-readiness-events:{run_id}"))))
        current = session.execute(
            select(func.coalesce(func.max(AtlasReadinessEventRecord.sequence_number), 0)).where(
                AtlasReadinessEventRecord.run_id == run_id
            )
        ).scalar_one()
        return int(current) + 1

    def create_run(self, run: AtlasReadinessRun) -> AtlasReadinessRun:
        with self._session_factory.begin() as session:
            session.add(
                AtlasReadinessRunRecord(
                    run_id=run.run_id,
                    organization_id=run.organization_id,
                    agent_id=run.agent_id,
                    agent_version_id=run.agent_version_id,
                    atlas_session_id=run.atlas_session_id,
                    scope=run.scope,
                    state=run.state,
                    provider_policy=run.provider_policy,
                    case_set_id=run.case_set_id,
                    document_hash=run.document_hash,
                    policy_hash=run.policy_hash,
                    provider_config_hash=run.provider_config_hash,
                    request_json=self._scrub_json(run.request.model_dump(mode="json")),
                    blocker_codes_json=run.blocker_codes,
                    error=run.error,
                    created_by_user_id=run.created_by_user_id,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                    completed_at=run.completed_at,
                )
            )
        return run

    @_org_scoped
    def get_run(self, run_id: str, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessRun | None:
        with self._session_factory() as session:
            record = session.get(AtlasReadinessRunRecord, run_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _run_from_record(record)

    @_org_scoped
    def list_runs_in_states(
        self,
        states: list[str],
        *,
        updated_before: datetime | None = None,
        organization_id: str | AtlasSystemScope,
        limit: int = 200,
    ) -> list[AtlasReadinessRun]:
        """Runs currently in one of ``states`` (optionally not touched since
        ``updated_before``) — used by the stuck-run / pause-TTL sweep (AR-4.4)."""
        if not states:
            return []
        with self._session_factory() as session:
            stmt = select(AtlasReadinessRunRecord).where(AtlasReadinessRunRecord.state.in_(states))
            if updated_before is not None:
                stmt = stmt.where(AtlasReadinessRunRecord.updated_at < updated_before)
            if organization_id is not None:
                stmt = stmt.where(AtlasReadinessRunRecord.organization_id == organization_id)
            rows = session.execute(
                stmt.order_by(AtlasReadinessRunRecord.updated_at.asc()).limit(limit)
            ).scalars().all()
            return [_run_from_record(record) for record in rows]

    @_org_scoped
    def list_runs(
        self,
        *,
        organization_id: str | AtlasSystemScope,
        agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AtlasReadinessRun], int, bool]:
        with self._session_factory() as session:
            filters = []
            if organization_id is not None:
                filters.append(AtlasReadinessRunRecord.organization_id == organization_id)
            if agent_id is not None:
                filters.append(AtlasReadinessRunRecord.agent_id == agent_id)
            count_stmt = select(func.count()).select_from(AtlasReadinessRunRecord)
            if filters:
                count_stmt = count_stmt.where(*filters)
            total_count = session.execute(count_stmt).scalar_one()
            stmt = select(AtlasReadinessRunRecord)
            if filters:
                stmt = stmt.where(*filters)
            rows = session.execute(
                stmt.order_by(AtlasReadinessRunRecord.created_at.desc()).offset(offset).limit(limit + 1)
            ).scalars().all()
            page = rows[:limit]
            return [_run_from_record(record) for record in page], int(total_count), len(rows) > limit

    @_org_scoped
    def update_run(
        self,
        run_id: str,
        *,
        organization_id: str | AtlasSystemScope,
        state: AtlasReadinessRunState | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
        atlas_session_id: str | None = None,
        case_set_id: str | None = None,
        document_hash: str | None = None,
        policy_hash: str | None = None,
        provider_config_hash: str | None = None,
        blocker_codes: list[str] | None = None,
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> AtlasReadinessRun:
        with self._session_factory.begin() as session:
            record = session.get(AtlasReadinessRunRecord, run_id)
            if record is None:
                raise KeyError(f"unknown atlas readiness run: {run_id}")
            if organization_id is not None and record.organization_id != organization_id:
                raise KeyError(f"unknown atlas readiness run: {run_id}")
            if (
                state is not None
                and state != record.state
                and record.state in _TERMINAL_READINESS_STATES
            ):
                # Terminal states are final: an in-flight transition that raced
                # past cancellation must not un-terminate the run.
                raise AtlasReadinessRunTerminal(
                    f"atlas readiness run {run_id} is terminal ({record.state}); "
                    f"refusing transition to {state}"
                )
            if state is not None:
                record.state = state
            if agent_id is not None:
                record.agent_id = agent_id
            if agent_version_id is not None:
                record.agent_version_id = agent_version_id
            if atlas_session_id is not None:
                record.atlas_session_id = atlas_session_id
            if case_set_id is not None:
                record.case_set_id = case_set_id
            if document_hash is not None:
                record.document_hash = document_hash
            if policy_hash is not None:
                record.policy_hash = policy_hash
            if provider_config_hash is not None:
                record.provider_config_hash = provider_config_hash
            if blocker_codes is not None:
                record.blocker_codes_json = blocker_codes
            if error is not None:
                record.error = error
            if completed_at is not None:
                record.completed_at = completed_at
            record.updated_at = _utcnow()
            session.flush()
            return _run_from_record(record)

    @_org_scoped
    def append_event(self, event: AtlasReadinessEvent, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessEvent:
        with self._session_factory.begin() as session:
            run = session.get(AtlasReadinessRunRecord, event.run_id)
            if run is None:
                raise KeyError(f"unknown atlas readiness run: {event.run_id}")
            if organization_id is not None and run.organization_id != organization_id:
                raise KeyError(f"unknown atlas readiness run: {event.run_id}")
            sequence_number = event.sequence_number
            if sequence_number <= 0:
                sequence_number = self._next_event_sequence(session, event.run_id)
            payload_json = self._scrub_json(event.payload)
            record = AtlasReadinessEventRecord(
                event_id=event.event_id or new_atlas_readiness_event_id(),
                organization_id=run.organization_id,
                run_id=event.run_id,
                sequence_number=sequence_number,
                event_type=event.type,
                payload_json=payload_json,
                created_at=event.created_at,
            )
            session.add(record)
            logger.info(
                "atlas_readiness_event",
                extra={
                    "run_id": event.run_id,
                    "organization_id": run.organization_id,
                    "event_type": event.type,
                    "sequence_number": sequence_number,
                    "payload": payload_json,
                },
            )
        return event.model_copy(update={"sequence_number": sequence_number})

    @_org_scoped
    def list_events(
        self,
        run_id: str,
        *,
        organization_id: str | AtlasSystemScope,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[AtlasReadinessEvent], int, bool]:
        with self._session_factory() as session:
            run = session.get(AtlasReadinessRunRecord, run_id)
            if run is None or (organization_id is not None and run.organization_id != organization_id):
                raise KeyError(f"unknown atlas readiness run: {run_id}")
            filters = [AtlasReadinessEventRecord.run_id == run_id]
            total_count = session.execute(
                select(func.count(AtlasReadinessEventRecord.event_id)).where(*filters)
            ).scalar_one()
            stmt = (
                select(AtlasReadinessEventRecord)
                .where(*filters)
                .order_by(AtlasReadinessEventRecord.sequence_number.asc())
                .limit(limit + 1)
            )
            if after_sequence is not None:
                stmt = stmt.where(AtlasReadinessEventRecord.sequence_number > after_sequence)
            rows = session.execute(stmt).scalars().all()
            page = rows[:limit]
            return [_event_from_record(row) for row in page], int(total_count), len(rows) > limit

    @_org_scoped
    def save_case_set(
        self,
        case_set: AtlasReadinessCaseSet,
        *,
        run_id: str | None = None,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasReadinessCaseSet:
        # F17: org consistency is enforced against the CALLER's scope, not only
        # the (possibly None) org on the payload — a None-org case set
        # previously collided silently with another tenant's record on ID reuse.
        if (
            organization_id is not None
            and case_set.organization_id is not None
            and case_set.organization_id != organization_id
        ):
            raise KeyError(f"unknown atlas readiness case set: {case_set.case_set_id}")
        row_organization_id = case_set.organization_id or organization_id
        with self._session_factory.begin() as session:
            existing = session.get(AtlasReadinessCaseSetRecord, case_set.case_set_id)
            if existing is not None:
                if organization_id is not None and existing.organization_id != organization_id:
                    raise KeyError(f"unknown atlas readiness case set: {case_set.case_set_id}")
                return _case_set_from_record(existing)
            session.add(
                AtlasReadinessCaseSetRecord(
                    case_set_id=case_set.case_set_id,
                    organization_id=row_organization_id,
                    agent_id=case_set.agent_id,
                    seed=case_set.seed,
                    provider_policy=case_set.provider_policy,
                    cases_json=self._scrub_json([case.model_dump(mode="json") for case in case_set.cases]),
                    created_at=case_set.created_at,
                )
            )
            session.flush()
            for case in case_set.cases:
                session.add(
                    AtlasReadinessCaseRecord(
                        readiness_case_id=f"atlas_readiness_case_{uuid4().hex}",
                        case_id=case.case_id,
                        case_set_id=case_set.case_set_id,
                        run_id=run_id,
                        organization_id=row_organization_id,
                        case_json=self._scrub_json(case.model_dump(mode="json")),
                        created_at=case_set.created_at,
                    )
                )
        return case_set

    @_org_scoped
    def get_case_set(self, case_set_id: str, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessCaseSet | None:
        with self._session_factory() as session:
            record = session.get(AtlasReadinessCaseSetRecord, case_set_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _case_set_from_record(record)

    @_org_scoped
    def save_trace(self, run_id: str, trace: AtlasReadinessTrace, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessTrace:
        with self._session_factory.begin() as session:
            run = session.get(AtlasReadinessRunRecord, run_id)
            if run is None or (organization_id is not None and run.organization_id != organization_id):
                raise KeyError(f"unknown atlas readiness run: {run_id}")
            trace_json = self._scrub_json(trace.model_dump(mode="json"))
            # Upsert: the trace is saved once after simulation and again after
            # the voice phase populates voice_metrics (AR-4.3), so a re-save of
            # the same snapshot id must update rather than collide.
            existing = session.get(AtlasReadinessTraceSnapshotRecord, trace.trace_id)
            if existing is not None:
                existing.case_id = trace.case_id
                existing.conversation_id = trace.conversation_id
                existing.trace_json = trace_json
            else:
                session.add(
                    AtlasReadinessTraceSnapshotRecord(
                        trace_snapshot_id=trace.trace_id,
                        run_id=run_id,
                        organization_id=run.organization_id,
                        case_id=trace.case_id,
                        conversation_id=trace.conversation_id,
                        trace_json=trace_json,
                        created_at=_utcnow(),
                    )
                )
        return trace

    @_org_scoped
    def save_score(self, run_id: str, score: AtlasReadinessScore, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessScore:
        with self._session_factory.begin() as session:
            run = session.get(AtlasReadinessRunRecord, run_id)
            if run is None or (organization_id is not None and run.organization_id != organization_id):
                raise KeyError(f"unknown atlas readiness run: {run_id}")
            session.add(
                AtlasReadinessScoreRecord(
                    score_id=f"atlas_readiness_score_{uuid4().hex}",
                    run_id=run_id,
                    organization_id=run.organization_id,
                    case_id=score.case_id,
                    passed=score.passed,
                    case_score=score.case_score,
                    score_json=self._scrub_json(score.model_dump(mode="json")),
                    created_at=_utcnow(),
                )
            )
        return score

    @_org_scoped
    def save_report(
        self,
        report: AtlasReadinessReport,
        *,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasReadinessReport:
        with self._session_factory.begin() as session:
            run = session.get(AtlasReadinessRunRecord, report.run_id)
            if run is None or (organization_id is not None and run.organization_id != organization_id):
                raise KeyError(f"unknown atlas readiness run: {report.run_id}")
            session.execute(delete(AtlasReadinessReportRecord).where(AtlasReadinessReportRecord.run_id == report.run_id))
            session.add(
                AtlasReadinessReportRecord(
                    report_id=f"atlas_readiness_report_{uuid4().hex}",
                    run_id=report.run_id,
                    organization_id=run.organization_id,
                    publish_recommendation=report.publish_recommendation,
                    report_json=self._scrub_json(report.model_dump(mode="json")),
                    created_at=_utcnow(),
                )
            )
        return report

    @_org_scoped
    def save_provider_invocation(
        self,
        run_id: str,
        metadata: AtlasProviderInvocationMetadata,
        *,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasProviderInvocationMetadata:
        with self._session_factory.begin() as session:
            run = session.get(AtlasReadinessRunRecord, run_id)
            if run is None or (organization_id is not None and run.organization_id != organization_id):
                raise KeyError(f"unknown atlas readiness run: {run_id}")
            session.add(
                AtlasModelInvocationRecord(
                    invocation_id=f"atlas_model_invocation_{uuid4().hex}",
                    run_id=run_id,
                    organization_id=run.organization_id,
                    provider=metadata.provider,
                    model=metadata.model,
                    role=metadata.role,
                    metadata_json=self._scrub_json(metadata.model_dump(mode="json")),
                    created_at=_utcnow(),
                )
            )
        return metadata

    @_org_scoped
    def save_voice_artifact(
        self,
        artifact: AtlasVoiceArtifact,
        *,
        organization_id: str | AtlasSystemScope,
    ) -> AtlasVoiceArtifact:
        with self._session_factory.begin() as session:
            run = session.get(AtlasReadinessRunRecord, artifact.run_id)
            if run is None or (organization_id is not None and run.organization_id != organization_id):
                raise KeyError(f"unknown atlas readiness run: {artifact.run_id}")
            session.add(
                AtlasVoiceArtifactRecord(
                    artifact_id=artifact.artifact_id,
                    run_id=artifact.run_id,
                    organization_id=run.organization_id,
                    case_id=artifact.case_id,
                    provider=artifact.provider,
                    artifact_type=artifact.artifact_type,
                    uri=artifact.uri,
                    metadata_json=self._scrub_json(artifact.metadata),
                    created_at=artifact.created_at,
                )
            )
        return artifact

    @_org_scoped
    def acquire_apply_lock(
        self,
        run_id: str,
        *,
        agent_id: str,
        draft_version_id: str,
        expires_at: datetime,
        organization_id: str | AtlasSystemScope,
    ) -> None:
        now = _utcnow()
        with self._session_factory.begin() as session:
            run = session.get(AtlasReadinessRunRecord, run_id)
            if run is None or (organization_id is not None and run.organization_id != organization_id):
                raise KeyError(f"unknown atlas readiness run: {run_id}")
            session.execute(
                delete(AtlasReadinessApplyLockRecord).where(
                    AtlasReadinessApplyLockRecord.agent_id == agent_id,
                    AtlasReadinessApplyLockRecord.draft_version_id == draft_version_id,
                    AtlasReadinessApplyLockRecord.expires_at <= now,
                )
            )
            session.add(
                AtlasReadinessApplyLockRecord(
                    lock_id=f"atlas_readiness_apply_lock_{uuid4().hex}",
                    run_id=run_id,
                    organization_id=run.organization_id,
                    agent_id=agent_id,
                    draft_version_id=draft_version_id,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                raise ValueError("readiness_apply_lock_conflict") from exc

    @_org_scoped
    def release_apply_lock(self, run_id: str, *, organization_id: str | AtlasSystemScope) -> None:
        with self._session_factory.begin() as session:
            stmt = delete(AtlasReadinessApplyLockRecord).where(AtlasReadinessApplyLockRecord.run_id == run_id)
            if organization_id is not None:
                stmt = stmt.where(AtlasReadinessApplyLockRecord.organization_id == organization_id)
            session.execute(stmt)

    @_org_scoped
    def get_report(self, run_id: str, *, organization_id: str | AtlasSystemScope) -> AtlasReadinessReport | None:
        with self._session_factory() as session:
            run = session.get(AtlasReadinessRunRecord, run_id)
            if run is None or (organization_id is not None and run.organization_id != organization_id):
                return None
            record = session.execute(
                select(AtlasReadinessReportRecord).where(AtlasReadinessReportRecord.run_id == run_id)
            ).scalar_one_or_none()
            if record is None:
                return None
            return AtlasReadinessReport.model_validate(record.report_json or {})

    @_org_scoped
    def latest_report_for_agent(
        self, agent_id: str, *, organization_id: str | AtlasSystemScope
    ) -> AtlasReadinessReport | None:
        """The most recent readiness report for an agent (AR-4.6), so publish
        review can surface the latest verdict."""
        with self._session_factory() as session:
            stmt = (
                select(AtlasReadinessReportRecord)
                .join(
                    AtlasReadinessRunRecord,
                    AtlasReadinessRunRecord.run_id == AtlasReadinessReportRecord.run_id,
                )
                .where(AtlasReadinessRunRecord.agent_id == agent_id)
                .order_by(AtlasReadinessRunRecord.created_at.desc())
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasReadinessRunRecord.organization_id == organization_id)
            record = session.execute(stmt).scalars().first()
            if record is None:
                return None
            return AtlasReadinessReport.model_validate(record.report_json or {})

    @_org_scoped
    def has_active_apply_lock(
        self, agent_id: str, draft_version_id: str, *, organization_id: str | AtlasSystemScope
    ) -> bool:
        """True if a readiness fix run holds an unexpired apply lock on the
        agent's draft (AR-4.6)."""
        now = _utcnow()
        with self._session_factory() as session:
            stmt = select(func.count()).select_from(AtlasReadinessApplyLockRecord).where(
                AtlasReadinessApplyLockRecord.agent_id == agent_id,
                AtlasReadinessApplyLockRecord.draft_version_id == draft_version_id,
                AtlasReadinessApplyLockRecord.expires_at > now,
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasReadinessApplyLockRecord.organization_id == organization_id)
            return session.execute(stmt).scalar_one() > 0
