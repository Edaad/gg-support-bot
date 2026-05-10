# GG Support Bot

Telegram bot and web dashboard for club operators: configurable welcome and list content, deposit and cashout flows with payment methods (including tiers and sub-options), custom commands, group linking, broadcasts, and optional cashout cooldowns. Data lives in PostgreSQL via SQLAlchemy.

## Architecture

| Component | Role |
|-----------|------|
| **Bot** (`bot/main.py`, `run_bot.py`) | Long-polling Telegram worker: `/start`, `/deposit`, `/cashout`, `/gc` (Telethon-backed megagroups), `/list`, `/set`, linked groups, cooldown bypass, etc. |
| **API** (`api/`, `run_api.py`) | FastAPI backend for the dashboard; creates tables on startup (`Base.metadata.create_all`). |
| **Dashboard** (`dashboard/`) | React + Vite + Tailwind SPA; in production the API serves `dashboard/dist`. |

Heroku-style split: `web` runs Uvicorn, `worker` runs the bot (see `Procfile`).

## Requirements

- **Python** 3.11+ (see `.python-version`)
- **PostgreSQL** (recommended; `DATABASE_URL` uses SQLAlchemy + `psycopg2`)
- **Node.js** 20+ (for dashboard dev/build)

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL URL (e.g. `postgresql://user:pass@host:5432/dbname`). `postgres://` is normalized to `postgresql://`. |
| `TELEGRAM_BOT_TOKEN` | Yes (bot) | Token from [@BotFather](https://t.me/BotFather). |
| `DASHBOARD_PASSWORD` | No | Shared password for dashboard login; JWT signing secret. Defaults to `changeme` — **set in production**. |
| `TG_API_ID` | Yes for `/gc` | Integer app id from [my.telegram.org](https://my.telegram.org/apps) — used only for MTProto (Telethon) sessions that create megagroups. |
| `TG_API_HASH` | Yes for `/gc` | Api hash paired with `TG_API_ID`. Do not expose publicly. |
| `GC_DM_GC_LISTENER_ENABLED` | No | **Default on.** Telethon listens for **outgoing** `/gc` in **private DMs** from each club’s MTProto admin to a player. Set to `false` / `0` / `no` / `off` to disable. Use **one** bot worker only (same MTProto session must not connect twice). |
| `GC_DM_GC_VERBOSE_LOGS` | No | **Default off.** Emit extra **INFO** for dm_gc (`dm_capture`, `/gc_match`, bootstrap). Set `true` / `1` / `yes` to enable. Warnings/errors always log. |
| `GC_CONTACT_SAVE_ENABLED` | No | **Default on.** On **`/info`** in a linked group only, the club MTProto user may **add/update one contact** (chat title as name) when exactly one non-admin, non-`GC_USERS_*` human remains. See [`docs/GC.md`](docs/GC.md). Disable with `false` / `0` / `no` / `off`. |

## Local setup

### 1. Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Database

Create a PostgreSQL database and set `DATABASE_URL`. Tables are created automatically when the API or bot starts.

If you are migrating from an older `user_commands` / `group_club` layout, see `db/migrate.py` (run with `DATABASE_URL` set).

To add the `support_group_chats` audit table for `/gc` (idempotent), run:

```bash
DATABASE_URL=postgresql://... python migrate_support_group_chats.py
DATABASE_URL=postgresql://... python migrate_support_group_chats_player_dm.py
```

The second script adds player-scoped columns and indexes for **outgoing `/gc` in admin→player DMs** (see below).

### 3. API + dashboard (development)

Terminal 1 — API (reload on port 8000):

```bash
python run_api.py
```

Terminal 2 — Dashboard dev server (proxies `/api` → `http://localhost:8000`):

```bash
cd dashboard
npm install
npm run dev
```

Open the Vite URL (e.g. `http://localhost:5173`), log in with `DASHBOARD_PASSWORD`, and manage clubs.

### 4. Bot

```bash
export TELEGRAM_BOT_TOKEN="your-token"
export DATABASE_URL="postgresql://..."
python run_bot.py
```

## Production build

Build the SPA so the API can serve it from `dashboard/dist`:

```bash
cd dashboard
npm install
npm run build
```

Start the API (serves static assets and `/api/*`):

```bash
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

Run the bot in a separate process:

```bash
python run_bot.py
```

### Heroku

Deploy runs **`npm run build` for `dashboard/` automatically** via the root `package.json` `heroku-postbuild` script (Node buildpack before Python). Buildpack order and details: **[`docs/HEROKU.md`](docs/HEROKU.md)**.

## Configuration

- **`config.py` — `ADMIN_USER_IDS`**  
  Global Telegram user IDs with extra operator access (e.g. across clubs). Club-specific admins are stored in the database.

- **Linked backup accounts**  
  Documented in [`docs/LINKED_ACCOUNTS.md`](docs/LINKED_ACCOUNTS.md).

### MTProto `/gc` (support megagroups)

There are two triggers:

1. **Bot command** — Authorized operators (per-club `command_admin_user_id` in [`club_gc_settings.py`](club_gc_settings.py)) send **`/gc` in private chat with the bot** to create a **generic** support megagroup (no target player row).
2. **Admin DM** — By default each club’s **MTProto user** session listens for the admin sending **`/gc` in a private DM with a player**. The message is deleted, one megagroup per `(club, player)` is created or reused, the player gets a DM, and metadata is stored on `support_group_chats`. Set **`GC_DM_GC_LISTENER_ENABLED=false`** to turn this off. **Do not** run two workers with the same Telethon session.

Shared setup:

1. **`TG_API_ID` / `TG_API_HASH`** from [my.telegram.org](https://my.telegram.org/apps) (never commit values; see [`.env.example`](.env.example)).
2. **Club tuning** in [`club_gc_settings.py`](club_gc_settings.py): session paths, staff invites, titles, photos, `GC_*` overrides, optional `GC_BOT_ACCOUNT=@Bot`.
3. **Sessions**: Telethon uses `*.session` under **`sessions/`** (gitignored) and/or Postgres `mtproto_session_credentials` when `GC_MTPROTO_DB_SESSIONS` is on.
4. **Login**: Use **Dashboard → Telegram login** or optional [`scripts/mtproto_login_cli.py`](scripts/mtproto_login_cli.py). **SMS codes and 2FA secrets are never written to logs or the database.**
5. **Migrate DB**: run [`migrate_support_group_chats.py`](migrate_support_group_chats.py) and [`migrate_support_group_chats_player_dm.py`](migrate_support_group_chats_player_dm.py) on existing databases.
6. **Testing (DM flow)**: Run one worker (listener is on by default), authorize all three MTProto sessions, open a DM from a club admin phone to a player, send exactly `/gc`, confirm the command disappears, the group exists, and the DB row has `player_telegram_user_id` set.

Full operator guide: [`docs/GC.md`](docs/GC.md).

## Project layout

```
api/           # FastAPI app, auth, routes
bot/           # Telegram handlers and services
dashboard/     # React dashboard (Vite)
db/            # SQLAlchemy models, connection, migrations
config.py            # ADMIN_USER_IDS and shorthand map
club_gc_settings.py # /gc clubs + GC_* env knobs
run_api.py     # Local API entrypoint
run_bot.py     # Bot worker entrypoint
```

## Legacy note

`main.py` at the repo root is an older monolithic bot script and is **not** used by `Procfile` or `run_bot.py`. The maintained application is under `bot/` and `api/`.
