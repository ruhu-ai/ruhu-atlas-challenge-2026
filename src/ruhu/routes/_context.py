"""RouterContext — the near-universal dependency bundle for extracted routers.

Frozen dataclass carrying the ~10 dependencies that almost every route group
needs (RP-3.1 blueprint, DI verdict). Group-specific dependencies stay
explicit keyword arguments on the individual ``build_X_router`` factories.
This is the seed for the RP-3.2 AppContainer.

Only fields, no logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from ..kernel import ConversationKernel
    from ..realtime import RealtimeControlPlane
    from ..registry import SQLAlchemyAgentRegistry
    from ..runtime_config import RuntimeSettings


@dataclass(frozen=True)
class RouterContext:
    kernel: ConversationKernel
    agent_registry: SQLAlchemyAgentRegistry
    settings: RuntimeSettings
    auth_enabled: bool
    bootstrap_organization_id: str | None
    org_rate_limiter: object | None
    runtime_session_factory: sessionmaker | None
    auth_session_factory: sessionmaker | None
    realtime_control_plane: RealtimeControlPlane | None
    # Forward seam: ConversationTurnService lands at blueprint step 11.
    turn_service: object | None = None
