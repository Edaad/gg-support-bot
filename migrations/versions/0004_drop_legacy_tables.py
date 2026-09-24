"""Drop legacy tables: v1 payment config, the old main.py bot, and unused Glide audit.

The bot reads club_payment_* only; nothing reads these tables.

Revision ID: 0004_drop_legacy_tables
Revises: 0003_bypass_chat_id_not_null
Create Date: 2026-09-23
"""

from migrations import helpers as h

revision = "0004_drop_legacy_tables"
down_revision = "0003_bypass_chat_id_not_null"
branch_labels = None
depends_on = None

# Children before parents (method_variants and friends reference payment_methods).
LEGACY_TABLES = (
    "method_variants",
    "payment_sub_options",
    "payment_method_tiers",
    "payment_methods",
    "group_club",
    "user_commands",
    "glide_audit_lines",
)


def upgrade() -> None:
    for table in LEGACY_TABLES:
        h.drop_table(table)


def downgrade() -> None:
    raise NotImplementedError("Forward-only: add a new revision instead.")
