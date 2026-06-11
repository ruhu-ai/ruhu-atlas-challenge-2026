"""WI-5.4: data-migrate fallback_policy "bounded" → "permissive".

Revision ID: 0078
Revises: 0077
Create Date: 2026-05-01 20:00:00.000000

The schema narrows ``fallback_policy`` from
``Literal["strict", "bounded", "permissive"]`` to
``Literal["strict", "permissive"]``. Existing rows that recorded the
legacy ``"bounded"`` value are rewritten to ``"permissive"`` so the
narrowed Pydantic models accept them on read.

Two storage sites:

1. ``turn_traces.rules_json["__trace_extensions__"]["decision_observability"]
   ["fallback_policy"]`` — every persisted classifier turn before this
   migration that recorded a fallback policy at all carries either
   ``"strict"`` or ``"bounded"`` (today's default). Rewrite ``"bounded"``.

2. ``agents.settings_json`` — classifier config dict carries
   ``fallback_policy`` per ``AgentClassifierConfig``. Rewrite
   ``"bounded"`` to ``"permissive"`` wherever it appears.

Forward-only: the downgrade is a no-op because reverting would have
to choose ``"bounded"`` or ``"permissive"`` arbitrarily, and the
narrowed schema can't accept ``"bounded"`` anyway. Operators rolling
back the schema would need to widen the Literal first; the data is
already valid for the narrowed shape after this migration.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    _rewrite_turn_traces(bind)
    _rewrite_agent_settings(bind)


def downgrade() -> None:  # noqa: D401 — no-op; see module docstring
    return None


def _rewrite_turn_traces(bind) -> None:
    """Walk turn_traces rows and rewrite the fallback_policy in-place."""
    rows = bind.execute(
        sa.text(
            "SELECT trace_id, rules_json FROM turn_traces "
            "WHERE rules_json IS NOT NULL"
        )
    ).fetchall()
    update = sa.text(
        "UPDATE turn_traces SET rules_json = :rules WHERE trace_id = :trace_id"
    ).bindparams(sa.bindparam("rules", type_=sa.JSON()))
    for trace_id, rules_json in rows:
        rules = _coerce_dict(rules_json)
        if rules is None:
            continue
        extensions = rules.get("__trace_extensions__")
        if not isinstance(extensions, dict):
            continue
        observability = extensions.get("decision_observability")
        if not isinstance(observability, dict):
            continue
        if observability.get("fallback_policy") != "bounded":
            continue
        observability["fallback_policy"] = "permissive"
        bind.execute(update, {"rules": rules, "trace_id": trace_id})


def _rewrite_agent_settings(bind) -> None:
    """Walk agents rows and rewrite any "bounded" fallback_policy values."""
    rows = bind.execute(
        sa.text(
            "SELECT agent_id, settings_json FROM agents "
            "WHERE settings_json IS NOT NULL"
        )
    ).fetchall()
    update = sa.text(
        "UPDATE agents SET settings_json = :settings WHERE agent_id = :agent_id"
    ).bindparams(sa.bindparam("settings", type_=sa.JSON()))
    for agent_id, settings_json in rows:
        settings = _coerce_dict(settings_json)
        if settings is None:
            continue
        if not _replace_bounded_recursive(settings):
            continue
        bind.execute(update, {"settings": settings, "agent_id": agent_id})


def _coerce_dict(value: object) -> dict | None:
    """SQLAlchemy may hand us a parsed dict (Postgres JSONB) or a raw string (SQLite TEXT)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json as _json

        try:
            parsed = _json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _replace_bounded_recursive(node: object) -> bool:
    """Walk nested dicts; rewrite any fallback_policy=='bounded' to 'permissive'.

    Returns True if at least one rewrite happened.
    """
    if not isinstance(node, dict):
        return False
    changed = False
    for key, value in list(node.items()):
        if key == "fallback_policy" and value == "bounded":
            node[key] = "permissive"
            changed = True
            continue
        if isinstance(value, dict):
            if _replace_bounded_recursive(value):
                changed = True
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    if _replace_bounded_recursive(item):
                        changed = True
    return changed


