# Zelle flow — payment tracking and group linking

This doc describes the **end-to-end Zelle flow**: how payments are tracked (manual staff workflow) and how support groups get **linked** to a Zelle account (customer `/deposit` setup). For API/env details see [`ZELLE_PAYMENTS.md`](ZELLE_PAYMENTS.md); for bind modes and dashboard filters see [`VENMO_GROUP_BINDING.md`](VENMO_GROUP_BINDING.md) (shared binding infrastructure).

Two concepts work together:

| Concept | What it tracks | Table |
|---------|----------------|-------|
| **Payment tracking** | A specific Zelle deposit → which support group it belongs to | `zelle_payments`, `zelle_payer_bindings` |
| **Group linking** | Which Zelle *variant/recipient* a support group uses in `/deposit` | `group_payment_method_bindings` |

Every incoming Zelle payment goes through **tracking** (Zapier → notification → bind to a group). A new group may need **linking** before `/deposit` works normally.

---

## Overview

Same ingest order as Venmo (see [`VENMO_FLOW.md`](VENMO_FLOW.md)):

1. Zapier POST → `/api/zelle/payments`
2. Insert `zelle_payments`, try auto-bind (memo setup → special amount → repeat payer)
3. Telegram notification (bound or unbound)
4. Manual bind via staff reply or dashboard if still unbound

## Test bot only (first-time linking)

Zelle **first-time deposit linking** (setup amount / memo code flow, `/unbindmethod` reset → setup again) runs only on the **test bot** (`python run_test_bot.py`, `BOT_TEST_WORKER=1`). On the production support bot, `/deposit` + Zelle uses normal deposit instructions with no one-time setup step.

Zapier ingest, payment tracking, staff notifications, and dashboard bind still work on production infrastructure.

---

## Part 1 — Zelle payment tracking

### Ingest

1. **Zapier** parses the Zelle “you received a payment” email and **POST**s to `/api/zelle/payments`.
2. The **web** dyno inserts a row in `zelle_payments` and tries to bind automatically.
3. **@ggnotificationbot** posts to the shared staff notification Telegram group.

### Staff notification

**Unbound:**

```
🔔 Zelle Payment Notification

Group Chat: Unbound — reply to this message with the group title to bind

Name: …
Amount: $…
Memo: …
Method: coachingg444@gmail.com
```

**Already bound:**

```
Group Chat: CC / 1234-5678 / @player
…
```

(No Goods/Services line — Zelle emails do not include that field.)

### Manual bind

Same as Venmo: reply with group title or dashboard **Payments → Zelle → Bind / Rebind**.

### Repeat payers

`zelle_payer_bindings` auto-bind by **normalized payer name** (recipient email/phone is not part of repeat lookup).

---

## Part 2 — Customer-side group linking (`/deposit`)

Configure per club: **Club → Deposit methods → Zelle → First-time deposit linking**.

| Mode | Auto-link on ingest when… |
|------|---------------------------|
| **Memo code** | Memo contains setup code + Zelle recipient matches variant (within **10 min**) |
| **Special amount** | Amount + Zelle recipient match pending attempt (within **10 min**) |

After linking, `/deposit` + Zelle uses the **sticky variant** confirmed during setup.

Reset with **`/unbindmethod`** in the group.

---

## Processes

| Dyno | Role |
|------|------|
| `web` | Zapier ingest, auto-bind logic, send notifications |
| `notification` | Poll for reply-to-bind in staff group |
| `worker` | Support bot `/deposit`, first-time setup messages, `/unbindmethod` |

---

## Code references

| Area | File |
|------|------|
| Ingest + notify + bind | [`bot/services/zelle_payments.py`](../bot/services/zelle_payments.py) |
| Setup attempts + group bindings | [`bot/services/payment_method_binding.py`](../bot/services/payment_method_binding.py) |
| `/deposit` setup gate | [`bot/handlers/deposit.py`](../bot/handlers/deposit.py) |
| Zapier webhook | [`api/routes/zelle_payments.py`](../api/routes/zelle_payments.py) |
| Reply-to-bind | [`notification/handlers/bind.py`](../notification/handlers/bind.py) |
| Dashboard | [`dashboard/src/pages/Payments.tsx`](../dashboard/src/pages/Payments.tsx), [`dashboard/src/pages/Analytics.tsx`](../dashboard/src/pages/Analytics.tsx) |

Migration:

```bash
DATABASE_URL=... python migrate_zelle_payments.py
```
