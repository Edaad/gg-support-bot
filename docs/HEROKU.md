# Heroku: API + React dashboard on one dyno

**Two ways** to get **`dashboard/dist/`** into the slug (it is **not** committed to git):

1. **Recommended (faster, clearer logs):** attach **`heroku/nodejs` before `heroku/python`**. The root **`heroku-postbuild`** runs `npm ci` + `npm run build` in `dashboard/`.
2. **Python-only apps:** if you only have **`heroku/python`**, [`bin/post_compile`](../bin/post_compile) runs at the end of the Python compile step, downloads a **Node** binary to `/tmp`, and builds `dashboard/` there. It **skips** if `dist/` already exists (e.g. Node buildpack ran first).

The **Python buildpack** still runs `pip install -r requirements.txt`. At runtime, **FastAPI** serves `/api/*` and, if `dashboard/dist/assets` and `dashboard/dist/index.html` exist, mounts the SPA (see [`api/app.py`](../api/app.py)).

## One-time setup

1. **Buildpack order** (optional but recommended — Node before Python):

   ```bash
   heroku buildpacks:clear -a YOUR_APP
   heroku buildpacks:add --index 1 heroku/nodejs -a YOUR_APP
   heroku buildpacks:add --index 2 heroku/python -a YOUR_APP
   ```

   If you skip this and keep **only** `heroku/python`, deploy anyway: **`bin/post_compile`** will build the dashboard. You can override the Node version used there with **`NODE_VERSION`** (default `20.18.1`).

   [`app.json`](../app.json) documents the recommended order for apps created from it; **existing** apps do not pick up `app.json` automatically — use the CLI or **Dashboard → Settings → Buildpacks**.

2. **Config vars**: set `DATABASE_URL`, `DASHBOARD_PASSWORD`, `TELEGRAM_BOT_TOKEN`, etc., as you already do.

3. **Deploy**: `git push heroku main` — compile runs `npm install` at the repo root, then **`heroku-postbuild`**, then Python.

## MTProto scripts vs worker

Telegram allows **one live connection per MTProto session**. If the **worker** dyno is connected (dm_gc listener) and you also run an MTProto script against production, Telegram may invalidate the session (`AuthKeyDuplicatedError`) and exports fail with `ConnectionError`.

**Before any invite-link backfill** (local or `heroku run`):

```bash
heroku config:set GC_MTPROTO_ENABLED=false -a YOUR_APP
heroku restart worker -a YOUR_APP
```

When finished, turn MTProto back on:

```bash
heroku config:unset GC_MTPROTO_ENABLED -a YOUR_APP   # or set true
heroku restart worker -a YOUR_APP
```

`GC_MTPROTO_ENABLED=false` disables the dm_gc listener and MTProto contact save on the worker. Finer control: `GC_DM_GC_LISTENER_ENABLED=false` only (when `GC_MTPROTO_ENABLED` is still on). See [`docs/GC.md`](GC.md).

### Invite link backfill (`support_group_chats.invite_link`)

Caches invite links for legacy groups so payment notifications can hyperlink group titles. Script: [`scripts/backfill_support_group_invite_links.py`](../scripts/backfill_support_group_invite_links.py). Re-run is safe (skips chats that already have a link).

**Local (recommended for large runs)** — set `DATABASE_URL`, `TG_API_ID`, and `TG_API_HASH` in `.env` or the shell:

```bash
python scripts/backfill_support_group_invite_links.py --apply --club-key clubgto --export-delay 5
```

**On Heroku** — use a one-off dyno. Always pass **`-a YOUR_APP`**. Put **`--`** before the script so Heroku does not treat `--apply`, `--club-key`, etc. as CLI flags:

```bash
heroku run -a YOUR_APP -- python scripts/backfill_support_group_invite_links.py --apply --club-key clubgto --export-delay 5
```

Quoted form (same effect):

```bash
heroku run -a YOUR_APP 'python scripts/backfill_support_group_invite_links.py --apply --club-key clubgto --export-delay 5'
```

**Do not close the terminal** while `heroku run` is attached — Heroku kills the one-off dyno when the SSH session drops (`Process exited with status 128`). For long runs, use **detached** mode and tail logs separately:

```bash
heroku run:detached -a YOUR_APP -- python scripts/backfill_support_group_invite_links.py --apply --club-key clubgto --export-delay 5
# prints: run.1234
heroku logs -a YOUR_APP --dyno run.1234 --tail
```

Filter recent one-off output: `heroku logs -a YOUR_APP --tail | rg 'run\\.|backfill|Exporting invite|Upsert'`

`club-key` choices: `round_table`, `creator_club`, `clubgto`. Omit `--club-key` to process all three. Dry-run: drop `--apply`.

## Files involved

| File | Role |
|------|------|
| [`package.json`](../package.json) (repo root) | Triggers Node buildpack; `heroku-postbuild` builds `dashboard/` |
| [`package-lock.json`](../package-lock.json) (repo root) | Lets `npm install` be reproducible on Heroku |
| [`dashboard/package-lock.json`](../dashboard/package-lock.json) | Used by `npm ci --prefix dashboard` in postbuild |
| [`bin/post_compile`](../bin/post_compile) | Python buildpack hook: builds `dashboard/` when Node buildpack did not run |
| [`app.json`](../app.json) | Documents recommended buildpack order |

## API-only boot

If the frontend build fails or `dist/` is missing, the API still starts; static files are only mounted when `dist` is complete (see `api/app.py`).

## Payments page (Stripe tables)

After deploying the Payments dashboard feature, run migrations on production once:

```bash
heroku run -a YOUR_APP -- python migrate_stripe_deposit_tracking.py
# or, if tables exist but Payments returns 500:
heroku run -a YOUR_APP -- python migrate_stripe_checkout_session_lifecycle.py
```

`migrate_stripe_deposit_tracking.py` now includes lifecycle columns (`completed_at`, `updated_at`, `stripe_payment_intent_id`) when run on an existing install.

Also set `STRIPE_WEBHOOK_SECRET` on the **web** dyno and register `https://YOUR_APP.herokuapp.com/api/stripe/webhook` in Stripe (event: `checkout.session.completed`). See [`docs/STRIPE_DEPOSIT.md`](STRIPE_DEPOSIT.md).
