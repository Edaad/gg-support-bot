# Stripe per-deposit checkout

When a player picks Apple Pay / Debit Card (`applepay`, `debitcard`, etc.), the bot creates a **unique Stripe Checkout Session** per request. The player **chooses the amount on Stripe** ($20 minimum, $100 maximum), not from the `/deposit` amount step. One **Stripe Customer** (`cus_…`) is reused per Telegram group chat.

## Stable customer (no guest checkout)

Every bot-generated Checkout Session is created with `customer=<stored stripe_customer_id>` from `stripe_customers` for that `telegram_chat_id`. The bot does **not** use guest checkout or `customer_creation="always"` on normal deposit links.

Mapping: `telegram_chat_id` → one `cus_…` in `stripe_customers` → all future Checkout Sessions and Zapier `customer_id` lookups.

Checkout links issued before this behavior was deployed may still be open; only **new** links from `create_stripe_checkout_session()` are guaranteed to be customer-bound.

## Database tables

| Table | Purpose |
|-------|---------|
| `stripe_customers` | `telegram_chat_id` → `stripe_customer_id`, club, last-seen GG id / display name |
| `stripe_checkout_sessions` | Each `/deposit` Stripe request: `cs_…`, amount, status |

Live group title at confirm time comes from **`groups.name`** (or `support_group_chats.telegram_chat_title`), not from a snapshot on the session row.

Migration:

```bash
DATABASE_URL=... python migrate_stripe_deposit_tracking.py
```

New installs also get tables from API startup `create_all`.

## Environment (bot + API)

| Variable | Required | Purpose |
|----------|----------|---------|
| `STRIPE_SECRET_KEY` | Yes (for Stripe checkout) | Stripe API secret |
| `STRIPE_ZAPIER_LOOKUP_SECRET` | Yes (for Zapier lookup) | Shared secret for lookup endpoint |
| `STRIPE_CHECKOUT_SUCCESS_URL` | No | Redirect after pay (default: Stripe docs URL) |
| `STRIPE_CHECKOUT_CANCEL_URL` | No | Redirect on cancel |

If `STRIPE_SECRET_KEY` is unset, Stripe deposits fall back to the static dashboard `response_text` / photos.

## Bot flow

1. `/deposit` → amount (for other methods) → Apple Pay / Debit Card
2. Bot calls Stripe: get/create customer for `chat_id`, create Checkout Session with **custom amount** ($20–$100)
3. Group receives: announcement + **Pay here** link (amount entered on Stripe)
4. Optional static instructions from the payment method still send if configured

## Zapier: (Glide) Confirm Stripe Payments

Update the Zap manually in [zapier.com](https://zapier.com).

### 1. Keep Stripe trigger

Use your existing trigger (e.g. **New Payment** on `Stripe aidenh1970@gmail.com #6`).

### 2. Add lookup step (before Glide / Telegram)

**Webhooks by Zapier — GET**

- **URL:** `https://<your-app-host>/api/stripe/deposit-context?customer_id={{Customer ID}}`
  - Map `Customer ID` from the Stripe trigger step (field is often `Customer ID` or `customer_id`).
- **Header:** `X-Stripe-Lookup-Secret: <STRIPE_ZAPIER_LOOKUP_SECRET from Heroku/.env>`

### 3. Use lookup fields in later steps

| Lookup JSON field | Use for |
|-------------------|---------|
| `group_title` | Telegram message + Glide name (current group title) |
| `gg_player_id` | Glide / accounting id segment |
| `player_display_name` | Name from title tail |
| `club_name` | Club label |
| `telegram_chat_id` | Debugging / optional routing |

Example Telegram text:

```text
Stripe payment confirmed — {{group_title}}
Amount: {{amount from Stripe trigger}}
```

### 4. Lookup API response shape

```json
{
  "telegram_chat_id": -1001234567890,
  "group_title": "RT / 6485-8168 / Angus Mcgoon",
  "club_id": 2,
  "club_name": "Round Table",
  "gg_player_id": "6485-8168",
  "player_display_name": "Angus Mcgoon",
  "stripe_customer_id": "cus_xxx"
}
```

**404** — no `stripe_customers` row (checkout was never created via the bot for that customer).

**401** — wrong or missing `X-Stripe-Lookup-Secret`.

## Code references

- [`bot/services/stripe_deposit.py`](../bot/services/stripe_deposit.py) — Stripe + DB
- [`bot/handlers/deposit.py`](../bot/handlers/deposit.py) — `/deposit` integration
- [`api/routes/stripe_deposit.py`](../api/routes/stripe_deposit.py) — Zapier lookup route
