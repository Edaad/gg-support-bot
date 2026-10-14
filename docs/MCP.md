# MCP server (AI access to the dashboard)

The `mcp_server/` package exposes the data behind the GG Dashboard to AI clients
over the [Model Context Protocol](https://modelcontextprotocol.io). It runs inside
the existing `web` dyno at `POST /mcp` (Streamable HTTP, static bearer tokens) and
locally over stdio via `run_mcp.py`. It is read-mostly: 40 read tools, 6
confirm-gated write tools, 5 reference resources and 2 prompts.

## Security model

- **Auth:** `MCP_API_TOKENS="alice:<token>,ops-bot:<token>"`. Each entry is a
  named bearer token (32+ random characters). Requests without a matching
  `Authorization: Bearer <token>` get 401; with no tokens configured the
  endpoint answers 503. Tokens are compared with `hmac.compare_digest`.
- **Scope:** admin, all clubs. There is no per-token club restriction (the
  dashboard's `gto` role scoping does not apply).
- **PII:** masked by default (`pii_masked: true` in every result): payer names,
  handles, emails, memos, payout details, invite links. Tools that can return
  PII take `include_pii=true`. Secrets are never returned: Plaid access tokens,
  Telethon session strings, Pushover keys, proof/attachment bytes, raw webhook
  bodies.
- **Reads** run in a `READ ONLY` transaction with a `statement_timeout`
  (`MCP_STATEMENT_TIMEOUT_MS`, default 15000) on the shared engine. Services
  that open their own sessions (cashouts, bonuses, manual deposit updates) are
  still read-only operations but bypass that guard.
- **Writes** require `confirm=true`. Without it the tool returns a dry run
  (`refused: true`) with the current state and the proposed change.
- **Audit:** every `tools/call` is logged to `mcp_tool_calls` (actor = token
  name, tool, redacted args, outcome `ok|error|refused`, duration). Review with
  `list_mcp_tool_calls` or SQL.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MCP_ENABLED` | `false` | Mount `/mcp` on the web dyno. |
| `MCP_API_TOKENS` | unset | `name:token,name:token`. Required for HTTP. |
| `MCP_STATEMENT_TIMEOUT_MS` | `15000` | Postgres statement timeout per tool call. |
| `MCP_ACTOR` | `stdio` | Actor name recorded for stdio (local) calls. |

Set `MCP_ENABLED=true` and `MCP_API_TOKENS` on Heroku, deploy (the release phase
runs migration `0027_mcp_tool_calls`), and the server is live at
`https://<app>.herokuapp.com/mcp`.

## Connecting

Claude Code (remote, recommended):

```bash
export GG_MCP_TOKEN=<your token>
claude mcp add --transport http gg-dashboard https://<app>.herokuapp.com/mcp \
  --header "Authorization: Bearer ${GG_MCP_TOKEN}"
```

Or copy `.mcp.json.example` to `.mcp.json` in the repo (gitignored) and export
`GG_MCP_TOKEN`; Claude Code expands `${GG_MCP_TOKEN}` in headers.

Local stdio (Claude Code or Claude Desktop on a machine with `DATABASE_URL`):

```json
{
  "mcpServers": {
    "gg-dashboard-local": {
      "command": "python",
      "args": ["run_mcp.py"],
      "cwd": "/path/to/gg-support-bot",
      "env": { "MCP_ACTOR": "your-name" }
    }
  }
}
```

`run_mcp.py` loads `.env`, logs to stderr and warns (does not refuse) if the
database is behind the Alembic head.

Claude Desktop or claude.ai (custom connector, no local install):

1. Customize, Connectors, Add custom connector.
2. Name it and enter the MCP URL with the **full** Heroku host, exactly what
   `heroku info -a <app> | grep "Web URL"` prints, plus `/mcp`
   (for example `https://gg-support-bot-2025-6f96168018cf.herokuapp.com/mcp`).
   A shortened host returns Heroku's "no such app" 404 and the connector reports
   "Couldn't reach".
3. "Checking the server" reports 401 on the connect step and that it could not
   determine how the server signs in. Both are expected (no OAuth here); click
   Continue anyway.
4. Authentication: No sign-in. Request headers: `Authorization` with the value
   `Bearer <token>` (type the word Bearer, a space, then the token).
5. Save, then confirm with `heroku logs -a <app> --tail | grep mcp`: a
   `POST /mcp ... status=200` line with `fwd=160.79.x.x` (Anthropic's egress
   range) means the connector is talking to the server.

Authentication settings cannot be edited later; remove and re-add the connector
to rotate a token. The Request headers section is a beta that some
organizations do not see; without it, use the stdio config above or a local
`mcp-remote` bridge. The Anthropic Messages API MCP connector expects OAuth
access tokens; a static bearer may work but is not documented.

## Tool catalogue

All list tools take `limit` (default 50, max 200) and `offset`, return
`{items, total, returned, truncated, pii_masked, notes, as_of}`, and accept
`club` as an id, key (`round_table|creator_club|clubgto`) or slug
(`round-table|creator-club|clubgto|aces-table`). Date filters `from_`/`to`
accept `YYYY-MM-DD` (an America/New_York day) or ISO-8601 datetimes (naive =
UTC); day-based tools take `activity_date` / `audit_date`.

| Domain | Tools |
|---|---|
| Discovery | `list_clubs`, `get_club`, `list_club_groups`, `find_group` (title / gg_player_id / chat_id), `list_payment_methods`, `get_dashboard_overview` |
| Payments | `search_payments` (unified; `method` all/venmo/zelle/cashapp/paypal/crypto/stripe/union, `status` bound/unbound), `get_payment`, `list_payers`, `payment_bindings_summary`, `list_bind_attempts`, `list_auto_deposits`, `list_stripe_sessions` |
| Pool Pay | `list_manual_deposit_requests`, `list_union_methods` |
| Cashouts | `list_cashout_records`, `get_cashout_record`, `list_cashout_money_sends` |
| Bonuses / expenses | `list_bonus_records`, `list_bonus_types`, `list_expenses` |
| Sends / balances | `list_outbound_sends`, `get_account_balances`, `list_blocking_sends` |
| Analytics | `deposit_funnel_summary`, `list_group_chat_activity`, `list_tickets`, `get_ticket_messages`, `get_chat_transcript` |
| Escalations | `list_escalation_events`, `get_escalation_episode` |
| Audit | `list_reconcile_runs`, `get_reconcile_report`, `list_trade_record_uploads`, `list_early_rakeback_snapshots` |
| Ops | `list_deposit_method_alerts`, `list_gto_zelle_cards`, `list_plaid_zelle`, `list_issue_reports`, `list_mcp_tool_calls` |
| Monday Review | `get_monday_review_board` (one week: tasks, links, assignees, notes, done state), `list_monday_review_tasks`, `list_monday_review_history` (past weeks, done vs missed) |
| **Writes** (confirm) | `bind_payment_to_group`, `update_cashout_record` (note / audited / sending / do_not_send), `update_manual_deposit_request` (trade_record_checked / amount), `create_expense`, `pause_variant`, `resume_variant`, `complete_monday_review_task` (check off / update who + notes), `add_monday_review_note` (persistent or temporary for a Monday), `create_monday_review_task` |

Resources: `gg://reference/clubs`, `vocabulary`, `enums`, `conventions`,
`caveats`. Prompts: `investigate_player(query)`, `daily_ops_check(date)`,
`monday_review(week_of)`.

Not exposed in v1: weekly player stats (external gg-computer service),
broadcasts, GC/MTProto login, response audit, quick links, flow simulator,
CSV/XLSX exports.

## Conventions the AI is told

- Ingested payments, Stripe, funnel events, outbound sends and balances use
  integer `amount_cents` (+ `amount_usd`); cashouts, bonuses, expenses, pool-pay
  requests and trade records use dollars.
- `group_title` (`SHORTHAND / GGPLAYERID / name`) stays visible even when PII
  is masked, because it is the main navigation key.
- Test chats are excluded from analytics by default.
- Monday Review weeks are identified by their Monday (`week_of`, America/New_York).
  Persistent notes show every Monday; temporary notes only for their `week_of`
  (default: next Monday). Completing a task for a future week is refused.
- `list_payment_methods` reads pause state without releasing expired pauses
  (unlike the dashboard GET), so a lapsed pause may still read as paused until
  the dashboard or bot touches it.

## Development

```bash
MCP_ENABLED=true MCP_API_TOKENS=dev:$(python -c 'import secrets;print(secrets.token_urlsafe(32))') python run_api.py
claude mcp add --transport http gg-local http://localhost:8000/mcp --header "Authorization: Bearer <token>"
python -m unittest discover -s tests -p 'test_mcp_*.py' -v
```

Adding a tool: write a plain function in the matching `mcp_server/tools/*.py`
module (docstring = description shown to the model, `@translate_errors`,
return `page()`/`single()` from `mcp_server/envelope.py`), register it in that
module's `register()` (or `register_writes()` plus `WRITE_TOOL_NAMES` for a
write), and add a test. The import smoke test fails if fewer than 25 tools
register.
