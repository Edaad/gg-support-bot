# GG Dashboard domain reference

Generated from the server's reference resources (gg://reference/*).

Contents: Clubs, Vocabulary, Enumerations, Conventions, Data-quality caveats

# Clubs

Four poker clubs share one bot ecosystem. Clubs are referenced three ways:
`club_id` (integer in Postgres), `club_key` (string used by MTProto/support
chats) and `club_slug` (gg-computer / audit slug). Group titles use a
shorthand prefix.

| clubs.name | key | slug | title shorthand |
|---|---|---|---|
| Round Table | round_table | round-table, aces-table | RT, AT |
| Creator Club | creator_club | creator-club | CC |
| ClubGTO | clubgto | clubgto | GTO |

Aces Table (`aces-table`, shorthand `AT`) has no club row or groups of its
own: its deposits flow through Round Table or Creator Club groups via a
union picker, so `aces-table` resolves to the Round Table club.

Use `list_clubs` for live ids and feature flags.

# Vocabulary

- **Support group / group chat**: one Telegram group per player, titled
  `SHORTHAND / GGPLAYERID / name` (e.g. `GTO / 8190-5287 / ThePirate343`).
  `chat_id` / `telegram_chat_id` is the Telegram id of that group and the primary
  join key across payments, cashouts, tickets and escalations.
- **gg_player_id**: the player's ClubGG id (`1234-5678`). `player_details` maps a
  player to the chats they own; `gg_nickname` is their in-game nickname.
- **Deposit**: money a player sends to the club. Ingested payments arrive from
  Zapier webhooks per rail (venmo, zelle, cashapp, paypal, crypto) or Stripe.
- **Bound / unbound**: an ingested payment is *bound* when it is linked to a
  support group (`telegram_chat_id` set). Binding happens automatically (memo
  emoji, special amount, repeat payer) or manually from the dashboard
  (`bound_via` = manual_dashboard) or MCP (`bind_payment_to_group`).
- **Bind attempt / first-time setup**: a player's first deposit with a rail is a
  setup attempt (special_amount or memo_emoji) that creates a group binding.
- **Auto deposit**: after a payment is bound, chips are added automatically on
  ClubGG via RPA; `payment_auto_deposit_events` records succeeded/skipped/failed.
- **Method / tier / variant / sub-option**: a payment method (e.g. Venmo deposit)
  has tiers (amount bands); each tier has variants (weighted rotation of receiving
  handles) and crypto methods have sub-options (networks). Variants can be paused
  (48 h) or disabled.
- **Method owner**: whose account receives money: `round-table` (RT),
  `vaughn` (GTO) or `mateos`.
- **Pool Pay / union method**: shared manual-deposit methods (`deposit_union`
  tmt|massiv) and large cashouts tracked as `manual_deposit_requests`;
  `trade_record_checked` marks a request verified against ClubGG trade records.
- **Cashout**: money the club sends to a player. `staff_cashout_records` hold
  destinations (`payments`) and money-sent rows (`sends`). Flags: `sending`
  (in progress), `do_not_send` (hold), `audited`; status active|cleared|oversent.
- **Early rakeback / earlyrb**: an advance on rakeback a player can claim.
- **Trade records**: ClubGG XLSX exports uploaded for daily audits;
  **net reconcile** compares them with early rakeback snapshots.
- **Ticket**: an AI-classified support conversation segment for one chat and
  one America/New_York day, with first-response time (FRT) and duration.
- **Escalation**: a Slack alert episode for a chat (player idle, deposit sent but
  unbound, cashout started, ...).
- **Test chats**: groups whose title marks them as test/staging are excluded from
  analytics by default.

# Enumerations

- cashout record status: active, cleared, oversent, do_not_send
- ticket category: auto_deposit, manual_deposit, unfinished_deposit, cashout, unfinished_cashout, early_rakeback, rakeback, bonus, other
- payment rail (`method`): venmo, zelle, cashapp, paypal, crypto, stripe; `all` and `union` are scopes of search_payments
- payment status filter: all, bound, unbound
- method owner: round-table, vaughn, mateos
- bound_via: backfill, manual_dashboard, manual_notification, memo_emoji, special_amount, test
- deposit funnel step: amount_entered, bind_setup_completed, chips_confirmed, chips_credited, deposit_started, instructions_sent, method_chosen, payment_bound, payment_received, union_chosen
- escalation reason: add_stale, auto_cashout_escalation, cashout_started, cash_stale, deposit_incomplete, deposit_player_message, deposit_sent_followup, deposit_sent_timeout, deposit_sent_unbound, earlyrb_auto_added, earlyrb_auto_failed, earlyrb_auto_over_max, earlyrb_chips_not_added, earlyrb_requested, large_cashout_payout, new_player_onboarded, payment_manual_action, player_dm_reached_out, player_idle, player_idle_followup, player_idle_staff_unanswered, referral_deposit_complete, rpa_cashout_failed, rpa_cashout_uncertain, rpa_deposit_failed, rpa_deposit_uncertain, transfer_escalation, union_deposit_first, union_deposit_repeat
- manual deposit request pool_pay_type: union_method, large_cashout
- manual deposit request deposit_union: tmt, massiv
- GTO Zelle variant state: active, paused, disabled
- mcp_tool_calls outcome: ok, error, refused

# Conventions

## Dates
- Payments, bindings, escalations, outbound sends: filter on UTC datetimes.
  `from_` / `to` accept `YYYY-MM-DD` (interpreted as an America/New_York calendar
  day: `from_` = start of day, `to` = end of day) or ISO-8601 datetimes (no offset
  = UTC).
- Tickets, group chat activity, transcripts, audits: one `activity_date` /
  `audit_date` per America/New_York day. Defaults to yesterday ET where allowed.
- Timestamps in results are ISO-8601; a trailing `Z` means UTC.

## Amounts
- Ingested payments, Stripe, funnel events, outbound sends and balances use
  integer `amount_cents` (plus `amount_usd` where available).
- Cashouts, bonuses, expenses, manual deposit requests, trade records use
  dollar amounts (`amount`, decimal rendered as a number).

## Paging
- List tools take `limit` (default 50, max 200) and `offset`; results carry
  `total`, `returned` and `truncated`. Narrow with `club`, dates and `q` before
  paging deep.

## PII
- Results are masked by default (`pii_masked: true`): payer names, handles,
  emails, memos, payout details, invite links. Pass `include_pii=true` only when
  the operator needs the real value. Secrets (bank tokens, session strings,
  attachment bytes) are never returned.
- `group_title` embeds the player's display name and stays visible because it is
  the main navigation key in the dashboard.

## Writes
- Write tools (`bind_payment_to_group`, `update_cashout_record`,
  `update_manual_deposit_request`, `create_expense`, `pause_variant`,
  `resume_variant`) take `confirm`. Without `confirm=true` they return a dry run
  (`refused: true`) showing the current state and the proposed change; with it
  they apply and return `before` / `after`.
- Every call is logged to `mcp_tool_calls` with the caller's token name
  (`list_mcp_tool_calls`).

# Data-quality caveats

- Cashout, bonus and manual-deposit services open their own database sessions,
  so their lists are not covered by the read-only transaction guard (they are
  still read-only operations).
- `list_bonus_records` has no offset paging; narrow with `club`, `bonus_type_id`
  or `q`.
- Stored reconcile reports (`get_reconcile_report`) exist only after an operator
  ran the reconcile on the Audit page; most historical runs have status `fail`.
- `search_payments` totals are summed per source; with `method=all` the summary
  mixes Stripe, rails and union manual deposits.
- Deposit funnel analytics exclude test chats by default.
- Some `cashier_cashout_jobs` and bot flow sessions stay in a non-terminal status
  after crashes; treat very old "active" rows with suspicion.
- Weekly player stats (rake / rakeback / profit) live in the external
  gg-computer service and are not exposed here.
