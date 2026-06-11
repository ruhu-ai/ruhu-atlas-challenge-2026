from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..audit.emitter import emit_audit_event
from .task_packs import BrowserCredentialKind, BrowserCredentialRequirement, BrowserTaskPack
from .worker_contracts import BrowserCredentialRef, BrowserWorkerRequest


class BrowserCredentialConnectionReader(Protocol):
    def get(self, connection_id: str) -> Any | None: ...


class BrowserCredentialConnectionStore(BrowserCredentialConnectionReader, Protocol):
    def decrypt_credentials_from_record(
        self,
        record: Any,
        *,
        actor_id: str | None,
        actor_type: str,
        purpose: str,
    ) -> dict[str, Any]: ...


class BrowserResolvedCredential(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    kind: BrowserCredentialKind
    storage_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserCredentialResolver(Protocol):
    def resolve(
        self,
        *,
        request: BrowserWorkerRequest,
        credential: BrowserCredentialRef,
    ) -> BrowserResolvedCredential: ...


@dataclass(slots=True)
class APIConnectionBrowserCredentialValidator:
    connection_store: BrowserCredentialConnectionReader

    def validate_task_credentials(
        self,
        *,
        organization_id: str | None,
        task_pack: BrowserTaskPack,
        credential_refs: dict[str, str],
    ) -> None:
        requirements = {requirement.name: requirement for requirement in task_pack.credentials}
        for name, secret_ref in credential_refs.items():
            requirement = requirements.get(name)
            if requirement is None:
                continue
            connection_id = _parse_connection_ref(secret_ref, credential_name=name)
            connection = self.connection_store.get(connection_id)
            if connection is None:
                raise ValueError(f"credential ref {name} references unknown connection")
            connection_org = getattr(connection, "organization_id", None)
            if organization_id is not None and connection_org != organization_id:
                raise ValueError(f"credential ref {name} references a connection outside this organization")
            status = str(getattr(connection, "status", "") or "").strip().lower()
            if status != "active":
                raise ValueError(f"credential ref {name} references an inactive connection")
            expected_provider = requirement.provider
            if expected_provider and str(getattr(connection, "provider", "")).strip() != expected_provider:
                raise ValueError(f"credential ref {name} references the wrong provider")
            expected_auth_type = _expected_auth_type(requirement)
            if expected_auth_type and str(getattr(connection, "auth_type", "")).strip() != expected_auth_type:
                raise ValueError(f"credential ref {name} references the wrong auth type")


@dataclass(slots=True)
class APIConnectionBrowserCredentialResolver:
    connection_store: BrowserCredentialConnectionStore
    actor_id: str | None = None
    audit_router: Any | None = None

    def resolve(
        self,
        *,
        request: BrowserWorkerRequest,
        credential: BrowserCredentialRef,
    ) -> BrowserResolvedCredential:
        if credential.kind != "session":
            raise ValueError("browser worker accepts only session credentials")
        connection_id = _parse_connection_ref(
            credential.secret_ref,
            credential_name=credential.name,
        )
        connection = self.connection_store.get(connection_id)
        if connection is None:
            raise ValueError(f"credential ref {credential.name} references unknown connection")
        connection_org = getattr(connection, "organization_id", None)
        if request.organization_id is not None and connection_org != request.organization_id:
            raise ValueError(f"credential ref {credential.name} references a connection outside this organization")
        status = str(getattr(connection, "status", "") or "").strip().lower()
        if status != "active":
            raise ValueError(f"credential ref {credential.name} references an inactive connection")
        auth_type = str(getattr(connection, "auth_type", "") or "").strip().lower()
        if auth_type not in {"browser_session", "session"}:
            raise ValueError("browser session credentials must use browser_session auth type")
        payload = self.connection_store.decrypt_credentials_from_record(
            connection,
            actor_id=self.actor_id,
            actor_type="tool_runtime",
            purpose="browser_task_session",
        )
        storage_state = _extract_storage_state(payload, credential_name=credential.name)
        self._emit_credential_used_audit(
            request=request,
            credential=credential,
            connection=connection,
            connection_id=connection_id,
            auth_type=auth_type,
        )
        return BrowserResolvedCredential(
            name=credential.name,
            kind=credential.kind,
            storage_state=storage_state,
            metadata={"connection_id": connection_id},
        )

    def _emit_credential_used_audit(
        self,
        *,
        request: BrowserWorkerRequest,
        credential: BrowserCredentialRef,
        connection: Any,
        connection_id: str,
        auth_type: str,
    ) -> None:
        if self.audit_router is None or request.organization_id is None:
            return
        emit_audit_event(
            self.audit_router,
            event_type="security.browser_task_credential_used",
            organization_id=request.organization_id,
            actor_id=self.actor_id,
            resource_type="browser_task",
            resource_id=request.task_id,
            detail={
                "request_id": request.request_id,
                "conversation_id": request.conversation_id,
                "agent_id": request.agent_id,
                "task_pack_id": request.pack_id,
                "task_pack_version": request.pack_version,
                "credential_name": credential.name,
                "credential_kind": credential.kind,
                "connection_id": connection_id,
                "connection_provider": getattr(connection, "provider", None),
                "connection_auth_type": auth_type,
            },
        )


def _parse_connection_ref(secret_ref: str, *, credential_name: str) -> str:
    prefix = "connection:"
    if not secret_ref.startswith(prefix):
        raise ValueError(f"credential ref {credential_name} must use connection:<connection_id>")
    connection_id = secret_ref[len(prefix):].strip()
    if not connection_id:
        raise ValueError(f"credential ref {credential_name} must include a connection id")
    return connection_id


def _expected_auth_type(requirement: BrowserCredentialRequirement) -> str | None:
    if requirement.auth_type:
        return requirement.auth_type
    if requirement.kind == "session":
        return "browser_session"
    if requirement.kind == "oauth":
        return "oauth2"
    if requirement.kind == "api_key":
        return "api_key"
    return None


def _extract_storage_state(payload: dict[str, Any], *, credential_name: str) -> dict[str, Any]:
    candidate = payload.get("playwright_storage_state")
    if candidate is None:
        candidate = payload.get("storage_state")
    if not isinstance(candidate, dict):
        raise ValueError(f"credential ref {credential_name} does not contain browser storage state")
    cookies = candidate.get("cookies")
    origins = candidate.get("origins")
    if cookies is not None and not isinstance(cookies, list):
        raise ValueError(f"credential ref {credential_name} has invalid browser storage state")
    if origins is not None and not isinstance(origins, list):
        raise ValueError(f"credential ref {credential_name} has invalid browser storage state")
    if cookies is None and origins is None:
        raise ValueError(f"credential ref {credential_name} has empty browser storage state")
    return dict(candidate)
