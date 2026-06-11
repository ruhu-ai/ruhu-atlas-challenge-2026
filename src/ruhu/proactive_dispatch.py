"""Result types for ``ConversationKernel._dispatch_proactive_move_selection``.

Per doc 39 WI-6 the kernel owns the proactive-dispatch decision; tool
runtime / integration runtime / pending-action projection paths emit
authoritative signals by calling ``_dispatch_proactive_move_selection``
and acting on the returned ``ProactiveDispatchResult``:

  - ``DISPATCHED`` — the kernel approved the trigger.  The caller should
    proceed to invoke a proactive move-selection turn (WI-7).
  - ``RATE_LIMITED`` — pacing rules forbid this dispatch right now.  The
    caller should drop the trigger silently.
  - ``INELIGIBLE`` — the conversation does not satisfy the P3 opt-in
    matrix (no capture-origin pending action, workflow not opted in, etc.).
    Caller should not retry until conversation state changes.

Realtime/bridge layer is delivery-only and must not import this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .schemas import ProactiveTrigger


class ProactiveDispatchOutcome(StrEnum):
    DISPATCHED = "dispatched"
    RATE_LIMITED = "rate_limited"
    INELIGIBLE = "ineligible"


@dataclass(slots=True, frozen=True)
class ProactiveDispatchResult:
    """Outcome of a proactive-trigger dispatch decision."""

    outcome: ProactiveDispatchOutcome
    reason: str | None = None
    origin_step_id: str | None = None
    trigger: ProactiveTrigger | None = None
