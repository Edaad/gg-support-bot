"""Tool registration. Each module exposes plain functions plus ``register(mcp)``."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

# Names of tools that change data; they take ``confirm`` and are logged as writes.
WRITE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "bind_payment_to_group",
        "update_cashout_record",
        "update_manual_deposit_request",
        "create_expense",
        "pause_variant",
        "resume_variant",
        "complete_monday_review_task",
        "add_monday_review_note",
        "create_monday_review_task",
        # cashouts / expenses
        "create_cashout_record",
        "delete_cashout_record",
        "add_cashout_destination",
        "update_cashout_destination",
        "remove_cashout_destination",
        "add_cashout_send",
        "update_cashout_send",
        "remove_cashout_send",
        "update_expense",
        "delete_expense",
    }
)


def register_all(mcp: MCPServer) -> None:
    from mcp_server.tools import (
        analytics,
        audit_tools,
        bonuses,
        cashouts,
        escalations,
        expenses,
        monday_review,
        ops,
        overview,
        payments,
        pool_pay,
        reference,
        sends_balances,
    )

    for module in (
        reference,
        payments,
        pool_pay,
        cashouts,
        bonuses,
        expenses,
        sends_balances,
        analytics,
        escalations,
        audit_tools,
        ops,
        overview,
        monday_review,
    ):
        module.register(mcp)
    for module in (payments, pool_pay, cashouts, expenses, ops, monday_review):
        module.register_writes(mcp)
