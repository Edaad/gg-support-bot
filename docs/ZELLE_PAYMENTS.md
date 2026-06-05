# Zelle payment notifications + manual group binding

For the **full flow** (manual tracking + customer `/deposit` linking + ingest order), see [`ZELLE_FLOW.md`](ZELLE_FLOW.md).

For **first-time linking** of Zelle to a support group, see [`VENMO_GROUP_BINDING.md`](VENMO_GROUP_BINDING.md) (shared bind modes and tables).

Zelle Confirm Zaps POST payment details to this API. The **notification bot** posts alerts to the shared staff Telegram group. Staff **reply** with a support group title to bind (or rebind) the payment.

## Flow

1. **Zapier** — Gmail trigger + formatters + Glide (unchanged) + **POST** to `/api/zelle/payments`
2. **API** — insert `zelle_payments`, auto-bind repeat payers / setup matches, send Telegram notification
3. **Notification bot** — staff reply with group title → bind + edit notification

Repeat payers (`zelle_payer_bindings`) auto-bind by **normalized payer name**. Display uses the **live** group title from `groups.name`.

## Database tables

| Table | Purpose |
|-------|---------|
| `zelle_payments` | One row per Zelle payment; binding keyed by `telegram_chat_id` |
| `zelle_payer_bindings` | Normalized payer name → last bound support group |

Migration:

```bash
DATABASE_URL=... python migrate_zelle_payments.py
```

Also adds `zelle_payment_id` to `payment_method_bind_attempts` when that column is missing.

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `ZELLE_ZAPIER_WEBHOOK_SECRET` | Yes (web dyno) | Auth for Zapier POST |
| `TELEGRAM_NOTIFICATION_BOT_TOKEN` | Yes (web + notification dynos) | @ggnotificationbot |
| `PAYMENT_NOTIFICATION_CHAT_ID` | Yes (web + notification dynos) | Shared staff notification group |
| `DEBUG_NOTIFICATION` | No | Verbose ingest/Telegram logs on web dyno |

## API

**POST** `/api/zelle/payments`

Header: `X-Zelle-Webhook-Secret: <ZELLE_ZAPIER_WEBHOOK_SECRET>`

```json
{
  "payer_name": "Jane Doe",
  "amount": "200.00",
  "zelle_recipient": "coachingg444@gmail.com",
  "paid_at": "Jun 5 2026, 02:15 PM",
  "source_external_id": "optional-gmail-message-id",
  "memo": "optional payment memo/caption (for memo_emoji setup-code bind)",
  "test": false
}
```

When `memo` is present and a pending `memo_emoji` setup attempt exists, ingest tries setup-code match **before** special-amount match.

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
🔔 Zelle Payment Notification

Group Chat: Unbound — reply to this message with the group title to bind

Name: Jane Doe
Amount: $200
Memo: GG-FLOP
Method: coachingg444@gmail.com
```

## Manual bind

Reply in the staff group with the full group title, e.g. `RT / 6485-8168 / PlayerName`.

Dashboard: **Nav → Payments**, provider **Zelle** — **Bind / Rebind** on payment rows.

## Dashboard API

JWT-protected ([`api/routes/payments.py`](../api/routes/payments.py)):

| Endpoint | Purpose |
|----------|---------|
| `GET /api/payments/providers` | Includes `{ id: "zelle" }` |
| `GET /api/payments/zelle/payments?club_id=&status=&from=&to=` | Paginated payments |
| `GET /api/payments/zelle/payers?club_id=&q=` | Paginated payer aggregates |
| `POST /api/payments/zelle/payments/{id}/bind` | Bind or rebind `{ "group_title": "RT / …" }` |
| `GET /api/payments/bindings?method=zelle` | Linked group chats |
| `GET /api/payments/bindings/summary?method=zelle` | Funnel + source breakdown |

## Processes

Same as Venmo — see [`VENMO_PAYMENTS.md`](VENMO_PAYMENTS.md#processes). The notification handler tries Zelle first, then Venmo, when matching a reply to a notification message.

## Code references

- [`bot/services/zelle_payments.py`](../bot/services/zelle_payments.py)
- [`api/routes/zelle_payments.py`](../api/routes/zelle_payments.py)
- [`notification/handlers/bind.py`](../notification/handlers/bind.py)
