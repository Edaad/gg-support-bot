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

   For **test payment** group confirmations (`test: true` on Zapier ingest), also set `TELEGRAM_TEST_BOT_TOKEN` on the **web** dyno (same value as local `run_test_bot.py`). Without it, only the production support bot is tried and test groups get `Chat not found`.

3. **Deploy**: `git push heroku main` — compile runs `npm install` at the repo root, then **`heroku-postbuild`**, then Python.

## Pipeline build failure notifications

Get Slack alerts when a Heroku **build** fails (Vite compile, `pip install`, release phase, etc.). This is separate from deploy-maintenance DMs (which only fire on successful deploys).

**Recommended: Heroku ChatOps → Slack.** Native pipeline notifications; failed builds show in deploy threads and in the routed channel when deploys come from the Dashboard or GitHub integration.

**Limitation:** ChatOps pipeline routing does **not** cover deploys started with `git push heroku …` from the CLI. If you still deploy that way, add the app webhook in step 4 below (or switch to GitHub-connected auto-deploy / `/h deploy`).

### 1. Pipeline + GitHub (one-time)

If the app is not in a pipeline yet (team-owned apps need `-t round-table`):

```bash
heroku pipelines:create gg-support-bot -a gg-support-bot-2025 -s production -t round-table
# optional second stage:
# heroku pipelines:add -a gg-support-bot-staging -s staging

heroku pipelines:connect gg-support-bot -r Edaad/gg-support-bot
```

**Current state:** pipeline **`gg-support-bot`** exists with **`gg-support-bot-2025`** in **production**, GitHub repo connected. Remaining step: Slack routing (below).

In **Dashboard → pipeline → Settings**, enable **Wait for GitHub checks** if you add CI later. Connect the repo if the CLI step above did not.

### 2. Install Heroku ChatOps in Slack

1. Install [Heroku ChatOps](https://slack.com/apps/A0BVC2A9Q-heroku) to the workspace.
2. In a **public** ops channel (same one as `SLACK_OPS_CHANNEL_ID` is fine), run:

   ```
   /h login
   /h route gg-support-bot to #your-ops-channel
   ```

3. In that channel, run `/h route`, pick **gg-support-bot**, and enable:
   - **App deployments** — deploy/promote progress; build failures appear in the thread
   - **GitHub Activity** (optional) — PR / commit status, including failed checks

ChatOps supports one routed channel per pipeline. Private Slack channels are not supported.

### 3. Verify

Trigger a failing build on a **non-production** app or review app (e.g. temporarily break `dashboard/package.json`), deploy from the Dashboard or `/h deploy gg-support-bot to staging`, and confirm the Slack thread shows the failed build step.

### 4. Optional: `git push heroku` build failures

Subscribe each app to build webhooks; filter for `data.status == "failed"` on `api:build` `update` events and forward to Slack (Zapier catch hook, Hookdeck, or a tiny relay — Heroku’s payload is not Slack’s incoming-webhook format):

```bash
heroku webhooks:add -a gg-support-bot-2025 \
  -i api:build \
  -l notify \
  -u https://YOUR_RELAY_URL/heroku-build
```

Inspect deliveries: `heroku webhooks:deliveries -a gg-support-bot-2025`.

## Deploy maintenance notifications

Each Heroku deploy runs the **`release`** Procfile phase before new dynos go live. That phase:

1. **Import smoke (blocking)** — verifies all Procfile entrypoints import cleanly (`web`, `worker`, `cashier`, `notification`). If any handler module is missing, release exits non-zero and the deploy is aborted (previous slug stays live).
2. **Deploy notify DMs (non-blocking)** — DMs every account in [`config.py`](../config.py) `ADMIN_USER_IDS` with a brief disruption warning (~1 minute while dynos restart).

- **Trigger:** any deploy path (GitHub auto-deploy, Dashboard, `git push heroku …`).
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
| `DEPLOY_NOTIFY_COOLDOWN_SECONDS` | `900` | Minimum seconds between notifications (15 minutes) |

Scripts: [`scripts/heroku_release.py`](../scripts/heroku_release.py) (orchestrator), [`scripts/pre_push_import_smoke.py`](../scripts/pre_push_import_smoke.py) (import smoke), [`scripts/notify_deploy_maintenance.py`](../scripts/notify_deploy_maintenance.py) (DMs). Import failures block deploy; failures to send DMs never block deploy.

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

`GC_MTPROTO_ENABLED=false` disables the dm_gc listener and MTProto contact save on the worker. Finer control: `GC_DM_GC_LISTENER_ENABLED=false` only (when `GC_MTPROTO_ENABLED` is still on). To pause **new** auto `/gc` groups while still re-adding bound players: `GC_DM_GC_NEW_GROUPS_ENABLED=false`. See [`docs/GC.md`](GC.md).

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
heroku run -a YOUR_APP -- python migrate_migration_recovery_slack_summary_last.py
heroku run -a YOUR_APP -- python migrate_migration_recovery_rate_limit_resume.py
heroku run -a YOUR_APP -- python migrate_migration_recovery_club_rate_limit.py
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

**Slack progress (tier 1+2, every 24h):** When `SLACK_OPS_BOT_TOKEN` + `SLACK_OPS_CHANNEL_ID` (or webhook) are set, the worker **MTProto-scans all tier 1+2 rows before each post**, finalizes pending/processing rows where a player is already in the group, then reports: **in group**, queue left/done, **in group pending queue**, direct added, joined via link, still missing. Tune with `GC_MIGRATION_RECOVERY_SLACK_SUMMARY_INTERVAL_SEC` (default `86400`), `GC_MIGRATION_RECOVERY_SLACK_SUMMARY_ENABLED=false` to disable.

**Deploy / worker restart:** The worker stores `last_slack_summary_at` in `migration_recovery_control` and schedules the next post at `last_slack_summary_at + GC_MIGRATION_RECOVERY_SLACK_SUMMARY_INTERVAL_SEC`. A deploy shortly after a post does **not** trigger an immediate resend; if the interval has already elapsed, the first post waits at least 60 seconds.

**Membership audit finalize (manual):** `python scripts/check_recovery_player_membership.py --apply` (or `--from-csv … --apply`) runs the same pending-row finalize plus optional player-ID binding when run locally.

Set `GC_MIGRATION_RECOVERY_SKIP_WELCOME=true` to suppress member-join preamble/TOS for chats in `migrated_group_recovery` during mass re-adds (independent of the recovery cron switch). Unset or set `false` to restore normal welcomes.

After each successful direct-add (not already-in-only skips), the **GG Support bot** DMs that club's GC admin with the player and GC name. **Rate limits (FloodWait)** pause only the affected club (or `elevate_catchup` for Elevate link-join); other clubs and Elevate keep running. Pauses post to **Slack ops** and auto-resume **1 hour after the FloodWait ends** per club (stored in `migration_recovery_control.club_rate_limit_resume_at`; survives worker restart/deploy). Tune extra cooldown with `GC_MIGRATION_RECOVERY_RATE_LIMIT_COOLDOWN_SEC` (default `3600`). Other failures (privacy blocked, entity resolution, tick errors, auto-disable) also post to **Slack** when `SLACK_OPS_BOT_TOKEN` + `SLACK_OPS_CHANNEL_ID` (or webhook) are set (see Slack ops below). Telegram DMs are success-only; admins must have `/start`ed the bot to receive those.

**Queue visibility:** Send `/whosnext` in a private DM with the bot (admin accounts only) to see the global top-10 pending rows, plus auto-add / auto-disable status.

**Auto-disable:** When **every active club's tier-scoped queue** (pending + processing) hits zero, the worker stops the cron job, persists a flag in `migration_recovery_control`, and posts to Slack ops. Tier scope: **Round Table = tier 1+2**; **Creator Club + ClubGTO = tier 3**. CC/GTO having no tier-3 pending while RT still has tier 1+2 work does **not** auto-disable (and vice versa). Disabled clubs (e.g. `GC_MIGRATION_RECOVERY_DISABLED_CLUBS`) are ignored. FloodWait pauses are per-club and do **not** auto-disable the cron (see above).

**Monitor** (SQL or local):

```bash
python scripts/seed_migrated_group_recovery.py --status
python scripts/seed_migrated_group_recovery.py --clear-auto-disable
```

**Round Table Elevate link-join recovery** (requires `GC_ELEVATE_CREATOR_ROUND_TABLE=true` and authorized `elevate_admin` session — see [`docs/GC.md`](GC.md)):

- **RT session** (tier 1+2): direct-add player + always export invite link.
- **Elevate session** (same tick): link-join oldest row that has a stored link but `readd_result.elevate_joined != true`.
- Row stays `processing` until both player re-add and Elevate join succeed.

One-group trial (dry-run first):

```bash
python scripts/run_migration_recovery_one.py --row-id ROW_ID --elevate-link-join --dry-run
python scripts/run_migration_recovery_one.py --row-id ROW_ID --elevate-link-join
```

On Heroku (pause worker or set `GC_MTPROTO_ENABLED=false` on worker to avoid session conflict):

```bash
heroku run -a YOUR_APP -- python scripts/run_migration_recovery_one.py --row-id ROW_ID --elevate-link-join --dry-run
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

## Inactive group outreach scan (one-shot, entity resolution only)

Scans all three clubs' support megagroups for **last non-support player message** activity (Telethon history, not deposit/bind signals), flags **90d / 180d** inactivity, resolves player entities for future DMs, and writes audit rows. **Does not send DMs.** Reuses the worker's live MTProto listener — no `heroku run` and no `GC_MTPROTO_ENABLED=false`.

**Migration:**

```bash
heroku run -a YOUR_APP -- python migrate_inactive_group_outreach.py
heroku run -a YOUR_APP -- python migrate_inactive_group_outreach_staging.py
heroku run -a YOUR_APP -- python migrate_inactive_group_outreach_dm.py
```

**Enable** on the worker dyno:

```bash
heroku config:set GC_INACTIVE_OUTREACH_SCAN_ENABLED=true -a YOUR_APP
heroku restart worker -a YOUR_APP
```

Optional knobs: `GC_INACTIVE_OUTREACH_BATCH_SIZE` (default `8`, groups per club per tick), `GC_INACTIVE_OUTREACH_INTERVAL_SEC` (default `120`), `GC_INACTIVE_OUTREACH_HISTORY_LIMIT` (default `200` messages per chat), `GC_INACTIVE_OUTREACH_FIRST_DELAY_SEC` (default `300`, delay after boot before first tick).

Requires `GC_DM_GC_LISTENER_ENABLED` (default on). The job seeds one row per tracking-title **megagroup** from `iter_dialogs`, dual-scans supergroup + legacy `old_chat_id`, merges timestamps, and stops when `inactive_group_outreach_control.scan_status=complete`. Unset env after completion (optional — DB gate prevents re-run).

**Inactivity definition:** last message from an **eligible player** only (same exclusions as contact save / player discovery). Staff or bot messages do **not** count. When a supergroup has multiple pre-migration basic groups, **all** known legacy chat ids are scanned and the newest player activity is kept.

**Monitor:**

```sql
SELECT scan_status, targets_total, rows_scanned,
       inactive_90d_count, inactive_180d_count, entity_resolvable_count,
       started_at, completed_at, last_error
FROM inactive_group_outreach_control WHERE id = 1;

SELECT club_key, COUNT(*) FROM inactive_group_outreach_rows
WHERE inactive_180d AND entity_resolvable
GROUP BY club_key;

SELECT club_key, activity_merged_from, COUNT(*)
FROM inactive_group_outreach_rows
WHERE duplicate_title
GROUP BY club_key, activity_merged_from;
```

Tail worker logs: `heroku logs -a YOUR_APP --dyno worker --tail | rg inactive_outreach`

**Reset for a future re-scan** (manual):

```sql
UPDATE inactive_group_outreach_control
SET scan_status = 'idle', started_at = NULL, completed_at = NULL,
    targets_total = 0, rows_scanned = 0, inactive_90d_count = 0,
    inactive_180d_count = 0, entity_resolvable_count = 0, last_error = NULL
WHERE id = 1;
TRUNCATE inactive_group_outreach_rows;
```

Then set `GC_INACTIVE_OUTREACH_SCAN_ENABLED=true` and restart the worker.

Local single-group debug: [`scripts/run_inactive_group_outreach_scan.py`](../scripts/run_inactive_group_outreach_scan.py) (`--chat-id` / `--row-id`, default dry-run). Uses a dedicated MTProto session — do not run against a club session held by the worker.

### Inactive outreach DM batch (phase 3)

After staging groups and resolving players (`entity_resolvable=true`), staff compose outreach copy via **`/sendinactive`** in a private DM with the support bot (preview + Confirm/Cancel). The worker sends DMs from the club MTProto account when:

```bash
heroku config:set GC_INACTIVE_OUTREACH_DM_ENABLED=true -a YOUR_APP
heroku restart worker -a YOUR_APP
```

Knobs: `GC_INACTIVE_OUTREACH_DM_BATCH_SIZE` (default `5`), `GC_INACTIVE_OUTREACH_DM_INTERVAL_SEC` (default `90`), `GC_INACTIVE_OUTREACH_DM_DELAY_SEC` (default `1.5` between sends), `GC_INACTIVE_OUTREACH_DM_FIRST_DELAY_SEC` (default `5`).

Monitor:

```sql
SELECT dm_batch_status, dm_sent_count, dm_failed_count,
       dm_campaign_started_at, dm_campaign_started_by_telegram_user_id
FROM inactive_group_outreach_control WHERE id = 1;

SELECT dm_status, COUNT(*) FROM inactive_group_outreach_rows
WHERE stage_status = 'staged' GROUP BY dm_status;
```

Test one row first: `/sendinactive row <outreach_row_id>` then confirm. Local dry-run: [`scripts/run_inactive_outreach_dm.py`](../scripts/run_inactive_outreach_dm.py).

Player replies to the club MTProto DM after `dm_status=sent` trigger re-onboard (erase old megagroup, fresh basic group with same title). See [`docs/GC.md`](GC.md) phase 3–4.

## Notification bot (`notification` dyno)

Separate bot for payment notification bind replies (`TELEGRAM_NOTIFICATION_BOT_TOKEN`, `PAYMENT_NOTIFICATION_CHAT_ID`).

**Report a buggy notification:** In the payment notification chat, **reply** to the notification message with `/report`. The bot asks what was wrong; send a short description. On success it confirms in chat and posts the ticket to Slack (Engineer noti service — see Slack ops below). Send `/cancel` to abort mid-flow.

Restart after deploy: `heroku restart notification -a YOUR_APP`

## Slack ops (Engineer noti service / custom app)

Migration re-add **errors** (failed rows, rate limits, auto-disable) and notification **`/report`** tickets post to Slack when configured. Telegram DMs are unchanged; Slack is additive.

**Preferred: custom Slack app** ([`chat.postMessage`](https://docs.slack.dev/reference/methods/chat.postMessage)) — e.g. **Engineer noti service**:

1. At [api.slack.com/apps](https://api.slack.com/apps), open your app → **OAuth & Permissions**.
2. Bot Token Scopes: `chat:write` (and `chat:write.public` if the bot is not invited to the channel). For **issue report screenshots**, also add `files:write`.
3. **Install to workspace** → copy **Bot User OAuth Token** (`xoxb-…`).
4. Invite the app to your ops channel (or rely on `chat:write.public` for public channels).
5. Channel ID: open the channel in Slack → channel name → **View channel details** → copy ID (`C…`), or right-click channel → Copy link (ID is in the URL).

```bash
heroku config:set SLACK_OPS_BOT_TOKEN=xoxb-... -a YOUR_APP
heroku config:set SLACK_OPS_CHANNEL_ID=C0123456789 -a YOUR_APP
heroku config:set SLACK_OPS_MENTION='<@UYOUR_SLACK_USER_ID>' -a YOUR_APP   # optional @JZ ping
```

**Optional fallback:** Incoming Webhook (`SLACK_OPS_WEBHOOK_URL`) if bot post fails or you have not set bot token yet.

**Issue reports (account managers):** Tickets are stored in Postgres (`issue_reports`). Create via `POST /api/issue-reports` (multipart) or `python scripts/create_issue_report.py`. Slack posts use a **dedicated** app/channel (`SLACK_ISSUE_REPORT_BOT_TOKEN` + `SLACK_ISSUE_REPORT_CHANNEL_ID`, or `SLACK_ISSUE_REPORT_WEBHOOK_URL`). Optional audience mentions via `ISSUE_REPORT_TAG_MENTIONS` JSON (e.g. `{"head_admin":"<!subteam^S_HEAD>","engineer":"<!subteam^S_ENG>"}`). Bot scopes: `chat:write`, `files:write`. Run `python migrate_issue_reports.py` once after deploy.

```bash
heroku config:set SLACK_ISSUE_REPORT_BOT_TOKEN=xoxb-... -a YOUR_APP
heroku config:set SLACK_ISSUE_REPORT_CHANNEL_ID=C... -a YOUR_APP
heroku config:set ISSUE_REPORT_TAG_MENTIONS='{"head_admin":"<!subteam^S_HEAD>","engineer":"<!subteam^S_ENG>"}' -a YOUR_APP
```

**Issue reports (AMs):** `/escalate` (group) and `/report` (DM) — see [`docs/ISSUE_REPORTS_BOT.md`](ISSUE_REPORTS_BOT.md). Run `python migrate_issue_reports_v2.py`, `python migrate_issue_report_drafts.py`, and `python migrate_issue_reports_resolve.py` once after deploy.

**Staff cashout records + bonus tables:** Editable GGCashier cashout history lives in `staff_cashout_records` / `staff_cashout_payments`; `/bonus` uses `bonus_records`. Run once after deploy:

```bash
heroku run -a YOUR_APP -- python migrate_staff_cashout_records.py
heroku run -a YOUR_APP -- python migrate_bonus_records.py
# optional: backfill completed cashier jobs into staff_cashout_records
heroku run -a YOUR_APP -- python scripts/backfill_staff_cashout_records.py
heroku run -a YOUR_APP -- python scripts/backfill_staff_cashout_records.py --apply
```

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

## Auto chip-adding on /add (ClubGG deposit bot)

Lets an admin `/add <amount>` in a linked group also send the chips to the ClubGG
deposit bot automatically. It is **off** unless (1) the per-club toggle "Auto chip
adding on /add" is enabled in the dashboard (Club → General) **and** (2) the worker
has the deposit-bot API configured. The customer-facing `/add` confirmation is
unchanged; failures alert staff out-of-band and degrade to today's manual behaviour.

Run the migration once after deploy (adds `clubs.auto_chip_adding_enabled` and
`groups.last_deposit_union` / `last_deposit_union_at`):

```bash
heroku run -a YOUR_APP -- python migrate_auto_chip_adding.py
```

Set on the **worker** dyno (see `.env.example` for the full list):

```bash
heroku config:set -a YOUR_APP \
  GG_DEPOSIT_API_BASE_URL=https://your-tunnel-url \
  GG_DEPOSIT_API_TOKEN=the-server.json-token \
  GG_DEPOSIT_API_DRY_RUN=true \
  GG_DEPOSIT_API_ALERT_CHAT_ID=-1001234567890
```

**Rollout / single-group test (do this before real sends):**

1. Run the migration; enable the toggle for **one** club only.
2. Keep `GG_DEPOSIT_API_DRY_RUN=true`. Make a `/deposit` in one Round Table group,
   pick RT or AT, then `/add <amount>` — confirm the staff alert shows a `dry_run`
   result for the correct ClubGG club (Round Table vs Aces Table) and player id.
3. For CC/GTO, confirm club resolves with no union needed.
4. Only after the dry-run looks correct, set `GG_DEPOSIT_API_DRY_RUN=false` and
   restart the worker. The ClubGG desktop app must be open/foregrounded and the
   deposit server + tunnel running.

`Round Table` deposits route to ClubGG **Round Table** (`522594`) or **Aces Table**
(`983183`) from the customer's last `/deposit` RT/AT choice when one exists (stale
choices are kept). If the customer never ran `/deposit` / never picked RT or AT,
`/add` defaults to **Round Table** (RT). `ClubGTO` (`790203`)
and `Creator Club` (`846162`) route by club name. Restart after config: `heroku restart worker -a YOUR_APP`.
