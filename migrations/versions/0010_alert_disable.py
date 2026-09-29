"""Add per-alert destination disable conditions.

Revision ID: 0010_alert_disable
Revises: 0009_crypto_outbound
Create Date: 2026-09-28
"""

from migrations import helpers as h

revision = "0010_alert_disable"
down_revision = "0009_crypto_outbound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column(
        "deposit_method_alerts",
        "disable_enabled",
        "BOOLEAN NOT NULL DEFAULT false",
    )
    h.add_column(
        "deposit_method_alerts",
        "disable_conditions",
        "JSONB NOT NULL DEFAULT '[]'::jsonb",
    )
    h.add_column(
        "deposit_method_alerts",
        "last_disable_fired_week_id",
        "VARCHAR(10)",
    )
    h.add_column(
        "deposit_method_alerts",
        "last_disable_fired_at",
        "TIMESTAMP WITH TIME ZONE",
    )


def downgrade() -> None:
    raise NotImplementedError
