# GG Support Bot

Telegram bot and web dashboard for club operators: configurable welcome and list content, deposit and cashout flows with payment methods (including tiers and sub-options), custom commands, group linking, broadcasts, and optional cashout cooldowns. Data lives in PostgreSQL via SQLAlchemy.

## Architecture

| Component | Role |
|-----------|------|
| **Bot** (`bot/main.py`, `run_bot.py`) | Long-polling Telegram worker: `/start`, `/deposit`, `/cashout`, `/list`, `/set`, linked groups, cooldown bypass, etc. |
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

## Configuration

- **`config.py` — `ADMIN_USER_IDS`**  
  Global Telegram user IDs with extra operator access (e.g. across clubs). Club-specific admins are stored in the database.

- **Linked backup accounts**  
  Documented in [`docs/LINKED_ACCOUNTS.md`](docs/LINKED_ACCOUNTS.md).

## Project layout

```
api/           # FastAPI app, auth, routes
bot/           # Telegram handlers and services
dashboard/     # React dashboard (Vite)
db/            # SQLAlchemy models, connection, migrations
config.py      # Admin user IDs
run_api.py     # Local API entrypoint
run_bot.py     # Bot worker entrypoint
```

## Legacy note

`main.py` at the repo root is an older monolithic bot script and is **not** used by `Procfile` or `run_bot.py`. The maintained application is under `bot/` and `api/`.
