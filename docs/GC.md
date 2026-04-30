# MTProto `/gc` — support group automation

This project supports creating **new Telegram support megagroups** via **MTProto (Telethon)**, triggered by the bot command **`/gc`**.

Key point: **the group is created by a club’s Telegram user account via MTProto, not by the bot via the Bot API**.

## What `/gc` does

When an authorized club operator sends `/gc` in a **private chat** with the bot:

- **Identifies club** by matching the sender’s Telegram user id against per-club config (`command_admin_user_id`).
- **Loads the club’s MTProto session** (Telethon `*.session` file).
- If the session is not authenticated, it runs an **interactive login flow** (SMS code + optional 2FA Cloud Password).
- Creates a new **megagroup** titled per the club config.
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
- Telethon **session files** contain authentication state and **must be treated as secrets**.

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
- `mtproto_phone_number` (optional: if set, `/gc` can request the SMS code without asking for a phone)
- `group_title`
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

## MTProto login flow (interactive)

If the club’s `mtproto_session` is missing or not authorized:

1. Operator runs `/gc` in DM.
2. Bot requests phone number *unless* `mtproto_phone_number` is configured for that club.
3. Bot requests Telegram to send an SMS/Telegram login code.
4. Operator replies with the code.
5. If Telegram requires 2FA, bot prompts for the **Cloud Password**.
6. Session is saved to disk and subsequent `/gc` runs skip login.

Operational notes:

- Login state is tracked **in memory** in `context.user_data` during the conversation.
- If the bot restarts mid-flow, just run `/gc` again.

## Database persistence

Table: `support_group_chats`

- SQLAlchemy model: `SupportGroupChat` in [`db/models.py`](../db/models.py)
- Insert helper: [`bot/services/support_group_chats.py`](../bot/services/support_group_chats.py)
- Migration script: [`migrate_support_group_chats.py`](../migrate_support_group_chats.py)

To create the table in an existing database:

```bash
DATABASE_URL=postgresql://... python migrate_support_group_chats.py
```

## Troubleshooting

- **Unauthorized**: Your Telegram user id does not match any `command_admin_user_id` in `club_gc_settings.py`.
- **`TG_API_ID` / `TG_API_HASH` missing**: set them in `.env` (or environment) before starting the bot.
- **Invite failures**: common causes include privacy restrictions, invalid usernames, or missing access. These appear under **Failed** in the `/gc` response.
- **FloodWait / rate limits**: the MTProto service will sleep + retry for short waits; long waits are surfaced cleanly.
- **Heroku / ephemeral FS**: Telethon sessions will be lost after redeploy unless you persist them. If sessions disappear, `/gc` will require login again.

