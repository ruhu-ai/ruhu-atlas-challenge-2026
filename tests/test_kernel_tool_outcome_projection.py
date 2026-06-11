"""WI-9 of doc 36: tool-outcome projection helpers.

P1 invariant: helpers are pure, callable in isolation, enforce the 5-record
cap and the byte budget, and have no production call site.
"""
from __future__ import annotations

import pytest

from ruhu.kernel import (
    TOOL_OUTCOME_HISTORY_MAX,
    TOOL_OUTCOME_OUTPUT_BYTES_BUDGET,
    build_tool_outcome_context,
    sanitize_tool_outcome_for_llm,
)
from ruhu.schemas import ToolCallRecord, ToolOutcomeRecord


def _call(
    tool_ref: str = "crm.submit_lead",
    *,
    status: str = "success",
    payload: dict | None = None,
    reason: str = "ok",
    invocation_id: str = "inv_1",
) -> ToolCallRecord:
    return ToolCallRecord(
        invocation_id=invocation_id,
        tool_ref=tool_ref,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        payload=payload or {},
    )


class TestHistoryCap:
    def test_empty_input_returns_empty(self) -> None:
        assert build_tool_outcome_context([]) == []

    def test_returns_at_most_history_max(self) -> None:
        calls = [_call(invocation_id=f"inv_{i}") for i in range(10)]
        out = build_tool_outcome_context(calls)
        assert len(out) == TOOL_OUTCOME_HISTORY_MAX

    def test_returns_most_recent_first(self) -> None:
        calls = [_call(invocation_id=f"inv_{i}") for i in range(7)]
        out = build_tool_outcome_context(calls)
        # Input is temporally ordered (oldest first); output is reversed
        assert out[0].invocation_id == "inv_6"
        assert out[1].invocation_id == "inv_5"

    def test_custom_history_max_honored(self) -> None:
        calls = [_call(invocation_id=f"inv_{i}") for i in range(10)]
        out = build_tool_outcome_context(calls, history_max=2)
        assert len(out) == 2


class TestStatusMapping:
    @pytest.mark.parametrize(
        "tool_status,expected_outcome",
        [
            ("success", "success"),
            ("error", "failed"),
            ("blocked", "failed"),
            ("cancelled", "failed"),
            ("timeout", "timeout"),
            ("running", "pending"),
            ("requested", "pending"),
            ("confirmation_required", "pending"),
        ],
    )
    def test_status_maps_to_outcome(
        self, tool_status: str, expected_outcome: str
    ) -> None:
        out = build_tool_outcome_context([_call(status=tool_status)])
        assert out[0].status == expected_outcome

    def test_pending_has_no_completed_at(self) -> None:
        out = build_tool_outcome_context([_call(status="running")])
        assert out[0].completed_at is None

    def test_terminal_has_completed_at(self) -> None:
        out = build_tool_outcome_context([_call(status="success")])
        assert out[0].completed_at is not None


class TestRequiredFieldsPreserved:
    def test_tool_name_preserved(self) -> None:
        out = build_tool_outcome_context([_call(tool_ref="some.tool")])
        assert out[0].tool_name == "some.tool"

    def test_summary_includes_status_and_reason(self) -> None:
        out = build_tool_outcome_context(
            [_call(tool_ref="x", status="success", reason="lead_created")]
        )
        assert "x" in out[0].output_summary
        assert "success" in out[0].output_summary
        assert "lead_created" in out[0].output_summary

    def test_error_kind_set_for_failed(self) -> None:
        out = build_tool_outcome_context([_call(status="error")])
        assert out[0].error_kind == "failed"

    def test_error_kind_none_for_success(self) -> None:
        out = build_tool_outcome_context([_call(status="success")])
        assert out[0].error_kind is None


class TestByteBudgetTruncation:
    def test_small_payload_passes_through(self) -> None:
        payload = {"key": "value"}
        out = build_tool_outcome_context([_call(payload=payload)])
        assert out[0].output_data == payload
        assert "[output truncated]" not in out[0].output_summary

    def test_oversize_payload_truncated(self) -> None:
        # 10KB string vastly exceeds the 8KB default budget
        payload = {"big_field": "x" * (10 * 1024)}
        out = build_tool_outcome_context([_call(payload=payload)])
        # Either the field was dropped or the dict is empty
        assert out[0].output_data == {} or "big_field" not in out[0].output_data
        assert "[output truncated]" in out[0].output_summary

    def test_custom_budget_respected(self) -> None:
        payload = {"a": "x" * 200, "b": "y" * 200, "c": "z" * 200}
        out = build_tool_outcome_context(
            [_call(payload=payload)], output_bytes_budget=300
        )
        # Some fields should have been dropped to fit within ~300 bytes
        assert len(out[0].output_data) < len(payload)
        assert "[output truncated]" in out[0].output_summary

    def test_extreme_oversize_results_in_empty_dict(self) -> None:
        # Single huge value that cannot fit even alone
        payload = {"single_huge": "z" * (50 * 1024)}
        out = build_tool_outcome_context(
            [_call(payload=payload)], output_bytes_budget=100
        )
        assert out[0].output_data == {}
        assert "[output truncated]" in out[0].output_summary


class TestSanitizationStub:
    def test_returns_input_unchanged_in_p1(self) -> None:
        record = build_tool_outcome_context([_call(payload={"k": "v"})])[0]
        sanitized = sanitize_tool_outcome_for_llm(record)
        assert sanitized.output_data == record.output_data
        assert sanitized.tool_name == record.tool_name
        assert sanitized.pii_redacted is False

    def test_does_not_mutate_input(self) -> None:
        record = build_tool_outcome_context([_call(payload={"k": "v"})])[0]
        original_dump = record.model_dump()
        sanitize_tool_outcome_for_llm(record)
        assert record.model_dump() == original_dump


class TestNoFeatureFlagCoupling:
    """The helpers must be pure utilities — no env/flag reads (doc 36 WI-9)."""

    def test_helpers_work_when_master_flag_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("RUHU_LLM_MOVE_SELECTION_ENABLED", raising=False)
        out = build_tool_outcome_context([_call()])
        assert len(out) == 1

    def test_helpers_work_when_master_flag_set(self, monkeypatch) -> None:
        monkeypatch.setenv("RUHU_LLM_MOVE_SELECTION_ENABLED", "true")
        out = build_tool_outcome_context([_call()])
        assert len(out) == 1
        # Same result regardless of flag — proves no coupling
