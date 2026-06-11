from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import AgentRecord, AgentVersionRecord
from .loader import load_agent_document_source
from .agent_document import AgentDocument
from .schemas import AgentVersionStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _version_record_to_agent_document(record: AgentVersionRecord) -> AgentDocument:
    payload = dict(record.agent_document_json or {})
    if not payload:
        raise ValueError(
            f"agent version {record.version_id!r} is missing canonical agent_document_json"
        )
    return AgentDocument.model_validate(payload)


@dataclass(frozen=True)
class AgentVersionSnapshot:
    agent_id: str
    name: str
    version_id: str
    version_number: int
    status: AgentVersionStatus
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    based_on_version_id: str | None
    is_current_draft: bool
    is_current_published: bool
    organization_id: str | None = None
    agent_document: AgentDocument | None = None


@dataclass(frozen=True)
class AgentRegistration:
    agent_id: str
    name: str
    created_at: datetime
    updated_at: datetime
    settings: dict[str, object]
    current_draft_version_id: str | None
    current_published_version_id: str | None
    organization_id: str | None = None
    is_widget_enabled: bool = False
    widget_mode: str = "multimodal"
    widget_config: dict[str, object] | None = None


@dataclass(frozen=True)
class _FileAgentEntry:
    agent_id: str
    name: str
    document: AgentDocument
    path: Path
    created_at: datetime
    updated_at: datetime

    @property
    def version_id(self) -> str:
        return f"file:{self.agent_id}"


class FileAgentRegistry:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._agents: dict[str, _FileAgentEntry] = {}
        self.reload()

    @property
    def root(self) -> Path:
        return self._root

    def reload(self) -> None:
        agents: dict[str, _FileAgentEntry] = {}
        if not self._root.exists():
            self._agents = agents
            return

        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
                continue
            document, agent_id, agent_name = load_agent_document_source(path)
            stats = path.stat()
            created_at = datetime.fromtimestamp(stats.st_ctime, tz=timezone.utc)
            updated_at = datetime.fromtimestamp(stats.st_mtime, tz=timezone.utc)
            entry = _FileAgentEntry(
                agent_id=agent_id,
                name=agent_name,
                document=document,
                path=path,
                created_at=created_at,
                updated_at=updated_at,
            )
            if entry.agent_id in agents:
                raise ValueError(f"duplicate agent id in registry: {entry.agent_id}")
            agents[entry.agent_id] = entry
        self._agents = agents

    def list_agents(self, *, organization_id: str | None = None) -> list[AgentRegistration]:
        if organization_id is not None:
            raise KeyError(f"unknown organization id: {organization_id}")
        return [
            AgentRegistration(
                agent_id=entry.agent_id,
                name=entry.name,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
                settings={},
                current_draft_version_id=None,
                current_published_version_id=entry.version_id,
                organization_id=None,
            )
            for entry in (self._agents[key] for key in sorted(self._agents))
        ]

    def get_agent_registration(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentRegistration:
        entry = self._get_entry(agent_id, organization_id=organization_id)
        return AgentRegistration(
            agent_id=entry.agent_id,
            name=entry.name,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            settings={},
            current_draft_version_id=None,
            current_published_version_id=entry.version_id,
            organization_id=None,
        )

    def get_agent_document(
        self,
        agent_id: str,
        *,
        target: AgentVersionStatus = "published",
        organization_id: str | None = None,
    ) -> AgentDocument:
        if target != "published":
            raise KeyError(f"agent {agent_id} has no {target} version")
        return self._get_entry(agent_id, organization_id=organization_id).document

    def list_versions(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AgentVersionSnapshot]:
        entry = self._get_entry(agent_id, organization_id=organization_id)
        return [self._snapshot_from_entry(entry)]

    def resolve_version_id(
        self,
        agent_id: str,
        *,
        target: AgentVersionStatus,
        organization_id: str | None = None,
    ) -> str:
        if target != "published":
            raise KeyError(f"agent {agent_id} has no {target} version")
        return self._get_entry(agent_id, organization_id=organization_id).version_id

    def get_version_snapshot(
        self,
        version_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentVersionSnapshot:
        if not version_id.startswith("file:"):
            raise KeyError(f"unknown agent version id: {version_id}")
        return self._snapshot_from_entry(
            self._get_entry(version_id.removeprefix("file:"), organization_id=organization_id)
        )

    def _get_entry(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> _FileAgentEntry:
        if organization_id is not None:
            raise KeyError(f"unknown agent id: {agent_id}")
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown agent id: {agent_id}") from exc

    @staticmethod
    def _snapshot_from_entry(entry: _FileAgentEntry) -> AgentVersionSnapshot:
        return AgentVersionSnapshot(
            agent_id=entry.agent_id,
            name=entry.name,
            version_id=entry.version_id,
            version_number=1,
            status="published",
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            published_at=entry.updated_at,
            based_on_version_id=None,
            is_current_draft=False,
            is_current_published=True,
            organization_id=None,
            agent_document=entry.document,
        )


class SQLAlchemyAgentRegistry:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def bootstrap_from_directory(
        self,
        root: str | Path,
        *,
        organization_id: str | None = None,
    ) -> None:
        for path in sorted(Path(root).rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
                continue
            document, agent_id, agent_name = load_agent_document_source(path)
            self.ensure_seeded_document(
                agent_id=agent_id,
                agent_name=agent_name,
                document=document,
                organization_id=organization_id,
            )

    def ensure_seeded_document(
        self,
        *,
        agent_id: str,
        agent_name: str,
        document: AgentDocument,
        organization_id: str | None = None,
    ) -> None:
        with self._session_factory.begin() as session:
            existing = session.get(AgentRecord, agent_id)
            if existing is not None:
                return

            now = _utcnow()
            published_id = str(uuid4())
            draft_id = str(uuid4())
            registration = AgentRecord(
                agent_id=agent_id,
                organization_id=organization_id,
                name=agent_name,
                settings_json={},
                current_draft_version_id=draft_id,
                current_published_version_id=published_id,
                created_at=now,
                updated_at=now,
            )
            session.add(registration)
            session.add(
                AgentVersionRecord(
                    version_id=published_id,
                    agent_id=agent_id,
                    organization_id=organization_id,
                    status="published",
                    version_number=1,
                    based_on_version_id=None,
                    agent_document_json=document.model_dump(mode="json"),
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                )
            )
            session.add(
                AgentVersionRecord(
                    version_id=draft_id,
                    agent_id=agent_id,
                    organization_id=organization_id,
                    status="draft",
                    version_number=2,
                    based_on_version_id=published_id,
                    agent_document_json=document.model_dump(mode="json"),
                    created_at=now,
                    updated_at=now,
                    published_at=None,
                )
            )

    def list_agents(self, *, organization_id: str | None = None) -> list[AgentRegistration]:
        statement: Select[tuple[AgentRecord]] = select(AgentRecord).order_by(AgentRecord.agent_id.asc())
        if organization_id is not None:
            statement = statement.where(
                or_(
                    AgentRecord.organization_id == organization_id,
                    AgentRecord.organization_id.is_(None),
                )
            )
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [
            AgentRegistration(
                agent_id=record.agent_id,
                name=record.name,
                created_at=record.created_at,
                updated_at=record.updated_at,
                settings=dict(record.settings_json or {}),
                current_draft_version_id=record.current_draft_version_id,
                current_published_version_id=record.current_published_version_id,
                organization_id=record.organization_id,
                is_widget_enabled=getattr(record, "is_widget_enabled", False),
                widget_mode=getattr(record, "widget_mode", "multimodal"),
                widget_config=dict(getattr(record, "widget_config", None) or {}),
            )
            for record in records
        ]

    def get_agent_registration(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentRegistration:
        with self._session_factory() as session:
            record = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            return AgentRegistration(
                agent_id=record.agent_id,
                name=record.name,
                created_at=record.created_at,
                updated_at=record.updated_at,
                settings=dict(record.settings_json or {}),
                current_draft_version_id=record.current_draft_version_id,
                current_published_version_id=record.current_published_version_id,
                organization_id=record.organization_id,
                is_widget_enabled=getattr(record, "is_widget_enabled", False),
                widget_mode=getattr(record, "widget_mode", "multimodal"),
                widget_config=dict(getattr(record, "widget_config", None) or {}),
            )

    def list_versions(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AgentVersionSnapshot]:
        with self._session_factory() as session:
            agent_record = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            statement = (
                select(AgentVersionRecord)
                .where(AgentVersionRecord.agent_id == agent_id)
                .order_by(AgentVersionRecord.version_number.asc())
            )
            versions = session.execute(statement).scalars().all()
            return [self._snapshot_from_record(record, agent_record) for record in versions]

    def get_version_snapshot(
        self,
        version_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentVersionSnapshot:
        with self._session_factory() as session:
            record = self._get_scoped_version(session, version_id, organization_id=organization_id)
            agent_record = self._get_scoped_agent(session, record.agent_id, organization_id=organization_id)
            return self._snapshot_from_record(record, agent_record)

    def create_agent_document(
        self,
        *,
        agent_id: str,
        agent_name: str,
        document: AgentDocument,
        settings: dict[str, object] | None = None,
        organization_id: str | None = None,
    ) -> AgentVersionSnapshot:
        now = _utcnow()
        version_id = str(uuid4())
        with self._session_factory.begin() as session:
            if session.get(AgentRecord, agent_id) is not None:
                raise ValueError(f"agent already exists: {agent_id}")
            registration = AgentRecord(
                agent_id=agent_id,
                organization_id=organization_id,
                name=agent_name,
                settings_json=dict(settings or {}),
                current_draft_version_id=version_id,
                current_published_version_id=None,
                created_at=now,
                updated_at=now,
            )
            session.add(registration)
            record = AgentVersionRecord(
                version_id=version_id,
                agent_id=agent_id,
                organization_id=organization_id,
                status="draft",
                version_number=1,
                based_on_version_id=None,
                agent_document_json=document.model_dump(mode="json"),
                created_at=now,
                updated_at=now,
                published_at=None,
            )
            session.add(record)
        return AgentVersionSnapshot(
            agent_id=agent_id,
            name=agent_name,
            version_id=version_id,
            version_number=1,
            status="draft",
            agent_document=document,
            created_at=now,
            updated_at=now,
            published_at=None,
            based_on_version_id=None,
            is_current_draft=True,
            is_current_published=False,
            organization_id=organization_id,
        )

    def delete_agent(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> None:
        # Inline imports to avoid circular dependency at module load time.
        from .db_models import SupportCaseRecord
        from .kpi.sqlalchemy_models import KPIGoalRecord, KPIMetricScopeRecord
        from .rules_sqlalchemy_models import RuleBindingRecord

        with self._session_factory.begin() as session:
            record = self._get_scoped_agent(session, agent_id, organization_id=organization_id)

            # ── 1. Abandon KPI goals scoped to this agent ────────────────────
            # Scopes can't be deleted (RESTRICT FK from observations/baselines),
            # so we mark the goals themselves as "abandoned". Goals already in a
            # terminal state (completed/abandoned) are left as-is.
            scope_q = session.query(KPIMetricScopeRecord.scope_id).filter(
                or_(
                    KPIMetricScopeRecord.agent_id == agent_id,
                    KPIMetricScopeRecord.workflow_id == agent_id,
                )
            )
            if organization_id is not None:
                scope_q = scope_q.filter(
                    KPIMetricScopeRecord.organization_id == organization_id
                )
            scope_ids = [row[0] for row in scope_q]
            if scope_ids:
                session.query(KPIGoalRecord).filter(
                    KPIGoalRecord.scope_id.in_(scope_ids),
                    KPIGoalRecord.status.notin_(["completed", "abandoned"]),
                ).update({"status": "abandoned", "updated_at": _utcnow()}, synchronize_session=False)

            # ── 2. Null owning_agent_id on support cases ──────────────────────
            case_q = session.query(SupportCaseRecord).filter(
                SupportCaseRecord.owning_agent_id == agent_id
            )
            if organization_id is not None:
                case_q = case_q.filter(
                    SupportCaseRecord.organization_id == organization_id
                )
            case_q.update({"owning_agent_id": None}, synchronize_session=False)

            # ── 3. Clean up rule_bindings.agent_ids arrays ────────────────────
            # Rule bindings store agent references in a legacy string array.
            # After removing the deleted agent from that array:
            #   - Delete the binding if the array becomes empty AND no other
            #     scope discriminators remain (channels, step_ids, tool_refs,
            #     event_types) — it was agent-only scoped and is now scope-less,
            #     which would incorrectly match every agent.
            #   - Otherwise keep the binding with the deleted agent removed.
            affected_bindings = (
                session.query(RuleBindingRecord)
                .filter(RuleBindingRecord.agent_ids.any(agent_id))
                .all()
            )
            for binding in affected_bindings:
                remaining_agent_ids = [gid for gid in (binding.agent_ids or []) if gid != agent_id]
                if (
                    not remaining_agent_ids
                    and not binding.channels
                    and not binding.step_ids
                    and not binding.tool_refs
                    and not binding.event_types
                ):
                    session.delete(binding)
                else:
                    binding.agent_ids = remaining_agent_ids

            session.delete(record)

    def update_agent_settings(
        self,
        agent_id: str,
        settings: dict[str, object],
        *,
        organization_id: str | None = None,
    ) -> AgentRegistration:
        now = _utcnow()
        with self._session_factory.begin() as session:
            record = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            # Enterprise posture: reject settings mutations on tenant-less
            # agents. These are typically legacy/system workflow exports that should
            # be treated as read-only templates.  Users should clone them
            # into their own org first.
            if record.organization_id is None:
                raise PermissionError(
                    f"agent {agent_id!r} is not tenant-scoped and cannot be edited directly. "
                    "Clone it into your organization first (create-from-template)."
                )
            record.settings_json = dict(settings)
            record.updated_at = now
            return AgentRegistration(
                agent_id=record.agent_id,
                name=record.name,
                created_at=record.created_at,
                updated_at=record.updated_at,
                settings=dict(record.settings_json or {}),
                current_draft_version_id=record.current_draft_version_id,
                current_published_version_id=record.current_published_version_id,
                organization_id=record.organization_id,
                is_widget_enabled=getattr(record, "is_widget_enabled", False),
                widget_mode=getattr(record, "widget_mode", "multimodal"),
                widget_config=dict(getattr(record, "widget_config", None) or {}),
            )

    def update_agent_name(
        self,
        agent_id: str,
        name: str,
        *,
        organization_id: str | None = None,
    ) -> AgentRegistration:
        now = _utcnow()
        with self._session_factory.begin() as session:
            record = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            if record.organization_id is None:
                raise PermissionError(
                    f"agent {agent_id!r} is not tenant-scoped and cannot be edited directly. "
                    "Clone it into your organization first (create-from-template)."
                )
            record.name = name
            record.updated_at = now
            return AgentRegistration(
                agent_id=record.agent_id,
                name=record.name,
                created_at=record.created_at,
                updated_at=record.updated_at,
                settings=dict(record.settings_json or {}),
                current_draft_version_id=record.current_draft_version_id,
                current_published_version_id=record.current_published_version_id,
                organization_id=record.organization_id,
                is_widget_enabled=getattr(record, "is_widget_enabled", False),
                widget_mode=getattr(record, "widget_mode", "multimodal"),
                widget_config=dict(getattr(record, "widget_config", None) or {}),
            )

    def get_agent_document(
        self,
        agent_id: str,
        *,
        target: AgentVersionStatus = "draft",
        organization_id: str | None = None,
    ) -> AgentDocument:
        version_id = self.resolve_version_id(agent_id, target=target, organization_id=organization_id)
        with self._session_factory() as session:
            record = self._get_scoped_version(session, version_id, organization_id=organization_id)
            return _version_record_to_agent_document(record)

    def update_draft_agent_document(
        self,
        agent_id: str,
        document: AgentDocument,
        *,
        organization_id: str | None = None,
    ) -> AgentDocument:
        now = _utcnow()
        with self._session_factory.begin() as session:
            registration = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            if registration.organization_id is None:
                raise PermissionError(
                    f"agent {agent_id!r} is not tenant-scoped and cannot be edited directly. "
                    "Clone it into your organization first (create-from-template)."
                )
            if registration.current_draft_version_id is None:
                raise ValueError("agent has no editable draft")
            record = self._get_scoped_version(
                session,
                registration.current_draft_version_id,
                organization_id=organization_id,
            )
            record.agent_document_json = document.model_dump(mode="json")
            record.updated_at = now
            registration.updated_at = now
            return document

    def create_draft(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
        source_version_id: str | None = None,
    ) -> AgentVersionSnapshot:
        now = _utcnow()
        with self._session_factory.begin() as session:
            registration = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            if registration.current_draft_version_id is not None:
                record = self._get_scoped_version(
                    session,
                    registration.current_draft_version_id,
                    organization_id=organization_id,
                )
                return self._snapshot_from_record(record, registration)

            source_record: AgentVersionRecord | None = None
            if source_version_id is not None:
                source_record = self._get_scoped_version(session, source_version_id, organization_id=organization_id)
                if source_record.agent_id != agent_id:
                    raise ValueError("source version does not belong to requested agent")
            elif registration.current_published_version_id is not None:
                source_record = self._get_scoped_version(
                    session,
                    registration.current_published_version_id,
                    organization_id=organization_id,
                )

            if source_record is None:
                raise ValueError("agent has no published version to draft from")

            version_number = self._next_version_number(session, agent_id)
            version_id = str(uuid4())
            source_document = _version_record_to_agent_document(source_record)
            record = AgentVersionRecord(
                version_id=version_id,
                agent_id=agent_id,
                organization_id=organization_id,
                status="draft",
                version_number=version_number,
                based_on_version_id=source_record.version_id,
                agent_document_json=source_document.model_dump(mode="json"),
                created_at=now,
                updated_at=now,
                published_at=None,
            )
            session.add(record)
            registration.current_draft_version_id = version_id
            registration.updated_at = now
            return self._snapshot_from_record(record, registration)

    def publish(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentVersionSnapshot:
        now = _utcnow()
        with self._session_factory.begin() as session:
            registration = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            if registration.current_draft_version_id is None:
                raise ValueError("agent has no draft to publish")
            record = self._get_scoped_version(
                session,
                registration.current_draft_version_id,
                organization_id=organization_id,
            )
            # Promote the draft to published.
            document = _version_record_to_agent_document(record)
            record.status = "published"
            record.updated_at = now
            record.published_at = now
            record.agent_document_json = document.model_dump(mode="json")
            registration.current_published_version_id = record.version_id
            registration.updated_at = now
            # Create a new draft based on the freshly published version so the
            # agent remains editable. This mirrors the seeding pattern where
            # agents always carry both a published and a draft version.
            next_draft_id = str(uuid4())
            next_version_number = self._next_version_number(session, agent_id)
            session.add(
                AgentVersionRecord(
                    version_id=next_draft_id,
                    agent_id=agent_id,
                    organization_id=organization_id,
                    status="draft",
                    version_number=next_version_number,
                    based_on_version_id=record.version_id,
                    agent_document_json=document.model_dump(mode="json"),
                    created_at=now,
                    updated_at=now,
                    published_at=None,
                )
            )
            registration.current_draft_version_id = next_draft_id
            return self._snapshot_from_record(record, registration)

    def unpublish(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentVersionSnapshot:
        """Remove the published version, reverting the agent to draft-only state."""
        now = _utcnow()
        with self._session_factory.begin() as session:
            registration = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            if registration.current_published_version_id is None:
                raise ValueError("agent has no published version to unpublish")
            registration.current_published_version_id = None
            registration.updated_at = now
            # Ensure a draft exists to return.
            if registration.current_draft_version_id is None:
                raise ValueError("agent has no draft version")
            draft = self._get_scoped_version(
                session,
                registration.current_draft_version_id,
                organization_id=organization_id,
            )
            return self._snapshot_from_record(draft, registration)

    def resolve_version_id(
        self,
        agent_id: str,
        *,
        target: AgentVersionStatus,
        organization_id: str | None = None,
    ) -> str:
        with self._session_factory() as session:
            registration = self._get_scoped_agent(session, agent_id, organization_id=organization_id)
            version_id = (
                registration.current_draft_version_id
                if target == "draft"
                else registration.current_published_version_id
            )
            if version_id is None:
                raise KeyError(f"agent {agent_id} has no {target} version")
            return version_id

    def _get_scoped_agent(
        self,
        session: Session,
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentRecord:
        record = session.get(AgentRecord, agent_id)
        if record is None:
            raise KeyError(f"unknown agent id: {agent_id}")
        if (
            organization_id is not None
            and record.organization_id is not None
            and record.organization_id != organization_id
        ):
            raise KeyError(f"unknown agent id: {agent_id}")
        return record

    def _get_scoped_version(
        self,
        session: Session,
        version_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentVersionRecord:
        record = session.get(AgentVersionRecord, version_id)
        if record is None:
            raise KeyError(f"unknown agent version id: {version_id}")
        if (
            organization_id is not None
            and record.organization_id is not None
            and record.organization_id != organization_id
        ):
            raise KeyError(f"unknown agent version id: {version_id}")
        return record

    def _next_version_number(self, session: Session, agent_id: str) -> int:
        current = session.scalar(
            select(func.max(AgentVersionRecord.version_number)).where(AgentVersionRecord.agent_id == agent_id)
        )
        return int(current or 0) + 1

    def _snapshot_from_record(
        self,
        record: AgentVersionRecord,
        registration: AgentRecord,
    ) -> AgentVersionSnapshot:
        document = _version_record_to_agent_document(record)
        return AgentVersionSnapshot(
            agent_id=record.agent_id,
            name=registration.name,
            version_id=record.version_id,
            version_number=record.version_number,
            status=record.status,  # type: ignore[arg-type]
            agent_document=document,
            created_at=record.created_at,
            updated_at=record.updated_at,
            published_at=record.published_at,
            based_on_version_id=record.based_on_version_id,
            is_current_draft=registration.current_draft_version_id == record.version_id,
            is_current_published=registration.current_published_version_id == record.version_id,
            organization_id=record.organization_id,
        )
