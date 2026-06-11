from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote_plus

import httpx

from .secret_sources import load_text_secret, normalize_gcp_secret_version


class TicketingProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


@dataclass(slots=True)
class ProviderConnectionConfig:
    connection_id: str
    provider: str
    auth_type: str
    credentials_ref: str | None
    provider_config: dict[str, object] = field(default_factory=dict)
    field_mappings: dict[str, object] = field(default_factory=dict)
    status_mappings: dict[str, object] = field(default_factory=dict)
    priority_mappings: dict[str, object] = field(default_factory=dict)
    default_queue: str | None = None


@dataclass(slots=True)
class RemoteCase:
    external_case_id: str
    external_case_key: str | None = None
    external_case_url: str | None = None
    external_case_status: str | None = None
    external_case_priority: str | None = None
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookSyncResult:
    event_type: str
    external_case_id: str | None = None
    external_case_key: str | None = None
    external_case_url: str | None = None
    external_case_status: str | None = None
    external_case_priority: str | None = None
    comments: list[dict[str, object]] = field(default_factory=list)
    payload_snapshot: dict[str, object] = field(default_factory=dict)


class TicketingAdapter(Protocol):
    provider: str

    def health_check(self) -> dict[str, object]: ...

    def create_case(
        self,
        *,
        title: str,
        description: str,
        priority: str | None = None,
        status: str | None = None,
        participant_email: str | None = None,
        participant_display: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RemoteCase: ...

    def fetch_case(self, external_case_id: str) -> RemoteCase | None: ...

    def search_cases(self, *, query: str, limit: int = 20) -> list[RemoteCase]: ...

    def add_comment(
        self,
        *,
        external_case_id: str,
        body: str,
        visibility: str = "internal",
    ) -> dict[str, object]: ...

    def transition_case(
        self,
        *,
        external_case_id: str,
        status_value: str,
    ) -> RemoteCase: ...

    def parse_webhook(
        self,
        *,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> WebhookSyncResult: ...


def verify_ticketing_webhook_signature(
    connection: ProviderConnectionConfig,
    *,
    body: bytes,
    headers: dict[str, str] | None = None,
) -> bool | None:
    provider = connection.provider.strip().lower()
    if provider == "zendesk":
        return _verify_zendesk_webhook_signature(connection, body=body, headers=headers)
    if provider == "jira":
        return _verify_jira_webhook_signature(connection, body=body, headers=headers)
    return None


def build_ticketing_adapter(
    connection: ProviderConnectionConfig,
    *,
    client: httpx.Client | None = None,
) -> TicketingAdapter:
    provider = connection.provider.strip().lower()
    if provider == "zendesk":
        return ZendeskTicketingAdapter(connection, client=client)
    if provider == "freshdesk":
        return FreshdeskTicketingAdapter(connection, client=client)
    if provider == "jira":
        return JiraTicketingAdapter(connection, client=client)
    raise TicketingProviderError(
        f"unsupported ticketing provider: {connection.provider}",
        provider=connection.provider,
    )


class BaseTicketingAdapter:
    provider = "other"
    retryable_status_codes = {429, 500, 502, 503, 504}

    def __init__(self, connection: ProviderConnectionConfig, *, client: httpx.Client | None = None) -> None:
        self.connection = connection
        self.credentials = _resolve_connection_credentials(connection.credentials_ref)
        self.client = client or httpx.Client(
            timeout=15.0,
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None = None,
        params: dict[str, object] | None = None,
        allowed_statuses: set[int] | None = None,
    ) -> httpx.Response:
        allowed = allowed_statuses or {200}
        attempts = int(self.connection.provider_config.get("retry_attempts", 3) or 3)
        last_error: Exception | None = None
        for attempt_index in range(1, attempts + 1):
            try:
                response = self.client.request(method, url, json=json_body, params=params)
                if response.status_code in allowed:
                    return response
                retryable = response.status_code in self.retryable_status_codes and attempt_index < attempts
                if retryable:
                    time.sleep(min(0.2 * attempt_index, 0.6))
                    continue
                raise TicketingProviderError(
                    _extract_error_message(response),
                    provider=self.provider,
                    status_code=response.status_code,
                    retryable=response.status_code in self.retryable_status_codes,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt_index < attempts:
                    time.sleep(min(0.2 * attempt_index, 0.6))
                    continue
                raise TicketingProviderError(
                    str(exc),
                    provider=self.provider,
                    retryable=True,
                ) from exc
        raise TicketingProviderError(
            str(last_error or "ticketing request failed"),
            provider=self.provider,
            retryable=True,
        )

    def health_check(self) -> dict[str, object]:
        raise NotImplementedError

    def create_case(
        self,
        *,
        title: str,
        description: str,
        priority: str | None = None,
        status: str | None = None,
        participant_email: str | None = None,
        participant_display: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RemoteCase:
        raise NotImplementedError

    def fetch_case(self, external_case_id: str) -> RemoteCase | None:
        raise NotImplementedError

    def search_cases(self, *, query: str, limit: int = 20) -> list[RemoteCase]:
        raise NotImplementedError

    def add_comment(
        self,
        *,
        external_case_id: str,
        body: str,
        visibility: str = "internal",
    ) -> dict[str, object]:
        raise NotImplementedError

    def transition_case(
        self,
        *,
        external_case_id: str,
        status_value: str,
    ) -> RemoteCase:
        raise NotImplementedError

    def parse_webhook(
        self,
        *,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> WebhookSyncResult:
        del headers
        return WebhookSyncResult(
            event_type="provider_webhook",
            payload_snapshot=dict(payload),
        )


class ZendeskTicketingAdapter(BaseTicketingAdapter):
    provider = "zendesk"

    @property
    def base_url(self) -> str:
        configured = str(self.connection.provider_config.get("api_base_url") or "").strip()
        if configured:
            return configured.rstrip("/")
        subdomain = str(self.connection.provider_config.get("subdomain") or "").strip()
        if not subdomain:
            raise TicketingProviderError("zendesk subdomain is required", provider=self.provider)
        return f"https://{subdomain}.zendesk.com/api/v2"

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self.connection.auth_type == "api_token":
            email = str(self.connection.provider_config.get("email") or "").strip()
            if not email or not self.credentials:
                raise TicketingProviderError("zendesk api_token auth requires email and credentials", provider=self.provider)
            token = base64.b64encode(f"{email}/token:{self.credentials}".encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {token}"
        else:
            headers["Authorization"] = f"Bearer {self.credentials}"
        return headers

    def health_check(self) -> dict[str, object]:
        response = self._request("GET", f"{self.base_url}/users/me.json")
        data = response.json().get("user") or {}
        return {"status": "active", "remote_user": data.get("email") or data.get("name") or "unknown"}

    def create_case(self, **kwargs: object) -> RemoteCase:
        payload = {
            "ticket": {
                "subject": str(kwargs["title"]),
                "comment": {"body": str(kwargs["description"]), "public": False},
                "priority": _map_priority_to_zendesk(str(kwargs.get("priority") or "medium")),
                "status": _map_status_to_zendesk(str(kwargs.get("status") or "open")),
                "tags": list(kwargs.get("tags") or []),
            }
        }
        participant_email = kwargs.get("participant_email")
        participant_display = kwargs.get("participant_display")
        if participant_email:
            payload["ticket"]["requester"] = {
                "email": str(participant_email),
                "name": str(participant_display or participant_email),
            }
        metadata = dict(kwargs.get("metadata") or {})
        if metadata:
            payload["ticket"]["custom_fields"] = [{"id": key, "value": value} for key, value in metadata.items()]
        response = self._request("POST", f"{self.base_url}/tickets.json", json_body=payload, allowed_statuses={200, 201})
        return self._from_ticket(response.json().get("ticket") or {})

    def fetch_case(self, external_case_id: str) -> RemoteCase | None:
        response = self._request(
            "GET",
            f"{self.base_url}/tickets/{external_case_id}.json",
            allowed_statuses={200, 404},
        )
        if response.status_code == 404:
            return None
        return self._from_ticket(response.json().get("ticket") or {})

    def search_cases(self, *, query: str, limit: int = 20) -> list[RemoteCase]:
        response = self._request(
            "GET",
            f"{self.base_url}/search.json",
            params={"query": f"type:ticket {query}", "per_page": min(limit, 100)},
        )
        return [self._from_ticket(item) for item in list(response.json().get("results") or [])[:limit]]

    def add_comment(self, *, external_case_id: str, body: str, visibility: str = "internal") -> dict[str, object]:
        payload = {
            "ticket": {
                "comment": {
                    "body": body.strip(),
                    "public": visibility == "customer_visible",
                }
            }
        }
        response = self._request(
            "PUT",
            f"{self.base_url}/tickets/{external_case_id}.json",
            json_body=payload,
        )
        ticket = response.json().get("ticket") or {}
        return {"ticket_id": str(ticket.get("id") or external_case_id), "status": ticket.get("status")}

    def transition_case(self, *, external_case_id: str, status_value: str) -> RemoteCase:
        payload = {"ticket": {"status": _map_status_to_zendesk(status_value)}}
        response = self._request(
            "PUT",
            f"{self.base_url}/tickets/{external_case_id}.json",
            json_body=payload,
        )
        return self._from_ticket(response.json().get("ticket") or {})

    def parse_webhook(self, *, payload: dict[str, object], headers: dict[str, str] | None = None) -> WebhookSyncResult:
        del headers
        ticket = payload.get("ticket")
        ticket_data = ticket if isinstance(ticket, dict) else payload
        latest_comment = payload.get("comment")
        comments: list[dict[str, object]] = []
        if isinstance(latest_comment, dict):
            body = str(latest_comment.get("body") or "").strip()
            if body:
                comments.append({"body": body, "visibility": "customer_visible" if latest_comment.get("public") else "internal"})
        return WebhookSyncResult(
            event_type=str(payload.get("event") or "zendesk_webhook"),
            external_case_id=_as_optional_str(ticket_data.get("id")),
            external_case_key=_as_optional_str(ticket_data.get("id")),
            external_case_url=_as_optional_str(ticket_data.get("url")),
            external_case_status=_as_optional_str(ticket_data.get("status")),
            external_case_priority=_as_optional_str(ticket_data.get("priority")),
            comments=comments,
            payload_snapshot=dict(ticket_data if isinstance(ticket_data, dict) else {}),
        )

    def _from_ticket(self, ticket: dict[str, object]) -> RemoteCase:
        return RemoteCase(
            external_case_id=str(ticket.get("id") or ""),
            external_case_key=_as_optional_str(ticket.get("id")),
            external_case_url=_as_optional_str(ticket.get("url")),
            external_case_status=_as_optional_str(ticket.get("status")),
            external_case_priority=_as_optional_str(ticket.get("priority")),
            payload=dict(ticket),
        )


class FreshdeskTicketingAdapter(BaseTicketingAdapter):
    provider = "freshdesk"

    @property
    def base_url(self) -> str:
        configured = str(self.connection.provider_config.get("api_base_url") or "").strip()
        if configured:
            return configured.rstrip("/")
        domain = str(self.connection.provider_config.get("domain") or "").strip()
        if not domain:
            raise TicketingProviderError("freshdesk domain is required", provider=self.provider)
        return f"https://{domain}.freshdesk.com/api/v2"

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self.connection.auth_type == "api_token":
            token = base64.b64encode(f"{self.credentials}:X".encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {token}"
        else:
            headers["Authorization"] = f"Bearer {self.credentials}"
        return headers

    def health_check(self) -> dict[str, object]:
        response = self._request("GET", f"{self.base_url}/tickets", params={"per_page": 1})
        return {"status": "active", "remote_count": len(list(response.json() or []))}

    def create_case(self, **kwargs: object) -> RemoteCase:
        payload: dict[str, object] = {
            "subject": str(kwargs["title"]),
            "description": str(kwargs["description"]),
            "priority": _map_priority_to_freshdesk(str(kwargs.get("priority") or "medium")),
            "status": _map_status_to_freshdesk(str(kwargs.get("status") or "open")),
            "tags": list(kwargs.get("tags") or []),
        }
        participant_email = kwargs.get("participant_email")
        if participant_email:
            payload["email"] = str(participant_email)
        participant_display = kwargs.get("participant_display")
        if participant_display:
            payload["name"] = str(participant_display)
        response = self._request("POST", f"{self.base_url}/tickets", json_body=payload, allowed_statuses={200, 201})
        return self._from_ticket(response.json())

    def fetch_case(self, external_case_id: str) -> RemoteCase | None:
        response = self._request(
            "GET",
            f"{self.base_url}/tickets/{external_case_id}",
            allowed_statuses={200, 404},
        )
        if response.status_code == 404:
            return None
        return self._from_ticket(response.json())

    def search_cases(self, *, query: str, limit: int = 20) -> list[RemoteCase]:
        response = self._request(
            "GET",
            f"{self.base_url}/search/tickets",
            params={"query": quote_plus(query)},
        )
        return [self._from_ticket(item) for item in list(response.json().get("results") or [])[:limit]]

    def add_comment(self, *, external_case_id: str, body: str, visibility: str = "internal") -> dict[str, object]:
        response = self._request(
            "POST",
            f"{self.base_url}/tickets/{external_case_id}/notes",
            json_body={"body": body.strip(), "private": visibility != "customer_visible"},
            allowed_statuses={200, 201},
        )
        return dict(response.json() or {})

    def transition_case(self, *, external_case_id: str, status_value: str) -> RemoteCase:
        response = self._request(
            "PUT",
            f"{self.base_url}/tickets/{external_case_id}",
            json_body={"status": _map_status_to_freshdesk(status_value)},
        )
        return self._from_ticket(response.json())

    def parse_webhook(self, *, payload: dict[str, object], headers: dict[str, str] | None = None) -> WebhookSyncResult:
        del headers
        ticket = payload.get("ticket")
        ticket_data = ticket if isinstance(ticket, dict) else payload
        return WebhookSyncResult(
            event_type=str(payload.get("triggered_event") or "freshdesk_webhook"),
            external_case_id=_as_optional_str(ticket_data.get("id") or ticket_data.get("ticket_id")),
            external_case_key=_as_optional_str(ticket_data.get("display_id") or ticket_data.get("id")),
            external_case_url=_as_optional_str(ticket_data.get("url")),
            external_case_status=_as_optional_str(ticket_data.get("status_name") or ticket_data.get("status")),
            external_case_priority=_as_optional_str(ticket_data.get("priority")),
            payload_snapshot=dict(ticket_data if isinstance(ticket_data, dict) else {}),
        )

    def _from_ticket(self, ticket: dict[str, object]) -> RemoteCase:
        external_id = str(ticket.get("id") or "")
        return RemoteCase(
            external_case_id=external_id,
            external_case_key=_as_optional_str(ticket.get("display_id")) or external_id,
            external_case_url=_as_optional_str(ticket.get("url")),
            external_case_status=_freshdesk_status_name(ticket.get("status")),
            external_case_priority=_freshdesk_priority_name(ticket.get("priority")),
            payload=dict(ticket),
        )


class JiraTicketingAdapter(BaseTicketingAdapter):
    provider = "jira"

    def __init__(self, connection: ProviderConnectionConfig, *, client: httpx.Client | None = None) -> None:
        self._cloud_id = _as_optional_str(connection.provider_config.get("cloud_id"))
        super().__init__(connection, client=client)

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        headers["Authorization"] = f"Bearer {self.credentials}"
        return headers

    def health_check(self) -> dict[str, object]:
        self._ensure_cloud_id()
        response = self._request("GET", f"{self.base_url}/myself")
        data = dict(response.json() or {})
        return {"status": "active", "remote_user": data.get("emailAddress") or data.get("displayName") or "jira"}

    @property
    def base_url(self) -> str:
        if not self._cloud_id:
            raise TicketingProviderError("jira cloud_id is required", provider=self.provider)
        configured = str(self.connection.provider_config.get("api_base_url") or "").strip()
        if configured:
            return configured.rstrip("/")
        return f"https://api.atlassian.com/ex/jira/{self._cloud_id}/rest/api/3"

    def _ensure_cloud_id(self) -> None:
        if self._cloud_id:
            return
        configured = str(self.connection.provider_config.get("accessible_resources_url") or "").strip()
        url = configured or "https://api.atlassian.com/oauth/token/accessible-resources"
        response = self._request("GET", url)
        resources = list(response.json() or [])
        if not resources:
            raise TicketingProviderError("no accessible jira resources found", provider=self.provider)
        site_name = _as_optional_str(self.connection.provider_config.get("site_name"))
        matched = None
        for item in resources:
            if not isinstance(item, dict):
                continue
            if site_name and item.get("name") == site_name:
                matched = item
                break
            if matched is None:
                matched = item
        cloud_id = _as_optional_str((matched or {}).get("id"))
        if not cloud_id:
            raise TicketingProviderError("jira cloud_id discovery failed", provider=self.provider)
        self._cloud_id = cloud_id

    def create_case(self, **kwargs: object) -> RemoteCase:
        self._ensure_cloud_id()
        project_key = str(self.connection.provider_config.get("project_key") or "").strip() or "SUP"
        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": str(kwargs["title"]),
                "description": _text_to_adf(str(kwargs["description"])),
                "issuetype": {"name": _jira_issue_type(self.connection.provider_config.get("issue_type"))},
                "priority": {"name": _map_priority_to_jira(str(kwargs.get("priority") or "medium"))},
            }
        }
        response = self._request("POST", f"{self.base_url}/issue", json_body=payload, allowed_statuses={200, 201})
        created = dict(response.json() or {})
        return RemoteCase(
            external_case_id=str(created.get("id") or ""),
            external_case_key=_as_optional_str(created.get("key")),
            external_case_url=_jira_browse_url(self.connection.provider_config, _as_optional_str(created.get("key"))),
            payload=created,
        )

    def fetch_case(self, external_case_id: str) -> RemoteCase | None:
        self._ensure_cloud_id()
        response = self._request(
            "GET",
            f"{self.base_url}/issue/{external_case_id}",
            params={"fields": "summary,status,priority"},
            allowed_statuses={200, 404},
        )
        if response.status_code == 404:
            return None
        return self._from_issue(response.json())

    def search_cases(self, *, query: str, limit: int = 20) -> list[RemoteCase]:
        self._ensure_cloud_id()
        response = self._request(
            "GET",
            f"{self.base_url}/search/jql",
            params={"jql": f'text ~ "{query}"', "maxResults": min(limit, 50), "fields": "summary,status,priority"},
        )
        return [self._from_issue(item) for item in list(response.json().get("issues") or [])[:limit]]

    def add_comment(self, *, external_case_id: str, body: str, visibility: str = "internal") -> dict[str, object]:
        self._ensure_cloud_id()
        payload = {"body": _text_to_adf(body.strip())}
        if visibility != "customer_visible":
            payload["visibility"] = {"type": "role", "value": "Administrators"}
        response = self._request(
            "POST",
            f"{self.base_url}/issue/{external_case_id}/comment",
            json_body=payload,
            allowed_statuses={200, 201},
        )
        return dict(response.json() or {})

    def transition_case(self, *, external_case_id: str, status_value: str) -> RemoteCase:
        self._ensure_cloud_id()
        transitions = self._request(
            "GET",
            f"{self.base_url}/issue/{external_case_id}/transitions",
        ).json().get("transitions") or []
        transition_id = None
        normalized = status_value.strip().lower()
        for item in transitions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower()
            to_name = str(((item.get("to") or {}) if isinstance(item.get("to"), dict) else {}).get("name") or "").strip().lower()
            if normalized in {name, to_name}:
                transition_id = item.get("id")
                break
        if transition_id is None:
            raise TicketingProviderError(
                f"jira transition {status_value!r} is not available",
                provider=self.provider,
            )
        self._request(
            "POST",
            f"{self.base_url}/issue/{external_case_id}/transitions",
            json_body={"transition": {"id": str(transition_id)}},
            allowed_statuses={204},
        )
        updated = self.fetch_case(external_case_id)
        if updated is None:
            raise TicketingProviderError("jira case disappeared after transition", provider=self.provider)
        return updated

    def parse_webhook(self, *, payload: dict[str, object], headers: dict[str, str] | None = None) -> WebhookSyncResult:
        del headers
        issue = payload.get("issue")
        issue_data = issue if isinstance(issue, dict) else {}
        fields = issue_data.get("fields") if isinstance(issue_data.get("fields"), dict) else {}
        status = fields.get("status") if isinstance(fields, dict) and isinstance(fields.get("status"), dict) else {}
        priority = fields.get("priority") if isinstance(fields, dict) and isinstance(fields.get("priority"), dict) else {}
        comment = payload.get("comment")
        comments: list[dict[str, object]] = []
        if isinstance(comment, dict):
            body = _flatten_jira_adf(comment.get("body"))
            if body:
                comments.append({"body": body, "visibility": "internal"})
        return WebhookSyncResult(
            event_type=str(payload.get("webhookEvent") or "jira_webhook"),
            external_case_id=_as_optional_str(issue_data.get("id")),
            external_case_key=_as_optional_str(issue_data.get("key")),
            external_case_url=_jira_browse_url(self.connection.provider_config, _as_optional_str(issue_data.get("key"))),
            external_case_status=_as_optional_str(status.get("name") if isinstance(status, dict) else None),
            external_case_priority=_as_optional_str(priority.get("name") if isinstance(priority, dict) else None),
            comments=comments,
            payload_snapshot=dict(issue_data),
        )

    def _from_issue(self, issue: dict[str, object]) -> RemoteCase:
        fields = issue.get("fields")
        fields_dict = fields if isinstance(fields, dict) else {}
        status = fields_dict.get("status")
        priority = fields_dict.get("priority")
        return RemoteCase(
            external_case_id=str(issue.get("id") or issue.get("key") or ""),
            external_case_key=_as_optional_str(issue.get("key")),
            external_case_url=_jira_browse_url(self.connection.provider_config, _as_optional_str(issue.get("key"))),
            external_case_status=_as_optional_str(status.get("name") if isinstance(status, dict) else None),
            external_case_priority=_as_optional_str(priority.get("name") if isinstance(priority, dict) else None),
            payload=dict(issue),
        )


def _resolve_connection_credentials(credentials_ref: str | None) -> str:
    return _resolve_secret_reference(credentials_ref, provider="ticketing")


def _resolve_secret_reference(raw_ref: object, *, provider: str) -> str:
    ref = str(raw_ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("env:"):
        env_name = ref.split(":", 1)[1].strip()
        value = os.getenv(env_name)
        if not value:
            raise TicketingProviderError(
                f"ticketing credential env var is not configured for {ref}",
                provider=provider,
            )
        return value
    if ref.startswith("projects/"):
        normalize_gcp_secret_version(ref)
        return load_text_secret(ref)
    return ref


def _resolve_webhook_secret(provider_config: dict[str, object], *, provider: str) -> str:
    return _resolve_secret_reference(
        provider_config.get("webhook_secret_ref") or provider_config.get("webhook_secret"),
        provider=provider,
    )


def _verify_zendesk_webhook_signature(
    connection: ProviderConnectionConfig,
    *,
    body: bytes,
    headers: dict[str, str] | None = None,
) -> bool | None:
    secret = _resolve_webhook_secret(connection.provider_config, provider="zendesk")
    if not secret:
        return None
    header_map = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    signature = header_map.get("x-zendesk-webhook-signature")
    timestamp = header_map.get("x-zendesk-webhook-signature-timestamp")
    if not signature or not timestamp:
        return False
    try:
        timestamp_value = int(timestamp)
    except ValueError:
        return False
    tolerance_seconds = int(connection.provider_config.get("webhook_tolerance_seconds", 300) or 300)
    if abs(int(time.time()) - timestamp_value) > max(1, tolerance_seconds):
        return False
    signed_payload = timestamp.encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(signature, expected)


def _verify_jira_webhook_signature(
    connection: ProviderConnectionConfig,
    *,
    body: bytes,
    headers: dict[str, str] | None = None,
) -> bool | None:
    secret = _resolve_webhook_secret(connection.provider_config, provider="jira")
    if not secret:
        return None
    header_map = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    signature_header = header_map.get("x-hub-signature")
    if not signature_header or "=" not in signature_header:
        return False
    algorithm_name, provided_digest = signature_header.split("=", 1)
    normalized_algorithm = algorithm_name.strip().lower()
    if normalized_algorithm not in {"sha1", "sha256", "sha384", "sha512"}:
        return False
    digestmod = getattr(hashlib, normalized_algorithm, None)
    if digestmod is None:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, digestmod).hexdigest()
    return hmac.compare_digest(provided_digest.strip().lower(), expected.lower())


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "message", "detail", "description"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
    text = response.text.strip()
    return text or f"{response.status_code} {response.reason_phrase}"


def _map_priority_to_zendesk(priority: str) -> str:
    mapping = {"low": "low", "medium": "normal", "high": "high", "urgent": "urgent"}
    return mapping.get(priority.strip().lower(), "normal")


def _map_status_to_zendesk(status_value: str) -> str:
    mapping = {
        "open": "open",
        "triaged": "open",
        "in_progress": "open",
        "waiting_customer": "pending",
        "waiting_internal": "hold",
        "resolved": "solved",
        "closed": "closed",
        "cancelled": "closed",
        "transferred": "hold",
    }
    return mapping.get(status_value.strip().lower(), status_value.strip().lower() or "open")


def _map_priority_to_freshdesk(priority: str) -> int:
    mapping = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
    return mapping.get(priority.strip().lower(), 2)


def _map_status_to_freshdesk(status_value: str) -> int:
    mapping = {
        "open": 2,
        "triaged": 2,
        "in_progress": 3,
        "waiting_customer": 3,
        "waiting_internal": 3,
        "resolved": 4,
        "closed": 5,
        "cancelled": 5,
        "transferred": 3,
    }
    return mapping.get(status_value.strip().lower(), 2)


def _freshdesk_status_name(value: object) -> str | None:
    mapping = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
    if isinstance(value, int):
        return mapping.get(value)
    return _as_optional_str(value)


def _freshdesk_priority_name(value: object) -> str | None:
    mapping = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
    if isinstance(value, int):
        return mapping.get(value)
    return _as_optional_str(value)


def _map_priority_to_jira(priority: str) -> str:
    mapping = {"low": "Low", "medium": "Medium", "high": "High", "urgent": "Highest"}
    return mapping.get(priority.strip().lower(), "Medium")


def _jira_issue_type(raw_value: object) -> str:
    value = _as_optional_str(raw_value)
    return value or "Task"


def _jira_browse_url(provider_config: dict[str, object], issue_key: str | None) -> str | None:
    if not issue_key:
        return None
    site_url = _as_optional_str(provider_config.get("site_url"))
    if site_url:
        return f"{site_url.rstrip('/')}/browse/{issue_key}"
    return None


def _text_to_adf(text: str) -> dict[str, object]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _flatten_jira_adf(raw_value: object) -> str:
    if not isinstance(raw_value, dict):
        return ""
    fragments: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                fragments.append(text)
            for child in list(node.get("content") or []):
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw_value)
    return " ".join(fragment.strip() for fragment in fragments if fragment.strip())


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
