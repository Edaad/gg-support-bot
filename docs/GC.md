# MTProto `/gc` — support group automation

This project supports creating **new Telegram support megagroups** via **MTProto (Telethon)**, triggered either by the bot command **`/gc`** or (optionally) by a club admin sending **`/gc` in a private DM with a player** from the MTProto-logged-in admin account.

Key point: **the group is created by a club’s Telegram user account via MTProto, not by the bot via the Bot API**.

## Outgoing `/gc` in admin → player DMs (optional)

**On by default** on the bot worker (set **`GC_DM_GC_LISTENER_ENABLED=false`** to disable). Use **one** process only — the same Telethon session must not connect twice:

- Each configured club starts a Telethon client using that club’s session (file and/or Postgres `StringSession`).
- If an outgoing private message text is **exactly** `/gc`, the handler deletes that message, resolves the **player** from the DM peer, and either **creates** a new megagroup or **reuses** the existing one for `(club_key, player_telegram_user_id)`.
- The player receives a **global** DM template (see [`bot/services/player_support_dm_messages.py`](../bot/services/player_support_dm_messages.py)).
- Metadata is written to **`support_group_chats`** (run [`migrate_support_group_chats_player_dm.py`](../migrate_support_group_chats_player_dm.py) on existing DBs).

**Testing:** Authorize the club’s MTProto session (Dashboard **Telegram login** or [`scripts/mtproto_login_cli.py`](../scripts/mtproto_login_cli.py)), run a single `python run_bot.py` worker (listener is on unless `GC_DM_GC_LISTENER_ENABLED=false`), open Telegram as the club admin user, DM a player, send `/gc`, and confirm the command disappears and the group + DB row appear.

## What `/gc` does (private chat with the **bot**)

When an authorized club operator sends `/gc` in a **private chat** with the bot:

- **Identifies club** by matching the sender’s Telegram user id against per-club config (`command_admin_user_id`).
- **Loads the club’s MTProto session** (Telethon `*.session` file).
- If the session is **not authenticated**, `/gc` tells you it **expired or is missing**, and directs you to **Dashboard → Telegram login** to complete SMS / Telegram code + optional 2FA (no OTP in Telegram bot DMs anymore).
- When the session is authorized, **`/gc` creates a new megagroup** titled **`{RT|CC|GTO} / / {player label}`**: club prefix (`RT`, `CC`, or `GTO`), then literal **` / / `**, then player identity in order **`@username` → `First Last` → `First` → `New Player`** (bot-only `/gc` uses `New Player` as the label). Telegram has a ~255-character title cap; extra-long labels are truncated.
- Adds members best-effort:
  - configured `users_to_add` (usernames like `@name`, phone contacts if resolvable, etc.)
  - the **bot account** (if it can be resolved as a username)
- Optionally sets a **group photo** (best-effort; failures do not abort).
- Exports a working **invite link** (best-effort; failures do not abort).
- Sends an **initial message in the new group via MTProto** (template includes the invite link).
- Writes an audit row to **`support_group_chats`** in Postgres.
- Replies to the operator with:
  - group title
  - invite link
  - added users
  - failed users + reasons
  - DB record id (when saved)

## Security / privacy guarantees

- **SMS codes and Cloud Passwords are never stored in the database.**
- **SMS codes and Cloud Passwords are not logged.**
- Telethon **session files** and **database-backed session strings** both grant account access — treat Postgres rows in `mtproto_session_credentials` as **secrets**.

## Command scope (private chat only)

`/gc` is intentionally restricted to **DMs** with the bot to avoid exposing login codes in group chats.

If you try `/gc` in a group, the bot will tell you to use a private chat.

## Configuration

Primary config is in [`club_gc_settings.py`](../club_gc_settings.py).

It defines `CLUB_GC_CONFIG` with three keys:

- `round_table`
- `creator_club`
- `clubgto`

Each entry includes:

- `club_key`
- `club_display_name`
- `command_admin_user_id` (who may run `/gc` for this club)
- `mtproto_session` (path to Telethon session file, typically under `sessions/`)
- `mtproto_phone_number` (optional: if set, Dashboard **Telegram login** does not ask for phone; `/gc` does not consume this for login anymore)
- `group_title` (legacy env fallback; megagroups use `RT/CC/GTO / / …` naming — see megagroup title helpers in [`bot/services/mtproto_group_create.py`](../bot/services/mtproto_group_create.py))
- `group_photo_path` (optional)
- `users_to_add`: default from [`config.py`](../config.py) **`GC_USERS_TO_INVITE`** tuples (e.g. `("@user1",)`); override with comma-separated env `GC_USERS_ROUND_TABLE`, `GC_USERS_CREATOR_CLUB`, or `GC_USERS_CLUB_GTO` when set
- `bot_account` (optional override; see below)
- `initial_group_message_template`

### Environment variables

MTProto requires Telegram developer API credentials:

- `TG_API_ID`
- `TG_API_HASH`

They are **shared across clubs** and used only for Telethon sessions.

Per-club overrides are supported via `GC_*` variables (see [`.env.example`](../.env.example)).

- **`GC_DM_GC_LISTENER_ENABLED`** — omit or leave empty for **on**; set `false` / `0` / `no` / `off` to disable outgoing-DM `/gc` listeners.
- **`GC_DM_GC_VERBOSE_LOGS`** — set `true` / `1` / `yes` to emit extra **INFO** lines for outgoing-DM `/gc` (`dm_capture`, `/gc_match`, bootstrap). Omit for **quiet** INFO (warnings and errors still log).

### Bot account invite behavior

To add the bot into the newly created megagroup, the MTProto account must be able to resolve the bot as a peer.

- If the bot has a public username, we resolve `@<bot_username>` automatically via `get_me()`.
- If Telegram hides the bot username (or it is missing), set:
  - `GC_BOT_ACCOUNT=@YourBotUsername`

If neither is available, `/gc` will still create the group and log a warning that the bot invite was skipped.

### Sessions and gitignore

Telethon sessions are stored as `*.session` (and sometimes `*.session-journal`).

This repo ignores them via [`.gitignore`](../.gitignore):

- `sessions/`
- `*.session`
- `*.session-journal`

**Do not commit session files.**

## MTProto login (Dashboard)

If the club’s Telethon session is missing or Telegram revokes authorization:

1. Open the **GG Dashboard** (JWT login) → **Telegram login** (`/telegram-login` in dev).
2. Pick the club, **Send login code**, then paste the OTP (and Cloud Password if 2FA is enabled).
3. During the OTP flow the web dyno writes the usual Telethon **SQLite `.session`** under `sessions/` (ephemeral during login). Once Telegram accepts OTP/2FA, the server snapshots that authorization into Postgres (`mtproto_session_credentials`).

4. **`/gc` on the Telegram bot dyno** prefers the **Postgres-backed StringSession**, so workers do **not** need the web filesystem.

Protected HTTP API (JWT), implemented in [`api/routes/gc_mtproto.py`](../api/routes/gc_mtproto.py):

- `GET /api/gc/mtproto/clubs`
- `POST /api/gc/mtproto/send-code`
- `POST /api/gc/mtproto/sign-in`
- `POST /api/gc/mtproto/cloud-password`
- `POST /api/gc/mtproto/sync-disk-session` `{ "club_key": "…" }` — promotes an authorized on-disk `.session` on **this host** into Postgres (migration helper).

### Postgres table

- Model [`MtProtoSessionCredential`](../db/models.py).
- Migration: [`migrate_mtproto_session_credentials.py`](../migrate_mtproto_session_credentials.py) (tables are also ensured by startup `create_all`).
- Rows hold **secrets** (same sensitivity as committing `*.session` files). Rotate if leaked.
- **`GC_MTPROTO_DB_SESSIONS=false`** — skip Postgres (file-only Telethon paths; suited to single-machine dev).

### Web vs bot workers (Heroku-style)

Dashboard OTP runs where **`run_api`/web** lives (scratch `sessions/`). The bot **`worker`** has a separate disk unless you bolt on shared volumes. Postgres is the canonical copy of authorization after OTP so **`/gc` works on the worker** without copying files manually. If you had already logged in on web **before** this feature shipped, redeploy migrations and either complete **Telegram login** once again or call **`/api/gc/mtproto/sync-disk-session`** with JWT while this release’s web dyno still has an authorized `sessions/` file.

## Player contact sync (rename, `/track`, `/info`)

When **`TG_API_ID` / `TG_API_HASH`** are set and the club’s Telethon session is authorized, the worker may **save or update one Telegram contact** on that club MTProto account (same session as `/gc`):

**Triggers:** **Group title change** after a successful bind (same path as the “Thank you for playing…” message), **`/track`** after a successful bind, and **`/info`**. Club resolution: title shorthand or **`groups`** link.

**Failure DM:** If sync cannot complete (e.g. session not authorized, ambiguous members, `AddContact` error), the bot sends a **short private message** to that club’s **`command_admin_user_id`** ([`GC_ADMIN_USER_*`](../club_gc_settings.py)). That user must have **`/start`**ed the bot so Telegram accepts the DM.

**Club selection:** Uses [`get_club_gc_config_by_link_club_id()`](../club_gc_settings.py) — **`clubs.id`** must match that club’s **`link_club_id`** (Round Table / Creator Club / ClubGTO profiles).

**Who is saved:** All **non-bot** members are scanned. **Excluded:** group **admins**; **`ClubGcConfig.users_to_add`** ([`GC_USERS_*`](../config.py)) plus **`GC_BOT_ACCOUNT`** when set (resolved to user ids); the three **`/gc` MTProto operator** Telegram IDs ([`gc_mtproto_operator_telegram_user_ids()`](../club_gc_settings.py)); and [`ADMIN_USER_IDS`](../config.py) (dashboard operators). Often the club MTProto human sits in the support group beside the player but is **not** a Telegram “admin”; excluding those IDs restores a single plausible **player** candidate. If **exactly one** human remains after exclusions, contact **first name** = **group chat title** (truncated). Otherwise the feature **does nothing** (ambiguous)—see worker logs **`contact_save: skip`** / **`candidate_count`**.

**Disable:** `GC_CONTACT_SAVE_ENABLED=false` (or `0` / `no` / `off`). Default is on.

**Edge cases:** A **player promoted to admin** is excluded from the candidate pool, so no contact is saved. Unresolvable `users_to_add` markers are skipped for exclusion (logged); they do not block the rest of the list.

Implementation: [`bot/services/mtproto_track_contact.py`](../bot/services/mtproto_track_contact.py).

## Database persistence

Table: `support_group_chats`

- SQLAlchemy model: `SupportGroupChat` in [`db/models.py`](../db/models.py)
- Insert helper: [`bot/services/support_group_chats.py`](../bot/services/support_group_chats.py)
- Migration scripts: [`migrate_support_group_chats.py`](../migrate_support_group_chats.py), [`migrate_support_group_chats_player_dm.py`](../migrate_support_group_chats_player_dm.py)

To create / extend the table in an existing database:

```bash
DATABASE_URL=postgresql://... python migrate_support_group_chats.py
DATABASE_URL=postgresql://... python migrate_support_group_chats_player_dm.py
```

## Troubleshooting

- **Unauthorized**: Your Telegram user id does not match any `command_admin_user_id` in `club_gc_settings.py`.
- **`TG_API_ID` / `TG_API_HASH` missing**: set them in `.env` (or environment) before starting the bot.
- **Invite failures**: common causes include privacy restrictions, invalid usernames, or missing access. These appear under **Failed** in the `/gc` response.
- **FloodWait / rate limits**: the MTProto service will sleep + retry for short waits; long waits are surfaced cleanly.
- **Heroku / ephemeral FS**: Telethon sessions will be lost after redeploy unless you persist them. If sessions disappear, `/gc` will require login again.

- **`PhoneNumberInvalidError` / Telegram says invalid phone**: same format (`+<country_code><subscriber>`). Typical mistakes: wrong **`MT_PROTO_PHONE_*`** on Heroku or pasting messy formats in Dashboard. Fix the Config Var (`+14155552671`–style), or omit it and submit the phone in **Telegram login**.

- **`PhoneCodeExpiredError`**: Telegram ties each code to one **`phone_code_hash`**. It can look “instant” but still fail if anything triggered a **second** `SendCode` (another `/gc`, retry logic, or **two bot workers** polling the same token so two processes both request codes). **Heroku:** use **exactly one** `worker` dyno for the Telegram bot. Another common case: two SMS messages — only the **latest** matches the hash the bot saved. The app no longer auto-retries `SendCode` after `FloodWait` (that retry could issue a second code and invalidate the first).

  **More detail:** Telegram’s MTProto layer usually only returns the generic string *“The confirmation code has expired”* (often with *“(caused by SignInRequest)”*). There is typically **no extra machine-readable reason**. The bot logs a **`PhoneCodeExpired`** line with **seconds since SendCode**, **`phone_code_hash` length**, **code length**, and **request type** (never the code or hash value) so you can tell if the failure was immediate or not.

