"""Audit log of MCP tool calls.

Revision ID: 0027_mcp_tool_calls
Revises: 0026_acct_balances
Create Date: 2026-10-12
"""

from migrations import helpers as h

revision = "0027_mcp_tool_calls"
down_revision = "0026_acct_balances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "mcp_tool_calls",
        """
        id SERIAL PRIMARY KEY,
        actor VARCHAR(64) NOT NULL,
        tool VARCHAR(64) NOT NULL,
        transport VARCHAR(16) NOT NULL,
        is_write BOOLEAN NOT NULL DEFAULT false,
        args_json JSONB NULL,
        outcome VARCHAR(16) NOT NULL,
        error_message TEXT NULL,
        duration_ms INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.create_index(
        "ix_mtc_tool_created_at", "mcp_tool_calls", "USING btree (tool, created_at)"
    )
    h.create_index(
        "ix_mtc_actor_created_at", "mcp_tool_calls", "USING btree (actor, created_at)"
    )


def downgrade() -> None:
    raise NotImplementedError
