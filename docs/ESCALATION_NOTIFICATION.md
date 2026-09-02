# Escalation notification

Per-club dashboard toggle **Escalation notification** (`clubs.enable_escalation_notification`, default off).

Uses shared group activity detection ([`bot/services/group_activity.py`](../bot/services/group_activity.py)) for staff/player role and deposit-chase flags. Popup keyboard remains a separate optional consumer of the same detection.

## Modes (support groups)

Escalation is **observe-only** relative to `/deposit` and `/cashout` wizards (never cancels or blocks them).

| Mode | What happens |
|------|----------------|
| Mid `/deposit` (wizard or payment-wait) | Existing chase ignore + `deposit_player_message` / `deposit_sent_*` Slacks. Those Slacks also open/feed the idle episode (no second Slack). |
| Mid `/cashout` | Unexpected free text opens/feeds the idle episode as `player_idle`. Method picks are callbacks (not seen by the message handler). Expected amount accepts are marked so activity skips escalate for that update. |
| Everything else | Player free text/media opens or feeds a durable idle episode. |

`cashout_started` is unchanged and does **not** open an episode. Bare `/deposit` / flow commands do not open an episode.

## Player idle episode (Slack only)

Durable state: table `support_group_idle_episode_state` ([`bot/services/support_group_idle_episode.py`](../bot/services/support_group_idle_episode.py)).

| Timer | Value |
|-------|--------|
| Open Slack | Immediate `player_idle` (*A player just reached out.*) with the player message |
| Follow-up burst | After **1 minute** of quiet with a non-empty burst → `player_idle_followup` (*Player follow-up.*) |
| Silence end | **5 minutes** with no human (player or staff) → close episode |
| Hard cap | **30 minutes** from episode open → close episode |

Behavior:

1. First player free text (not a flow command, not expected wizard input) **opens** an episode: Slack `player_idle`, call no-op in-group menu hook (`offer_idle_help_prompt` → false for now), arm silence + hard-cap timers.
2. Further player messages while open **feed** the burst and reset the 1m debounce + 5m silence.
3. Staff/AM message while open: clear burst, cancel 1m debounce, bump `last_human_at`, reschedule 5m silence; episode stays open.
4. Flow end (deposit/cashout success, cancel, timeout): quietly `close_episode`; next free text opens a fresh episode.
5. Denied `/cashout` / `/earlyrb`: no special arm — next free text opens a normal episode (no 5m silence gate).

Worker restart: open episodes restore remaining debounce / silence / hard-cap delays.

```bash
DATABASE_URL=... python migrate_support_group_idle_episode_state.py
```

`/earlyrb`:

| Case | Slack |
|------|-------|
| Eligible (no 24h block) | `Early rakeback requested.` |
| Denied (24h constraint) | No Slack on the command |

## GC create / DM reach-out

When **Escalation notification** is on:

| Event | Headline | Code span |
|--------|----------|-----------|
| New player-bound GC (`/gc @user` or auto create) | Welcome the new player who just joined the group chat. | GC title |
| Incoming player DM reuses existing GC | A player reached out in DM. | `Name (@username)` |

Skip: generic `/gc` (no player); staff `/gc` or outgoing MTProto `/gc` that only reuses an existing group.

## Deposit payment chase

After deposit **instructions** are posted (including first-time setup and Stripe checkout), when escalation is enabled the bot shows an **inline** button:

> I have sent the payment

On tap:

1. Remove the button and reply:
   > Thank you! Chips will be added as soon as we receive the payment.
2. If the group has **no** binding for the **selected** method → Slack **Manual deposit request — no {method} binding for this group.** immediately. Handle methods (Venmo/Zelle/…) check `group_payment_method_bindings`; **crypto** checks any `crypto_wallet_bindings` row for this chat (so auto-add wallets are not false-alarmed as unbound).
3. If **bound** → arm a durable 5-minute wait:
   - No payment/`/add` in 5 minutes → Slack `deposit_sent_timeout` (*5 minutes have passed since the player said they sent the payment — please look out for a payment in this group chat.*) (**re-checks DB** so API payment notify cancels correctly across dynos).
   - Player message containing `sent` / `done` (case-insensitive) **or any media** → ignore for follow-up Slack (wait stays armed). Does **not** open/feed an idle episode.
   - Any other player text before payment → Slack `deposit_sent_followup` **with the player message body**, cancel the wait, and open/feed the idle episode (no second Slack).
   - Payment group notify clears the wait via durable columns and strips the
     **I have sent the payment** button (even if it was never tapped).

Arming the chase is **button only** (typed “sent” / media do not start the wait).

**Stripe checkout** (CashApp/Apple Pay/Debit via `use_group_checkout_link` Stripe provider, including `/cashapp` `/stripe` group links): the chase button is **not** shown. Payment confirmation is via Stripe webhook; offering the button previously false-fired `deposit_sent_unbound` because those methods have no group handle binding.

### Union manual deposit (`tracks_manual_requests`)

When a player picks a union pool method (Zelle / Cash App / Apple Pay), a `manual_deposit_requests` row is created and Slack fires immediately. The bot first posts **special instructions** with an “I have read the instructions above” button; min/max payment details appear only after the player taps. A 10-minute ack timer edits the special-instructions message if they never tap; a separate 10-minute timer (starting on ack) edits the payment-tag message when it expires.

Classification is **per support group + union type** (prior rows in `manual_deposit_requests` for the same `telegram_chat_id` and method type):

| Case | Headline | Instruction | Head-admin fan-out |
|------|----------|-------------|-------------------|
| First ever (no prior row for that type) | Union method deposit | Verify the time, ensure payment status is visible, and if you are unsure, contact head admins. | Yes |
| Repeat, prior verified (`trade_record_checked`) | Union method deposit | Verify the time, ensure payment status is visible, and if you are unsure, contact head admins. | Yes |
| Repeat, prior still open (unchecked priors only) | Union method deposit | Verify the time, ensure payment status is visible, and if you are unsure, contact head admins. | Yes |

Slack body includes club, group title, amount, and method (union type name). Respects the club escalation toggle; skipped on the test bot worker.

### Free text during /deposit (before button)

When escalation is on, **immediate** Slack (`deposit_player_message`, no 5m silence) for player free text/media while a deposit is open — mid `/deposit` flow (e.g. method picker) **or** after instructions were shown but before payment / “I have sent the payment” arming:

| Message | Slack? |
|---------|--------|
| Valid amount answer during amount step (`100`, `$50`, …) | No — including the same update after amount is stored (`deposit_amount_message_id`) |
| Free text on choose-method / sub / union / setup (including bare numbers) | Yes — *Player messaged during deposit.* + body |
| First-deposit referral answer | No |
| Other text (e.g. “Is Venmo available?”) | Yes — *Player messaged during deposit.* + body |
| Text/caption containing `sent` / `done` (case-insensitive) | No — same ignore as armed chase; deposit flow unchanged |
| Media (any attachment) | No — same ignore as armed chase |

Does not cancel the bot conversation / timeout. Those Slacks also open/feed the idle episode. After the button arms the 5m wait, sent/done/media handling stays on the follow-up path above.

The **10-minute deposit reminder** clears `deposit_instructions_pending` when it runs (unless the payment-sent watch is still armed). Abandoned deposits no longer stay “open” overnight and block deposit-chase attribution.

If the depositing player replies before 10m, that cancel of the reminder job **also** schedules a deferred chase clear (after the current update). The reply can still Slack *Player messaged during deposit.*; later free text is no longer stuck behind a cancelled TTL.

## RPA (ClubGG auto chip-add / auto-claim)

When escalation is on and RPA was **attempted** but needs manual follow-up:

| Event | Headline |
|--------|----------|
| Auto chip-add fail / manual skip (`/add` or payment auto-deposit) | RPA deposit failed — add chips manually. |
| Auto chip-add UNCERTAIN (OCR mismatch / unknown outcome) | Deposit UNCERTAIN — verify on ClubGG (do not retry). + machine reason |
| Auto-claim fail on `/cash` | RPA cashout failed — claim chips manually. |
| Auto-claim UNCERTAIN on `/cash` | Cashout UNCERTAIN — verify on ClubGG (do not re-claim). + machine reason |

Skip Slack when auto is disabled / not configured, or when the request was never queued (idempotency claim miss). Existing Telegram staff alerts are unchanged.

## Slack

Dedicated channel via:

```bash
SLACK_ESCALATION_BOT_TOKEN=xoxb-...
SLACK_ESCALATION_CHANNEL_ID=C...
# optional fallback:
# SLACK_ESCALATION_WEBHOOK_URL=https://hooks.slack.com/services/...
```

**Head-admin fan-out:** `rpa_deposit_failed`, `rpa_cashout_failed`, `rpa_deposit_uncertain`, `rpa_cashout_uncertain`, `union_deposit_first`, and `union_deposit_repeat` also post the **same** text to a second channel, reusing `SLACK_ESCALATION_BOT_TOKEN`:

```bash
SLACK_HEAD_ADMIN_ESCALATION_CHANNEL_ID=C...
```

If that env is unset, the normal escalation channel still works; head-admin post is skipped with a warning. Other reasons do not fan out.

### Watched non-support groups (listen-only → head-admin)

The bot can sit in **non-support** Telegram groups and escalate human activity to the **head-admin** channel only (not the normal escalation channel; not gated by the club toggle).

```bash
# Comma-separated Telegram chat ids. Empty/unset = feature off.
WATCH_GROUP_ESCALATION_CHAT_IDS=-100123,-100456
```

**Telegram Privacy Mode must be off** (@BotFather → `/setprivacy` → Disable) so the bot receives free-text messages in those groups.

Behavior:

1. Allowlisted chat that is **not** a `support_group_chats` row.
2. Any non-bot human text/media/caption/command opens or feeds a durable episode (table `watched_group_escalation_state`). `@rtaccountant` and `@widget_stick` are ignored (union automation accounts).
3. Reuses awaiting-agent timings (1 minute quiet debounce, 10 minute episode).
4. On debounce: one Slack post to head-admin, then quiet until the episode ends.
5. No bot reply in the group; commands are swallowed. Allowlisted chats skip club auto-link/welcome.
6. Club welcome/link on join runs for ops titles (e.g. `Round Table Support & GG Support`) as well as GC titles. No admin DM is sent when the bot joins a non-support group.

Slack copy:

```
Watched group activity.
Group: {title}
From: {name} (@username)
{message}
```

(no chat id, no numeric user id)

```bash
DATABASE_URL=... python migrate_watched_group_escalation_state.py
```

### Inbound webhook (Make / Zapier → head-admin Slack)

For external alerts (Hub cashout wait email, etc.) that should land in the **same** head-admin channel:

```bash
HEAD_ADMIN_ESCALATION_WEBHOOK_SECRET=generate-a-long-random-string
```

```http
POST /api/head-admin-escalation
X-Head-Admin-Escalation-Webhook-Secret: <secret>
Content-Type: application/json

{"message": "🚨 URGENT 🚨\n\nContact head admins immediately ..."}
```

Posts `message` verbatim (no club-toggle gate). Requires `SLACK_ESCALATION_BOT_TOKEN` + `SLACK_HEAD_ADMIN_ESCALATION_CHANNEL_ID`. Returns `{ "ok": true }` on success; `401` bad/missing secret; `503` if secret unset; `502` if Slack post fails.

Copy (no user id, no chat id):

| Reason | Headline | Player message body? |
|--------|----------|----------------------|
| `player_idle` | A player just reached out. | Yes (the player's message that triggered idle) |
| `player_idle_followup` | Player follow-up. | Yes (burst body after 1m quiet) |
| `cashout_started` | Cash out initiated. | No |
| `earlyrb_requested` | Early rakeback requested. | No |
| `deposit_sent_timeout` | 5 minutes have passed since the player said they sent the payment — please look out for a payment in this group chat. | No |
| `deposit_sent_followup` | Player sent a message after confirming they sent the payment. | Yes |
| `deposit_sent_unbound` | Manual deposit request — no {method} binding for this group. | No |
| `deposit_player_message` | Player messaged during deposit. | Yes |
| `new_player_onboarded` | Welcome the new player who just joined the group chat. | No |
| `player_dm_reached_out` | A player reached out in DM. | No |
| `rpa_deposit_failed` | RPA deposit failed — add chips manually. | No |
| `rpa_cashout_failed` | RPA cashout failed — claim chips manually. | No |
| `rpa_deposit_uncertain` | Deposit UNCERTAIN — verify on ClubGG (do not retry). | Yes (OCR/reason detail) |
| `rpa_cashout_uncertain` | Cashout UNCERTAIN — verify on ClubGG (do not re-claim). | Yes (OCR/reason detail) |

Each post:

```
{headline}
Club: {club name}
`{gc title or contact}`
[{player message when included}]
```

GC title / contact is a Slack code span (tap-to-copy on mobile). Free-text bodies are truncated (~500 chars); media with no caption uses `(media)`.

## Migration

```bash
DATABASE_URL=... python migrate_enable_escalation_notification.py
DATABASE_URL=... python migrate_escalation_activity_state.py
DATABASE_URL=... python migrate_escalation_deposit_sent_button_message_id.py
DATABASE_URL=... python migrate_escalation_post_deposit_idle.py
DATABASE_URL=... python migrate_support_group_idle_episode_state.py
DATABASE_URL=... python migrate_escalation_observability.py
DATABASE_URL=... python migrate_escalation_decision_log.py
DATABASE_URL=... python migrate_watched_group_escalation_state.py
```

JWT read API (no dashboard page): `GET /api/escalations/events` and `GET /api/escalations/episodes/{id}`.

## Decision log (debug)

Append-only table `escalation_decision_log` records every miss-relevant **skip** or **fire** from `group_activity_handler` (not a Slack substitute — see `escalation_events` for notifies).

| `decision` | Example `reason` values |
|------------|-------------------------|
| `skipped` | `escalation_off`, `staff_no_episode`, `staff_cleared_burst`, `empty_body`, `expected_flow`, `flow_cmd`, `deposit_flow_answer`, `deposit_sent_ack_ignore` |
| `fired` | `player_idle_opened`, `player_idle_fed`, `deposit_player_message`, `deposit_sent_followup` |

No API in v1 — query SQL:

```sql
SELECT created_at, decision, reason, telegram_user_id, telegram_message_id, episode_id
FROM escalation_decision_log
WHERE telegram_chat_id = -1003995457474
ORDER BY created_at DESC
LIMIT 50;
```

```bash
DATABASE_URL=... python migrate_escalation_decision_log.py
```


## Overlap with popup keyboard

When both toggles are on: escalation is Slack-only for player idle; popup keyboard owns its own install/remove Telegram copy independently (unchanged by escalation).
