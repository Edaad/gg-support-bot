# PayPal payment notifications + manual group binding

Manual PayPal deposits are ingested from Zapier, stored in Postgres, and announced in the shared staff Telegram group. Staff reply with a group title to bind unbound payments — same pattern as Cash App and Venmo.

For shared concepts (first-time linking, ingest order, repeat payers), see [`VENMO_FLOW.md`](VENMO_FLOW.md) and [`VENMO_GROUP_BINDING.md`](VENMO_GROUP_BINDING.md).

## Flow

1. **Zapier** — PayPal email trigger + parser + **POST** to `/api/paypal/payments` (replace the legacy direct-Telegram step)
2. **API** — insert `paypal_payments`, try memo/amount setup bind, repeat-payer auto-bind, send Telegram notification
3. **Notification bot** — staff reply with group title → bind + edit notification

Repeat payers (`paypal_payer_bindings`) auto-bind by **normalized payer name** only (email is last-seen, not used for lookup).

## Database tables

| Table | Purpose |
|-------|---------|
| `paypal_payments` | One row per PayPal payment; binding keyed by `telegram_chat_id` |
| `paypal_payer_bindings` | Normalized payer name → last bound support group |

Migration (run once after deploy):

```bash
DATABASE_URL=... python migrate_paypal_payments.py
```

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `PAYPAL_ZAPIER_WEBHOOK_SECRET` | Yes (web dyno) | Auth for Zapier POST |
| `TELEGRAM_NOTIFICATION_BOT_TOKEN` | Yes (web + notification dynos) | @ggnotificationbot |
| `PAYMENT_NOTIFICATION_CHAT_ID` | Yes (web + notification dynos) | Shared staff notification group |

## API

**POST** `/api/paypal/payments`

Header: `X-Paypal-Webhook-Secret: <PAYPAL_ZAPIER_WEBHOOK_SECRET>`

```json
{
  "payer_name": "Marcus Ahlbäck",
  "amount": "200.00",
  "paypal_email": "payments@clubgto.com",
  "paid_at": "Jun 3 2026, 03:05 PM",
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
🔔 PayPal Payment Notification

Group Chat: Unbound — reply to this message with the group title to bind

Name: Marcus Ahlbäck
Amount: $200
Memo: FLOP
Method: PayPal (payments@clubgto.com)
```

## First-time deposit linking

Enable per club in the dashboard (Deposit method editor → PayPal → First-time deposit linking). Modes:

- **memo_emoji** — bot assigns a setup code; player includes it in the payment note; Zapier must send `memo`
- **special_amount** — player sends an exact sub-minimum setup amount

Variant response text should include the receiving email, e.g. `PayPal Email: payments@clubgto.com`.

## Zapier cutover

1. Deploy code and run `migrate_paypal_payments.py`
2. Set `PAYPAL_ZAPIER_WEBHOOK_SECRET` on the web dyno
3. Update the PayPal Zap: replace the Telegram step with POST to `/api/paypal/payments`
4. **Retire** the old Zap that posted directly to Telegram (avoids duplicate notifications)

## Dashboard

Payments → Provider **PayPal** — list payments, payers, manual bind, export CSV, and group-binding analytics (when first-time linking is enabled).
