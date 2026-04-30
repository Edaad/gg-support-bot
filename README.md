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
```

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

Authorized operators (configured per-club Telegram user IDs) can run **`/gc` only in private chat with the bot** to spin up a new **support megagroup** using **MTProto/Telethon** with that club’s user session—not the Bot API:

1. **`TG_API_ID` / `TG_API_HASH`** from [my.telegram.org](https://my.telegram.org/apps) (never commit values; see [`.env.example`](.env.example)).
2. **Club tuning** lives in [`club_gc_settings.py`](club_gc_settings.py). Each club entry includes `command_admin_user_id`, which selects who may run `/gc`. Defaults align with historic support-bot IDs (`6713100304`, `8318575265`, `7516419496`).
3. **`GC_*` env vars** override defaults (session filenames, invites list, titles, phones, templates, photo paths). Comma-separated usernames → `GC_USERS_*`. Optional `GC_BOT_ACCOUNT=@Bot` invites the dashboard bot when Telegram hides the bot `@username`.
4. **Sessions**: Telethon persists `*.session` under **`sessions/`** (gitignored). On Heroku/ephemeral disks you must persist or re-upload sessions after redeploy or MTProto breaks.
5. **Login UX**: Missing session → `/gc` starts an interactive SMS (and optional Cloud Password) flow inside the DM. **SMS codes and 2FA secrets are never written to logs or the database.**
6. **Migrate DB**: [`migrate_support_group_chats.py`](migrate_support_group_chats.py) ensures the audit table matches production expectations.
7. **Testing checklist**: Obtain real club MTProto consent, preload `sessions/<club>.session` or login via `/gc`, ensure photo assets exist locally or disable via env blank path, configure `GC_USERS_*`, then `/gc` and confirm Telegram has the invites + DB row insertion.

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
