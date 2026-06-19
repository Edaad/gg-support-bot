# First-time group chat binding

For the **full Venmo flow** (payment tracking, manual bind, and how setup connects to Zapier ingest), see [`VENMO_FLOW.md`](VENMO_FLOW.md).

Before a support group can use a configured deposit method in `/deposit`, the chat may need a **one-time link** step. After linking, deposits use the sticky variant that was confirmed during setup.

**Multiple accounts per group:** One player may use more than one Venmo or Zelle account with the same support group. Each payer name is remembered separately for auto-bind on future payments. The group keeps one sticky deposit variant after the first successful link.

- **First account on an unbound group:** `/deposit` → Venmo/Zelle runs first-time setup automatically.
- **Already linked:** `/deposit` → Venmo/Zelle shows normal deposit instructions.
- **Another Venmo/Zelle account:** payment arrives unbound; staff bind it manually (same as any other unbound payment). No separate customer setup flow.

A payer name can be linked to **multiple** support group candidates (same payer, different players). When exactly one candidate exists, ingest auto-binds as before. When two or more exist, the payment stays unbound and staff pick from inline buttons on the notification.

**Test vs production:** Candidate lists are scoped by payment mode — `test: true` ingest only matches test/staging group titles (`/ TEST` or `@jz034`); production ingest excludes them. Use test groups to rehearse multi-candidate binding without affecting production payer rows.

If that name is already linked elsewhere during first-time setup, setup is blocked and staff get a warning plus Reassign / Add possible user buttons on the unbound payment notification.

Configure per deposit method in the dashboard (**Club → Deposit methods → Venmo/Zelle → First-time deposit linking**).

During setup, the bot shows instructions and the setup code or exact amount first. The player taps **I have read the instructions above** before the payment link or Zelle email is sent.

## Bind modes (per method)

| Mode | Behavior |
|------|----------|
| `special_amount` | Exact cent amount (one below chosen /deposit amount) |
| `memo_emoji` | Cycled setup code in payment memo/caption |

Only **Venmo** and **Zelle** deposit methods support first-time linking.

### Special amount (`special_amount`)

1. Bot assigns a variant and an exact setup amount one cent below the amount the player entered in `/deposit`, minus one cent per other pending `special_amount` setup on that variant (e.g. $90.00 chosen → $89.99, then $89.98, …).
2. Player sends that exact amount to the method’s payment destination and posts a screenshot.
3. Zapier POSTs to `/api/venmo/payments` (Venmo) or `/api/zelle/payments` (Zelle). Within **10 minutes**, if **amount + account** match the pending attempt, the payment auto-binds the group.

If the payer or setup chat is **already linked**, ingest sends a staff warning (existing group + last bound deposit time), cancels the setup attempt, and leaves the payment **unbound** for manual rebinding.

### Memo code (`memo_emoji`)

1. Bot assigns a variant and a **setup code cycled left-to-right** through a fixed pool (up to 10 concurrent pending setups per variant).
2. Player sends that **exact code** in the Venmo **caption** (or Zelle **caption** in instructions) with payment, then posts a screenshot.
3. Zapier POSTs to `/api/venmo/payments` or `/api/zelle/payments` with optional **`memo`**. Within **10 minutes**, if **memo contains the code** and the payment account matches the variant, the payment auto-binds the group.

Same **already-linked** warning behavior as special amount (payment stays unbound).

**Local dev:** `run_api.py` (or Heroku `web`) must use the same `DATABASE_URL` as `run_test_bot.py`. Setup matching runs on ingest in the API process — it does **not** require `BOT_TEST_WORKER` on the web dyno.

Zelle uses the same deposit setup + DB attempts as Venmo; ingest is via `/api/zelle/payments` — see [`ZELLE_FLOW.md`](ZELLE_FLOW.md) and [`ZELLE_PAYMENTS.md`](ZELLE_PAYMENTS.md).

## Unbind (test bot)

```text
/unbindmethod
```

Clears **all** `group_payment_method_bindings` for that chat, **all** `venmo_payer_bindings` / `zelle_payer_bindings` for that chat, bind-attempt history, and cancels **all** pending setup attempts. Available on production and test bots. Staff only.

## Database

| Table | Purpose |
|-------|---------|
| `group_payment_method_bindings` | `telegram_chat_id` + `payment_method_slug` → linked variant / handle |
| `payment_method_bind_attempts` | In-flight setup (`bind_kind`, `amount_cents` and/or `setup_emoji`) |

Migrations:

```bash
DATABASE_URL=... python migrate_payment_method_bindings.py
DATABASE_URL=... python migrate_payment_method_bind_memo.py
DATABASE_URL=... python migrate_club_payment_first_time_linking.py
DATABASE_URL=... python migrate_payment_bind_multi_candidates.py
```

## Observability (dashboard + API)

JWT API bind-attempt rows include `bind_kind`, `setup_emoji`, and optional `amount_cents`.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/payments/bindings/summary?method=venmo&club_id=&bound_via=` | Funnel + bindings by source (`bound_via`: `special_amount`, `memo_emoji`, `manual`, `backfill`, `test`, or omit for all) |
| `GET /api/payments/bindings?method=venmo&club_id=&bound_via=` | Linked group chats (same `bound_via` filter) |
| `DELETE /api/payments/bindings/{id}` | Unbind |
| `GET /api/payments/bind-attempts?method=venmo` | Attempt rows |

`bound_via` values include `special_amount`, `memo_emoji`, `manual_notification`, `manual_dashboard`, `backfill`, `test`. Dashboard **Analytics** (`/analytics`) shows bound GC counts, source breakdown, setup funnel, and a filterable table (club, linking source, date range).

## Code

- [`bot/services/payment_method_binding.py`](../bot/services/payment_method_binding.py)
- [`bot/handlers/deposit.py`](../bot/handlers/deposit.py)
- [`bot/services/venmo_payments.py`](../bot/services/venmo_payments.py)

See [`VENMO_PAYMENTS.md`](VENMO_PAYMENTS.md) for Zapier ingest (`memo` field) and staff reply-to-bind.
