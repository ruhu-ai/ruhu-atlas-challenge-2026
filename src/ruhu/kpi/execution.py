from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import ExecutionIntent


@dataclass(slots=True)
class AdapterExecutionOutcome:
    status: str
    changed_object_refs: list[dict[str, object]]
    before_state_summary: dict[str, object]
    after_state_summary: dict[str, object]
    diff_artifact_ref: str | None
    adapter_diagnostics: dict[str, object]
    rollback_handle: dict[str, object] | None
    error_code: str | None = None
    error_message: str | None = None


class KPIExecutionAdapter(Protocol):
    adapter_kind: str

    def preview(self, intent: ExecutionIntent) -> AdapterExecutionOutcome: ...

    def apply(self, intent: ExecutionIntent) -> AdapterExecutionOutcome: ...


class KPIExecutionAdapterRegistry:
    def __init__(self, adapters: list[KPIExecutionAdapter] | None = None) -> None:
        self._adapters = {adapter.adapter_kind: adapter for adapter in (adapters or [])}

    def get(self, adapter_kind: str) -> KPIExecutionAdapter | None:
        return self._adapters.get(adapter_kind)


class TemplateValidationAdapter:
    adapter_kind = "template_validation"

    def preview(self, intent: ExecutionIntent) -> AdapterExecutionOutcome:
        payload = dict(intent.approved_payload)
        return AdapterExecutionOutcome(
            status="preview_succeeded",
            changed_object_refs=[],
            before_state_summary={"preview_type": "template_validation_only"},
            after_state_summary={"requested_change": payload},
            diff_artifact_ref=None,
            adapter_diagnostics={
                "adapter_kind": self.adapter_kind,
                "validation_only": True,
                "message": "Preview validated the execution payload shape only. No target system state was mutated or read.",
            },
            rollback_handle=None,
        )

    def apply(self, intent: ExecutionIntent) -> AdapterExecutionOutcome:
        return AdapterExecutionOutcome(
            status="apply_failed",
            changed_object_refs=[],
            before_state_summary={},
            after_state_summary={},
            diff_artifact_ref=None,
            adapter_diagnostics={
                "adapter_kind": self.adapter_kind,
                "validation_only": True,
            },
            rollback_handle=None,
            error_code="adapter_not_bound",
            error_message="No production execution adapter is registered for this recommendation.",
        )
