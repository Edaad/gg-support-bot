# Crypto payment notifications + manual group binding

Arkham (ARKM) alerts fire to Zapier; Zapier extracts transfer fields and POSTs to this API. The **notification bot** posts alerts to the shared staff Telegram group. Staff **reply** with a support group title to bind each payment.

Repeat wallets (`crypto_wallet_bindings`) can map to **multiple** support group candidates per **`from_address` + `alert_scope`**. When exactly one in-scope candidate exists, ingest auto-binds as before. When two or more exist, the payment stays unbound and staff pick from inline buttons (scope-filtered: ClubGTO vs RT/AT/CC). Manual bind with confirm updates the mapping.

## Alert scopes (two buckets)

Arkham `alertName` decides which club bucket a payment belongs to. Only these two alerts are accepted:

| Arkham `alertName` | Scope | Dashboard clubs | Bind to group titles |
|--------------------|-------|-------------------|----------------------|
| `ClubGTO Crypto Payment` | `clubgto` | ClubGTO | `GTO / …` only |
| `RT/AT/CC Crypto Payment` | `rt_at_cc` | Round Table, Creator Club | `RT`, `AT`, or `CC / …` |

Unbound RT/AT/CC payments appear when viewing **Round Table** or **Creator Club** in the dashboard (same bucket). Once bound, they show only under the bound club.

Unknown `alert_name` values are rejected at ingest with HTTP 400.

## Flow

1. **Arkham** — alert webhook to Zapier when a transfer hits a monitored deposit address
2. **Zapier** — parse nested JSON + **POST** to `/api/crypto/payments`
3. **API** — insert `crypto_payments`, auto-bind if wallet+scope known, send Telegram notification
4. **Notification bot** — staff reply with group title → bind + edit notification (updates wallet binding)

## Database

| Table | Purpose |
|-------|---------|
| `crypto_payments` | One row per on-chain deposit; binding keyed by `telegram_chat_id` |
| `crypto_wallet_bindings` | Normalized `from_address` + `alert_scope` → last bound support group |
| `payment_chip_matches` | Best-effort `/add` or auto-deposit → payment link (all methods; crypto stores tx hash in `metadata`) |

Migrations:

```bash
DATABASE_URL=... python migrate_crypto_payments.py
DATABASE_URL=... python migrate_crypto_wallet_bindings.py
DATABASE_URL=... python migrate_payment_bind_multi_candidates.py
DATABASE_URL=... python migrate_payment_chip_matches.py
```

The wallet-bindings migration backfills from existing bound `crypto_payments` rows (most recent bind per address+scope).

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `CRYPTO_ZAPIER_WEBHOOK_SECRET` | Yes (web dyno) | Auth for Zapier POST |
| `TELEGRAM_NOTIFICATION_BOT_TOKEN` | Yes (web + notification dynos) | @ggnotificationbot |
| `TELEGRAM_NOTIFICATION_BOT_TOKEN` | Yes (web + notification dynos) | Notification bot token |
| `PAYMENT_NOTIFICATION_CHAT_ID_GTO` | No* | ClubGTO bind chat |
| `PAYMENT_NOTIFICATION_CHAT_ID_RT_AT` | No* | Round Table + Aces Table bind chat |
| `PAYMENT_NOTIFICATION_CHAT_ID_CREATOR_CLUB` | No* | Creator Club bind chat |
| Slack AM escalations | No | Mirror of every payment notification via `SLACK_ESCALATION_BOT_TOKEN` + `SLACK_ESCALATION_CHANNEL_ID` (or webhook) |
| `DEBUG_NOTIFICATION` | No | Verbose ingest/Telegram logs on web dyno |

## API

**POST** `/api/crypto/payments`

Header: `X-Crypto-Webhook-Secret: <CRYPTO_ZAPIER_WEBHOOK_SECRET>`

```json
{
  "amount": "122.00",
  "token_symbol": "USDC",
  "token_name": "USD Coin",
  "chain": "bsc",
  "from_address": "0x8894E0a0c962CB723c1976a4421c95949bE2D4E3",
  "from_entity_name": "Binance",
  "to_address": "0x7063760294b901CF56b34BEB6275A641B5178CDa",
  "transaction_hash": "0xa64ed1c7ecf9dbd350f2738f9d8f0699625ee957e42a4bd6dc165c619936f6d3",
  "paid_at": "2026-05-06T23:56:53Z",
  "source_external_id": "0xa64ed1c7ecf9dbd350f2738f9d8f0699625ee957e42a4bd6dc165c619936f6d3_59",
  "alert_name": "ClubGTO Crypto Payment",
  "method_owner": "round-table",
  "test": false
}
```

- `amount` — USD value as a number string with two decimals (from `transfer.historicalUSD` or `transfer.unitValue` for stablecoins)
- `source_external_id` — use `transfer.id` for idempotent dedup
- `alert_name` — **required**; must be exactly `ClubGTO Crypto Payment` or `RT/AT/CC Crypto Payment` (case-insensitive)
- `method_owner` — **required**; ledger bucket: `round-table`, `vaughn`, or `mateos`. Use `round-table` for all standard crypto deposits unless you route Vaughn/Mateos wallets separately
- `from_entity_name` — optional; from `transfer.fromAddress.arkhamEntity.name` when Arkham labels the sender

Set `"test": true` only on a duplicate test Zap.

Response:

```json
{
  "payment_id": 42,
  "status": "unbound",
  "auto_bound": false,
  "created": true
}
```

## Notification format

Unbound example:

```
🔔 Crypto Payment Notification
ClubGTO Crypto Payment

Group Chat: Unbound — reply to this message with the group title to bind

Amount: $122
Chain: BSC
From: Binance (0x8894…D4E3)
Tx: <code>0xa64ed1c7ecf9dbd350f2738f9d8f0699625ee957e42a4bd6dc165c619936f6d3</code>
Paid: May 06, 2026 07:56 PM EDT
```

`Tx:` is the full transaction hash wrapped in Telegram HTML `<code>` so staff can tap-to-copy on mobile.

When auto-bound from a known wallet, `Group Chat:` shows the support group title instead of "Unbound".

Bound group titles may be hyperlinked with member-only `https://t.me/c/…` deep links. Joinable invite links (`t.me/+…`) are never embedded in staff payment notifications.

## Chip-add matching (`/add` and auto-deposit)

When staff runs `/add` in a linked support group (Bot API or MTProto), or payment auto-deposit attempts a ClubGG chip-add, the bot best-effort matches that chip-add to a bound payment notification for **the same** support group:

- Methods: crypto, Venmo, Zelle, Cash App, PayPal, Stripe (completed sessions)
- Amount within **±$1**, payment reference time within **2 hours**
- Prefer unmatched payments; then closest amount; then newest
- Successful matches are stored in `payment_chip_matches` (unique per method + payment id). Crypto rows also store `metadata.transaction_hash` for a future `/cryptoadd` dedupe path
- A **new** short message is posted to `PAYMENT_NOTIFICATION_CHAT_ID` (payment notification is not edited). Unmatched attempts are not persisted; the message lists up to 3 recent unmatched payments for that GC

This is a **match link only** — not confirmation that ClubGG RPA settled.

```bash
DATABASE_URL=... python migrate_payment_chip_matches.py
```

## Manual bind

Reply in the staff group with the full group title:

- **ClubGTO alert** → `GTO / 8190-5287 / PlayerName`
- **RT/AT/CC alert** → `RT / 6485-8168 / PlayerName`, `CC / …`, etc.

Binding a payment to the wrong club bucket is rejected (e.g. GTO group on an RT/AT/CC payment).

Dashboard: **Nav → Payments**, provider **Crypto** — **Bind / Rebind** on payment rows.

`/unbindmethod` in a support group clears `crypto_wallet_bindings` for that chat (along with Venmo/Zelle payer bindings), so repeat deposits from those wallets will show as unbound until staff bind again.

## Dashboard API

JWT-protected ([`api/routes/payments.py`](../api/routes/payments.py)):

| Endpoint | Purpose |
|----------|---------|
| `GET /api/payments/providers` | Includes `{ id: "crypto" }` |
| `GET /api/payments/crypto/payments?club_id=&status=&from=&to=` | Paginated payments |
| `POST /api/payments/crypto/payments/{id}/bind` | Bind or rebind `{ "group_title": "RT / …" }` |

## Processes

Same as Venmo/Zelle — see [`VENMO_PAYMENTS.md`](VENMO_PAYMENTS.md#processes). Bind replies try crypto first, then Zelle, then Venmo.

## Zapier: Arkham alert → parse → webhook

### Zap structure

1. **Trigger** — Webhooks by Zapier (Catch Hook) or Arkham’s native Zapier trigger, receiving the Arkham alert payload
2. **Parse** — Formatter or AI step using the prompt below (input = full trigger JSON)
3. **Action** — Webhooks POST to your API

Production URL example:

`https://gg-support-bot-2025.herokuapp.com/api/crypto/payments`

### Parser prompt

Use the full Arkham webhook JSON as input. Example root shape:

```json
{
  "transfer": { "id": "...", "transactionHash": "...", "fromAddress": { ... }, "toAddress": { ... }, "tokenSymbol": "USDC", "historicalUSD": 122, "chain": "bsc", "blockTimestamp": "2026-05-06T23:56:53Z" },
  "alertName": "ClubGTO Crypto Payment",
  "id": 543983
}
```

```text
Parse this Arkham crypto transfer alert JSON and return these fields:

amount — USD value as a number string with two decimals, no dollar sign. Prefer transfer.historicalUSD when present; otherwise use transfer.unitValue for stablecoins (example: 122.00).

token_symbol — transfer.tokenSymbol uppercased (example: USDC).

token_name — transfer.tokenName if present; otherwise empty.

chain — transfer.chain lowercased (example: bsc, ethereum, polygon).

from_address — transfer.fromAddress.address (full hex).

from_entity_name — transfer.fromAddress.arkhamEntity.name if present (example: Binance); otherwise empty.

to_address — transfer.toAddress.address (full hex).

transaction_hash — transfer.transactionHash (full hex).

paid_at — transfer.blockTimestamp ISO string if present (example: 2026-05-06T23:56:53Z); otherwise empty.

source_external_id — transfer.id (example: 0xa64e...f6d3_59). Required for dedup.

alert_name — top-level alertName. Must be exactly ClubGTO Crypto Payment or RT/AT/CC Crypto Payment (case-insensitive). This decides the club bucket.

method_owner — always round-table unless this deposit is explicitly Vaughn- or Mateos-routed (same slugs as other payment ingests).
```

### Webhook POST body

Header: `X-Crypto-Webhook-Secret: <CRYPTO_ZAPIER_WEBHOOK_SECRET>`

Map parse outputs into JSON:

```json
{
  "amount": "<parse: amount>",
  "token_symbol": "<parse: token_symbol>",
  "token_name": "<parse: token_name>",
  "chain": "<parse: chain>",
  "from_address": "<parse: from_address>",
  "from_entity_name": "<parse: from_entity_name>",
  "to_address": "<parse: to_address>",
  "transaction_hash": "<parse: transaction_hash>",
  "paid_at": "<parse: paid_at>",
  "source_external_id": "<parse: source_external_id>",
  "alert_name": "<parse: alert_name>",
  "method_owner": "round-table"
}
```

Add `"test": true` only on your duplicate test Zap.

### Field mapping without AI (Code step alternative)

If you prefer a Code by Zapier step instead of an AI parser:

```javascript
const root = inputData;
const t = root.transfer || {};
const from = t.fromAddress || {};
// Bitcoin/UTXO alerts use fromAddresses[]; EVM uses fromAddress
const fromEntry = (t.fromAddresses && t.fromAddresses[0]) || {};
const fromAddrObj = fromEntry.address || {};
const to = t.toAddress || {};
const entity = from.arkhamEntity || fromAddrObj.arkhamEntity || {};

const usd = t.historicalUSD != null ? t.historicalUSD : t.unitValue;
const amount = Number(usd).toFixed(2);
const chain = (t.chain || '').toLowerCase();

// Native BTC has no tokenSymbol — default BTC (do not leave blank; API requires it)
let token_symbol = (t.tokenSymbol || '').toUpperCase();
if (!token_symbol && chain === 'bitcoin') token_symbol = 'BTC';

const from_address =
  from.address ||
  (typeof fromAddrObj === 'string' ? fromAddrObj : fromAddrObj.address) ||
  '';

return {
  amount,
  token_symbol,
  token_name: t.tokenName || (token_symbol === 'BTC' ? 'Bitcoin' : ''),
  chain,
  from_address,
  from_entity_name: entity.name || '',
  to_address: to.address || '',
  transaction_hash: t.transactionHash || t.txid || '',
  paid_at: t.blockTimestamp || '',
  source_external_id: t.id || root.id || '',
  alert_name: root.alertName || '',
  method_owner: 'round-table',
};
```

**Bitcoin note:** Arkham sends `fromAddresses[].address.address` (nested), not `fromAddress.address`. Never POST if `from_address` or `token_symbol` is empty.

You need **two Arkham alerts** (or two Zaps), one per `alertName` above, each posting to the same API endpoint.

### Example from sample payload

Given the sample alert in the repo discussion:

| Field | Value |
|-------|-------|
| amount | `122.00` |
| token_symbol | `USDC` |
| token_name | `USD Coin` |
| chain | `bsc` |
| from_address | `0x8894E0a0c962CB723c1976a4421c95949bE2D4E3` |
| from_entity_name | `Binance` |
| to_address | `0x7063760294b901CF56b34BEB6275A641B5178CDa` |
| transaction_hash | `0xa64ed1c7ecf9dbd350f2738f9d8f0699625ee957e42a4bd6dc165c619936f6d3` |
| paid_at | `2026-05-06T23:56:53Z` |
| source_external_id | `0xa64ed1c7ecf9dbd350f2738f9d8f0699625ee957e42a4bd6dc165c619936f6d3_59` |
| alert_name | `ClubGTO Crypto Payment` |
| method_owner | `round-table` |

## Code references

- [`bot/services/crypto_payments.py`](../bot/services/crypto_payments.py) — ingest, notify, bind
- [`api/routes/crypto_payments.py`](../api/routes/crypto_payments.py) — Zapier ingest webhook
- [`api/routes/payments.py`](../api/routes/payments.py) — dashboard list + bind API
- [`notification/handlers/bind.py`](../notification/handlers/bind.py) — reply-to-bind handler
- [`dashboard/src/pages/Payments.tsx`](../dashboard/src/pages/Payments.tsx) — Payments UI
