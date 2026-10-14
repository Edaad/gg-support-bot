# GG Dashboard playbooks

Step-by-step tool sequences for the recurring jobs. Each step names the tool and
the arguments that matter; everything else keeps its default.

Contents: Daily ops check, Investigate a player, Unbound payment triage, Pool Pay
check, Cashout review, Support quality review, Audit status, Monday Review,
Binding a payment safely.

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
2. `list_manual_deposit_requests(trade_record_checked=false, method_slug=<slug>)`.
3. When the operator has verified a request against the trade record:
   dry run `update_manual_deposit_request(request_id, trade_record_checked=true)`
   then confirm. Use `amount` only to correct a typo, and say the old and new
   value.

## Cashout review

1. `list_cashout_records(status="active", club=<club>)`; each item carries
   `amount`, `sent`, `remaining`, flags and destinations.
2. `get_cashout_record(record_id)` for the full picture; `include_pii=true`
   only when the operator needs the payout handle itself.
3. `list_cashout_money_sends(from_=<day>, to=<day>)` to reconcile what left the
   accounts; `get_account_balances()` for Venmo / Cash App send capacity.
4. Annotate with `update_cashout_record(record_id, note=..., sending=...,
   do_not_send=..., audited=...)`: dry run, confirm. Amount and group title are
   not editable here; those stay in the dashboard.

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
