# Venmo payment notifications + manual group binding

For the **full flow** (manual tracking + customer `/deposit` linking + ingest order), see [`VENMO_FLOW.md`](VENMO_FLOW.md).

For **first-time linking** of Venmo to a support group (special setup amount + `/deposit` gate), see [`VENMO_GROUP_BINDING.md`](VENMO_GROUP_BINDING.md).

Venmo Confirm Zaps POST payment details to this API. The **notification bot** (`@ggnotificationbot`) posts alerts to a shared staff Telegram group. Staff **reply** to a notification with a support group title to bind (or rebind) the payment.

## Flow

1. **Zapier** — Gmail trigger + formatters + Glide (unchanged) + **POST** to `/api/venmo/payments`
2. **API** — insert `venmo_payments`, auto-bind repeat payers, send Telegram notification
3. **Notification bot** — staff reply with group title → bind + edit notification

Repeat payers (`venmo_payer_bindings`) auto-bind to their last group by **normalized payer name** (shared Venmo accounts rotate across clubs, so recipient `@handle` is not part of the lookup). Display always uses the **live** group title from `groups.name` via `get_group_title_for_chat`.

## Database tables

| Table | Purpose |
|-------|---------|
| `venmo_payments` | One row per Venmo payment; binding keyed by `telegram_chat_id` |
| `venmo_payer_bindings` | Normalized payer name → last bound support group (`venmo_handle` = last seen recipient) |

Migration:

```bash
DATABASE_URL=... python migrate_venmo_payments.py
DATABASE_URL=... python migrate_venmo_payer_name_only.py
```

`migrate_venmo_payer_name_only.py` dedupes payer rows and switches repeat-payer lookup to name-only (run once after deploy).

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `VENMO_ZAPIER_WEBHOOK_SECRET` | Yes (web dyno) | Auth for Zapier POST |
| `TELEGRAM_NOTIFICATION_BOT_TOKEN` | Yes (web + notification dynos) | @ggnotificationbot |
| `PAYMENT_NOTIFICATION_CHAT_ID` | Yes (web + notification dynos) | Shared staff notification group (all payment types) |
| `DEBUG_NOTIFICATION` | No | Set `true` for verbose ingest/Telegram logs on web dyno |

## API

**POST** `/api/venmo/payments`

Header: `X-Venmo-Webhook-Secret: <VENMO_ZAPIER_WEBHOOK_SECRET>`

```json
{
  "payer_name": "Moshe Toussoun",
  "amount": "200.00",
  "venmo_handle": "@godfather4444",
  "goods_or_services": false,
  "paid_at": "Oct 31 2024, 02:15 PM",
  "source_external_id": "optional-gmail-message-id",
  "memo": "optional payment memo/caption from email (for memo_emoji setup-code bind)",
  "test": true
}
```

When `memo` is present and a pending `memo_emoji` setup attempt exists, ingest tries setup-code match **before** special-amount match.

Set `"test": true` only on your **duplicate test Zap** — notifications will be prefixed with `TEST (Please ignore)`.

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
🔔 Payment Notification

Group Chat: Unbound — reply to this message with the group title to bind

Name: Moshe Toussoun
Amount: $200.00
Memo: 🍕
Method: @godfather4444
Goods/Services: False
```

(`Memo` is omitted when Zapier does not send `memo` or the payment has no caption.)

Auto-bound repeat payer:

```
🔔 Payment Notification

Group Chat: RT / 6485-8168 / Angus Mcgoon

Name: Moshe Toussoun
Amount: $200.00
Method: @godfather4444
Goods/Services: False
```

When bound, the **Group Chat** title is a hyperlink when a URL resolves: supergroups use `t.me/c/…`, legacy groups use a cached `support_group_chats.invite_link`, or on ingest the **web** dyno calls Bot API `exportChatInviteLink` (`TELEGRAM_BOT_TOKEN`, bot must be admin in the group) and caches the result. If all three fail, the title stays plain text. Optional offline backfill: `python scripts/backfill_support_group_invite_links.py`.

## Manual bind

Reply to the notification in the staff group with the full group title, e.g. `RT / 6485-8168 / Angus Mcgoon`. Must match a linked group in `groups.name` exactly.

Anyone who can post in the notification group may bind or rebind.

You can also bind or rebind from the dashboard (**Nav → Payments**, provider **Venmo**) — see [Dashboard](#dashboard-payments-page) below.

## Dashboard: Payments page

**Nav → Payments** (`/payments`), provider **Venmo**:

- **Payments** — all ingested Venmo deposits (bound + unbound); filter by status, date range; **Bind / Rebind** opens a modal to enter the group title
- **Payers** — aggregated totals per payer + Venmo handle for bound payments in the selected club

Test payments (`test: true` on ingest) are excluded from the dashboard by default.

Filters: club, status (All / Bound / Unbound), date range. **Export CSV** on each tab pages through the list endpoints below.

JWT-protected API (same router as Stripe — [`api/routes/payments.py`](../api/routes/payments.py)):

| Endpoint | Purpose |
|----------|---------|
| `GET /api/payments/providers` | Includes `{ id: "venmo" }` |
| `GET /api/payments/venmo/payments?club_id=&status=&from=&to=` | Paginated payments (`status`: `all`, `bound`, `unbound`) |
| `GET /api/payments/venmo/payers?club_id=&q=` | Paginated payer aggregates |
| `POST /api/payments/venmo/payments/{id}/bind` | Bind or rebind `{ "group_title": "RT / …" }` |

Bind from the dashboard updates the Telegram notification message when one was sent on ingest (same as reply-to-bind).

## Processes

| Process | Entrypoint | Role |
|---------|------------|------|
| `web` | `api/app.py` | Ingest API + send Telegram on POST |
| `notification` | `run_notification_bot.py` | Poll for bind replies |

```bash
python run_notification_bot.py
```

**Important:** Bind replies only work when the `notification` dyno is running. The `web` dyno sends alerts but does not handle replies.

```bash
heroku ps:scale notification=1 --app gg-support-bot-2025
```

Verify with `heroku ps` — you should see `notification.1: up`.

## Zapier: Confirm Venmo parse + webhook

Use your **existing** Confirm Venmo Zap (Gmail → parse → Glide → POST). Replace the parse-step instructions with the prompt below, and include `memo` in the webhook JSON.

### Parser prompt (full)

Use the Gmail email body as input. Map each output field into the same Webhooks POST you already use.

```text
Parse this Venmo "you received a payment" email and return these fields:

payer_name — Full name of the person who paid, from text like "Daniel Cushing paid you". Do not include "paid you".

amount — Dollar amount as a number string with two decimals, no dollar sign (example: 200.00).

venmo_handle — The recipient's Venmo username with @ prefix if it appears in the email; otherwise leave empty.

goods_or_services — true only if the email clearly says the payment was for goods or services; otherwise false.

paid_at — Date or date/time from the email if present (example: Nov 26, 2025 or Nov 26 2025, 7:45 AM); otherwise empty.

memo — Payment memo or caption from the main white transaction card: the line between the large dollar amount (example $200.00) and the blue "See transaction" button. Often a single emoji (example 🍕) or short text. Return exact characters including emoji. Empty if that line is missing. Do not use Transaction details (date, transaction ID, Sent to, footer).

source_external_id — Gmail message ID if available from the trigger; otherwise empty.
```

### Webhook POST body

Header: `X-Venmo-Webhook-Secret: <VENMO_ZAPIER_WEBHOOK_SECRET>`

```json
{
  "payer_name": "<parse: payer_name>",
  "amount": "<parse: amount>",
  "venmo_handle": "<parse: venmo_handle>",
  "goods_or_services": <parse: goods_or_services>,
  "paid_at": "<parse: paid_at>",
  "source_external_id": "<parse: source_external_id or Gmail message id>",
  "memo": "<parse: memo>"
}
```

Add `"test": true` only on your duplicate test Zap.

### What gg-support-bot does with `memo`

- Saves it on `venmo_payments.memo`
- Shows **Memo:** in the staff Telegram notification (after Amount)
- Uses it for **memo code** first-time group binding when the memo contains the setup code from `/deposit`

Run `python migrate_payment_method_bind_memo.py` once if `venmo_payments.memo` is not on your database yet.

## Code references

- [`bot/services/venmo_payments.py`](../bot/services/venmo_payments.py) — ingest, notify, bind
- [`api/routes/venmo_payments.py`](../api/routes/venmo_payments.py) — Zapier ingest webhook
- [`api/routes/payments.py`](../api/routes/payments.py) — dashboard list + bind API (shared with Stripe)
- [`notification/handlers/bind.py`](../notification/handlers/bind.py) — reply-to-bind handler
- [`dashboard/src/pages/Payments.tsx`](../dashboard/src/pages/Payments.tsx) — Payments UI
