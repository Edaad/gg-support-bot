# Escalation notification

Per-club dashboard toggle **Escalation notification** (`clubs.enable_escalation_notification`, default off).

Uses shared group activity detection ([`bot/services/group_activity.py`](../bot/services/group_activity.py)). Popup keyboard remains a separate optional consumer of the same detection.

## Player-facing ack

On a **player idle** open (free text/media after ≥10 minutes of human silence in the group):

> We'll be with you in just a second.

No *Looks like your request was handled…* from this feature. That copy stays tied to popup keyboard install only.

Cold start (no prior human activity observed in-process): do not fire until activity is seen and then 10 minutes of silence elapse. State is **in-memory** (resets on worker restart).

AM/staff message then player reply **without** 10 minutes silence: no ack, no Slack.

Bare `/deposit` does **not** escalate. Allowed `/cashout` Slack-escalates without Telegram ack. Denied cashout (cooldown/hours): no escalate; a later player message may idle-fire under normal rules.

## Deposit payment chase (Slack only)

After deposit **instructions** are posted:

1. Player payment-confirm phrase **or any media** → arm a 5-minute wait (no Slack yet).
2. No payment group notify in 5 minutes → Slack `deposit_sent_timeout`.
3. Another player message before payment → Slack `deposit_sent_followup`.
4. Payment notify (or deposit reminder cancel) → cancel the wait.

## Slack

Dedicated channel via:

```bash
SLACK_ESCALATION_BOT_TOKEN=xoxb-...
SLACK_ESCALATION_CHANNEL_ID=C...
# optional fallback:
# SLACK_ESCALATION_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Copy (no user id, no message body):

| Reason | Headline |
|--------|----------|
| `player_idle` | A player just reached out. |
| `cashout_started` | Cash out initiated. |
| `deposit_sent_timeout` | Deposit payment not seen. |
| `deposit_sent_followup` | Deposit follow-up after payment claim. |

Each post also includes `Club:` and `Group: {title} ({chat_id})`.

## Migration

```bash
DATABASE_URL=... python migrate_enable_escalation_notification.py
```

## Overlap with popup keyboard

When both toggles are on: escalation owns the player-facing ack; popup keyboard only installs/removes the reply keyboard (strip without duplicate ack copy).
