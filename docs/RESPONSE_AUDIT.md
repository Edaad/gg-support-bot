# Response audit

A nightly, deterministic record of every moment a human support agent was needed in a player support group, with the seconds it took staff to respond. An external daily job reads the slow and unanswered ones through a token-guarded API and posts verdicts back.

| Piece | Where |
|-------|-------|
| Builder (rules) | [`bot/services/response_audit.py`](../bot/services/response_audit.py) |
| Staff / bot identity | [`bot/services/response_audit_staff.py`](../bot/services/response_audit_staff.py) |
| Automated staff-account posts | [`bot/services/automated_staff_messages.py`](../bot/services/automated_staff_messages.py) |
| Bot hand-off lines | [`bot/services/agent_handoff_copy.py`](../bot/services/agent_handoff_copy.py) |
| API | [`api/routes/response_audit.py`](../api/routes/response_audit.py) |
| 09:00 ET heartbeat | [`bot/services/response_audit_heartbeat.py`](../bot/services/response_audit_heartbeat.py) |
| Manual run | `python scripts/run_response_audit.py --activity-date YYYY-MM-DD [--chat-id N] [--force]` |
| Mark a group internal | `python scripts/set_internal_chat.py --chat-id N [--off]` / `--list` |

## When it runs

The worker's 03:00 ET transcript job ([`group_chat_transcript_cron.py`](../bot/services/group_chat_transcript_cron.py)) fetches day D, runs the Claude ticket analysis, then runs the response audit for D (`force=True`). The builder only reads Postgres, so it runs after MTProto has resumed.

The fetch window for day D is `[D 00:00 ET, D+1 03:00 ET)`. Messages before `D+1 00:00` go to `group_chat_daily_transcripts.messages` (unchanged: analysis, ticket views, CSV export and `message_count` still see day D only). The 3-hour tail goes to `tail_messages`, which only the response audit reads. A clock that starts late on D can therefore be stopped by a reply after midnight.

Re-running a day is idempotent. Rows are upserted per chat by natural key `(start_kind, clock_start_msg_id | escalation_event_id)`. Events that still exist keep their ids, so verdicts survive. Events that no longer match are deleted, and their verdicts cascade. Without `--force`, the script leaves a day alone once a builder run has been recorded. `--chat-id` rebuilds one chat and does not touch the day's run marker.

## Which chats

Complete transcripts for D, minus:

- `support_group_chats.is_internal = true` (set with `scripts/set_internal_chat.py --chat-id N [--off]`). The staff test chat `GTO / 1111-1111 / @jz034` is marked internal by migration `0013_audit_shifts`. The title rule below also catches it.
- titles matching `/ 1111-1111 /`, `/ 2222-2222 /` or `/ 8888-8888 /`, or exactly `Rt/` (case-insensitive)
- chats whose only non-staff member is @rtaccountant. Membership isn't stored, so this uses the chat's bound player username when set, and otherwise the non-staff, non-bot senders in that day's transcript.
- club-linked chats with no `support_group_chats` row whose title is not a GC title (`CLUB / id / name`), such as ops or union chats

## Roles (by sender id, never display name)

| Role | Rule |
|------|------|
| staff | `clubs.telegram_user_id`, `club_linked_accounts`, `/gc` invite handles (`GC_USERS_TO_INVITE` + `GC_USERS_*`) resolved to ids, `ADMIN_USER_IDS`, each club's `command_admin_user_id`, `RESPONSE_AUDIT_EXTRA_STAFF_IDS`. Anonymous group-admin posts (sender = the chat) also count. |
| bot | Telegram `is_bot`, or username `YTranslateBot` / a club `bot_account` |
| player | `support_group_chats.player_telegram_user_id` for the chat |
| unknown_sender | anyone else. Treated as a player for clock starts and flagged in `pre_labels.unknown_sender`. |

Invite handles are resolved once via MTProto during the nightly fetch, while MTProto is paused and the club client is already connected, so no second connection is opened. Results are cached in `staff_handle_ids`. Failed lookups are retried the next night. Use `RESPONSE_AUDIT_EXTRA_STAFF_IDS` for anyone else.

## Clock starts (only on day D)

| `start_kind` | Rule | Clock |
|--------------|------|-------|
| `player_question` | A burst of player messages, each less than 60 s after the previous one. Bot messages don't break a burst; staff messages do. The burst needs at least one text that qualifies (below). Media-only bursts never qualify. | first message of the burst |
| `bot_handoff` | The bot posts `An agent will be with you shortly.` (automated cashout, chip transfer) or `An Admin will be with you shortly.` (early feeback). Both come from `agent_handoff_copy.py`. | that message |
| `slack_escalation` | An `escalation_events` row for the chat with reason `rpa_deposit_failed`, `rpa_cashout_failed`, `rpa_deposit_uncertain`, `rpa_cashout_uncertain`, `deposit_sent_timeout`, `deposit_sent_unbound`, `union_deposit_first`, `union_deposit_repeat` or `payment_manual_action` | `created_at` |
| `crypto_txid` | The bot posted the crypto "Please send your transaction hash (TxID)" ack, and within 60 min the player posts a hash-like message (64 hex with or without `0x`, a 32+ character alphanumeric token, or an explorer link containing a 64-hex hash) | the hash message |

A text **does not** qualify when it is:

- a bot command (`/…`)
- a wizard answer: a bare amount (`100`, `$50`, `1,000`, `1k`), a handle (`@user`, `$cashtag`, email, phone number, Venmo / Cash App / PayPal link), a TxID, or "sent" / "done" / "just sent it"
- a message the escalation decision log skipped as `expected_flow`, `flow_cmd` or `deposit_flow_answer`. This covers any text while a bot flow was expecting input. It is only logged when the club's escalation toggle is on.
- gratitude or a closer. There is one shared list in `response_audit.py` (`CLOSER_PHRASES`, `CLOSER_WORDS`, `CONTEXT_CLOSER_WORDS`), and the escalation closer list is accepted too. Words: thanks, thank you, ty, thx, bet, ok, okay, okey, okk, okkk, k, got it, gotchu, gotcha, sounds good, perfect, no rush, no worries, all good, cool, nice, lol, haha, gl. Emoji-only messages count. Case, punctuation and emoji are ignored. `sure`, `yes`, `yep` and `yup` count only as a reply to a staff message from the previous 5 minutes (chronologically or via Telegram reply). A few trailing words are allowed: up to 6 after a thanks ("Bet bet thank you g looks"), up to 2 after any other closer ("Okey boss"). Neither applies when the text has a `?` or a question word.

**Attaching.** A `bot_handoff`, `slack_escalation` or `crypto_txid` within 60 s after an open **player_question** attaches to it instead of opening its own event: it fills `escalation_event_id` and is listed in `pre_labels.attached`. Nothing ever folds into a hand-off, escalation or TxID event. A player question after one of those opens its own `player_question`. A new player burst is blocked only while another `player_question` is open. One staff reply stops all open events. The `auto_cashout_escalation`, `transfer_escalation` and `earlyrb_auto_failed` Slack events never start a clock. They only attach to the hand-off line (or player question) posted alongside them.

**Not a trigger.** `deposit_incomplete` is the bot's own 10-minute "Hey! Just checking in…" deposit reminder, and it never opens an event (ra-2). A real player message after it still opens a `player_question` under the rules above.

A player clock still open from D-1 (a D-1 event with no stop, or a stop after D 00:00) blocks new player bursts until it ends.

## Clock stop

The clock stops at the first later message from staff that is not an automated staff-account post. Any staff text counts, including a staff-run bot command. Bot and service messages never stop it. If nothing stops it by the end of the tail, `response_seconds` is null.

ra-2 adds:

- **Lookback.** A `slack_escalation` or `bot_handoff` with a human staff message in the 120 s before it is already handled. The clock stops at that message: `response_seconds = 0`, not a candidate, `pre_labels.staff_before_escalation`.
- **Same second.** A staff message in the same second as the clock-starting message stops the clock with `response_seconds = 0`, whichever message id is lower.
- **Bot-resolved.** Within 5 minutes of the start, with no staff reply in between, the bot may post "We have received your payment for $N, credits will be loaded…" (`PAYMENT_RECEIVED_PREFIX` / `SUFFIX` in `payment_group_notify.py`) or "Added N…" (`ADD_CONFIRMATION_PREFIX` in `mtproto_group_add.py`; the bot, or an automated staff-account post). Either closes the event: `clock_stop_at` = that message, `response_seconds = null`, `pre_labels.bot_resolved = true`, not a candidate. This applies to `player_question` and to `slack_escalation` with `deposit_sent_unbound` or `deposit_sent_timeout`. The Goods & Services refund line is not a resolution.

A staff-account message counts as automated when it is either:

- a row in `automated_staff_messages` with a system kind, or
- (for messages older than that table) a match on the fallback list: any `$N owed` pin (including `$0 owed` / `0 owed`), `Your cashout will be processed ASAP!`, `Group created. Invite link…`, the player DM redirect templates, or the club's welcome / join captions.

### `automated_staff_messages`

Every MTProto send records `(chat_id, message_id, kind)`:

| kind | Sent by | Stops the clock? |
|------|---------|------------------|
| `cash_owed_pin`, `cash_asap` | cashier `/cash` completion (`mtproto_group_cash._execute_cash_flow`) | no |
| `gc_invite_message` | `/gc` group create (`mtproto_group_create`) | no |
| `player_dm_redirect`, `staff_gc_confirmation` | `/gc` player DM / staff DM (`mtproto_group_create`, `mtproto_dm_gc_listener`) | no (DMs) |
| `auto_add_confirmation` | payment auto-deposit chip add (`payment_auto_deposit`) | no |
| `cashout_send_proof` | dashboard money-send proof (`cashout_send_notify`) | no |
| `outreach_dm`, `admin_dm` | inactive-group outreach, `/delete` failure DM | no (DMs) |
| `staff_add_confirmation`, `staff_bonus_confirmation`, `staff_lmk`, `staff_cash_working` | echo of a staff `/add`, `/bonus`, `/lmk`, `/cash` | **yes**: the bot deletes the staff command and posts this instead, so it is the only trace of the staff reply |

Welcome media after `/gc` is sent by the support bot, not MTProto, so bot rules already cover it. Its captions are in the fallback list in case staff repost them.

## `pre_labels` (best effort, for the judge)

| key | meaning |
|-----|---------|
| `gratitude_only` | the trigger messages are all gratitude or closers |
| `in_bot_flow` | a flow command (`/deposit`, `/cashout`, `/transfer`, `/earlyrb`, `/cash`) within 10 min before the start, or a trigger message the decision log marked as flow input |
| `media_only` | the trigger messages are all media without captions |
| `staff_drove_wizard` | staff ran `/deposit`, `/cashout`, `/transfer`, `/earlyrb`, `/cash`, `/add` or `/bonus` between 15 min before the start and the stop (or start + 15 min) |
| `staff_pinged_head_admins` | a staff message mentioning "head admin(s)" between the start and 5 min after the stop |
| `unknown_sender` | a trigger message came from an unknown sender |
| `escalation_reason` | the Slack reason (`slack_escalation` only) |
| `attached` | signals merged into this event |

Trigger messages: the burst for `player_question`, the hash for `crypto_txid`, otherwise the player burst in the 5 min before the start.

`is_candidate` = `response_seconds` is null or greater than 300. Only candidates get an `excerpt`: messages from 20 min before the start to 5 min after the stop (or the end of the tail), at most 60. When trimming, it keeps the 15 messages before the start, the earliest messages after it, and the stop message. Each item has the `serialize_telethon_message` shape plus a `role` field.

## API (`/api/response-audit`)

Header `X-Audit-Token: $RESPONSE_AUDIT_TOKEN`, compared in constant time. `401` for a missing or wrong token, `503` while the env var is unset. The dashboard JWT is not accepted.

| Method | Path | Returns |
|--------|------|---------|
| GET | `/status?date=YYYY-MM-DD` | `active_chats`, `transcripts_complete`, `transcripts_failed[]` (`chat_id`, `club_id`, `error`, `attempt_count`), `transcripts_pending` (pending rows plus active chats with no transcript row), `events`, `candidates`, `verdicts`, `builder_ran_at` |
| GET | `/candidates?date=YYYY-MM-DD[&unjudged=true]` | candidate `response_events` with `excerpt`, `pre_labels` and any existing `verdict` |
| POST | `/verdicts` | body `{"verdicts": [{response_event_id, verdict, reason_code, summary, sling_user_id, agent_name, judge_version}]}`. Upserts by `response_event_id` and never touches `disputed`. Returns `{received, created, updated, missing_event_ids}`. `verdict` must be `BREACH`, `EXCUSED`, `NOT_A_TRIGGER`, `OWNER_COVER` or `NEEDS_REVIEW`. |
| POST | `/shifts` | body `{"shifts": [{sling_shift_id, sling_user_id, agent_name, starts_at, ends_at, label}]}`. Upserts `audit_shifts` by `sling_shift_id`. The payload is the full schedule for the range it covers (earliest `starts_at` to latest `ends_at`): stored shifts starting in that range that are missing from the payload are deleted, which handles swaps and removals. Returns `{received, created, updated, deleted, range_start, range_end}`. `400` when `ends_at <= starts_at`. |
| GET | `/weekly?week_start=YYYY-MM-DD` | A Monday, Pacific week. Each event lands in a week by `clock_start_at` in America/Los_Angeles. Per `agent_name`: `triggers`, `unanswered`, `median_seconds`, `p90_seconds`, `pct_under_120s`, `breaches[]` (`date`, `time_et`, `group_title`, `response_seconds`, `summary`). Also `overall` with the same fields. |

Weekly attribution uses `audit_shifts`. An event belongs to the shift where `starts_at <= clock_start + 5 min < ends_at`, so a deadline exactly at `ends_at` goes to the next shift. Overlapping shifts credit every agent on duty, while `overall` counts each event once. Events with no shift are `Unattributed`. `NOT_A_TRIGGER` verdicts and bot-resolved events are excluded. Unanswered events rank slower than any answered one, and a median or p90 that lands on one reads `null`.

## Heartbeat

At 09:00 ET the worker checks yesterday (ET). If there are candidates but zero verdicts, it posts `Response audit for {date} has not run.` to the ops Slack channel (`SLACK_OPS_*`).

## Related changes

- Escalation Slack posts (`notify_escalation_slack` and the staff-unanswered issue-channel post) end with `Chat: \`-100…\` https://t.me/c/…`. Basic groups get the id only.
- A payment notification with a *Manual action required* footer, for a payment tied to a support group, now logs an `escalation_events` row with reason `payment_manual_action` (Slack is unchanged).

## Tables (Alembic `0012_response_audit`, `0013_audit_shifts`)

`response_events`, `response_audit_verdicts`, `automated_staff_messages`, `staff_handle_ids` (handle → id cache), `response_audit_runs` (per-day `builder_ran_at`), `audit_shifts` (Sling shifts), plus `group_chat_daily_transcripts.tail_messages` and `support_group_chats.is_internal`.

## Cashout escalation traceability

Only a `/cash` job's auto-claim emits `rpa_cashout_failed` / `rpa_cashout_uncertain` (`cashier/services/group_cash_init.py`). Deposit chip-add failures always use `rpa_deposit_*`. The event's `trigger_messages` records the job: `/cash job <id> (<amount> chips): claim <status> — <reason>`. That makes a surprising label, such as event 13993 on Ldog67, checkable against `cashier_cashout_jobs`. A staff reply within 120 s before such an event also closes it at 0 s (lookback).

## Rule versions

- `ra-1`: initial rules.
- `ra-2`: the 10-minute deposit reminder is not a trigger; staff lookback before escalations and hand-offs; shared closer list with context-only yes/sure; no folding a player question into a non-question event; bot-resolved closes; same-second tie goes to staff; more internal and test chat rules.

## Env

```bash
RESPONSE_AUDIT_TOKEN=generate-a-long-random-string
RESPONSE_AUDIT_EXTRA_STAFF_IDS=123456789,987654321   # optional
```
