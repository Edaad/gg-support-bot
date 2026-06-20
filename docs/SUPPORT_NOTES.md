# Support notes — `/notes`, `/note`, `/resolve`

Staff-only bot commands for **player dispute handoff** between AM shifts. Notes are stored in Postgres and never posted to player support groups.

## Who can use it

Club staff, MTProto operators (`GC_MTPROTO_*` config), and global admins (`ADMIN_USER_IDS`).

## Commands (private chat with the bot)

| Command | What it does |
|---------|--------------|
| `/notes` | List all **open** issues (club name, player id, latest next steps, note count, age) |
| `/notes 8190-5287` | Full note history for one player (newest first) |
| `/note` | Start a guided note flow (situation → actions → next steps) |
| `/note 8190-5287` | Same flow, player id pre-filled |
| `/resolve 8190-5287` | Mark all **open** issues for that player as resolved |

All read/write output stays in **DM with the bot**. `/notes` and `/resolve` refuse to run in groups.

## Adding a note from a support group

Staff can send `/note` **in the player's support group** to capture context without typing the player id:

1. Bot reads the GG player id from the group title (e.g. `RT / 8190-5287 / PlayerName`).
2. Bot deletes the `/note` command message (best-effort).
3. Bot replies: context saved — open a **private chat** and send `/note` to finish.

The DM flow then skips player/club prompts when that pending context exists.

## Note flow fields

Each note captures three required fields:

- **Situation** — what happened
- **Actions taken** — what staff already did
- **Next steps** — what the next AM should do

Multiple notes append to the same **open issue** per `(club, player id)`. A new open issue is created automatically on the first note.

## Data model

| Table | Purpose |
|-------|---------|
| `player_support_issues` | One row per open/resolved dispute (`status`: `open` or `resolved`) |
| `player_support_notes` | Append-only notes linked to an issue |

At most one **open** issue per club + player id (enforced by a partial unique index).

## Migration

Run once on each environment:

```bash
DATABASE_URL=... python migrate_player_support_notes.py
```

On Heroku: `heroku run -a YOUR_APP -- python migrate_player_support_notes.py`

## Code

- Handlers: [`bot/handlers/support_notes.py`](../bot/handlers/support_notes.py)
- Service / formatting: [`bot/services/player_support_notes.py`](../bot/services/player_support_notes.py)
