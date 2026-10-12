"""MCPServer factory: tools, resources, prompts and the audit middleware."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

INSTRUCTIONS = """GG Support dashboard data server (poker club operations).

Four clubs: Round Table (RT), Creator Club (CC), ClubGTO (GTO) and Aces Table (AT,
routed through RT/CC). Start with list_clubs or get_dashboard_overview; use
find_group to resolve a player from a group title ("GTO / 8190-5287 / name"),
gg_player_id or chat_id. Read gg://reference/conventions before filtering.

Amounts: ingested payments use amount_cents (+ amount_usd); cashouts, bonuses,
expenses and pool-pay requests use dollars. Dates: payments/escalations/sends
filter on UTC datetimes (YYYY-MM-DD = that America/New_York day); tickets,
activity and audits use one ET activity_date.

Monday Review is the heads' weekly checklist: get_monday_review_board shows what
must be done this Monday (links, notes, assignees, done state);
complete_monday_review_task checks a task off and add_monday_review_note queues a
reminder for a later Monday.

PII is masked unless include_pii=true. Write tools require confirm=true, return a
dry run otherwise, and every call is logged with your token name to
mcp_tool_calls (see list_mcp_tool_calls)."""


def build_server() -> MCPServer:
    from mcp_server import prompts, resources
    from mcp_server.audit import AuditMiddleware
    from mcp_server.tools import register_all

    mcp = MCPServer(
        name="gg-dashboard",
        title="GG Support Dashboard",
        instructions=INSTRUCTIONS,
        version="1.0.0",
        middleware=[AuditMiddleware()],
    )
    register_all(mcp)
    resources.register(mcp)
    prompts.register(mcp)
    return mcp
