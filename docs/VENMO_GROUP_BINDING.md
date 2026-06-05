# First-time group chat binding (test bot)

**Test bot only** (`python run_test_bot.py` / `BOT_TEST_WORKER=1`). Production `run_bot.py` does not run first-time setup flows.

Before a support group can use a configured deposit method in `/deposit`, the chat may need a **one-time link** step. After linking, deposits use the sticky variant that was confirmed during setup.

## Bind modes (per method)

| Club (test bot) | `venmo` + `zelle` bind mode |
|-----------------|---------------------------|
| Creator Club | `special_amount` — exact cent amount (one below chosen /deposit amount) |
| Round Table | `memo_emoji` — cycled setup code in payment memo/caption |

Other clubs: no first-time binding on the test bot. Production `run_bot.py`: disabled for all clubs.

### Special amount (`special_amount`)

1. Bot assigns a variant and an exact setup amount one cent below the amount the player entered in `/deposit`, minus one cent per other pending `special_amount` setup on that variant (e.g. $90.00 chosen → $89.99, then $89.98, …).
2. Player sends that exact amount to the method’s payment destination and posts a screenshot.
3. Zapier POSTs to `/api/venmo/payments` (Venmo). Within **10 minutes**, if **amount + Venmo handle** match the pending attempt, the payment auto-binds the group.

If the payer or setup chat is **already linked**, ingest sends a staff warning (existing group + last bound deposit time), cancels the setup attempt, and leaves the payment **unbound** for manual rebinding.

### Memo code (`memo_emoji`)

1. Bot assigns a variant and a **setup code cycled left-to-right** through a fixed pool (up to 10 concurrent pending setups per variant).
2. Player sends that **exact code** in the Venmo **caption** (or Zelle **caption** in instructions) with payment, then posts a screenshot.
3. Zapier POSTs to `/api/venmo/payments` with optional **`memo`**. Within **10 minutes**, if **memo contains the code** and Venmo handle matches the variant, the payment auto-binds the group.

Same **already-linked** warning behavior as special amount (payment stays unbound).

**Local dev:** `run_api.py` (or Heroku `web`) must use the same `DATABASE_URL` as `run_test_bot.py`. Setup matching runs on ingest in the API process — it does **not** require `BOT_TEST_WORKER` on the web dyno.

Zelle uses deposit setup + DB attempts on the test bot; **Zelle Zapier ingest is not implemented yet** (manual bind still works).

## Unbind (test bot)

```text
/unbindmethod
```

Clears **all** `group_payment_method_bindings` for that chat (venmo, zelle, etc.) and cancels **all** pending setup attempts. Registered only on `run_test_bot.py`. Staff only.

## Database

| Table | Purpose |
|-------|---------|
| `group_payment_method_bindings` | `telegram_chat_id` + `payment_method_slug` → linked variant / handle |
| `payment_method_bind_attempts` | In-flight setup (`bind_kind`, `amount_cents` and/or `setup_emoji`) |

Migrations:

```bash
DATABASE_URL=... python migrate_payment_method_bindings.py
DATABASE_URL=... python migrate_payment_method_bind_memo.py
```

## Observability (dashboard + API)

JWT API bind-attempt rows include `bind_kind`, `setup_emoji`, and optional `amount_cents`.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/payments/bindings/summary?method=venmo&club_id=` | Funnel + bindings by source |
| `GET /api/payments/bindings?method=venmo&club_id=` | Linked group chats |
| `DELETE /api/payments/bindings/{id}` | Unbind |
| `GET /api/payments/bind-attempts?method=venmo` | Attempt rows |

`bound_via` values include `special_amount`, `memo_emoji`, `manual_notification`, `manual_dashboard`, `backfill`, `test`.

## Code

- [`bot/services/payment_method_binding.py`](../bot/services/payment_method_binding.py)
- [`bot/handlers/deposit.py`](../bot/handlers/deposit.py)
- [`bot/services/venmo_payments.py`](../bot/services/venmo_payments.py)

See [`VENMO_PAYMENTS.md`](VENMO_PAYMENTS.md) for Zapier ingest (`memo` field) and staff reply-to-bind.
