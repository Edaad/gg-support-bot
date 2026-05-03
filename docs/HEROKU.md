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
