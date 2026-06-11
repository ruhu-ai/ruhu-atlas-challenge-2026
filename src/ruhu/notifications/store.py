from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .models import NotificationCreate, NotificationRecord, _new_notification_id, _utc_now
from .sqlalchemy_models import NotificationORM


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class NotificationStore(Protocol):
    def create(self, record: NotificationCreate) -> NotificationRecord: ...

    def list_for_user(
        self,
        organization_id: str,
        user_id: str,
        *,
        limit: int = 20,
        unread_only: bool = False,
        include_expired: bool = False,
    ) -> list[NotificationRecord]: ...

    def count_unread(
        self,
        organization_id: str,
        user_id: str,
    ) -> int: ...

    def mark_read(
        self,
        notification_id: str,
        organization_id: str,
        user_id: str,
    ) -> bool: ...

    def mark_all_read(
        self,
        organization_id: str,
        user_id: str,
    ) -> int: ...

    def dismiss(
        self,
        notification_id: str,
        organization_id: str,
        user_id: str,
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Visibility helper (identical logic in both implementations)
# ---------------------------------------------------------------------------

def _is_visible(
    record: NotificationRecord,
    organization_id: str,
    user_id: str,
    *,
    include_expired: bool,
) -> bool:
    if record.organization_id != organization_id:
        return False
    if record.user_id is not None and record.user_id != user_id:
        return False
    if record.dismissed_at is not None:
        return False
    if not include_expired and record.expires_at is not None:
        now = _utc_now()
        exp = record.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            return False
    return True


def _can_mutate(
    record: NotificationRecord,
    organization_id: str,
    user_id: str,
) -> bool:
    if record.organization_id != organization_id:
        return False
    return record.user_id is None or record.user_id == user_id


# ---------------------------------------------------------------------------
# In-memory implementation (tests only)
# ---------------------------------------------------------------------------

class InMemoryNotificationStore:
    def __init__(self) -> None:
        self._records: list[NotificationRecord] = []

    def create(self, spec: NotificationCreate) -> NotificationRecord:
        now = _utc_now()
        expires_at: datetime | None = None
        if spec.expires_after_hours is not None:
            from datetime import timedelta
            expires_at = now + timedelta(hours=spec.expires_after_hours)

        record = NotificationRecord(
            notification_id=_new_notification_id(),
            organization_id=spec.organization_id,
            user_id=spec.user_id,
            category=spec.category,
            level=spec.level,
            urgency=spec.urgency,
            title=spec.title,
            message=spec.message,
            url=spec.url,
            url_label=spec.url_label,
            source_type=spec.source_type,
            source_id=spec.source_id,
            payload=deepcopy(spec.payload),
            read_at=None,
            dismissed_at=None,
            expires_at=expires_at,
            created_at=now,
        )
        self._records.append(record)
        return record

    def list_for_user(
        self,
        organization_id: str,
        user_id: str,
        *,
        limit: int = 20,
        unread_only: bool = False,
        include_expired: bool = False,
    ) -> list[NotificationRecord]:
        results = [
            r for r in self._records
            if _is_visible(r, organization_id, user_id, include_expired=include_expired)
            and (not unread_only or r.read_at is None)
        ]
        results.sort(key=lambda r: r.created_at, reverse=True)
        return results[:limit]

    def count_unread(self, organization_id: str, user_id: str) -> int:
        return sum(
            1 for r in self._records
            if _is_visible(r, organization_id, user_id, include_expired=False)
            and r.read_at is None
        )

    def mark_read(self, notification_id: str, organization_id: str, user_id: str) -> bool:
        for i, r in enumerate(self._records):
            if r.notification_id == notification_id and _can_mutate(r, organization_id, user_id):
                if r.dismissed_at is not None:
                    return False
                if r.read_at is not None:
                    return False
                self._records[i] = r.model_copy(update={"read_at": _utc_now()})
                return True
        return False

    def mark_all_read(self, organization_id: str, user_id: str) -> int:
        now = _utc_now()
        count = 0
        for i, r in enumerate(self._records):
            if (
                _is_visible(r, organization_id, user_id, include_expired=False)
                and r.read_at is None
            ):
                self._records[i] = r.model_copy(update={"read_at": now})
                count += 1
        return count

    def dismiss(self, notification_id: str, organization_id: str, user_id: str) -> bool:
        for i, r in enumerate(self._records):
            if r.notification_id == notification_id and _can_mutate(r, organization_id, user_id):
                if r.dismissed_at is not None:
                    return False
                self._records[i] = r.model_copy(update={"dismissed_at": _utc_now()})
                return True
        return False


# ---------------------------------------------------------------------------
# SQLAlchemy implementation
# ---------------------------------------------------------------------------

def _orm_to_record(row: NotificationORM) -> NotificationRecord:
    def _as_dt(v: object) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        return None

    return NotificationRecord(
        notification_id=row.notification_id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        category=row.category,
        level=row.level,
        urgency=row.urgency,
        title=row.title,
        message=row.message,
        url=row.url,
        url_label=row.url_label,
        source_type=row.source_type,
        source_id=row.source_id,
        payload=dict(row.payload) if row.payload else {},
        read_at=_as_dt(row.read_at),
        dismissed_at=_as_dt(row.dismissed_at),
        expires_at=_as_dt(row.expires_at),
        created_at=_as_dt(row.created_at) or _utc_now(),
    )


class SQLAlchemyNotificationStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def _session(self) -> Session:
        return self._session_factory()

    def create(self, spec: NotificationCreate) -> NotificationRecord:
        now = _utc_now()
        expires_at: datetime | None = None
        if spec.expires_after_hours is not None:
            from datetime import timedelta
            expires_at = now + timedelta(hours=spec.expires_after_hours)

        row = NotificationORM(
            notification_id=_new_notification_id(),
            organization_id=spec.organization_id,
            user_id=spec.user_id,
            category=spec.category,
            level=spec.level,
            urgency=spec.urgency,
            title=spec.title,
            message=spec.message,
            url=spec.url,
            url_label=spec.url_label,
            source_type=spec.source_type,
            source_id=spec.source_id,
            payload=spec.payload,
            read_at=None,
            dismissed_at=None,
            expires_at=expires_at,
            created_at=now,
        )
        with self._session() as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            return _orm_to_record(row)

    def _visibility_clause(self, organization_id: str, user_id: str, *, include_expired: bool):
        now = _utc_now()
        clauses = [
            NotificationORM.organization_id == organization_id,
            self._recipient_clause(user_id),
            NotificationORM.dismissed_at.is_(None),
        ]
        if not include_expired:
            clauses.append(
                or_(
                    NotificationORM.expires_at.is_(None),
                    NotificationORM.expires_at > now,
                )
            )
        return and_(*clauses)

    @staticmethod
    def _recipient_clause(user_id: str):
        return or_(
            NotificationORM.user_id == user_id,
            NotificationORM.user_id.is_(None),
        )

    def list_for_user(
        self,
        organization_id: str,
        user_id: str,
        *,
        limit: int = 20,
        unread_only: bool = False,
        include_expired: bool = False,
    ) -> list[NotificationRecord]:
        with self._session() as db:
            stmt = (
                select(NotificationORM)
                .where(self._visibility_clause(organization_id, user_id, include_expired=include_expired))
            )
            if unread_only:
                stmt = stmt.where(NotificationORM.read_at.is_(None))
            stmt = stmt.order_by(NotificationORM.created_at.desc()).limit(limit)
            rows = db.execute(stmt).scalars().all()
            return [_orm_to_record(r) for r in rows]

    def count_unread(self, organization_id: str, user_id: str) -> int:
        with self._session() as db:
            stmt = (
                select(func.count())
                .select_from(NotificationORM)
                .where(self._visibility_clause(organization_id, user_id, include_expired=False))
                .where(NotificationORM.read_at.is_(None))
            )
            result = db.execute(stmt).scalar()
            return int(result or 0)

    def mark_read(self, notification_id: str, organization_id: str, user_id: str) -> bool:
        with self._session() as db:
            stmt = (
                update(NotificationORM)
                .where(
                    NotificationORM.notification_id == notification_id,
                    NotificationORM.organization_id == organization_id,
                    self._recipient_clause(user_id),
                    NotificationORM.read_at.is_(None),
                    NotificationORM.dismissed_at.is_(None),
                )
                .values(read_at=_utc_now())
            )
            result = db.execute(stmt)
            db.commit()
            return result.rowcount > 0

    def mark_all_read(self, organization_id: str, user_id: str) -> int:
        with self._session() as db:
            stmt = (
                update(NotificationORM)
                .where(self._visibility_clause(organization_id, user_id, include_expired=False))
                .where(NotificationORM.read_at.is_(None))
                .values(read_at=_utc_now())
            )
            result = db.execute(stmt)
            db.commit()
            return result.rowcount

    def dismiss(self, notification_id: str, organization_id: str, user_id: str) -> bool:
        with self._session() as db:
            stmt = (
                update(NotificationORM)
                .where(
                    NotificationORM.notification_id == notification_id,
                    NotificationORM.organization_id == organization_id,
                    self._recipient_clause(user_id),
                    NotificationORM.dismissed_at.is_(None),
                )
                .values(dismissed_at=_utc_now())
            )
            result = db.execute(stmt)
            db.commit()
            return result.rowcount > 0
