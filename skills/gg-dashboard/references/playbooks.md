# GG Dashboard playbooks

Step-by-step tool sequences for the recurring jobs. Each step names the tool and
the arguments that matter; everything else keeps its default.

Contents: Daily ops check, Investigate a player, Unbound payment triage, Pool Pay
check, Recording a cashout, Cashout review, Recording a bonus, Expenses, Outbound
sends and balances, Deposit alerts, Filing an issue report, Exporting bonuses or
expenses, Support quality review, Audit status, Monday Review, Binding a payment safely, Undoing a
mistake.

## Daily ops check

1. `get_dashboard_overview()` (or with `club`). Note which sections are non-zero.
2. For each rail with unbound payments: `search_payments(method=<rail>,
   status="unbound", club=<club>, from_=<7 days ago>)`. Group by `group_title`
   or payer and flag amounts that match an open deposit flow.
3. `list_manual_deposit_requests(trade_record_checked=false)`: Pool Pay deposits
   still to verify against trade records; total dollars is in `notes`.
4. `list_cashout_records(status="active")`: flag `do_not_send`, large `remaining`
   and anything older than a day.
5. `list_tickets()` (yesterday ET) grouped by `category`; `list_escalation_events(
   from_=<yesterday>)` for alerts that fired.
6. If it is Monday (or the operator asks): `get_monday_review_board()`.
7. Report a prioritised list: money waiting to be credited, cashouts to send,
   checks to do, conversations to look at. Do not run writes unless asked.

## Investigate a player

1. `find_group(query=<title fragment | gg_player_id | chat_id>)`. Take `chat_id`,
   `group_title`, `club_id`, `gg_player_id`.
2. `search_payments(method="all", q=<group_title>)` for deposits; call again with
   `include_test=true` only if the operator asks about test rows.
3. `list_cashout_records(q=<group_title>)`; `get_cashout_record(record_id)` for
   an active one (destinations in `payments`, money sent in `sends`).
4. `list_bonus_records(q=<group_title>)` if bonuses are relevant.
5. `list_tickets(chat_id=<chat_id>, activity_date=<day>)` for recent days;
   `get_ticket_messages(ticket_id)` for an unresolved one;
   `list_escalation_events(chat_id=<chat_id>)`.
6. Summarise: who the player is, money in and out (with units), open issues,
   and anything an operator should act on.

## Unbound payment triage

1. `search_payments(method=<rail>, status="unbound", club=<club>, from_=<day>)`.
2. For a candidate: `get_payment(method=<rail>, payment_id=<id>)`. Check
   `bind_attempts` (a first-time setup in progress?) and `auto_deposit_events`.
3. Find the group: `find_group(query=<payer handle or name fragment>)` with
   `include_pii=true` only if the masked value is not enough to match.
4. Confirm amount and time line up with the player's deposit flow
   (`deposit_funnel_summary` or the chat transcript if needed).
5. Dry run `bind_payment_to_group(method, payment_id, group_title)`; show the
   operator the `proposed.bind_to`; on yes, call again with `confirm=true`.

## Pool Pay check

1. `list_union_methods()` for capacity (`used_sum`, `remaining_limit`,
   `unchecked_count`) per method.
1a. To log a new Pool Pay deposit: dry run `create_manual_deposit_request(
   method_id, amount, group_title=... or telegram_chat_id=...)`, then confirm.
   It posts to Slack for union and large-cashout methods unless
   `notify_slack=false`. Capacity limits are not enforced, as on the dashboard.
2. `list_manual_deposit_requests(trade_record_checked=false, method_slug=<slug>)`.
3. When the operator has verified a request against the trade record:
   dry run `update_manual_deposit_request(request_id, trade_record_checked=true)`
   then confirm. Use `amount`, `telegram_chat_id` or `created_at` only to
   correct a mistake, and say the old and new value.
4. Remove a duplicate with `delete_manual_deposit_request`.

## Recording a cashout

1. `find_group(query=<player>)` for the exact `group_title` and club.
2. `list_payment_methods(club, direction="cashout")` to get `payment_method_id`
   and `payment_sub_option_id` for catalogue methods (crypto networks are
   sub-options). Catalogue methods need `payout_details`; a custom method needs
   `method_display_name` instead.
3. Dry run `create_cashout_record(club, group_title, amount, payments=[{...}],
   note=...)`. Amount is dollars and is recorded as given, cents included, as
   on the dashboard. Show the operator the destinations.
4. On yes, `confirm=true`. Staff get the "New cashout" Pushover.
5. Fix destinations later with `add_/update_/remove_cashout_destination`.

## Cashout review

1. `list_cashout_records(status="active", club=<club>)`; each item carries
   `amount`, `sent`, `remaining`, flags and destinations.
2. `get_cashout_record(record_id)` for the full picture; `include_pii=true`
   only when the operator needs the payout handle itself.
3. `list_cashout_money_sends(from_=<day>, to=<day>)` to reconcile what left the
   accounts; `get_account_balances()` for Venmo / Cash App send capacity.
4. Annotate with `update_cashout_record(record_id, note=..., sending=...,
   do_not_send=..., audited=..., amount=..., group_title=...)`: dry run,
   confirm. Amount can only change while the record is active.
5. Record money that left with `add_cashout_send(record_id, amount,
   method_display_name, sender_name, notify=...)`. `notify=true` messages the
   player's group ("Sent $X!") and only works on chat-linked records; the
   default is off, as on the dashboard. Screenshot proofs stay on the
   dashboard; crypto sends can carry a `proof_link`.
6. Correct a send with `update_cashout_send` or `remove_cashout_send`. The
   ledger and owed-pin clear recompute automatically.

## Recording a bonus

1. `list_bonus_types()` for `bonus_type_id`; for a one-off, omit it and pass
   `custom_description` (shows as "Other").
2. Dry run `create_bonus_record(club, amount, group_title=<full title> or
   gg_player_id=..., bonus_type_id=...)`. Amount is dollars. The player is
   resolved from the title exactly as the dashboard does.
3. Confirm. No Telegram or Slack message is sent.
4. Fix with `update_bonus_record` (`clear_bonus_type=true` turns it into
   "Other") or remove with `delete_bonus_record`.
5. Prefer `update_bonus_type(is_active=false)` over `delete_bonus_type`;
   deleting a type turns its records into "Other".

## Expenses

1. `list_expenses(club, pending=..., from_=..., to=...)` to find the row.
2. `create_expense`, `update_expense` (at least one field; amount must not be
   zero) or `delete_expense`, each dry run then confirm. Dollars.

## Outbound sends and balances

1. `get_account_balances()` for each Venmo / Cash App tag's balance, weekly
   inflow and remaining send capacity.
2. Record money sent from a Round Table account with `record_outbound_send(
   method, tag, amount, recipient, paid_at=...)`. Amount is dollars here and
   stored as cents; it reduces that tag's balance and send capacity.
3. Correct with `update_outbound_send` or `delete_outbound_send`.
4. Adjust a card with `update_account_balance_settings(method, tag, offset=...,
   sending_ceiling=..., inflow_max=...)` in dollars.

## Deposit alerts

1. `list_deposit_method_alerts()` for current alerts and this week's numbers.
2. `create_deposit_method_alert(name, method, variant, conditions=[{type:
   "weekly_volume", operator: "gte", threshold_usd: ...}], ...)`. Creating or
   updating an active alert evaluates it at once, which can post to Slack and,
   with `disable_enabled=true`, take the destination out of rotation. Say so in
   the dry-run summary.
3. `update_deposit_method_alert` / `delete_deposit_method_alert`; deleting an
   alert that is currently disabling a destination re-enables it.

## Filing an issue report

1. Dry run `create_issue_report(title, description, category=..., tags=[...],
   club=..., telegram_chat_id=...)`. Attachments stay on the dashboard.
2. Confirm. It posts to the issue-report Slack channel unless
   `notify_slack=false`.

## Exporting bonuses or expenses

1. Pin down the filters with the operator: date range (America/New_York days,
   inclusive), club, and for bonuses a type (`list_bonus_types`) or
   `other=true`; for expenses `pending` and a search term if needed.
2. Optionally preview with `list_bonus_records` / `list_expenses` so the totals
   are known before creating a file.
3. `export_bonus_records(from_, to, club=..., bonus_type_id=...)` or
   `export_expenses(club=..., pending=..., from_=..., to=...)`.
4. Reply with the `url`, `rows`, `total_amount` (dollars) and the filters used.
   Remind the operator the link is viewable by anyone who has it.
5. If the tool says Drive is not configured or the token was rejected, point to
   docs/MCP.md, Google Drive exports; nothing was uploaded.

## Support quality review

1. `list_group_chat_activity(activity_date=<day>, club=<club>)` for volume.
2. `list_tickets(activity_date=<day>, club=<club>, category=<optional>)`; sort
   by `frt_seconds` to find slow first responses.
3. `get_ticket_messages(ticket_id)` to read one conversation with roles
   (customer / admin / bot); `get_chat_transcript(chat_id, activity_date)` for
   the whole day.
4. `list_escalation_events(from_=<day>, to=<day>)` to see what the bot escalated.

## Audit status

1. `list_reconcile_runs(club=<slug>)`: status per `audit_date`.
2. `get_reconcile_report(club=<slug>, audit_date=<day>)`: stored report with
   matched and unmatched lines (dollars). If none is stored, an operator must
   run the reconcile on the Audit page first; say so.
3. `list_trade_record_uploads` and `list_early_rakeback_snapshots` to confirm the
   inputs for that day exist.

## Monday Review

1. `get_monday_review_board()` (or `week_of=<Monday>`): tasks with links,
   assignees, notes, done state and the `done`/`pending`/`total` summary.
2. To check a task off: dry run `complete_monday_review_task(task=<id or title
   fragment>, completed_by=<name>, notes=...)`, then confirm.
3. To leave a reminder: `add_monday_review_note(task, body, kind="temporary" |
   "persistent", week_of=...)`, dry run then confirm.
4. `list_monday_review_history(limit=<weeks>)` for what was missed in past weeks.

## Binding a payment safely

Binding links money to a player and may credit chips automatically, so the bar
is the same as for a human on the dashboard:

- The group title must be the full linked title (`RT / 6485-8168 / Name`), not a
  fragment. Get it from `find_group`.
- The payment's amount and `paid_at` should match the player's deposit flow.
- Rebinding an already bound payment moves it; say so in the dry-run summary.
- After `confirm=true`, re-read with `get_payment` and report `telegram_chat_id`
  and `group_title` from `after`.

## Undoing a mistake

Every write has an inverse; use it rather than leaving a wrong row.

| Wrong action | Undo with |
|---|---|
| Bound a payment to the wrong group | `bind_payment_to_group` with the right title (rebinds), or `unbind_payment` |
| Created a cashout, bonus, expense, Pool Pay request, send or alert | the matching `delete_*` / `remove_*` tool |
| Paused a variant | `resume_variant` |
| Checked off a Monday Review task | `complete_monday_review_task` again with corrected notes |

`list_mcp_tool_calls(actor=<name>, from_=<day>)` shows exactly what was
changed and when, which is the fastest way to find what to undo. Notifications
already sent (Slack, Pushover, Telegram) cannot be recalled; tell the operator
so they can follow up by hand.
