from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from jsonschema import Draft7Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import BrowserApprovalKind

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None


BrowserCredentialKind = Literal["password", "api_key", "oauth", "session", "mfa"]
BrowserArtifactKind = Literal["screenshot", "result_json", "download", "action_log"]
BrowserDomActionKind = Literal["fill", "click", "download", "upload", "wait_for_selector"]
BrowserDomExtractionAttribute = Literal["text", "value", "href", "aria_label", "data"]

_DEFAULT_OBJECT_SCHEMA: dict[str, Any] = {"type": "object"}


def _normalize_domain(value: str) -> str:
    candidate = value.strip().lower()
    if not candidate:
        raise ValueError("allowed domain cannot be empty")
    if "*" in candidate:
        raise ValueError("wildcard domains are not supported")
    if "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("allowed domain must not include a path, query, or fragment")
        candidate = parsed.hostname or ""
    else:
        parsed = urlparse(f"https://{candidate}")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("allowed domain must be a hostname or origin")
        candidate = parsed.hostname or ""
    if not candidate or "." not in candidate:
        raise ValueError("allowed domain must be a fully qualified hostname")
    return candidate.rstrip(".")


def normalize_allowed_domains(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        domain = _normalize_domain(value)
        if domain in seen:
            continue
        seen.add(domain)
        normalized.append(domain)
    if not normalized:
        raise ValueError("at least one allowed domain is required")
    return normalized


def is_url_allowed(url: str, allowed_domains: Iterable[str]) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    for domain in normalize_allowed_domains(allowed_domains):
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False


class BrowserCredentialRequirement(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kind: BrowserCredentialKind
    name: str
    provider: str | None = None
    auth_type: str | None = None
    required: bool = True
    description: str | None = None


class BrowserTaskPackRetryPolicy(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    max_attempts: int = Field(default=1, ge=1, le=5)
    retryable_error_kinds: list[str] = Field(
        default_factory=lambda: ["network", "timeout", "rate_limited"]
    )


class BrowserTaskPackExecutionPolicy(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    max_execution_seconds: int = Field(default=120, ge=5, le=1800)
    max_steps: int = Field(default=50, ge=1, le=500)
    allow_downloads: bool = False
    allow_uploads: bool = False
    capture_screenshots: bool = True
    retry_policy: BrowserTaskPackRetryPolicy = Field(default_factory=BrowserTaskPackRetryPolicy)


class BrowserTaskPackApprovalPolicy(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    approval_required: bool = False
    approval_kinds: list[BrowserApprovalKind] = Field(default_factory=list)
    approval_ttl_seconds: int = Field(default=300, ge=15, le=86_400)
    require_reapproval_after_navigation: bool = False

    @model_validator(mode="after")
    def normalize_approval_kinds(self) -> BrowserTaskPackApprovalPolicy:
        kinds = list(dict.fromkeys(self.approval_kinds))
        if self.approval_required and not kinds:
            kinds = ["generic_access"]
        if not self.approval_required:
            kinds = []
        self.approval_kinds = kinds
        return self


class BrowserTaskPackArtifactPolicy(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    allowed_artifacts: list[BrowserArtifactKind] = Field(
        default_factory=lambda: ["screenshot", "result_json", "action_log"]
    )
    retain_artifacts: bool = True
    redact_sensitive_values: bool = True
    allowed_download_content_types: list[str] = Field(default_factory=list)
    max_download_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    screenshot_redaction_required: bool = True
    screenshot_redaction_selectors: list[str] = Field(
        default_factory=lambda: [
            'input[type="password"]',
            'input[type="email"]',
            'input[name*="token" i]',
            'input[name*="secret" i]',
            'input[name*="password" i]',
            'textarea[name*="secret" i]',
            '[data-ruhu-redact]',
        ]
    )

    @field_validator("allowed_download_content_types")
    @classmethod
    def normalize_content_types(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip().lower()
            if not candidate:
                continue
            if "/" not in candidate:
                raise ValueError("download content types must be MIME types")
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @model_validator(mode="after")
    def validate_download_policy(self) -> BrowserTaskPackArtifactPolicy:
        if "download" in self.allowed_artifacts and not self.allowed_download_content_types:
            raise ValueError("download artifacts require allowed_download_content_types")
        return self


class BrowserTaskPackOperatorPolicy(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    operator_takeover_enabled: bool = True
    operator_takeover_after_seconds: int | None = Field(default=None, ge=1, le=3600)
    operator_message: str | None = None


class BrowserTaskPackDomAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kind: BrowserDomActionKind
    selector: str
    value: str | None = None
    value_from_input: str | None = None
    timeout_ms: int = Field(default=10_000, ge=100, le=120_000)

    @model_validator(mode="after")
    def validate_action_value(self) -> BrowserTaskPackDomAction:
        if self.kind == "fill":
            has_literal = self.value is not None
            has_input = self.value_from_input is not None
            if has_literal == has_input:
                raise ValueError("fill actions require exactly one of value or value_from_input")
        elif self.kind == "upload":
            if self.value is not None or self.value_from_input is None:
                raise ValueError("upload actions require value_from_input and no literal value")
        elif self.value is not None or self.value_from_input is not None:
            raise ValueError("only fill and upload actions can define value fields")
        return self


class BrowserTaskPackDomExtraction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    field: str
    selector: str
    attribute: BrowserDomExtractionAttribute = "text"
    data_attribute: str | None = None
    required: bool = False
    timeout_ms: int = Field(default=5_000, ge=100, le=120_000)

    @model_validator(mode="after")
    def validate_data_attribute(self) -> BrowserTaskPackDomExtraction:
        if self.attribute == "data" and not self.data_attribute:
            raise ValueError("data extractions require data_attribute")
        if self.attribute != "data" and self.data_attribute is not None:
            raise ValueError("data_attribute is only valid for data extractions")
        return self


class BrowserTaskPackBrowserPlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actions: list[BrowserTaskPackDomAction] = Field(default_factory=list)
    extractions: list[BrowserTaskPackDomExtraction] = Field(default_factory=list)


class BrowserTaskPack(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    pack_id: str
    version: str
    display_name: str
    description: str | None = None
    allowed_domains: list[str]
    start_url: str | None = None
    performs_write: bool = False
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_DEFAULT_OBJECT_SCHEMA))
    result_schema: dict[str, Any] = Field(default_factory=lambda: dict(_DEFAULT_OBJECT_SCHEMA))
    credentials: list[BrowserCredentialRequirement] = Field(default_factory=list)
    execution_policy: BrowserTaskPackExecutionPolicy = Field(
        default_factory=BrowserTaskPackExecutionPolicy
    )
    approval_policy: BrowserTaskPackApprovalPolicy = Field(default_factory=BrowserTaskPackApprovalPolicy)
    artifact_policy: BrowserTaskPackArtifactPolicy = Field(default_factory=BrowserTaskPackArtifactPolicy)
    operator_policy: BrowserTaskPackOperatorPolicy = Field(default_factory=BrowserTaskPackOperatorPolicy)
    browser_plan: BrowserTaskPackBrowserPlan | None = None

    @field_validator("pack_id", "version")
    @classmethod
    def require_token(cls, value: str) -> str:
        if not value:
            raise ValueError("value cannot be empty")
        return value

    @field_validator("allowed_domains")
    @classmethod
    def validate_allowed_domains(cls, value: list[str]) -> list[str]:
        return normalize_allowed_domains(value)

    @field_validator("input_schema", "result_schema")
    @classmethod
    def validate_schema_shape(cls, value: dict[str, Any]) -> dict[str, Any]:
        schema_type = value.get("type")
        if schema_type is not None and schema_type != "object":
            raise ValueError("browser task pack schemas must be object schemas")
        try:
            Draft7Validator.check_schema(value)
        except SchemaError as exc:
            raise ValueError(f"invalid browser task pack JSON schema: {exc.message}") from exc
        return value

    @model_validator(mode="after")
    def validate_pack_policy(self) -> BrowserTaskPack:
        if self.start_url and not is_url_allowed(self.start_url, self.allowed_domains):
            raise ValueError("start_url must match an allowed domain")
        if self.performs_write and "change_confirmation" not in self.approval_policy.approval_kinds:
            raise ValueError("packs that perform writes require change_confirmation approval")
        if self.browser_plan is not None:
            step_count = len(self.browser_plan.actions) + len(self.browser_plan.extractions)
            if step_count > self.execution_policy.max_steps:
                raise ValueError("browser_plan exceeds execution_policy.max_steps")
            action_kinds = {action.kind for action in self.browser_plan.actions}
            if "download" in action_kinds and not self.execution_policy.allow_downloads:
                raise ValueError("browser_plan download actions require execution_policy.allow_downloads")
            if "download" in action_kinds and "download" not in self.artifact_policy.allowed_artifacts:
                raise ValueError("browser_plan download actions require download artifacts to be allowed")
            if "upload" in action_kinds and not self.execution_policy.allow_uploads:
                raise ValueError("browser_plan upload actions require execution_policy.allow_uploads")
        return self


class BrowserTaskPackRegistry:
    def __init__(self, packs: Iterable[BrowserTaskPack] | None = None) -> None:
        self._packs: dict[tuple[str, str], BrowserTaskPack] = {}
        self._latest_versions: dict[str, str] = {}
        for pack in packs or []:
            self.register(pack)

    def register(self, pack: BrowserTaskPack) -> None:
        key = (pack.pack_id, pack.version)
        if key in self._packs:
            raise ValueError(f"browser task pack already registered: {pack.pack_id}@{pack.version}")
        self._packs[key] = pack
        self._latest_versions[pack.pack_id] = pack.version

    def get(self, pack_id: str, version: str | None = None) -> BrowserTaskPack:
        selected_version = version or self._latest_versions.get(pack_id)
        if selected_version is None:
            raise KeyError(pack_id)
        key = (pack_id, selected_version)
        if key not in self._packs:
            raise KeyError(f"{pack_id}@{selected_version}")
        return self._packs[key]

    def list_packs(self) -> list[BrowserTaskPack]:
        return list(self._packs.values())

    def is_url_allowed_for_pack(self, pack_id: str, url: str, version: str | None = None) -> bool:
        pack = self.get(pack_id, version)
        return is_url_allowed(url, pack.allowed_domains)


@dataclass(slots=True)
class BrowserTaskPackAccessPolicy:
    allowed_pack_ids: set[str] | None = None
    org_allowed_pack_ids: dict[str, set[str]] = field(default_factory=dict)
    agent_allowed_pack_ids: dict[tuple[str | None, str], set[str]] = field(default_factory=dict)

    def assert_allowed(
        self,
        *,
        pack_id: str,
        organization_id: str | None,
        agent_id: str | None,
    ) -> None:
        if self.allowed_pack_ids is not None and pack_id not in self.allowed_pack_ids:
            raise ValueError(f"browser task pack is not enabled: {pack_id}")
        if organization_id is not None:
            allowed_for_org = self.org_allowed_pack_ids.get(organization_id)
            if allowed_for_org is not None and pack_id not in allowed_for_org:
                raise ValueError(f"browser task pack is not enabled for this organization: {pack_id}")
        if agent_id is not None:
            allowed_for_agent = self.agent_allowed_pack_ids.get((organization_id, agent_id))
            if allowed_for_agent is None:
                allowed_for_agent = self.agent_allowed_pack_ids.get((None, agent_id))
            if allowed_for_agent is not None and pack_id not in allowed_for_agent:
                raise ValueError(f"browser task pack is not enabled for this agent: {pack_id}")


def builtin_browser_task_packs() -> list[BrowserTaskPack]:
    return [
        BrowserTaskPack(
            pack_id="invoice_lookup",
            version="1.0.0",
            display_name="Invoice lookup",
            description="Open a billing portal and read invoice status or balance details.",
            allowed_domains=["billing.example.com"],
            start_url="https://billing.example.com/invoices",
            input_schema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "customer_email": {"type": "string"},
                },
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "status": {"type": "string"},
                    "amount_due": {"type": "string"},
                    "due_date": {"type": "string"},
                },
                "additionalProperties": False,
            },
            credentials=[
                BrowserCredentialRequirement(
                    kind="session",
                    name="billing_session",
                    auth_type="browser_session",
                    description="Scoped browser session for the billing portal.",
                )
            ],
            browser_plan=BrowserTaskPackBrowserPlan(
                actions=[
                    BrowserTaskPackDomAction(
                        kind="fill",
                        selector='[data-ruhu-field="invoice_id"]',
                        value_from_input="invoice_id",
                    ),
                    BrowserTaskPackDomAction(kind="click", selector='[data-ruhu-action="search"]'),
                    BrowserTaskPackDomAction(kind="wait_for_selector", selector='[data-ruhu-result="invoice"]'),
                ],
                extractions=[
                    BrowserTaskPackDomExtraction(field="invoice_id", selector='[data-ruhu-result="invoice_id"]'),
                    BrowserTaskPackDomExtraction(field="status", selector='[data-ruhu-result="status"]'),
                    BrowserTaskPackDomExtraction(field="amount_due", selector='[data-ruhu-result="amount_due"]'),
                    BrowserTaskPackDomExtraction(field="due_date", selector='[data-ruhu-result="due_date"]'),
                ],
            ),
        ),
        BrowserTaskPack(
            pack_id="order_status_lookup",
            version="1.0.0",
            display_name="Order status lookup",
            description="Open a merchant admin portal and read shipment or fulfillment status.",
            allowed_domains=["merchant.example.com"],
            start_url="https://merchant.example.com/orders",
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "customer_email": {"type": "string"},
                },
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "status": {"type": "string"},
                    "tracking_number": {"type": "string"},
                    "estimated_delivery": {"type": "string"},
                },
                "additionalProperties": False,
            },
            credentials=[
                BrowserCredentialRequirement(
                    kind="session",
                    name="merchant_session",
                    auth_type="browser_session",
                    description="Scoped browser session for the merchant portal.",
                )
            ],
            browser_plan=BrowserTaskPackBrowserPlan(
                actions=[
                    BrowserTaskPackDomAction(
                        kind="fill",
                        selector='[data-ruhu-field="order_id"]',
                        value_from_input="order_id",
                    ),
                    BrowserTaskPackDomAction(kind="click", selector='[data-ruhu-action="search"]'),
                    BrowserTaskPackDomAction(kind="wait_for_selector", selector='[data-ruhu-result="order"]'),
                ],
                extractions=[
                    BrowserTaskPackDomExtraction(field="order_id", selector='[data-ruhu-result="order_id"]'),
                    BrowserTaskPackDomExtraction(field="status", selector='[data-ruhu-result="status"]'),
                    BrowserTaskPackDomExtraction(field="tracking_number", selector='[data-ruhu-result="tracking_number"]'),
                    BrowserTaskPackDomExtraction(field="estimated_delivery", selector='[data-ruhu-result="estimated_delivery"]'),
                ],
            ),
        ),
        BrowserTaskPack(
            pack_id="ticket_status_lookup",
            version="1.0.0",
            display_name="Ticket status lookup",
            description="Open a support desk and read the current state of an existing ticket.",
            allowed_domains=["support.example.com"],
            start_url="https://support.example.com/tickets",
            input_schema={
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string"},
                    "customer_email": {"type": "string"},
                },
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string"},
                    "status": {"type": "string"},
                    "assignee": {"type": "string"},
                    "last_update": {"type": "string"},
                },
                "additionalProperties": False,
            },
            credentials=[
                BrowserCredentialRequirement(
                    kind="session",
                    name="support_session",
                    auth_type="browser_session",
                    description="Scoped browser session for the support desk.",
                )
            ],
            browser_plan=BrowserTaskPackBrowserPlan(
                actions=[
                    BrowserTaskPackDomAction(
                        kind="fill",
                        selector='[data-ruhu-field="ticket_id"]',
                        value_from_input="ticket_id",
                    ),
                    BrowserTaskPackDomAction(kind="click", selector='[data-ruhu-action="search"]'),
                    BrowserTaskPackDomAction(kind="wait_for_selector", selector='[data-ruhu-result="ticket"]'),
                ],
                extractions=[
                    BrowserTaskPackDomExtraction(field="ticket_id", selector='[data-ruhu-result="ticket_id"]'),
                    BrowserTaskPackDomExtraction(field="status", selector='[data-ruhu-result="status"]'),
                    BrowserTaskPackDomExtraction(field="assignee", selector='[data-ruhu-result="assignee"]'),
                    BrowserTaskPackDomExtraction(field="last_update", selector='[data-ruhu-result="last_update"]'),
                ],
            ),
        ),
        BrowserTaskPack(
            pack_id="appointment_reschedule",
            version="1.0.0",
            display_name="Appointment reschedule",
            description="Prepare an appointment change and require confirmation before submitting it.",
            allowed_domains=["scheduling.example.com"],
            start_url="https://scheduling.example.com/appointments",
            performs_write=True,
            input_schema={
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "requested_time": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["appointment_id", "requested_time"],
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "status": {"type": "string"},
                    "confirmed_time": {"type": "string"},
                },
                "additionalProperties": False,
            },
            credentials=[
                BrowserCredentialRequirement(
                    kind="session",
                    name="scheduling_session",
                    auth_type="browser_session",
                    description="Scoped browser session for the scheduling portal.",
                )
            ],
            approval_policy=BrowserTaskPackApprovalPolicy(
                approval_required=True,
                approval_kinds=["change_confirmation"],
                approval_ttl_seconds=300,
                require_reapproval_after_navigation=True,
            ),
            execution_policy=BrowserTaskPackExecutionPolicy(max_execution_seconds=180, max_steps=75),
        ),
    ]


def load_browser_task_pack_file(path: str | Path) -> list[BrowserTaskPack]:
    file_path = Path(path)
    payload = _load_task_pack_payload(file_path)
    items = _extract_task_pack_items(payload, file_path=file_path)
    return [BrowserTaskPack.model_validate(item) for item in items]


def load_browser_task_pack_registry(
    path: str | Path | None,
    *,
    include_builtin: bool = True,
) -> BrowserTaskPackRegistry:
    registry = BrowserTaskPackRegistry(builtin_browser_task_packs() if include_builtin else [])
    if path is None:
        return registry
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"browser task pack path does not exist: {source_path}")
    files = [source_path] if source_path.is_file() else _iter_task_pack_files(source_path)
    for file_path in files:
        for pack in load_browser_task_pack_file(file_path):
            try:
                registry.register(pack)
            except ValueError as exc:
                raise ValueError(f"{file_path}: {exc}") from exc
    return registry


def _iter_task_pack_files(path: Path) -> list[Path]:
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in {".json", ".yaml", ".yml"}
    )


def _load_task_pack_payload(path: Path) -> Any:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load browser task pack YAML files")
        return yaml.safe_load(text)
    raise ValueError(f"unsupported browser task pack file type: {path}")


def _extract_task_pack_items(payload: Any, *, file_path: Path) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("browser_task_packs"), list):
        items = payload["browser_task_packs"]
    elif isinstance(payload, dict) and isinstance(payload.get("task_pack"), dict):
        items = [payload["task_pack"]]
    elif isinstance(payload, dict):
        items = [payload]
    else:
        raise ValueError(f"browser task pack file must contain an object or list: {file_path}")
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"browser task pack entries must be objects: {file_path}")
        result.append(item)
    return result
