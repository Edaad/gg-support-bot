# Database schema and business logic

This document describes the relational model in [`db/models.py`](../db/models.py), how pieces relate, and how the **bot** and **dashboard API** use them. Tables are created automatically via SQLAlchemy `Base.metadata.create_all` when the API or bot starts (no separate migration runner in normal operation).

Typical deployment uses **PostgreSQL** (`DATABASE_URL`). Types below match the SQLAlchemy declarations; exact SQL types may vary slightly by dialect.

---

## High-level relationships

```mermaid
erDiagram
    clubs ||--o{ club_linked_accounts : "backup admins"
    clubs ||--o{ payment_methods : "deposit or cashout"
    clubs ||--o{ groups : "telegram groups"
    clubs ||--o{ player_details : "GG player chats"
    clubs ||--o{ custom_commands : "slash commands"
    clubs ||--o{ broadcast_jobs : "mass messages"
    clubs ||--o{ player_activities : "cooldown timeline"
    clubs ||--o{ cooldown_bypasses : "per player"
    payment_methods ||--o{ payment_sub_options : "e.g. crypto networks"
    payment_methods ||--o{ payment_method_tiers : "amount bands"
```

---

## Tables

### `clubs`

One row per **club** (poker/gaming room operator). The **primary** Telegram identity is `telegram_user_id` (must be globally unique). Settings drive welcome/list copy, optional **simple** deposit/cashout (skip interactive flow), **cashout cooldown** and **business hours**, and feature toggles.

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | Internal id; referenced everywhere. |
| `name` | string(100) | Display name in dashboard. |
| `telegram_user_id` | bigint, unique | Primary owner’s Telegram user id; `/set` in DM applies to this club. |
| `welcome_*` | type, text, file_id, caption | Message when bot is added to a group (see **Groups**). |
| `list_*` | type, text, file_id, caption | Content for `/list` (group uses linked club; DM uses user’s own club if they are the owner). |
| `allow_multi_cashout` | bool | If true, `/cashout` lets players pick multiple methods then **Done**; if false, one method then submit. |
| `allow_admin_commands` | bool | If false, global `ADMIN_USER_IDS` from [`config.py`](../config.py) cannot use `/deposit` or `/cashout` in this club’s groups (silent ignore). |
| `deposit_simple_mode` + `deposit_simple_*` | bool + content | When on, `/deposit` sends one canned response (text/photo) with no amount/method UI. |
| `cashout_simple_mode` + `cashout_simple_*` | bool + content | Same for `/cashout`; no cooldown UI in-flow (eligibility still checked first). |
| `cashout_cooldown_enabled` | bool | Enforces wait between **cashouts** based on last activity (see **Player activity**). |
| `cashout_cooldown_hours` | int | Hours after last **deposit or cashout** before another cashout is allowed. |
| `cashout_hours_enabled` | bool | Restricts cashout to a daily window (interpreted in **America/New_York** in code). |
| `cashout_hours_start` / `cashout_hours_end` | string(5) | e.g. `08:00`–`23:00` local to that timezone. |
| `is_active` | bool | Inactive clubs are excluded from owner resolution in bot queries. |
| `created_at` | datetime | Server default `now()`. |

**Business logic:** Club staff = primary `telegram_user_id` **or** any row in `club_linked_accounts` for that `club_id`. They are exempt from cashout cooldown checks. Global admins are not “staff” unless linked; they follow the same rules as players unless `allow_admin_commands` allows them to use deposit/cashout in groups.

---

### `club_linked_accounts`

**Backup** Telegram accounts that share the same club configuration as the primary. Each `telegram_user_id` is **globally unique** (cannot be another club’s primary or another link).

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `club_id` | FK → `clubs.id` CASCADE | |
| `telegram_user_id` | bigint, unique | Linked user; can add bot to groups, trigger staff-only custom commands, etc. |
| `created_at` | datetime | |

**Business logic:** Documented in [`LINKED_ACCOUNTS.md`](LINKED_ACCOUNTS.md). `/set`, `/mycmds`, `/delete` in private chat remain **primary-only**; linked users use the dashboard or the primary.

---

### `payment_methods`

Configurable **deposit** and **cashout** rails per club. `direction` is either `deposit` or `cashout`. The bot filters methods by **entered amount** against `min_amount` / `max_amount`, then shows inline keyboards; responses can be plain text or Telegram **photo** (`response_file_id`, `response_caption`).

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `club_id` | FK → `clubs.id` CASCADE | |
| `direction` | string(10) | `deposit` or `cashout` (DB check constraint). |
| `name`, `slug` | string(50) | **Unique `(club_id, direction, slug)`**. `slug` is for internal reference; labels use `name`. |
| `min_amount`, `max_amount` | numeric(12,2), nullable | Optional band; method hidden if amount is out of range. |
| `has_sub_options` | bool | If true, bot may show `payment_sub_options` after method pick. |
| `response_type`, `response_text`, `response_file_id`, `response_caption` | | Default response when **no tier** matches and **no** sub-option path (or tier fallback). |
| `is_active` | bool | Inactive methods hidden from flows and simulate API. |
| `sort_order` | int | Ordering in UI and keyboards (reorder API). |
| `created_at` | datetime | |

**Business logic:**

- **Tiers** (`payment_method_tiers`): For a given amount, the bot selects the matching tier (if any) and uses that row’s response instead of the method default. Used for amount-dependent instructions.
- **Sub-options** (`payment_sub_options`): If `has_sub_options` and options exist, user picks a sub-option; response comes from that row. Typical for multiple networks under one “Crypto” method.

Deposit flow records **`player_activities`** with `activity_type = 'deposit'` after a successful completion. Cashout does the same with `'cashout'`.

---

### `payment_sub_options`

Sub-choices under one **payment method** (e.g. USDT vs BTC). Unique **`(method_id, slug)`**.

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `method_id` | FK → `payment_methods.id` CASCADE | |
| `name`, `slug` | string(50) | |
| `response_*` | | Same pattern as method. |
| `is_active` | bool | |
| `sort_order` | int | |

---

### `payment_method_tiers`

Optional **amount bands** for a method. Each tier has `label`, optional min/max, and its own `response_*`. The bot picks the tier whose bounds contain the user’s amount (see `get_tier_for_amount` in [`bot/services/club.py`](../bot/services/club.py)).

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `method_id` | FK → `payment_methods.id` CASCADE | |
| `label` | string(50) | Shown in internal logic / display name assembly. |
| `min_amount`, `max_amount` | numeric(12,2), nullable | |
| `response_*` | | |
| `sort_order` | int | |

---

### `groups`

Maps a **Telegram group/supergroup** (`chat_id` = Telegram chat id) to exactly one **club**. When the bot is added to a group, the linking user must be the club’s **primary** or a **linked** account; the row is created or updated.

| Column | Type | Business meaning |
|--------|------|------------------|
| `chat_id` | bigint PK | Telegram group id. |
| `club_id` | FK → `clubs.id` CASCADE | Which club’s config applies (`/deposit`, `/cashout`, `/list`, custom commands, etc.). |
| `added_at` | datetime | |

**Business logic:** `/deposit` and `/cashout` only run in groups that have a row here. **Broadcast** sends to **all** `chat_id`s for the club’s `groups`. There is no per-group override table; everything is club-level.

---

### `player_details`

Maps an external **GG player id** to a **club** and a list of **Telegram group chat ids** (`chat_ids`). One row per **`(gg_player_id, club_id)`**; multiple groups are stored in the **`BIGINT[]`** column (not `INTEGER[]`, so typical Telegram supergroup ids fit).

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `chat_ids` | `bigint[]` | Telegram group chat ids for this player–club link. |
| `gg_player_id` | string(255) | External GG player identifier. |
| `club_id` | FK → `clubs.id` CASCADE | |

**Constraint:** `uq_player_details_gg_player_club` — unique `(gg_player_id, club_id)`.

**Indexes:** B-tree on `club_id` and `gg_player_id`; **GIN** on `chat_ids` for containment queries (e.g. `chat_ids @> ARRAY[id]::bigint[]`).

**No FK to `groups`:** PostgreSQL cannot attach a foreign key to individual elements of an array. Whether each id exists in `groups` must be enforced in **application code** (or custom triggers). Deleting a `groups` row does **not** remove that `chat_id` from arrays automatically.

**Migration:** [`migrate_player_details.py`](../migrate_player_details.py) (`DATABASE_URL=... python migrate_player_details.py`). New deploys also get the table from `Base.metadata.create_all` once the model exists.

**Bulk import (CSV):** [`scripts/import_player_details_csv.py`](../scripts/import_player_details_csv.py) reads `chat_id`, `gg_player_id`, `club_id` (supports `[n]` and `"[2, 3]"`). It aggregates rows, merges `chat_ids` on duplicate `(gg_player_id, club_id)`, and uses `ON CONFLICT` to merge with existing DB rows. **Strict validation:** `gg_player_id` must match `^[0-9]{1,48}-[0-9]{1,48}$`; `chat_id` must be negative (Telegram group chats) unless `--allow-nonnegative-chat-id`; `club_id` must be in `[1, 1000000]`; control characters and CSV formula prefixes (`=`, `+`, `@` on non-chat columns) are stripped. Run **dry run** first (default): `python scripts/import_player_details_csv.py --csv player_data_mapped.csv`. To write: `DATABASE_URL=... python scripts/import_player_details_csv.py --csv player_data_mapped.csv --apply`. Rows with unknown `club_id` in the DB or invalid fields are skipped (warnings printed).

**Auto-tracking via group title:** The bot can bind a group chat to `player_details` by parsing the group title and appending the chat id to `chat_ids` for the `(gg_player_id, club_id)` row.

- **One-group-per-(club,player)**: The bot enforces that a given `(club_id, gg_player_id)` pair cannot be tracked by multiple different Telegram group chats. If another group chat id is already present in `chat_ids` for that row, the bind is blocked and the bot returns a conflict message (title-change and “bot added” triggers still remain silent only for invalid format, not for conflicts).
- **Format**: `SHORTHAND / GGPLAYERID / anything` (example: `GTO / 8190-5287 / ThePirate343`)
- **Club resolution**: `SHORTHAND` is mapped to a canonical `clubs.name` via `CLUB_SHORTHAND_TO_NAME` in [`config.py`](../config.py), then resolved to `clubs.id` (case-insensitive exact match).
- **Triggers**:
  - Rename the group title (bot listens for NEW_CHAT_TITLE). If invalid format, bot is silent.
  - `/track` in the group to bind now (responds with invalid format if it can't parse/resolve).
  - `/info` shows what GG player id(s) are currently bound for this chat (or Not bound).

---

### `broadcast_jobs`

Tracks **dashboard-initiated broadcasts** to all linked groups. Status values include `running`, `done`, `cancelled`. Payload snapshot is stored (`response_type`, text, file id, caption) for audit; progress fields `sent`, `failed`, `errors_json` update during the async send. See [`api/routes/broadcast.py`](../api/routes/broadcast.py).

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `club_id` | FK → `clubs.id` CASCADE | |
| `status` | string(20) | |
| `total_groups` | int | Target count at start. |
| `sent`, `failed` | int | Progress counters. |
| `errors_json` | text | JSON array of error strings (truncated in worker). |
| `response_*` | | Copy of message being broadcast. |
| `created_at`, `finished_at` | datetime | |

---

### `player_activities`

Append-only style log of **completed** deposit and cashout actions for **cooldown**. One row per completion (not per message). `cancelled` exists so a user can abort a cashout **after** a row was written in edge cases—`cancel_last_cashout_activity` marks the latest cashout row cancelled so cooldown looks back to the previous non-cancelled activity.

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `club_id` | FK → `clubs.id` CASCADE | |
| `telegram_user_id` | bigint | Player (audit; eligibility is per group). |
| `chat_id` | bigint | Support group where it happened; **cooldown key**. |
| `activity_type` | string(10) | `deposit` or `cashout`. |
| `cancelled` | bool | |
| `created_at` | datetime | |

**Business logic:** `check_cashout_eligibility` uses the **latest non-cancelled** deposit or cashout timestamp for that **support group** (`club_id` + `chat_id`). If cooldown is enabled, the next `/cashout` in that group must wait `cashout_cooldown_hours` after that timestamp (subject to business hours and bypasses). Admin `/add` and `/cash` set the timer for the current group without replying to a message.

---

### `cooldown_bypasses`

Per **support group chat** exceptions for cooldown only (not for business hours alone—see code in `check_cashout_eligibility`).

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `club_id` | FK → `clubs.id` CASCADE | |
| `chat_id` | bigint | Support group the bypass applies to. |
| `telegram_user_id` | bigint | Legacy; unused for eligibility. |
| `bypass_type` | string(20) | `one_time` (consumed on next successful check) or `permanent`. |
| `used` | bool | For one-time bypass after use. |
| `created_at` | datetime | |

**Business logic:** Granted via `/bypass` and `/bypasspermanent` in the support group (no reply required); see [`bot/handlers/bypass.py`](../bot/handlers/bypass.py). Run [`migrate_cooldown_bypass_chat_id.py`](../migrate_cooldown_bypass_chat_id.py) on existing DBs; re-grant bypasses per group after migration.

---

### `custom_commands`

Club-defined **slash commands** (without the leading slash in the column) with optional **customer visibility**.

| Column | Type | Business meaning |
|--------|------|------------------|
| `id` | integer PK | |
| `club_id` | FK → `clubs.id` CASCADE | |
| `command_name` | string(32) | **Unique per club** (`uq_club_command`); stored lowercased on create/update via API. |
| `response_*` | | Same text/photo pattern. |
| `customer_visible` | bool | If false, only club staff + global admins can trigger in groups; DM behavior follows router rules. |
| `is_active` | bool | |

**Business logic:** The bot resolves the group’s `club_id`, then looks up `command_name`. If `customer_visible` is false, the user must be staff or in `ADMIN_USER_IDS` (see [`bot/handlers/commands.py`](../bot/handlers/commands.py)). `/set` in DM syncs presets into this table for the owner’s club.

---

## Constraints summary

| Constraint | Table |
|------------|--------|
| `uq_club_direction_slug` | `payment_methods` — unique `(club_id, direction, slug)` |
| `ck_direction` | `payment_methods` — `direction IN ('deposit', 'cashout')` |
| `uq_method_slug` | `payment_sub_options` — unique `(method_id, slug)` |
| `uq_club_command` | `custom_commands` — unique `(club_id, command_name)` |
| `uq_player_details_gg_player_club` | `player_details` — unique `(gg_player_id, club_id)` |
| Unique | `clubs.telegram_user_id`, `club_linked_accounts.telegram_user_id` |

Foreign keys generally use **ON DELETE CASCADE** from `clubs` so child rows disappear if a club is deleted.

---

## Operational notes

- **Schema changes:** New columns may be added with manual SQL or small scripts (e.g. [`migrate_cooldown.py`](../migrate_cooldown.py), [`migrate_player_details.py`](../migrate_player_details.py)) if `create_all` already ran without them.
- **Legacy:** Root [`main.py`](../main.py) uses older tables (`user_commands`, `group_club`); migration from that layout is described in [`db/migrate.py`](../db/migrate.py). The running bot uses [`bot/`](../bot/) and the models above.

---

## See also

- [API.md](API.md) — REST surface that edits most of these tables
- [LINKED_ACCOUNTS.md](LINKED_ACCOUNTS.md) — Linked accounts behavior in detail
