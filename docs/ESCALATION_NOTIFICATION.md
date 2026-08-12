# Escalation notification

Per-club dashboard toggle **Escalation notification** (`clubs.enable_escalation_notification`, default off).

Uses shared group activity detection ([`bot/services/group_activity.py`](../bot/services/group_activity.py)). Popup keyboard remains a separate optional consumer of the same detection.

## Player idle (Slack only)

On a **player idle** open (free text/media after ≥5 minutes of human silence in the group), the bot posts a single `player_idle` alert to the Slack escalation channel (headline *A player just reached out.*, including the player's message body). It is **silent in the support group** — no in-group prompt, no buttons, no ack.

One alert per idle episode (`idle_episode_fired`); the episode re-arms after another ≥5 minutes of silence.

Cold start / worker restart: activity timestamps are **durable** on
`support_group_chats`, so restarts do not wipe silence state. If
`escalation_last_human_at` is unset (never recorded), the next **player**
message may idle-fire (treated as already silent).

AM/staff message then player reply **without** 5 minutes of silence: no Slack.

Bare `/deposit` does **not** idle-fire. Allowed `/cashout` Slack-escalates (`cashout_started`) without an idle alert. Denied cashout (cooldown/hours) via typed `/cashout`: no Slack.

`/earlyrb` is treated like a flow command (no idle escalate on the command itself):

| Case | Slack |
|------|-------|
| Eligible (no 24h block) | `Early rakeback requested.` |
| Denied (24h constraint) | No; idle episode reset so a later free-text follow-up can idle-fire |

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
2. If the group has **no** `group_payment_method_bindings` row for the **selected** method (e.g. Venmo tap checks Venmo only) → Slack **Manual deposit request — no {method} binding for this group.** immediately.
3. If **bound** → arm a durable 5-minute wait:
   - No payment/`/add` in 5 minutes → Slack `deposit_sent_timeout` (*5 minutes have passed since the player said they sent the payment — please look out for a payment in this group chat.*) (**re-checks DB** so API payment notify cancels correctly across dynos).
   - Player message containing `sent` / `done` (case-insensitive) **or any media** → ignore for follow-up Slack (wait stays armed). Does **not** block `player_idle` if silence criteria are met.
   - Any other player text before payment → Slack `deposit_sent_followup` **with the player message body** and cancel the wait (skips idle).
   - Payment group notify clears the wait via durable columns and strips the
     **I have sent the payment** button (even if it was never tapped).

Arming the chase is **button only** (typed “sent” / media do not start the wait).

**Stripe checkout** (CashApp/Apple Pay/Debit via `use_group_checkout_link` Stripe provider, including `/cashapp` `/stripe` group links): the chase button is **not** shown. Payment confirmation is via Stripe webhook; offering the button previously false-fired `deposit_sent_unbound` because those methods have no group handle binding.

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

Does not cancel the bot conversation / timeout. After the button arms the 5m wait, sent/done/media handling stays on the follow-up path above.

The **10-minute deposit reminder** clears `deposit_instructions_pending` when it runs (unless the payment-sent watch is still armed). Abandoned deposits no longer stay “open” overnight and block `player_idle` escalation.

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

**Head-admin fan-out:** `rpa_deposit_failed`, `rpa_cashout_failed`, `rpa_deposit_uncertain`, and `rpa_cashout_uncertain` also post the **same** text to a second channel, reusing `SLACK_ESCALATION_BOT_TOKEN`:

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
2. Any non-bot human text/media/caption/command opens or feeds a durable episode (table `watched_group_escalation_state`).
3. Reuses awaiting-agent timings (1 minute quiet debounce, 10 minute episode).
4. On debounce: one Slack post to head-admin, then quiet until the episode ends.
5. No bot reply in the group; no club auto-link/welcome; commands are swallowed.
6. When the bot joins any non-support group, it DMs `ADMIN_USER_IDS` with `chat_id` + title so you can fill the allowlist.
7. Club welcome/link on join runs only when the title parses as a GC title (`CLUB / PLAYER_ID / NAME`). Titles like `Round Table Support & GG Support` skip onboarding.

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
DATABASE_URL=... python migrate_watched_group_escalation_state.py
```

## Overlap with popup keyboard

When both toggles are on: escalation is Slack-only for player idle; popup keyboard owns its own install/remove Telegram copy independently (unchanged by escalation).
