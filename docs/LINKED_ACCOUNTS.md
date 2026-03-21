# Linked Telegram accounts (multi-account clubs)

Each club has a **primary** Telegram user ID on the club record (`Telegram User ID` in the dashboard General tab). You can add **backup** accounts that share the same club configuration (commands, payment methods, group links).

## Behavior

- **Primary** and **linked** accounts can add the bot to a group; the group is linked to that club.
- **Custom commands** (including those not visible to customers): in **groups**, both **primary** and **linked** accounts can trigger them, as can global `ADMIN_USER_IDS`. In **private** chat, the same rule applies when the user is tied to that club (primary or linked).
- **`/set`, `/mycmds`, `/delete`**: only the **primary** Telegram user ID on the club can use these in DMs. Linked accounts should use the dashboard or ask the primary owner. Changes from `/set` apply to the whole club.
- Each numeric Telegram user ID can only be used **once** across the system: either as one club’s primary, or as one linked row (not both clubs).

## Configuration

1. **Dashboard (recommended)**  
   Open a club → **General** → **Linked Telegram accounts (backup)**.  
   Enter a numeric Telegram user ID (from `@userinfobot`, `/whoami` in your bot, etc.) and click **Add backup account**.

2. **`config.py` / `ADMIN_USER_IDS`**  
   **Not required** for linked accounts to use admin-only **custom commands** in groups.  
   `ADMIN_USER_IDS` is still used for **global** operators (e.g. extra access across clubs).  
   Add users there only if they should be global admins.

## Database / deployment

The `club_linked_accounts` table is created automatically when:

- The **API** starts (`Base.metadata.create_all` in [`api/app.py`](../api/app.py)), or  
- The **bot worker** starts ([`bot/main.py`](../bot/main.py)).

**Heroku:** Deploy the new code, then restart web and worker dynos. If `create_all` does not run (e.g. API only), ensure at least one process runs migrations or create the table manually:

```sql
CREATE TABLE club_linked_accounts (
  id SERIAL PRIMARY KEY,
  club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
  telegram_user_id BIGINT NOT NULL UNIQUE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX ix_club_linked_accounts_club_id ON club_linked_accounts (club_id);
```

(Exact SQL may match your dialect; SQLAlchemy’s `create_all` is preferred.)

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/clubs/{club_id}/linked-accounts` | List linked rows (not the primary) |
| POST | `/api/clubs/{club_id}/linked-accounts` | Body: `{"telegram_user_id": 123}` |
| DELETE | `/api/clubs/{club_id}/linked-accounts/{account_id}` | Remove a linked row |

`GET /api/clubs` and `GET /api/clubs/{id}` include `linked_account_count`.

## Changing the primary ID

Edit **Telegram User ID** in Club Info and save. If the new ID was previously a **linked** account for that club, the link row is removed automatically when you promote it to primary.
