---
name: gg-dashboard
description: How to answer questions and do basic operations on the GG Support dashboard (poker club operations) through the GG Dashboard MCP connector. Use this whenever someone asks about clubs (Round Table, Creator Club, ClubGTO, Aces Table), players or support groups, deposits or payments (Venmo, Zelle, Cash App, PayPal, crypto, Stripe), unbound payments, Pool Pay or trade-record checks, cashouts and money sent, bonuses, expenses, balances, tickets or chat transcripts, escalations, audits or reconciles, GTO Zelle variants, the Monday Review checklist, or "what needs attention today", even if they do not name the dashboard or MCP. Also use it before any write such as binding a payment, annotating a cashout, pausing a variant, creating an expense, or checking off a Monday Review task.
---

# GG Dashboard

The GG Dashboard connector exposes the data behind the GG Support dashboard: four
poker clubs, one Telegram support group per player, the money that flows in
(deposits) and out (cashouts), and the operational checklists around them.
This skill tells you how to find the right rows quickly and how to change
things safely. Keep `references/domain.md` and `references/tools.md` at hand;
read them when a term or a parameter is unclear rather than guessing.

## Orientation

Four clubs share one system. Reference them by id, key or slug; all tools
accept any of the three.

| Club | key | slug | title shorthand |
|---|---|---|---|
| Round Table | round_table | round-table | RT |
| Creator Club | creator_club | creator-club | CC |
| ClubGTO | clubgto | clubgto | GTO |
| Aces Table (no groups of its own; routed through RT/CC) | — | aces-table | AT |

Every player has a support group titled `SHORTHAND / GGPLAYERID / name`, for
example `GTO / 8190-5287 / ThePirate343`. The group's `chat_id` and that title
are the join keys across payments, cashouts, tickets and escalations. When a
request mentions a player by name, id or title, call `find_group` first and
carry its `chat_id` and `group_title` into the next calls.

Two starting points cover most sessions:

- `get_dashboard_overview` for "what needs attention": unbound payments per
  rail, active cashouts, unchecked Pool Pay, pending expenses, alerts, paused
  variants, yesterday's tickets, escalations in the last 24 h.
- `find_group` for anything about one player.

## Conventions that change results

These trip people up, so check them before filtering (full detail in
`references/domain.md`).

- **Amounts.** Ingested payments, Stripe, funnel events, outbound sends and
  balances are in `amount_cents` (with `amount_usd` alongside). Cashouts,
  bonuses, expenses, Pool Pay requests and trade records are in dollars. Say
  which unit you are quoting.
- **Dates.** Payments, bindings, escalations and sends filter on UTC datetimes;
  pass `from_` / `to` as `YYYY-MM-DD` and they are read as that
  America/New_York day. Tickets, chat activity, transcripts and audits take one
  `activity_date` / `audit_date` (an ET day; tickets default to yesterday).
- **Paging.** `limit` defaults to 50 (max 200); results carry `total`,
  `returned` and `truncated`. Narrow with `club`, dates or `q` before paging.
- **PII.** Names, handles, emails, memos, payout details and invite links come
  back masked (`pii_masked: true`). Only pass `include_pii=true` when the
  operator needs the real value, and say that you did. Secrets never appear.
- **Test chats** are excluded from analytics by default.
- **Notes.** Every result has a `notes` list with caveats for that call (summary
  totals, "amounts are dollars", "no offset paging"). Read it before summarising.

## Writes: dry run first

Seven tools change data: `bind_payment_to_group`, `update_cashout_record`,
`update_manual_deposit_request`, `create_expense`, `pause_variant`,
`resume_variant`, and the Monday Review writes (`complete_monday_review_task`,
`add_monday_review_note`, `create_monday_review_task`). They all take `confirm`.

1. Call without `confirm` (or `confirm=false`). The reply has `refused: true`
   with `current` and `proposed`.
2. Show the operator what would change and wait for an explicit yes.
3. Call again with `confirm=true`. The reply has `applied: true` with `before`
   and `after`.

Binding a payment posts to the player's Telegram group and can trigger an
automatic chip deposit, so treat it like the dashboard's bind button: confirm
the group title matches the payment's amount and timing first. Every call,
read or write, is logged under the caller's token name; `list_mcp_tool_calls`
shows the trail.

## Picking the tool

| Question | Start with |
|---|---|
| What is going on today / this week? | `get_dashboard_overview`, then the domain tool it points at |
| Who is this player, which group, which club? | `find_group` |
| Deposits for a player, rail, club or period; what is unbound? | `search_payments` (`status="unbound"` needs a single rail and a club) |
| Why didn't a deposit bind or auto-credit? | `get_payment`, `list_bind_attempts`, `list_auto_deposits` |
| Repeat payers, who sent the most | `list_payers` |
| Pool Pay / union deposits awaiting trade-record checks | `list_manual_deposit_requests(trade_record_checked=false)`, `list_union_methods` |
| Cashouts in flight, what was sent, what is owed | `list_cashout_records(status="active")`, `get_cashout_record`, `list_cashout_money_sends` |
| Bonuses, expenses | `list_bonus_records`, `list_expenses` |
| Venmo / Cash App account balances and send capacity | `get_account_balances`, `list_blocking_sends`, `list_outbound_sends` |
| Support quality: tickets, response times, what was said | `list_tickets`, `get_ticket_messages`, `get_chat_transcript` |
| Deposit conversion | `deposit_funnel_summary` |
| Alerts fired to staff | `list_escalation_events`, `get_escalation_episode` |
| Daily audit / reconcile status | `list_reconcile_runs`, `get_reconcile_report`, `list_trade_record_uploads`, `list_early_rakeback_snapshots` |
| GTO Zelle handles paused or disabled | `list_gto_zelle_cards`, `list_payment_methods` |
| Monday checklist | `get_monday_review_board`, `list_monday_review_history` |
| What did the AI change? | `list_mcp_tool_calls` |

Full parameters for every tool are in `references/tools.md`. Step-by-step
sequences for the recurring jobs (daily ops check, investigate a player,
unbound payment triage, cashout review, Monday Review) are in
`references/playbooks.md`; follow them when the request matches.

## Reporting back

Operators work fast and the stakes are real money, so answer like a colleague
at the next desk: lead with the finding, give amounts with their unit and the
club, name players by group title (it is the key they use), and list the ids
(`chat_id`, payment id, cashout record id) they would need to act in the
dashboard. When a tool returned `truncated: true`, say so and offer to narrow
or page. When you did not run a write because the operator had not confirmed,
say exactly what the confirmed call would do.
