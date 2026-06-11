from __future__ import annotations

from typing import Protocol

from .agent_policy import AgentToolPolicyBackend
from .specs import ToolSpec
from .types import ToolAuthorizationResult, ToolCall


class ToolAuthorizer(Protocol):
    def authorize(self, spec: ToolSpec, call: ToolCall) -> ToolAuthorizationResult: ...


class DefaultToolAuthorizer:
    """Deterministic policy gate before execution.

    Evaluation order:
      1. Explicit block list (global)
      2. Channel restriction (per-spec)
      3. **Agent tool policy** — per (org, agent, tool_ref) RBAC.  Open-by-default:
         if no policy row exists the tool is allowed.  Only an explicit
         ``enabled=False`` row causes a deny.
      4. Confirmation rules (global + per-spec)
      5. Default allow
    """

    def __init__(
        self,
        *,
        blocked_refs: set[str] | None = None,
        confirmation_refs: set[str] | None = None,
        agent_tool_policy: AgentToolPolicyBackend | None = None,
    ) -> None:
        self._blocked_refs = set(blocked_refs or set())
        self._confirmation_refs = set(confirmation_refs or set())
        self._agent_tool_policy = agent_tool_policy

    def authorize(self, spec: ToolSpec, call: ToolCall) -> ToolAuthorizationResult:
        if spec.ref in self._blocked_refs:
            return ToolAuthorizationResult(decision="deny", reason="tool_ref_blocked")

        if spec.allowed_channels and call.caller.channel not in set(spec.allowed_channels):
            return ToolAuthorizationResult(
                decision="deny",
                reason=f"channel_not_allowed:{call.caller.channel}",
                metadata={"allowed_channels": list(spec.allowed_channels)},
            )

        # Agent tool policy (granular RBAC)
        if (
            self._agent_tool_policy is not None
            and call.caller.tenant_id is not None
            and call.caller.agent_id is not None
        ):
            enabled = self._agent_tool_policy.is_tool_enabled(
                organization_id=call.caller.tenant_id,
                agent_id=call.caller.agent_id,
                tool_ref=spec.ref,
            )
            if enabled is False:
                return ToolAuthorizationResult(
                    decision="deny",
                    reason="agent_tool_policy_denied",
                    metadata={
                        "organization_id": call.caller.tenant_id,
                        "agent_id": call.caller.agent_id,
                        "tool_ref": spec.ref,
                    },
                )

        if spec.ref in self._confirmation_refs:
            return ToolAuthorizationResult(decision="confirm", reason="tool_ref_requires_confirmation")

        if spec.confirmation == "always":
            return ToolAuthorizationResult(decision="confirm", reason="confirmation_policy_always")

        if spec.confirmation == "destructive_only" and spec.annotations.destructive:
            return ToolAuthorizationResult(decision="confirm", reason="destructive_tool_requires_confirmation")

        return ToolAuthorizationResult(decision="allow", reason="policy_allow")

