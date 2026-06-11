"""Rename legacy ``gtpl_demo_sales`` template to ``gtpl_sales_agent``.

Also renames the embedded graph id/name from ``demo_sales_graph`` / ``Demo Sales Graph``
to ``sales_agent`` / ``Sales Agent`` inside the stored ``state_graph_json``.

The new-name row is (re)seeded on startup by ``_seed_graph_templates``; this
migration deletes any orphaned legacy row so the two don't coexist.
"""

from __future__ import annotations

from alembic import op

revision = "0047"
down_revision = "0046_audit_events_trace_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM graph_templates WHERE template_id = 'gtpl_demo_sales'"
    )


def downgrade() -> None:
    # No-op: the legacy template is no longer shipped as a system template,
    # so there is no authoritative source to restore it from on downgrade.
    pass
