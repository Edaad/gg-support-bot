# Venmo flow — payment tracking and group linking

This doc describes the **end-to-end Venmo flow**: how payments are tracked (manual staff workflow) and how support groups get **linked** to a Venmo account (customer `/deposit` setup). For API/env details see [`VENMO_PAYMENTS.md`](VENMO_PAYMENTS.md); for bind modes and dashboard filters see [`VENMO_GROUP_BINDING.md`](VENMO_GROUP_BINDING.md).

Two concepts work together:

| Concept | What it tracks | Table |
|---------|----------------|-------|
| **Payment tracking** | A specific Venmo deposit → which support group it belongs to | `venmo_payments`, `venmo_payer_bindings` |
| **Group linking** | Which Venmo *variant/handle* a support group uses in `/deposit` | `group_payment_method_bindings` |

A new group may need **linking** before `/deposit` works normally. Every incoming payment still goes through **tracking** (Zapier → notification → bind to a group).

---

## Overview

```mermaid
flowchart TB
  subgraph ingest [Payment ingest — every Venmo deposit]
    Zap[Zapier POST /api/venmo/payments]
    API[Web API ingest]
    Zap --> API
  end

  subgraph auto [Auto-bind attempts — in order]
    Memo[1. Memo setup match]
    Amt[2. Special-amount setup match]
    Repeat[3. Repeat payer by name]
    API --> Memo
    Memo -->|no match| Amt
    Amt -->|no match| Repeat
  end

  subgraph manual [Manual bind — if still unbound]
    TG[Staff notification group]
    Reply[Reply with group title]
    Dash[Dashboard Payments bind]
    API --> TG
    TG --> Reply
    Dash --> Bind[Bind payment to group]
    Reply --> Bind
  end

  subgraph customer [Customer linking — once per group]
    Dep[/deposit in support group]
    Setup[First-time setup instructions]
    Pay[Player sends Venmo + screenshot]
    Dep --> Setup
    Setup --> Pay
    Pay --> Zap
  end

  Memo -->|match| Linked[Group linked + payment bound]
  Amt -->|match| Linked
  Repeat -->|match| Bound[Payment bound only]
  Bind --> Linked
```

---

## Part 1 — Venmo payment tracking (manual workflow)

This is the **original** flow: every Venmo deposit email becomes a tracked payment and a staff notification.

### 1. Ingest

1. **Zapier** parses the Venmo “you received a payment” email and **POST**s to `/api/venmo/payments` (see [`VENMO_PAYMENTS.md`](VENMO_PAYMENTS.md) for payload and parser prompt).
2. The **web** dyno inserts a row in `venmo_payments` and tries to bind automatically (Part 2).
3. **@ggnotificationbot** posts to the shared staff notification Telegram group.

### 2. Staff notification

**Unbound payment** (could not auto-bind):

```
🔔 Payment Notification

Group Chat: Unbound — reply to this message with the group title to bind

Name: …
Amount: $…
Memo: …          ← shown when Zapier sends memo
Method: @…
Goods/Services: …
```

**Already bound** (repeat payer or setup match):

```
Group Chat: CC / 1234-5678 / @player
…
```

### 3. Manual bind (staff)

If the payment is **Unbound**, staff attach it to a support group in one of two ways:

| Method | How | `bound_via` on group link |
|--------|-----|---------------------------|
| **Telegram reply** | Reply to the notification with the full group title, e.g. `CC / 6485-8168 / PlayerName` | `manual_notification` |
| **Dashboard** | **Payments → Venmo → Bind / Rebind** on the payment row | `manual_dashboard` |

Requirements:

- Title must match a linked group in `groups.name` exactly (`CLUB / PLAYER_ID / label`).
- The **notification** dyno must be running for reply-to-bind (`run_notification_bot.py`).

Manual bind also:

- Sets `venmo_payments.telegram_chat_id` and updates `venmo_payer_bindings` (payer name → last group).
- Creates or updates `group_payment_method_bindings` for that chat (Venmo variant inferred from handle).
- Edits the Telegram notification to show the bound group title.

### 4. Repeat payers

If auto-bind did not run via setup, ingest looks up **`venmo_payer_bindings`** by **normalized payer name** (not handle — shared accounts rotate). Known payers auto-bind to their **last** support group; notification shows the group title immediately.

---

## Part 2 — Customer-side group linking (`/deposit`)

Before a group can use Venmo in `/deposit` without setup, the club may require **first-time deposit linking** (dashboard: **Club → Deposit methods → Venmo → First-time deposit linking**).

Enabled per club/method. Modes:

| Mode | Player must… | Auto-link on ingest when… |
|------|----------------|---------------------------|
| **Memo code** (`memo_emoji`) | Paste a short code (e.g. `GG-FLOP`) in the Venmo **caption** | Memo contains code + handle matches variant (within **10 min** of `/deposit`) |
| **Special amount** (`special_amount`) | Send an exact setup amount (e.g. $89.99 for a $90 deposit) | Amount + handle match pending attempt (within **10 min**) |

Only **Venmo** and **Zelle** auto-link via ingest today (see [`ZELLE_FLOW.md`](ZELLE_FLOW.md) for Zelle).

### Customer steps

1. Player runs **`/deposit`** in the support group and chooses **Venmo**.
2. If the group is **not** linked yet and linking is enabled:
   - Bot shows **one-time setup** (not normal deposit copy).
   - **Memo mode:** bot sends a copy-paste code, then Venmo instructions.
   - **Amount mode:** bot sends the exact dollar amount to send, then Venmo instructions.
3. Player sends payment on Venmo (with code or exact amount), posts a **screenshot** in the group.
4. Staff confirm and add chips as usual.

### What happens on the server

1. `/deposit` creates a row in `payment_method_bind_attempts` (pending, expires in **10 minutes**).
2. When Zapier ingests the payment, the API tries **memo match first**, then **special-amount match**.
3. On success:
   - Payment is **bound** to that support group (`venmo_payments`).
   - Group is **linked** to the Venmo variant (`group_payment_method_bindings`, `bound_via`: `memo_emoji` or `special_amount`).
   - Payer binding is updated for future repeat deposits.
4. On success, the next `/deposit` in that group uses **normal** Venmo instructions (sticky variant — no setup again).

### Already linked?

If the payer or setup chat is **already linked** elsewhere, ingest sends a **staff warning**, cancels the setup attempt, and leaves the payment **unbound** for manual bind (Part 1).

### Reset linking

Staff run **`/unbindmethod`** in the group (no arguments). Clears all payment-method links for that chat and pending setup attempts. Next `/deposit` requires setup again.

---

## Part 3 — How ingest decides (order of operations)

For **every** POST to `/api/venmo/payments`:

1. **Idempotency** — skip if `source_external_id` already seen.
2. **Insert** `venmo_payments` row.
3. **First-time setup match** (if pending attempt exists):
   - Try **memo** (`memo_emoji`) when `memo` field is present.
   - Else try **special amount** (`special_amount`).
   - On match → bind payment + link group + notify (bound).
   - On “already linked” conflict → warning notification, payment stays unbound.
4. **Repeat payer** — if still unbound, match `venmo_payer_bindings` by payer name → bind payment only (group was already linked from a prior deposit).
5. **Notify** — Telegram alert (bound or unbound).
6. **Manual** — staff reply or dashboard bind if still unbound.

---

## Part 4 — After a group is linked

| Action | Behavior |
|--------|----------|
| `/deposit` + Venmo | Normal instructions; same Venmo handle/variant as linked during setup or manual bind |
| New payment from same payer | Usually auto-binds to last group via payer binding |
| New payment, new payer, linked group | Payment may still need manual bind unless setup or payer binding applies |
| `/unbindmethod` | Clears group link; `/deposit` triggers setup again if enabled |
| **Analytics** (`/analytics`) | Filter bound GCs by club and source (`memo_emoji`, `manual`, etc.) |

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
| Ingest + notify + bind | [`bot/services/venmo_payments.py`](../bot/services/venmo_payments.py) |
| Setup attempts + group bindings | [`bot/services/payment_method_binding.py`](../bot/services/payment_method_binding.py) |
| `/deposit` setup gate | [`bot/handlers/deposit.py`](../bot/handlers/deposit.py) |
| Zapier webhook | [`api/routes/venmo_payments.py`](../api/routes/venmo_payments.py) |
| Reply-to-bind | [`notification/handlers/bind.py`](../notification/handlers/bind.py) |
| Dashboard | [`dashboard/src/pages/Payments.tsx`](../dashboard/src/pages/Payments.tsx), [`dashboard/src/pages/Analytics.tsx`](../dashboard/src/pages/Analytics.tsx) |
