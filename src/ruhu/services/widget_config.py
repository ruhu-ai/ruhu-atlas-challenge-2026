"""Public widget-config projection — extracted from api.py (RP-3.1 step 13,
hazard H6 resolution).

The single ``_widget_config`` implementation: project an agent's stored
widget config + cosmetic persona into the unauthenticated
``WidgetConfigResponse``. Both remaining consumers thread through one
``make_widget_config`` product:

- ``create_app()`` builds it before the ConversationTurnService and passes
  ``company_name_lookup=lambda agent_id: _widget_config(agent_id).company_name``
  (the swallow-to-None semantics live inside the turn service, unchanged);
- ``routes/public_widget.py``'s ``GET /public/widget/config`` takes the same
  callable as an explicit kwarg.

``WidgetConfigResponse`` is an api.py DTO, so this module follows the
routes-module convention: it is imported INSIDE ``create_app()`` (after
``ruhu.api`` has fully loaded), never at api.py's module top.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..api import WidgetConfigResponse

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

__all__ = ["make_widget_config"]


def make_widget_config(
    *,
    runtime_session_factory: "sessionmaker",
) -> Callable[[str], WidgetConfigResponse]:
    def _widget_config(agent_id: str) -> WidgetConfigResponse:
        from ..db_models import AgentRecord as _WidgetAgentRecord
        with runtime_session_factory() as _wc_session:
            _wc_record = _wc_session.get(_WidgetAgentRecord, agent_id)
        if _wc_record is None:
            return WidgetConfigResponse(agent_id=agent_id)
        _wc_stored = dict(getattr(_wc_record, "widget_config", None) or {})
        # Pull cosmetic persona out of agent_settings JSON. This endpoint is
        # unauthenticated, so we expose **only** the customer-facing identity
        # fields. Server-only persona surfaces (``role_title``,
        # ``signoff_template``, and the entire behavioural persona on the
        # AgentDocument) deliberately stay out of this response.
        _wc_persona: dict[str, object] = {}
        try:
            _wc_settings_json = dict(getattr(_wc_record, "settings_json", None) or {})
            _wc_agent_settings = _wc_settings_json.get("agent_settings") or {}
            _wc_persona = dict(_wc_agent_settings.get("persona") or {})
        except Exception:
            _wc_persona = {}
        _wc_pronouns_raw = _wc_persona.get("pronouns")
        _wc_pronouns_custom = _wc_persona.get("pronouns_custom")
        _wc_pronouns = (
            _wc_pronouns_custom
            if _wc_pronouns_raw == "custom"
            else _wc_pronouns_raw
        )
        return WidgetConfigResponse(
            agent_id=agent_id,
            widget_mode=getattr(_wc_record, "widget_mode", "multimodal"),
            company_name=_wc_stored.get("company_name", "Ruhu"),
            button_text=_wc_stored.get("button_text", "Talk to us"),
            primary_color=_wc_stored.get("primary_color", "#E64E20"),
            accent_color=_wc_stored.get("accent_color", "#D44D00"),
            position=_wc_stored.get("position", "bottom-right"),
            show_powered_by=_wc_stored.get("show_powered_by", True),
            welcome_message=_wc_stored.get("welcome_message", "Hi! Ask us anything."),
            persona_name=_wc_persona.get("persona_name"),
            pronouns=_wc_pronouns if isinstance(_wc_pronouns, str) else None,
            avatar_url=_wc_persona.get("avatar_url"),
            greeting_template=_wc_persona.get("greeting_template"),
        )

    return _widget_config
