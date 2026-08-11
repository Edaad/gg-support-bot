# Escalation notification

Per-club dashboard toggle **Escalation notification** (`clubs.enable_escalation_notification`, default off).

Uses shared group activity detection ([`bot/services/group_activity.py`](../bot/services/group_activity.py)). Popup keyboard remains a separate optional consumer of the same detection.

## Player idle (in-group help prompt)

On a **player idle** open (free text/media after ≥5 minutes of human silence in the group): the bot posts in the support group:

> Thanks for reaching out — how can we help you?

With inline buttons **Deposit**, **Cashout**, and **Talk to agent**.

| Button | Behavior |
|--------|----------|
| Deposit | Same as `/deposit` (ConversationHandler entry) |
| Cashout | Same as `/cashout`; if denied (hours/cooldown), also Slack `player_idle` |
| Talk to agent | Short ack *Got it — someone will be with you shortly.* + Slack `player_idle` (same copy as the old idle Slack, including the player message body) |

**Free text while the prompt is still up** (buttons not yet tapped): same as Talk to agent — strip buttons, ack, Slack `player_idle` using **this new message** body, then clear the in-memory stash. A later silence episode can offer a fresh thanks prompt; typing instead of tapping does not block future idle.

### Awaiting agent (after Talk to agent)

After Talk to agent **or** free-text-as-agent, a **10-minute** in-memory episode opens:

1. Arm a **1-minute** quiet debounce (seeded with the message that opened agent help).
2. Each further player free text/media **restarts** the 1m clock and **accumulates** that burst’s messages.
3. If the 1m elapses with no club-staff / global-admin reply → Slack `awaiting_agent_timeout` (*Player responded in the group chat — no agent reply.*) with the accumulated burst body; clear the burst; episode stays open.
4. Staff/admin reply cancels the current 1m job and clears the burst — does **not** end the 10m episode (later player bursts can fire again).
5. At 10m from open, the episode ends quietly. Lost on worker restart (not durable).
6. While the episode is open, another idle *Thanks for reaching out…* prompt is suppressed.

Idle itself does **not** post to Slack. One prompt per idle episode (`idle_episode_fired`). After any button tap (or free-text-as-agent), the InlineKeyboard is removed.

When **Escalation notification** is on, popup keyboard install and free-text strip are **suppressed** for that group so this prompt is the only CTA. Escalation-off clubs keep popup keyboard unchanged.

No *Looks like your request was handled…* from this feature. That copy stays tied to popup keyboard install only.

Cold start / worker restart: activity timestamps are **durable** on
`support_group_chats`, so restarts do not wipe silence state. If
`escalation_last_human_at` is unset (never recorded), the next **player**
message may idle-fire (treated as already silent). Pending idle-help stash
(player message for Talk to agent) is in-memory `chat_data` only.

AM/staff message then player reply **without** 5 minutes silence: no prompt.

Bare `/deposit` does **not** idle-fire. Allowed `/cashout` Slack-escalates without a Telegram idle prompt. Denied cashout (cooldown/hours) via **typed** `/cashout`: no Slack. Denied cashout from the idle-help **Cashout** button: Slack `player_idle` (same as Talk to agent) because the help buttons were already removed.

`/earlyrb` is treated like a flow command (no idle escalate on the command itself):

| Case | Telegram | Slack |
|------|----------|-------|
| Eligible (no 24h block) | No | `Early rakeback requested.` |
| Denied (24h constraint) | No | No; idle episode reset so follow-up free text can idle-fire |
| Follow-up free text after deny | Idle help prompt | Only if they tap Talk to agent **or** type free text while that prompt is still up |

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
| Free text/media on choose-method / sub / union / setup (including bare numbers) | Yes — *Player messaged during deposit.* + body |
| First-deposit referral answer | No |
| Other text (e.g. “Is Venmo available?”) | Yes — *Player messaged during deposit.* + body |
| Media before the button wait is armed | Yes |

Does not cancel the bot conversation / timeout. After the button arms the 5m wait, sent/done/media handling stays on the follow-up path above.

The **10-minute deposit reminder** clears `deposit_instructions_pending` when it runs (unless the payment-sent watch is still armed). Abandoned deposits no longer stay “open” overnight and block the idle help prompt.

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
| `player_idle` | A player just reached out. | Yes (Talk to agent, or free text while idle help prompt is still up) |
| `awaiting_agent_timeout` | Player responded in the group chat — no agent reply. | Yes (accumulated burst since last arm/reset) |
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
```

## Overlap with popup keyboard

When both toggles are on: escalation is Slack-only for player idle; popup keyboard owns its own install/remove Telegram copy independently.
