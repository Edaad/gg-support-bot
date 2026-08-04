# Escalation notification

Per-club dashboard toggle **Escalation notification** (`clubs.enable_escalation_notification`, default off).

Uses shared group activity detection ([`bot/services/group_activity.py`](../bot/services/group_activity.py)). Popup keyboard remains a separate optional consumer of the same detection.

## Player idle (silent in Telegram)

On a **player idle** open (free text/media after ≥5 minutes of human silence in the group): **Slack only** — no bot message in the support group.

No *Looks like your request was handled…* from this feature. That copy stays tied to popup keyboard install only.

Cold start / worker restart: activity timestamps are **durable** on
`support_group_chats`, so restarts do not wipe silence state. If
`escalation_last_human_at` is unset (never recorded), the next **player**
message may escalate (treated as already silent).

AM/staff message then player reply **without** 5 minutes silence: no Slack.

Bare `/deposit` does **not** escalate. Allowed `/cashout` Slack-escalates without a Telegram message. Denied cashout (cooldown/hours): no escalate; a later player message may idle-fire under normal rules.

`/earlyrb` is treated like a flow command (no idle escalate on the command itself):

| Case | Telegram | Slack |
|------|----------|-------|
| Eligible (no 24h block) | No | `Early rakeback requested.` |
| Denied (24h constraint) | No | No; idle episode reset so follow-up free text can idle-fire |
| Follow-up free text after deny | Silent | Idle + player message body |

## GC create / DM reach-out

When **Escalation notification** is on:

| Event | Headline | Code span |
|--------|----------|-----------|
| New player-bound GC (`/gc @user` or auto create) | Welcome the new player who just joined the group chat. | GC title |
| Incoming player DM reuses existing GC | A player reached out in DM. | `Name (@username)` |

Skip: generic `/gc` (no player); staff `/gc` or outgoing MTProto `/gc` that only reuses an existing group.

## Deposit payment chase

After non-Stripe deposit **instructions** are posted (including first-time setup), when escalation is enabled the bot shows an **inline** button:

> I have sent the payment

**Stripe** instructions never get this button.

On tap:

1. Remove the button and reply:
   > Thank you! Chips will be added as soon as we receive the payment.
2. If the group has **no** `group_payment_method_bindings` row for the chosen method → Slack **Manual deposit request.** immediately.
3. If **bound** → arm a durable 5-minute wait:
   - No payment/`/add` in 5 minutes → Slack `deposit_sent_timeout` (**re-checks DB** so API payment notify cancels correctly across dynos).
   - Player message containing `sent` / `done` (case-insensitive) **or any media** → ignore for follow-up Slack (wait stays armed). Does **not** block `player_idle` if silence criteria are met.
   - Any other player text before payment → Slack `deposit_sent_followup` **with the player message body** and cancel the wait (skips idle).
   - Payment group notify clears the wait via durable columns.

Arming the chase is **button only** (typed “sent” / media do not start the wait).

## RPA (ClubGG auto chip-add / auto-claim)

When escalation is on and RPA was **attempted** but needs manual follow-up:

| Event | Headline |
|--------|----------|
| Auto chip-add fail / manual skip (`/add` or payment auto-deposit) | RPA deposit failed — add chips manually. |
| Auto-claim fail on `/cash` | RPA cashout failed — claim chips manually. |

Skip Slack when auto is disabled / not configured, or when the request was never queued (idempotency claim miss). Existing Telegram staff alerts are unchanged.

## Slack

Dedicated channel via:

```bash
SLACK_ESCALATION_BOT_TOKEN=xoxb-...
SLACK_ESCALATION_CHANNEL_ID=C...
# optional fallback:
# SLACK_ESCALATION_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Copy (no user id, no chat id):

| Reason | Headline | Player message body? |
|--------|----------|----------------------|
| `player_idle` | A player just reached out. | Yes |
| `cashout_started` | Cash out initiated. | No |
| `earlyrb_requested` | Early rakeback requested. | No |
| `deposit_sent_timeout` | Deposit payment not seen. | No |
| `deposit_sent_followup` | Player sent a message after confirming they sent the payment. | Yes |
| `deposit_sent_unbound` | Manual deposit request. | No |
| `new_player_onboarded` | Welcome the new player who just joined the group chat. | No |
| `player_dm_reached_out` | A player reached out in DM. | No |
| `rpa_deposit_failed` | RPA deposit failed — add chips manually. | No |
| `rpa_cashout_failed` | RPA cashout failed — claim chips manually. | No |

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
```

## Overlap with popup keyboard

When both toggles are on: escalation is Slack-only for player idle; popup keyboard owns its own install/remove Telegram copy independently.
