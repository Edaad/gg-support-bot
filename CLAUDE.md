# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Python (backend)

```bash
# Install deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run processes individually (load .env automatically)
python run_api.py          # FastAPI on :8000, auto-reload
python run_bot.py          # Support bot (long-polling)
python run_cashier.py      # GGCashier staff wizard bot

# One-off DB migrations (run with DATABASE_URL set)
python migrate_support_group_chats.py
python migrate_support_group_chats_player_dm.py
python migrate_cashier_jobs.py
```

### Dashboard (React + Vite)

```bash
cd dashboard
npm install
npm run dev      # Dev server with /api proxy → localhost:8000
npm run build    # Production build → dashboard/dist
npm run lint     # ESLint
```

There are no automated tests in this repo.

## Architecture

Three independent long-running processes (see `Procfile`):

| Process | Entrypoint | Role |
|---------|------------|------|
| `web` | `api/app.py` (Uvicorn) | FastAPI REST API + serves `dashboard/dist` in production |
| `worker` | `run_bot.py` → `bot/main.py` | Support bot: player-facing commands (`/start`, `/deposit`, `/cashout`, `/gc`, `/list`, `/set`, etc.) |
| `cashier` | `run_cashier.py` → `cashier/main.py` | Staff wizard bot: guided cashout flow via DM, Zapier → Glide integration |

### Bot (`bot/`)

- `bot/handlers/` — one file per command/feature; registered in `bot/main.py`
- `bot/services/` — business logic and MTProto (Telethon) utilities
- `bot/services/mtproto_dm_gc_listener.py` — background Telethon client that watches for incoming player DMs and outgoing `/gc` staff commands to auto-create support megagroups
- The Telethon session **must not run in two workers simultaneously**; controlled by `GC_DM_GC_LISTENER_ENABLED`

### Cashier (`cashier/`)

- `cashier/handlers/wizard.py` — ConversationHandler driving the multi-step cashout wizard
- `cashier/services/jobs.py` — `CashierCashoutJob` state management in Postgres
- `cashier/services/zapier.py` — POST to Zapier webhook on cashout completion
- Staff trigger: `/cash <amount>` in a linked support group → bot → GGCashier DM wizard

### API (`api/`)

- `api/app.py` — FastAPI app factory; calls `Base.metadata.create_all` on startup (tables auto-created)
- `api/auth.py` — shared-password JWT auth (`DASHBOARD_PASSWORD`)
- `api/routes/` — one router per resource; all protected by JWT bearer token

### Database (`db/`)

- `db/models.py` — all SQLAlchemy 2.0 models (`Club`, `PaymentMethod`, `MethodVariant`, `PaymentSubOption`, `PaymentMethodTier`, `Group`, `PlayerDetails`, `CashierCashoutJob`, `SupportGroupChat`, etc.)
- `db/connection.py` — `get_db()` context manager and `get_db_dependency()` for FastAPI
- No migration framework; schema changes use standalone `migrate_*.py` scripts at the repo root

### Dashboard (`dashboard/`)

React 19 + Vite + Tailwind 4 SPA. JWT token stored in `localStorage`. In production, the FastAPI app mounts `dashboard/dist` and falls back to `index.html` for client-side routing.

### Configuration

- `config.py` — `ADMIN_USER_IDS` (global operator IDs), `CLUB_SHORTHAND_TO_NAME`, `GC_USERS_TO_INVITE`
- `club_gc_settings.py` — per-club MTProto config (`CLUB_GC_CONFIG`), session paths, staff invite lists, `GC_*` env knobs
- `.env` / `.env.example` — all runtime secrets; loaded via `python-dotenv` in each entrypoint

### Legacy

`main.py` at the repo root is an older monolithic bot script. It is **not used** — the live code is under `bot/` and `api/`.
