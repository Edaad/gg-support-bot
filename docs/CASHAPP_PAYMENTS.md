# Cash App payment notifications + manual group binding

Manual (non-Stripe) Cash App deposits are ingested from Zapier, stored in Postgres, and announced in the shared staff Telegram group. Staff reply with a group title to bind unbound payments — same pattern as Venmo.

For shared concepts (first-time linking, ingest order, repeat payers), see [`VENMO_FLOW.md`](VENMO_FLOW.md) and [`VENMO_GROUP_BINDING.md`](VENMO_GROUP_BINDING.md).

## Flow

1. **Zapier** — Cash App email trigger + parser + **POST** to `/api/cashapp/payments` (replace the legacy direct-Telegram step)
2. **API** — insert `cashapp_payments`, try memo/amount setup bind, repeat-payer auto-bind, send Telegram notification
3. **Notification bot** — staff reply with group title → bind + edit notification

Repeat payers (`cashapp_payer_bindings`) auto-bind by **normalized payer name** only (handle is last-seen, not used for lookup).

## Database tables

| Table | Purpose |
|-------|---------|
| `cashapp_payments` | One row per Cash App payment; binding keyed by `telegram_chat_id` |
| `cashapp_payer_bindings` | Normalized payer name → last bound support group |

Migration (run once after deploy):

```bash
DATABASE_URL=... python migrate_cashapp_payments.py
```

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `CASHAPP_ZAPIER_WEBHOOK_SECRET` | Yes (web dyno) | Auth for Zapier POST |
| `TELEGRAM_NOTIFICATION_BOT_TOKEN` | Yes (web + notification dynos) | @ggnotificationbot |
| `PAYMENT_NOTIFICATION_CHAT_ID` | Yes (web + notification dynos) | Shared staff notification group |

## API

**POST** `/api/cashapp/payments`

Header: `X-Cashapp-Webhook-Secret: <CASHAPP_ZAPIER_WEBHOOK_SECRET>`

```json
{
  "payer_name": "Jackson Taylor",
  "amount": "15.00",
  "cashapp_handle": "$michaelc4444",
  "paid_at": "Jun 2 2026, 02:47 PM",
  "source_external_id": "optional-gmail-message-id",
  "memo": "FLOP",
  "test": false
}
```

When `memo` is present and a pending `memo_emoji` setup attempt exists, ingest tries setup-code match **before** special-amount match.

Set `"test": true` only on a duplicate test Zap — notifications are prefixed with `TEST (Please ignore)`.

Response:

```json
{
  "payment_id": 42,
  "status": "bound",
  "auto_bound": true,
  "created": true
}
```

## Notification format

Unbound:

```
🔔 Cash App Payment Notification

Group Chat: Unbound — reply to this message with the group title to bind

Name: Jackson Taylor
Amount: $15
Memo: FLOP
Method: Cashapp ($michaelc4444)
```

## First-time deposit linking

Enable per club in the dashboard (Deposit method editor → Cash App → First-time deposit linking). Modes:

- **memo_emoji** — bot assigns a setup code; player includes it in the payment note; Zapier must send `memo`
- **special_amount** — player sends an exact sub-minimum setup amount

Stripe Cash App checkout variants are unchanged — they use the Stripe webhook, not this ingest.

## Zapier cutover

1. Deploy code and run `migrate_cashapp_payments.py`
2. Set `CASHAPP_ZAPIER_WEBHOOK_SECRET` on the web dyno
3. Update the Cash App Zap: replace the Telegram step with POST to `/api/cashapp/payments`
4. **Retire** the old Zap that posted directly to Telegram (avoids duplicate notifications)

## Dashboard

Payments → Provider **Cash App** — list payments, payers, manual bind, export CSV, and group-binding analytics (when first-time linking is enabled).
