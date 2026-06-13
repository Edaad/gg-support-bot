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

## Deploy maintenance notifications

Each Heroku deploy runs the **`release`** Procfile phase before new dynos go live. That phase DMs every account in [`config.py`](../config.py) `ADMIN_USER_IDS` with a brief disruption warning (~1 minute while dynos restart).

- **Trigger:** `git push heroku …` only (not `git push origin`).
- **Cooldown:** at most one notification per hour; rapid redeploys within the hour are skipped (logged as `deploy_notify: skipped (cooldown)`).
- **Admins must have `/start`'d the support bot** in DM to receive messages.

The release script auto-creates `deploy_notify_state` if missing (`CREATE TABLE IF NOT EXISTS`). You only need the manual migration when running outside release (e.g. local testing):

```bash
heroku run -a YOUR_APP -- python migrate_deploy_notify_state.py
```

**Config vars** (optional):

| Var | Default | Purpose |
|-----|---------|---------|
| `DEPLOY_NOTIFY_ENABLED` | `true` | Set `false` to disable release DMs |
| `DEPLOY_NOTIFY_COOLDOWN_SECONDS` | `3600` | Minimum seconds between notifications |

Script: [`scripts/notify_deploy_maintenance.py`](../scripts/notify_deploy_maintenance.py). Failures to send DMs never block deploy (release always exits 0).

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

`GC_MTPROTO_ENABLED=false` disables the dm_gc listener and MTProto contact save on the worker. Finer control: `GC_DM_GC_LISTENER_ENABLED=false` only (when `GC_MTPROTO_ENABLED` is still on). To pause **new** auto `/gc` megagroups while still re-adding bound players: `GC_DM_GC_NEW_GROUPS_ENABLED=false`. See [`docs/GC.md`](GC.md).

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

## Migrated supergroup recovery (direct-add cron on worker)

After a basic-group → supergroup upgrade drops members, the worker can drain a Postgres queue in batches (default 5 groups every 5 minutes) using the **same** dm_gc Telethon sessions (no extra `heroku run` needed).

**One-time setup** (run against production Postgres):

```bash
heroku run -a YOUR_APP -- python migrate_migrated_group_recovery.py
heroku run -a YOUR_APP -- python migrate_migration_recovery_control.py
heroku run -a YOUR_APP -- python migrate_migration_recovery_last_tick.py
heroku run -a YOUR_APP -- python scripts/seed_migrated_group_recovery.py
```

Seed needs a pre-migration dump under `backups/upgrade_supergroup_*/` in the slug, or pass `--affected-csv` from a local export. Re-seed is safe (`ON CONFLICT DO NOTHING`).

**Enable** on the worker dyno:

```bash
heroku config:set GC_MIGRATION_RECOVERY_ENABLED=true -a YOUR_APP
heroku config:set GC_MIGRATION_RECOVERY_SKIP_WELCOME=true -a YOUR_APP
heroku restart worker -a YOUR_APP
```

**Pause one club** while keeping recovery on for others (e.g. skip Round Table, run Creator Club + GTO):

```bash
heroku config:set GC_MIGRATION_RECOVERY_DISABLED_CLUBS=round_table -a YOUR_APP
# or: heroku config:set GC_MIGRATION_RECOVERY_ROUND_TABLE=false -a YOUR_APP
heroku restart worker -a YOUR_APP
```

Optional knobs: `GC_MIGRATION_RECOVERY_INTERVAL_SEC` (default `300`), `GC_MIGRATION_RECOVERY_BATCH_SIZE` (default `5`, **direct-add quota per active club per tick**), `GC_MIGRATION_RECOVERY_INVITE_DELAY_SEC` (default `2`, between direct-add attempts only), `GC_MIGRATION_RECOVERY_SKIP_WELCOME` (default `false`).

**Deploy / worker restart:** The worker stores `last_tick_at` in `migration_recovery_control` and schedules the next tick at `last_tick_at + GC_MIGRATION_RECOVERY_INTERVAL_SEC`. A deploy shortly after a tick does **not** trigger an immediate readd; if the interval has already elapsed, the first tick waits at least 60 seconds for the Telethon listener to connect.

With `GC_MIGRATION_RECOVERY_BATCH_SIZE=1`, each tick processes up to **one direct-add attempt per active club** (already-in rows are skipped without counting toward quota).

Requires `GC_DM_GC_LISTENER_ENABLED` (default on). Recovery direct-adds **only the mapped player** (not staff/bot accounts from `GC_USERS_*`); staff can join via invite link or manual add. Membership is checked with `GetParticipantRequest` before any invite. Each tick **drains already-in players without consuming batch quota** — it keeps claiming rows until `GC_MIGRATION_RECOVERY_BATCH_SIZE` groups that actually needed a direct add are processed per active club. Already-in-only rows finalize as `complete` without an admin DM. Each group is attempted **once**; no automatic retries. For one-off staff re-adds, use [`scripts/readd_migrated_group_members.py`](../scripts/readd_migrated_group_members.py) with `--invite-staff`.

**Slack progress (tier 1+2, every 6h):** When `SLACK_OPS_BOT_TOKEN` + `SLACK_OPS_CHANNEL_ID` (or webhook) are set, the worker **MTProto-scans all tier 1+2 rows before each post**, finalizes pending/processing rows where a player is already in the group, then reports: **in group**, queue left/done, **in group pending queue**, direct added, joined via link, still missing. Tune with `GC_MIGRATION_RECOVERY_SLACK_SUMMARY_INTERVAL_SEC` (default `21600`), `GC_MIGRATION_RECOVERY_SLACK_SUMMARY_ENABLED=false` to disable.

**Membership audit finalize (manual):** `python scripts/check_recovery_player_membership.py --apply` (or `--from-csv … --apply`) runs the same pending-row finalize plus optional player-ID binding when run locally.

Set `GC_MIGRATION_RECOVERY_SKIP_WELCOME=true` to suppress member-join preamble/TOS for chats in `migrated_group_recovery` during mass re-adds (independent of the recovery cron switch). Unset or set `false` to restore normal welcomes.

After each direct-add attempt (not already-in-only skips), the **GG Support bot** DMs that club's GC admin with a tappable GC title (supergroup `t.me/c/…` link when available), result status, and which accounts were added. **Rate limits (FloodWait)** halt recovery immediately, auto-disable the cron, and DM **all three club GC admins**. Other failures also DM the Round Table GC admin (`GC_ADMIN_USER_ROUND_TABLE`) for central ops visibility. Errors also post to **Slack** when `SLACK_OPS_BOT_TOKEN` + `SLACK_OPS_CHANNEL_ID` (or webhook) are set (see Slack ops below). Admins must have `/start`ed the bot.

**Queue visibility:** Send `/whosnext` in a private DM with the bot (admin accounts only) to see the global top-10 pending rows, plus auto-add / auto-disable status.

**Auto-disable:** When **any active club's** queue (pending + processing) hits zero, the worker stops the cron job, persists a flag in `migration_recovery_control`, and DMs the RT admin. Disabled clubs (e.g. `GC_MIGRATION_RECOVERY_DISABLED_CLUBS`) are ignored for exhaustion. This can still happen while other active clubs have pending rows — review and re-enable manually if you want to continue those clubs. Rate limits also trigger auto-disable (see above).

**Monitor** (SQL or local):

```bash
python scripts/seed_migrated_group_recovery.py --status
python scripts/seed_migrated_group_recovery.py --clear-auto-disable
```

```sql
SELECT club_key, group_title, priority_tier, readd_status, readd_attempted_at, last_error
FROM migrated_group_recovery
ORDER BY priority_tier, priority_rank;
```

Tail worker logs: `heroku logs -a YOUR_APP --dyno worker --tail | rg migration_recovery`

When recovery finishes (or auto-disables), unset the env var:

```bash
heroku config:unset GC_MIGRATION_RECOVERY_ENABLED -a YOUR_APP
```

To resume after auto-disable: clear the DB flag (`--clear-auto-disable`), set the env var again, and restart the worker.

## Notification bot (`notification` dyno)

Separate bot for payment notification bind replies (`TELEGRAM_NOTIFICATION_BOT_TOKEN`, `PAYMENT_NOTIFICATION_CHAT_ID`).

**Report a buggy notification:** In the payment notification chat, **reply** to the notification message with `/report`. The bot asks what was wrong; send a short description. On success it confirms in chat and posts the ticket to Slack (Engineer noti service — see Slack ops below). Send `/cancel` to abort mid-flow.

Restart after deploy: `heroku restart notification -a YOUR_APP`

## Slack ops (Engineer noti service / custom app)

Migration re-add **errors** (failed rows, rate limits, auto-disable) and notification **`/report`** tickets post to Slack when configured. Telegram DMs are unchanged; Slack is additive.

**Preferred: custom Slack app** ([`chat.postMessage`](https://docs.slack.dev/reference/methods/chat.postMessage)) — e.g. **Engineer noti service**:

1. At [api.slack.com/apps](https://api.slack.com/apps), open your app → **OAuth & Permissions**.
2. Bot Token Scopes: `chat:write` (and `chat:write.public` if the bot is not invited to the channel).
3. **Install to workspace** → copy **Bot User OAuth Token** (`xoxb-…`).
4. Invite the app to your ops channel (or rely on `chat:write.public` for public channels).
5. Channel ID: open the channel in Slack → channel name → **View channel details** → copy ID (`C…`), or right-click channel → Copy link (ID is in the URL).

```bash
heroku config:set SLACK_OPS_BOT_TOKEN=xoxb-... -a YOUR_APP
heroku config:set SLACK_OPS_CHANNEL_ID=C0123456789 -a YOUR_APP
heroku config:set SLACK_OPS_MENTION='<@UYOUR_SLACK_USER_ID>' -a YOUR_APP   # optional @JZ ping
```

**Optional fallback:** Incoming Webhook (`SLACK_OPS_WEBHOOK_URL`) if bot post fails or you have not set bot token yet.

Set app-wide (worker + notification dynos). Restart after deploy: `heroku restart worker notification -a YOUR_APP`

## Payment binding audit log

After deploying binding-event tracking, run once on production Postgres:

```bash
heroku run -a YOUR_APP -- python migrate_payment_binding_events.py
```

This creates `payment_binding_events`, an append-only log of binds, group-link updates, notification sends, and notification edit outcomes. Find payments whose Telegram message may be stale:

```bash
heroku run -a YOUR_APP -- python scripts/audit_payment_notification_sync.py --method zelle
```

## Payments page (Stripe tables)

After deploying the Payments dashboard feature, run migrations on production once:

```bash
heroku run -a YOUR_APP -- python migrate_stripe_deposit_tracking.py
# or, if tables exist but Payments returns 500:
heroku run -a YOUR_APP -- python migrate_stripe_checkout_session_lifecycle.py
```

`migrate_stripe_deposit_tracking.py` now includes lifecycle columns (`completed_at`, `updated_at`, `stripe_payment_intent_id`) when run on an existing install.

Also set `STRIPE_WEBHOOK_SECRET` on the **web** dyno and register `https://YOUR_APP.herokuapp.com/api/stripe/webhook` in Stripe (event: `checkout.session.completed`). See [`docs/STRIPE_DEPOSIT.md`](STRIPE_DEPOSIT.md).
