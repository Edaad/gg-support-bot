"""Response audit ra-2: Sling shifts for per-agent KPIs; mark @jz034 internal.

Revision ID: 0013_audit_shifts
Revises: 0012_response_audit
Create Date: 2026-09-30
"""

from migrations import helpers as h

revision = "0013_audit_shifts"
down_revision = "0012_response_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "audit_shifts",
        """
        sling_shift_id TEXT PRIMARY KEY,
        sling_user_id BIGINT,
        agent_name TEXT,
        starts_at TIMESTAMPTZ NOT NULL,
        ends_at TIMESTAMPTZ NOT NULL,
        label TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.create_index("ix_audit_shifts_starts_at", "audit_shifts", "(starts_at)")
    h.create_index("ix_audit_shifts_ends_at", "audit_shifts", "(ends_at)")

    # Staff test chat: never part of the response audit (also caught by the
    # "/ 1111-1111 /" title rule). Idempotent; no-op when the chat is absent.
    h.execute_data(
        "UPDATE support_group_chats SET is_internal = true "
        "WHERE telegram_chat_title = 'GTO / 1111-1111 / @jz034' "
        "AND is_internal = false"
    )


def downgrade() -> None:
    raise NotImplementedError
