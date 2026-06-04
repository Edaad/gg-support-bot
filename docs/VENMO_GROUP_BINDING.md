# Venmo first-time group chat binding

Before a support group can use **Venmo** in `/deposit`, the chat must be **linked** via a one-time setup payment at a special sub-minimum amount. After linking, deposits use the same Venmo variant account that was confirmed during setup.

## Flow

1. Player runs `/deposit` → enters amount → selects **Venmo**.
2. If the chat is not linked, the bot assigns a **variant** (weighted, same as normal deposits) and an **exact setup amount** one cent below the configured minimum, minus one cent per other pending setup on that variant ($99.99, $99.98, …).
3. Player sends that exact amount to the variant’s Venmo URL and posts a screenshot; staff still confirm chips manually.
4. Zapier POSTs to `/api/venmo/payments`. Within **10 minutes**, if amount + handle match the pending attempt, the API binds the payment to the group, records payer binding, and marks the chat as Venmo-linked.
5. Future `/deposit` → Venmo uses the **sticky variant** and normal deposit copy.

Repeat payers (`venmo_payer_bindings`) still auto-bind ingested payments when the chat is already linked or the payer was seen before.

## Database

| Table | Purpose |
|-------|---------|
| `group_payment_method_bindings` | `telegram_chat_id` + `payment_method_slug` → linked variant / handle |
| `payment_method_bind_attempts` | In-flight setup tickets (`pending` / `succeeded` / `expired` / `cancelled`) |

Migration:

```bash
DATABASE_URL=... python migrate_payment_method_bindings.py
```

## Backfill (existing bound payments)

Chats that already have bound `venmo_payments` skip first-time setup after backfill:

```bash
DATABASE_URL=... python scripts/backfill_venmo_group_bindings.py --dry-run
DATABASE_URL=... python scripts/backfill_venmo_group_bindings.py --apply
```

## Observability (dashboard + API)

**Nav → Payments** → provider **Venmo** shows a **Venmo group bindings** panel: setup initiated, succeeded, expired, pending, success rate, and counts by `bound_via` (`special_amount`, `manual_notification`, `manual_dashboard`, `backfill`, `test`).

JWT API:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/payments/bindings/summary?method=venmo&club_id=&from=&to=` | Funnel + bindings by source |
| `GET /api/payments/bind-attempts?method=venmo&status=&club_id=` | Paginated attempt rows |

## Code

- [`bot/services/payment_method_binding.py`](../bot/services/payment_method_binding.py) — allocation, attempts, group bindings
- [`bot/handlers/deposit.py`](../bot/handlers/deposit.py) — first-time Venmo branch
- [`bot/services/venmo_payments.py`](../bot/services/venmo_payments.py) — ingest match + manual bind updates

See also [`VENMO_PAYMENTS.md`](VENMO_PAYMENTS.md) for Zapier ingest and staff reply-to-bind.
