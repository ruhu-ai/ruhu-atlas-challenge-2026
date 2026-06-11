from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .db_models import PhoneNumberBindingRecord, PhoneNumberRecord, PhoneNumberRouteRecord
from .phone_numbers import PhoneNumberRouteConfig, detect_e164_country_code, normalize_e164_number


PhoneNumberStatus = Literal["draft", "active", "suspended", "archived"]
PhoneNumberOwnershipMode = Literal["imported", "provider_managed"]
PhoneBindingChannel = Literal["phone", "sms", "whatsapp"]
PhoneBindingVerificationStatus = Literal["unverified", "pending", "verified", "manual_required", "failed"]
PhoneBindingHealthStatus = Literal["unknown", "healthy", "degraded", "misconfigured", "disabled"]

_DEFAULT_BINDING_CAPABILITIES: dict[PhoneBindingChannel, tuple[str, ...]] = {
    "phone": ("voice_inbound",),
    "sms": ("sms_inbound",),
    "whatsapp": ("whatsapp_inbound",),
}
_UNSET = object()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _normalize_optional_string(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None


def _normalize_metadata(value: object | None) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    return {str(key): item for key, item in value.items()}


def _normalize_capabilities(channel: PhoneBindingChannel, capabilities: list[str] | tuple[str, ...] | None) -> list[str]:
    if capabilities is None:
        return list(_DEFAULT_BINDING_CAPABILITIES[channel])
    normalized: list[str] = []
    for item in capabilities:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("capabilities must contain non-empty strings")
        normalized.append(item.strip())
    return _dedupe_strings(normalized)


class PhoneNumberRegistryConflictError(ValueError):
    pass


class PhoneNumberRegistryNotFoundError(LookupError):
    pass


class PhoneNumber(BaseModel):
    phone_number_id: str
    organization_id: str
    e164_number: str
    display_name: str | None = None
    country_code: str | None = None
    status: PhoneNumberStatus = "active"
    ownership_mode: PhoneNumberOwnershipMode = "imported"
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class PhoneNumberBinding(BaseModel):
    binding_id: str
    phone_number_id: str
    organization_id: str
    channel: PhoneBindingChannel
    provider: str
    provider_resource_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    verification_status: PhoneBindingVerificationStatus = "unverified"
    health_status: PhoneBindingHealthStatus = "unknown"
    is_active: bool = True
    transport_metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class PhoneNumberRoute(BaseModel):
    route_id: str
    phone_number_id: str
    organization_id: str
    channel: PhoneBindingChannel
    agent_id: str
    priority: int = 100
    enabled: bool = True
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class PhoneNumberDetail(BaseModel):
    number: PhoneNumber
    bindings: list[PhoneNumberBinding] = Field(default_factory=list)
    routes: list[PhoneNumberRoute] = Field(default_factory=list)


class PhoneNumberRegistryService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_number(
        self,
        *,
        organization_id: str,
        e164_number: str,
        display_name: str | None = None,
        ownership_mode: PhoneNumberOwnershipMode = "imported",
        status: PhoneNumberStatus = "active",
        metadata: dict[str, object] | None = None,
    ) -> PhoneNumber:
        normalized_number = normalize_e164_number(e164_number)
        now = _utcnow()
        record = PhoneNumberRecord(
            phone_number_id=f"pn_{uuid4().hex}",
            organization_id=organization_id,
            e164_number=normalized_number,
            display_name=_normalize_optional_string(display_name),
            country_code=detect_e164_country_code(normalized_number),
            status=status,
            ownership_mode=ownership_mode,
            metadata_json=_normalize_metadata(metadata),
            created_at=now,
            updated_at=now,
        )
        try:
            with self._session_factory.begin() as session:
                session.add(record)
        except IntegrityError as exc:
            raise PhoneNumberRegistryConflictError(f"phone number already exists: {normalized_number}") from exc
        return self.get_number(record.phone_number_id, organization_id=organization_id)

    def list_numbers(
        self,
        *,
        organization_id: str,
        status: PhoneNumberStatus | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[PhoneNumber]:
        with self._session_factory() as session:
            statement = (
                select(PhoneNumberRecord)
                .where(PhoneNumberRecord.organization_id == organization_id)
                .order_by(PhoneNumberRecord.created_at.desc(), PhoneNumberRecord.phone_number_id.desc())
                .limit(max(1, min(int(limit), 2000)))
                .offset(max(0, int(offset)))
            )
            if status is not None:
                statement = statement.where(PhoneNumberRecord.status == status)
            records = session.scalars(statement).all()
        return [_number_from_record(record) for record in records]

    def find_number_by_e164(self, *, organization_id: str, e164_number: str) -> PhoneNumber | None:
        normalized_number = normalize_e164_number(e164_number)
        with self._session_factory() as session:
            record = session.scalar(
                select(PhoneNumberRecord).where(
                    PhoneNumberRecord.organization_id == organization_id,
                    PhoneNumberRecord.e164_number == normalized_number,
                )
            )
        return None if record is None else _number_from_record(record)

    def get_number(self, phone_number_id: str, *, organization_id: str) -> PhoneNumber:
        with self._session_factory() as session:
            record = self._scoped_number(session, phone_number_id, organization_id=organization_id)
            return _number_from_record(record)

    def get_number_detail(self, phone_number_id: str, *, organization_id: str) -> PhoneNumberDetail:
        with self._session_factory() as session:
            record = self._scoped_number(session, phone_number_id, organization_id=organization_id)
            bindings = session.scalars(
                select(PhoneNumberBindingRecord)
                .where(PhoneNumberBindingRecord.phone_number_id == phone_number_id)
                .order_by(
                    PhoneNumberBindingRecord.channel.asc(),
                    PhoneNumberBindingRecord.updated_at.desc(),
                    PhoneNumberBindingRecord.binding_id.desc(),
                )
            ).all()
            routes = session.scalars(
                select(PhoneNumberRouteRecord)
                .where(PhoneNumberRouteRecord.phone_number_id == phone_number_id)
                .order_by(
                    PhoneNumberRouteRecord.channel.asc(),
                    PhoneNumberRouteRecord.priority.asc(),
                    PhoneNumberRouteRecord.updated_at.desc(),
                )
            ).all()
        return PhoneNumberDetail(
            number=_number_from_record(record),
            bindings=[_binding_from_record(item) for item in bindings],
            routes=[_route_from_record(item) for item in routes],
        )

    def update_number(
        self,
        phone_number_id: str,
        *,
        organization_id: str,
        display_name: str | None | object = _UNSET,
        status: PhoneNumberStatus | object = _UNSET,
        ownership_mode: PhoneNumberOwnershipMode | object = _UNSET,
        metadata: dict[str, object] | object = _UNSET,
    ) -> PhoneNumber:
        with self._session_factory.begin() as session:
            record = self._scoped_number(session, phone_number_id, organization_id=organization_id)
            if display_name is not _UNSET:
                record.display_name = _normalize_optional_string(display_name)
            if status is not _UNSET:
                record.status = status
            if ownership_mode is not _UNSET:
                record.ownership_mode = ownership_mode
            if metadata is not _UNSET:
                record.metadata_json = _normalize_metadata(metadata)
            record.updated_at = _utcnow()
        return self.get_number(phone_number_id, organization_id=organization_id)

    def create_binding(
        self,
        *,
        phone_number_id: str,
        organization_id: str,
        channel: PhoneBindingChannel,
        provider: str,
        provider_resource_id: str | None = None,
        capabilities: list[str] | tuple[str, ...] | None = None,
        verification_status: PhoneBindingVerificationStatus = "unverified",
        health_status: PhoneBindingHealthStatus = "unknown",
        is_active: bool = True,
        transport_metadata: dict[str, object] | None = None,
    ) -> PhoneNumberBinding:
        normalized_provider = _normalize_optional_string(provider)
        if normalized_provider is None:
            raise ValueError("provider is required")
        now = _utcnow()
        binding = PhoneNumberBindingRecord(
            binding_id=f"pnb_{uuid4().hex}",
            organization_id=organization_id,
            phone_number_id=self.get_number(phone_number_id, organization_id=organization_id).phone_number_id,
            channel=channel,
            provider=normalized_provider,
            provider_resource_id=_normalize_optional_string(provider_resource_id),
            capabilities_json=_normalize_capabilities(channel, capabilities),
            verification_status=verification_status,
            health_status=health_status,
            is_active=is_active,
            transport_metadata_json=_normalize_metadata(transport_metadata),
            created_at=now,
            updated_at=now,
        )
        try:
            with self._session_factory.begin() as session:
                session.add(binding)
        except IntegrityError as exc:
            raise PhoneNumberRegistryConflictError("provider binding already exists") from exc
        return self.get_binding(phone_number_id, binding.binding_id, organization_id=organization_id)

    def get_binding(self, phone_number_id: str, binding_id: str, *, organization_id: str) -> PhoneNumberBinding:
        with self._session_factory() as session:
            self._scoped_number(session, phone_number_id, organization_id=organization_id)
            record = self._scoped_binding(
                session,
                binding_id,
                organization_id=organization_id,
                phone_number_id=phone_number_id,
            )
            return _binding_from_record(record)

    def update_binding(
        self,
        phone_number_id: str,
        binding_id: str,
        *,
        organization_id: str,
        provider_resource_id: str | None | object = _UNSET,
        capabilities: list[str] | tuple[str, ...] | object = _UNSET,
        verification_status: PhoneBindingVerificationStatus | object = _UNSET,
        health_status: PhoneBindingHealthStatus | object = _UNSET,
        is_active: bool | object = _UNSET,
        transport_metadata: dict[str, object] | object = _UNSET,
    ) -> PhoneNumberBinding:
        with self._session_factory.begin() as session:
            record = self._scoped_binding(
                session,
                binding_id,
                organization_id=organization_id,
                phone_number_id=phone_number_id,
            )
            if provider_resource_id is not _UNSET:
                record.provider_resource_id = _normalize_optional_string(provider_resource_id)
            if capabilities is not _UNSET:
                record.capabilities_json = _normalize_capabilities(record.channel, capabilities)
            if verification_status is not _UNSET:
                record.verification_status = verification_status
            if health_status is not _UNSET:
                record.health_status = health_status
            if is_active is not _UNSET:
                record.is_active = bool(is_active)
            if transport_metadata is not _UNSET:
                record.transport_metadata_json = _normalize_metadata(transport_metadata)
            record.updated_at = _utcnow()
        return self.get_binding(phone_number_id, binding_id, organization_id=organization_id)

    def list_bindings(self, phone_number_id: str, *, organization_id: str) -> list[PhoneNumberBinding]:
        return self.get_number_detail(phone_number_id, organization_id=organization_id).bindings

    def list_bindings_for_organization(
        self,
        *,
        organization_id: str,
        provider: str | None = None,
        phone_number_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[PhoneNumberBinding]:
        normalized_provider = _normalize_optional_string(provider)
        normalized_phone_number_id = _normalize_optional_string(phone_number_id)
        with self._session_factory() as session:
            statement = (
                select(PhoneNumberBindingRecord)
                .where(PhoneNumberBindingRecord.organization_id == organization_id)
                .order_by(
                    PhoneNumberBindingRecord.updated_at.desc(),
                    PhoneNumberBindingRecord.binding_id.desc(),
                )
                .limit(max(1, min(int(limit), 500)))
            )
            if normalized_provider is not None:
                statement = statement.where(PhoneNumberBindingRecord.provider == normalized_provider)
            if normalized_phone_number_id is not None:
                statement = statement.where(PhoneNumberBindingRecord.phone_number_id == normalized_phone_number_id)
            if active_only:
                statement = statement.where(PhoneNumberBindingRecord.is_active.is_(True))
            records = session.scalars(statement).all()
        return [_binding_from_record(record) for record in records]

    def create_or_replace_route(
        self,
        *,
        phone_number_id: str,
        organization_id: str,
        channel: PhoneBindingChannel,
        agent_id: str,
        priority: int = 100,
        enabled: bool = True,
        metadata: dict[str, object] | None = None,
    ) -> PhoneNumberRoute:
        now = _utcnow()
        route = PhoneNumberRouteRecord(
            route_id=f"pnr_{uuid4().hex}",
            organization_id=organization_id,
            phone_number_id=self.get_number(phone_number_id, organization_id=organization_id).phone_number_id,
            channel=channel,
            agent_id=agent_id.strip(),
            priority=priority,
            enabled=enabled,
            metadata_json=_normalize_metadata(metadata),
            created_at=now,
            updated_at=now,
        )
        with self._session_factory.begin() as session:
            session.add(route)
        return self.get_route(phone_number_id, route.route_id, organization_id=organization_id)

    def get_route(self, phone_number_id: str, route_id: str, *, organization_id: str) -> PhoneNumberRoute:
        with self._session_factory() as session:
            self._scoped_number(session, phone_number_id, organization_id=organization_id)
            record = self._scoped_route(session, route_id, organization_id=organization_id, phone_number_id=phone_number_id)
            return _route_from_record(record)

    def update_route(
        self,
        phone_number_id: str,
        route_id: str,
        *,
        organization_id: str,
        agent_id: str | object = _UNSET,
        priority: int | object = _UNSET,
        enabled: bool | object = _UNSET,
        metadata: dict[str, object] | object = _UNSET,
    ) -> PhoneNumberRoute:
        with self._session_factory.begin() as session:
            record = self._scoped_route(
                session,
                route_id,
                organization_id=organization_id,
                phone_number_id=phone_number_id,
            )
            if agent_id is not _UNSET:
                normalized_agent_id = str(agent_id).strip()
                if not normalized_agent_id:
                    raise ValueError("agent_id is required")
                record.agent_id = normalized_agent_id
            if priority is not _UNSET:
                record.priority = int(priority)
            if enabled is not _UNSET:
                record.enabled = bool(enabled)
            if metadata is not _UNSET:
                record.metadata_json = _normalize_metadata(metadata)
            record.updated_at = _utcnow()
        return self.get_route(phone_number_id, route_id, organization_id=organization_id)

    def list_routes(self, phone_number_id: str, *, organization_id: str) -> list[PhoneNumberRoute]:
        return self.get_number_detail(phone_number_id, organization_id=organization_id).routes

    def resolve_route(
        self,
        *,
        phone_number: str,
        channel: PhoneBindingChannel = "phone",
        provider: str | None = None,
    ) -> PhoneNumberRouteConfig | None:
        normalized_number = normalize_e164_number(phone_number)
        normalized_provider = _normalize_optional_string(provider)
        with self._session_factory() as session:
            number_record = session.scalar(
                select(PhoneNumberRecord).where(
                    PhoneNumberRecord.e164_number == normalized_number,
                    PhoneNumberRecord.status == "active",
                )
            )
            if number_record is None:
                return None
            route_record = session.scalar(
                select(PhoneNumberRouteRecord)
                .where(
                    PhoneNumberRouteRecord.phone_number_id == number_record.phone_number_id,
                    PhoneNumberRouteRecord.channel == channel,
                    PhoneNumberRouteRecord.enabled.is_(True),
                )
                .order_by(PhoneNumberRouteRecord.priority.asc(), PhoneNumberRouteRecord.updated_at.desc())
                .limit(1)
            )
            if route_record is None:
                return None
            bindings = session.scalars(
                select(PhoneNumberBindingRecord)
                .where(
                    PhoneNumberBindingRecord.phone_number_id == number_record.phone_number_id,
                    PhoneNumberBindingRecord.channel == channel,
                    PhoneNumberBindingRecord.is_active.is_(True),
                )
                .order_by(PhoneNumberBindingRecord.updated_at.desc(), PhoneNumberBindingRecord.binding_id.desc())
            ).all()
            binding_record = _select_binding_record(bindings, provider=normalized_provider)
            metadata = dict(route_record.metadata_json or {})
            metadata.setdefault("phone_number_id", number_record.phone_number_id)
            if binding_record is not None:
                metadata.setdefault("binding_id", binding_record.binding_id)
            return PhoneNumberRouteConfig(
                route_key=route_record.route_id,
                phone_number=number_record.e164_number,
                agent_id=route_record.agent_id,
                channel=route_record.channel,
                organization_id=route_record.organization_id,
                provider=None if binding_record is None else binding_record.provider,
                provider_resource_id=None if binding_record is None else binding_record.provider_resource_id,
                display_name=number_record.display_name,
                country_code=number_record.country_code,
                enabled=route_record.enabled,
                capabilities=(
                    _DEFAULT_BINDING_CAPABILITIES[channel]
                    if binding_record is None
                    else tuple(str(item) for item in binding_record.capabilities_json or [])
                ),
                metadata=metadata,
            )

    def _scoped_number(self, session: Session, phone_number_id: str, *, organization_id: str) -> PhoneNumberRecord:
        record = session.get(PhoneNumberRecord, phone_number_id)
        if record is None or record.organization_id != organization_id:
            raise PhoneNumberRegistryNotFoundError("unknown phone number")
        return record

    def _scoped_binding(
        self,
        session: Session,
        binding_id: str,
        *,
        organization_id: str,
        phone_number_id: str,
    ) -> PhoneNumberBindingRecord:
        record = session.get(PhoneNumberBindingRecord, binding_id)
        if (
            record is None
            or record.organization_id != organization_id
            or record.phone_number_id != phone_number_id
        ):
            raise PhoneNumberRegistryNotFoundError("unknown phone number binding")
        return record

    def _scoped_route(
        self,
        session: Session,
        route_id: str,
        *,
        organization_id: str,
        phone_number_id: str,
    ) -> PhoneNumberRouteRecord:
        record = session.get(PhoneNumberRouteRecord, route_id)
        if (
            record is None
            or record.organization_id != organization_id
            or record.phone_number_id != phone_number_id
        ):
            raise PhoneNumberRegistryNotFoundError("unknown phone number route")
        return record


def _select_binding_record(
    bindings: list[PhoneNumberBindingRecord],
    *,
    provider: str | None,
) -> PhoneNumberBindingRecord | None:
    if provider is not None:
        for binding in bindings:
            if binding.provider == provider:
                return binding
    if len(bindings) == 1:
        return bindings[0]
    return None


def _number_from_record(record: PhoneNumberRecord) -> PhoneNumber:
    return PhoneNumber(
        phone_number_id=record.phone_number_id,
        organization_id=record.organization_id,
        e164_number=record.e164_number,
        display_name=record.display_name,
        country_code=record.country_code,
        status=record.status,
        ownership_mode=record.ownership_mode,
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _binding_from_record(record: PhoneNumberBindingRecord) -> PhoneNumberBinding:
    return PhoneNumberBinding(
        binding_id=record.binding_id,
        phone_number_id=record.phone_number_id,
        organization_id=record.organization_id,
        channel=record.channel,
        provider=record.provider,
        provider_resource_id=record.provider_resource_id,
        capabilities=[str(item) for item in record.capabilities_json or []],
        verification_status=record.verification_status,
        health_status=record.health_status,
        is_active=record.is_active,
        transport_metadata=dict(record.transport_metadata_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _route_from_record(record: PhoneNumberRouteRecord) -> PhoneNumberRoute:
    return PhoneNumberRoute(
        route_id=record.route_id,
        phone_number_id=record.phone_number_id,
        organization_id=record.organization_id,
        channel=record.channel,
        agent_id=record.agent_id,
        priority=record.priority,
        enabled=record.enabled,
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
