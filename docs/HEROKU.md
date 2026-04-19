# Heroku: API + React dashboard on one dyno

The repo has a **root `package.json`** so Heroku’s **Node.js buildpack** runs first. Its **`heroku-postbuild`** script installs dependencies in `dashboard/` (including devDependencies needed for Vite/TypeScript) and runs **`npm run build`**, producing **`dashboard/dist/`** during the slug compile. That folder is **not** committed to git.

The **Python buildpack** runs second (`pip install -r requirements.txt`). At runtime, **FastAPI** serves `/api/*` and, if `dashboard/dist/assets` and `dashboard/dist/index.html` exist, mounts the SPA (see [`api/app.py`](../api/app.py)).

## One-time setup

1. **Buildpack order** (Node must be before Python):

   ```bash
   heroku buildpacks:clear -a YOUR_APP
   heroku buildpacks:add --index 1 heroku/nodejs -a YOUR_APP
   heroku buildpacks:add --index 2 heroku/python -a YOUR_APP
   ```

   Or rely on [`app.json`](../app.json) when creating the app with tooling that reads it.

2. **Config vars**: set `DATABASE_URL`, `DASHBOARD_PASSWORD`, `TELEGRAM_BOT_TOKEN`, etc., as you already do.

3. **Deploy**: `git push heroku main` — compile runs `npm install` at the repo root, then **`heroku-postbuild`**, then Python.

## Files involved

| File | Role |
|------|------|
| [`package.json`](../package.json) (repo root) | Triggers Node buildpack; `heroku-postbuild` builds `dashboard/` |
| [`package-lock.json`](../package-lock.json) (repo root) | Lets `npm install` be reproducible on Heroku |
| [`dashboard/package-lock.json`](../dashboard/package-lock.json) | Used by `npm ci --prefix dashboard` in postbuild |
| [`app.json`](../app.json) | Documents recommended buildpack order |

## API-only boot

If the frontend build fails or `dist/` is missing, the API still starts; static files are only mounted when `dist` is complete (see `api/app.py`).
