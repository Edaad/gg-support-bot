"""Reusable prompts that chain the tools for common operator questions."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def investigate_player(query: str) -> str:
    """Trace one player across groups, payments, cashouts and tickets."""
    return (
        f"Investigate the player identified by {query!r} (a group title fragment, "
        "gg_player_id or chat_id) in the GG Support dashboard.\n\n"
        "1. Read gg://reference/conventions, then call find_group to resolve the "
        "player's support group(s) and club.\n"
        "2. Call search_payments with q set to the group title (and method=all) "
        "for recent deposits; note unbound or test payments.\n"
        "3. Call list_cashout_records with q for active or recent cashouts and "
        "get_cashout_record for the details of any active one.\n"
        "4. Call list_tickets with chat_id for the last few days and "
        "get_ticket_messages on anything unresolved; check "
        "list_escalation_events for the chat.\n"
        "5. Summarise: who the player is, money in/out, open issues, and anything "
        "an operator should act on. Keep PII masked unless asked."
    )


def daily_ops_check(date: str = "") -> str:
    """Morning checklist over unbound payments, pool pay, cashouts and tickets."""
    day = date.strip() or "yesterday (America/New_York)"
    return (
        f"Run the daily operations check for {day}.\n\n"
        "1. Call get_dashboard_overview for the headline counts.\n"
        "2. Call search_payments(status='unbound') per rail that shows unbound "
        "payments and list who needs binding.\n"
        "3. Call list_manual_deposit_requests(trade_record_checked=false) for "
        "Pool Pay requests awaiting trade-record verification.\n"
        "4. Call list_cashout_records(status='active') and flag records with "
        "do_not_send or a large remaining amount.\n"
        "5. Call list_tickets for the day grouped by category and "
        "list_escalation_events for the same window.\n"
        "6. Report a short prioritised list of actions. Do not run write tools "
        "unless the operator asks."
    )


def monday_review(week_of: str = "") -> str:
    """Walk the heads' Monday Review checklist and report what is still open."""
    week = week_of.strip() or "the current week"
    return (
        f"Run the Monday Review for {week}.\n\n"
        "1. Call get_monday_review_board (pass week_of if given) and read every "
        "task: title, assignees, links, persistent_notes and week_notes.\n"
        "2. List the pending tasks first, grouped by assignee, each with its "
        "links and the notes to keep in mind; then the done tasks with who "
        "completed them and their completion notes.\n"
        "3. Mention upcoming_notes that are queued for later Mondays.\n"
        "4. If asked to check something off, call complete_monday_review_task "
        "with confirm=true only after the operator confirms who did it; to "
        "remember something for next Monday, use add_monday_review_note.\n"
        "5. For trends, call list_monday_review_history and point out tasks "
        "that were missed in recent weeks."
    )


def register(mcp: MCPServer) -> None:
    mcp.prompt(
        name="investigate_player",
        title="Investigate a player",
        description="Trace one player across groups, payments, cashouts and tickets.",
    )(investigate_player)
    mcp.prompt(
        name="daily_ops_check",
        title="Daily ops check",
        description="Morning checklist over unbound payments, pool pay, cashouts.",
    )(daily_ops_check)
    mcp.prompt(
        name="monday_review",
        title="Monday Review",
        description="Walk the heads' weekly checklist: pending, done, notes.",
    )(monday_review)
