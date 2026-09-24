# Production database snapshot — 2026-09-23

Read-only probe of the prod Postgres behind Heroku app `gg-support-bot-2025` (add-on `postgresql-curly-71375`), taken 2026-09-23 ~15:00 UTC. All queries ran with `default_transaction_read_only=on`; nothing was written.

For column-level meaning see [DATABASE.md](DATABASE.md). Raw probe output (columns, constraints, indexes, status counts) is in `scripts/output/prod_db_probe/`.

---

## Summary

- **Healthy shape.** All 86 SQLAlchemy models in [`db/models.py`](../db/models.py) exist in prod with **no missing or extra columns**. Only drift is nullability on timestamp defaults and one legacy column (see [Schema drift](#schema-drift-vs-dbmodelspy)).
- **Size:** 182 MB of the 1 GB `essential-0` cap (17%). Four append-only log tables are ~93 MB of that and grow fastest.
- **Connections:** 18 of 20 in use at probe time (17 idle, oldest ~21 h). Little headroom for `heroku run` / psql sessions.
- **Volume (last 30 days):** ~9,000 deposits and ~400 cashouts logged in `player_activities`; ~2,280 ingested P2P/crypto payments worth ~$346k.
- **Things worth a look:** stuck in-progress rows (cashier jobs, flow sessions, open Stripe sessions), `audit_reconcile_runs` failing most days, 21 `cooldown_bypasses` rows with `NULL chat_id`.

---

## Instance

| Property | Value |
|---|---|
| Plan | Heroku Postgres `essential-0` (1 GB, 20 connections, no fork/follow/rollback) |
| Version | PostgreSQL 17.9 (aarch64) |
| Created | 2025-08-18 |
| DB size | 182 MB (17.45% of cap) |
| Schemas | `public` (app), `_heroku` (managed) |
| Extensions | `pg_stat_statements`, `plpgsql` |
| Server TZ | UTC |
| Tables | 89 in `public` (86 modeled + 3 unmodeled) |
| Columns / FKs / uniques / checks / indexes | 1,142 / 97 / 42 / 11 / 310 |
| Views, triggers, functions, enums | None app-defined (only `pg_stat_statements` views) |

`pg_stat_user_tables` / `pg_stat_user_indexes` counters were **reset within the last week** (insert counters are lower than 7-day row counts), so scan/usage stats below cover only a few days.

---

## Clubs

| id | Club | Groups | `player_details` | Active deposit methods | Active cashout methods | Custom cmds |
|---:|---|---:|---:|---:|---:|---:|
| 2 | Round Table | 1,289 | 1,219 | 4 (+17 inactive) | 2 | 16 |
| 3 | Creator Club | 680 | 416 | 3 | 2 | 18 |
| 4 | ClubGTO | 1,492 | 1,181 | 7 | 3 | 20 |
| 34 | Aces Table | 0 | 0 | 0 | 0 | 0 |

`support_group_chats` by `club_key`: clubgto 1,324 · creator_club 1,156 · round_table 1,270 (all have `telegram_chat_id`). Only ClubGTO has a `club_linked_accounts` row (1).

### Feature flags

| Setting | Round Table | Creator Club | ClubGTO | Aces Table |
|---|---|---|---|---|
| Cashout cooldown | on, 24 h | on, 24 h | on, 24 h | off |
| Cashout hours (ET) | 08:00–23:00 | 08:00–23:00 | 08:00–23:00 | off |
| `cashout_soft_limit` | 1000 | 1000 | 1000 | — |
| `cashout_max_amount` | — | — | — | — |
| First-deposit bonus | 25% cap $100 | 25% cap $100 | 5%, no cap | off |
| `auto_chip_adding` / `auto_claim` / `auto_deposit_on_payment` | on | on | on | off |
| `enable_auto_cashout` | on | on | on | off |
| `enable_escalation_notification` | on | on | on | off |
| `enable_auto_early_rakeback` (max auto $) | on (500) | on (500) | on (500) | off |
| `enable_transfer` | on | on | off | off |
| `aces_option_min_deposits` | 0 | 3 | 0 | 0 |
| `enable_popup_keyboard` | off | off | off | off |
| `referral_enabled` | off | off | off | off |
| `allow_multi_cashout` | off | off | off | on |
| Simple deposit / cashout mode | off | off | off | off |

Aces Table (id 34) has no groups or methods of its own; its deposits flow through Round Table / Creator Club groups (union picker).

### Payment config (v2 `club_payment_*`)

41 methods, 28 tiers, 50 variants, 73 sub-options.

| Club | Deposit (active) | Cashout (active) |
|---|---|---|
| Round Table | Crypto (14 sub-opts), Venmo (2 tiers / 7 variants), Cashapp (1 / 4), Zelle pool-pay `zelle-union-zelle-09-16` ($500–2000) | Crypto (11), Venmo |
| Creator Club | Crypto (14), Cashapp (1 / 4), Venmo (2 / 6) | Crypto (11), Venmo |
| ClubGTO | Crypto (11), Venmo (2 / 4), Cashapp (2 / 5, max $2000), Zelle (2 / 10), PayPal, Apple Pay ($20–100), Debit Card ($20–100) | Crypto (12), Zelle, Venmo |

- A `Chips` cashout method exists (inactive) on all three clubs.
- Round Table's 17 inactive deposit methods are almost all dated pool-pay rotations (`*-union-*-MM-DD`, `*-lc-quincy-*`). Pool-pay methods have 0 tiers/variants. `club_payment_method_clubs` has 34 rows linking them across clubs.

---

## Activity and volume

Monthly `player_activities` (all clubs, incl. cancelled):

| Month | Deposits | Cashouts |
|---|---:|---:|
| 2026-04 | 2,699 | 271 |
| 2026-05 | 5,108 | 274 |
| 2026-06 | 10,864 | 433 |
| 2026-07 | 11,456 | 540 |
| 2026-08 | 11,151 | 619 |
| 2026-09 (to 23rd) | 6,548 | 242 |

Last 30 days, non-cancelled: ClubGTO 5,515 deposits / 246 cashouts · Round Table 2,236 / 106 · Creator Club 1,290 / 49.

`activity_type` values in prod are `deposit` 47,825 · `dep_cmd` 14,068 · `add_cmd` 5,328 · `cashout` 2,379 · `earlyrb` 644. [DATABASE.md](DATABASE.md) only documents `deposit` / `cashout`.

### Ingested payments (last 30 days)

| Provider | Rows (30 d) | USD (30 d) | Bound to a group | Auto-bound | All-time rows |
|---|---:|---:|---:|---:|---:|
| Venmo | 959 | $142,679 | 98.5% | 93.0% | 3,127 |
| Zelle | 718 | $88,587 | 99.3% | 94.2% | 3,523 |
| Crypto | 463 | $88,679 | 80.1% | 45.6% | 1,158 |
| Cash App | 108 | $18,875 | 98.1% | 73.1% | 138 |
| PayPal | 32 | $6,976 | 100% | 90.6% | 114 |

Stripe: 8,098 checkout sessions (8,021 `complete`, 77 `open`), 772 customers.

`webhook_ingest_requests` since 2026-09-01 (3,041): stripe 1,040 · venmo 743 · crypto 626 · zelle 499 · cashapp 103 · paypal 31. Outcomes: `success_created` 1,708, `stripe_processed` 892, `success_duplicate` 245, `stripe_ignored` 148, `processing_error` 22, `rejected` 17, `validation_error` 9, `auth_failed` 1.

`payment_auto_deposit_events` (10,839): `succeeded` 8,961 · `skipped` 1,705 · `failed` 173. Chip-add failures: `fail` 96, `health_failed` 59, `uncertain` 9, `error` 6.

### Other state distributions

| Table | Breakdown |
|---|---|
| `cashier_cashout_jobs` | completed 950 · cancelled 99 · in_progress 35 · initiated 31 |
| `bot_flow_sessions` | completed 10,054 · abandoned 2,972 · active 266 |
| `escalation_events` (top reasons) | player_idle 5,509 · deposit_player_message 1,639 · player_idle_followup 1,589 · player_dm_reached_out 404 · new_player_onboarded 325 · earlyrb_requested 296 |
| `escalation_decision_log` | skipped 20,911 · fired 10,027 (started 2026-09-02) |
| `issue_reports` | resolved 83 · open 14 |
| `bonus_drafts` | submitted 202 · pending 15 · cancelled 7 |
| `audit_reconcile_runs` | fail 145 · pass 12 · blocked 1 |
| `group_chat_daily_transcripts` | analysis complete 8,746 · pending 4 · failed 2 |
| `early_rakeback_claims` | chips_added 79 (all since 2026-09-17) |
| `migrated_group_recovery` | skipped 858 · complete 738 · failed 13 · pending 6 (idle since June) |
| `inactive_group_outreach_rows` | DM sent 380 · reonboarded 55 · failed 16 · not DM'd 1,427 (idle since June) |
| `mtproto_club_health` | 3 clubs `connected` |

---

## Findings worth attention

| # | Finding | Detail |
|---|---|---|
| 1 | **Connection headroom** | 18/20 connections at probe time; 17 idle from app pools, oldest ~21 h. A one-off `heroku run` plus dashboard spike could hit `too many connections`. |
| 2 | **Stuck cashier jobs** | 66 `cashier_cashout_jobs` in `initiated`/`in_progress` older than 24 h (created 2026-05-23 → 2026-09-21). |
| 3 | **Stale flow sessions** | 257 `bot_flow_sessions` still `active` after 24 h; likely never closed out to `abandoned`. |
| 4 | **Open Stripe sessions** | 77 `stripe_checkout_sessions` `open` for >24 h. Stripe expires sessions after 24 h, so status is probably not syncing `expired`. |
| 5 | **Auto-deposit stuck** | 3 `payment_auto_deposit_events` still `running`/`queued` after >1 h. |
| 6 | **Audit reconcile failing** | Last 7 days: 18 `fail`, 3 `pass` (latest run 2026-09-23 10:09 UTC failed). |
| 7 | **`cooldown_bypasses.chat_id` NULL** | 21 rows (pre-`migrate_cooldown_bypass_chat_id.py`). Model says `NOT NULL`, DB column is nullable. |
| 8 | **Unbound groups** | 850 `groups` rows have no `player_details` entry; 187 `player_details` rows have empty `chat_ids`. No `chat_ids` point at missing groups. |
| 9 | **`support_group_chats` without `groups` row** | 646 rows, all older than 30 days — likely pre-supergroup-migration chat ids or unlinked groups. |
| 10 | **Crypto binding lags** | Only 80% bound / 46% auto-bound in 30 days vs 93–99% for Venmo/Zelle. |
| 11 | **Log-table growth** | `deposit_funnel_events` (45 MB), `escalation_decision_log` (18 MB in 21 days), `group_chat_daily_transcripts` (18 MB), `group_chat_tickets` (12 MB). No code prunes these tables. Rough estimate: tens of MB/month, so the 1 GB cap is roughly a year out at current pace. |
| 12 | **Legacy tables still present** | `group_club` (233), `user_commands` (54), `payment_methods` (30), `payment_sub_options` (68), `payment_method_tiers` (15), `method_variants` (20), `glide_audit_lines` (0). No writes since May 2026 (none since Feb for `user_commands`). |

---

## Schema drift vs `db/models.py`

**Tables in DB but not in models:** `deploy_notify_state` (created by `migrate_deploy_notify_state.py`), `group_club`, `user_commands` (legacy `main.py`). No model tables are missing from prod.

**Columns:** none missing, none extra.

**Nullability:**

- `cooldown_bypasses.chat_id`: model `NOT NULL`, DB nullable (21 NULL rows; see finding 7).
- `payment_methods.use_group_checkout_link`, `payment_method_tiers.use_group_checkout_link`: model nullable, DB `NOT NULL` (legacy tables).
- 48 `created_at` / `updated_at` / `*_at` columns across 32 tables: model nullable, DB `NOT NULL DEFAULT now()` (set by migration scripts). Harmless.

**Constraints / indexes:** all named model indexes, uniques and FKs exist. The model's `ck_cpm_direction` on `club_payment_methods` exists under the Postgres auto-name `club_payment_methods_direction_check` (same definition).

**Index usage (few days of stats only):** zero scans on `ix_dfe_chat_created_at` (4.3 MB), `ix_dfe_club_created_at` (3.0 MB), `ix_esc_dec_reason_created_at` (2.4 MB), `ix_pbe_event_type_created` (1.5 MB), `ix_esc_dec_decision_created_at` (1.0 MB), `ix_pbe_notification_msg` (0.7 MB). These may serve dashboard/reporting queries that simply haven't run since the reset — re-check after a few weeks before dropping anything.

---

## Table inventory

"Last 30d" counts rows whose primary timestamp (`created_at`, or the closest equivalent) falls in the last 30 days. `—` means the table has no timestamp column.

### Clubs & config

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `custom_commands` | 54 | 104 kB | — | — | — |
| `bonus_types` | 6 | 72 kB | 2026-05-08 | 2026-09-19 22:51:22 | 1 |
| `payment_quick_links` | 6 | 32 kB | 2026-09-14 | 2026-09-14 02:33:14 | 6 |
| `clubs` | 4 | 144 kB | 2026-03-07 | 2026-05-08 22:11:50 | 0 |
| `mtproto_session_credentials` | 4 | 64 kB | 2026-06-20 | 2026-09-08 14:29:13 | 3 |
| `mtproto_club_health` | 3 | 64 kB | 2026-09-23 | 2026-09-23 15:02:17 | 3 |
| `club_linked_accounts` | 1 | 72 kB | 2026-03-21 | 2026-03-21 14:54:39 | 0 |
| `deploy_notify_state` | 1 | 56 kB | 2026-09-22 | 2026-09-22 17:42:11 | 1 |

### Payment config v2

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `group_deposit_destination_stickiness` | 94 | 80 kB | 2026-09-20 | 2026-09-23 14:59:34 | 94 |
| `club_payment_sub_options` | 73 | 96 kB | 2026-05-29 | 2026-09-15 21:16:49 | 6 |
| `club_payment_tier_variants` | 50 | 112 kB | 2026-05-30 | 2026-09-22 17:42:10 | 24 |
| `club_payment_methods` | 41 | 120 kB | 2026-05-29 | 2026-09-20 04:08:52 | 21 |
| `club_payment_method_clubs` | 34 | 72 kB | 2026-08-29 | 2026-09-20 04:08:52 | 34 |
| `club_payment_tiers` | 28 | 96 kB | 2026-05-29 | 2026-09-07 21:25:25 | 5 |
| `group_deposit_method_access` | 11 | 120 kB | 2026-07-12 | 2026-09-22 01:35:17 | 5 |
| `deposit_method_alerts` | 3 | 64 kB | 2026-09-20 | 2026-09-20 16:06:27 | 3 |

### Legacy (pre-v2 / pre-bot)

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `group_club` | 233 | 64 kB | — | — | — |
| `payment_sub_options` | 68 | 96 kB | — | — | — |
| `user_commands` | 54 | 80 kB | 2025-08-18 | 2026-02-23 18:57:03 | 0 |
| `payment_methods` | 30 | 88 kB | 2026-03-07 | 2026-05-21 02:55:51 | 0 |
| `method_variants` | 20 | 64 kB | — | — | — |
| `payment_method_tiers` | 15 | 64 kB | — | — | — |
| `glide_audit_lines` | 0 | 32 kB | — | — | 0 |

### Groups & players

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `player_activities` | 70,242 | 7064 kB | 2026-04-01 | 2026-09-23 15:01:00 | 16,541 |
| `support_group_chats` | 3,750 | 2160 kB | 2026-04-30 | 2026-09-23 14:42:46 | 255 |
| `groups` | 3,461 | 728 kB | 2026-03-07 | 2026-09-23 14:42:45 | 278 |
| `player_details` | 2,816 | 1232 kB | — | — | — |
| `group_photo_backfill_rows` | 348 | 184 kB | 2026-09-17 | 2026-09-23 14:47:25 | 348 |
| `broadcast_jobs` | 124 | 280 kB | 2026-03-30 | 2026-09-22 22:29:23 | 14 |
| `cooldown_bypasses` | 41 | 56 kB | 2026-04-02 | 2026-09-22 14:25:19 | 4 |
| `broadcast_group_members` | 6 | 72 kB | — | — | — |
| `referral_links` | 4 | 88 kB | 2026-09-15 | 2026-09-16 16:01:20 | 4 |
| `referral_attributions` | 2 | 104 kB | 2026-09-16 | 2026-09-16 17:28:27 | 2 |
| `broadcast_groups` | 2 | 56 kB | 2026-06-19 | 2026-06-22 12:04:04 | 0 |
| `player_support_issues` | 1 | 120 kB | 2026-06-21 | 2026-06-21 14:06:46 | 0 |
| `player_support_notes` | 1 | 96 kB | 2026-06-21 | 2026-06-21 14:06:46 | 0 |

### Payment ingest & binding

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `payment_binding_events` | 17,277 | 6336 kB | 2026-06-11 | 2026-09-23 15:02:45 | 5,417 |
| `payment_auto_deposit_events` | 10,839 | 3240 kB | 2026-07-07 | 2026-09-23 14:44:18 | 3,731 |
| `payment_notification_posts` | 8,626 | 2144 kB | 2026-08-24 | 2026-09-23 15:02:45 | 2,865 |
| `stripe_checkout_sessions` | 8,098 | 5496 kB | 2026-05-27 | 2026-09-23 11:48:06 | 1,481 |
| `zelle_payments` | 3,523 | 2424 kB | 2026-06-05 | 2026-09-23 11:52:44 | 718 |
| `venmo_payments` | 3,126 | 2000 kB | 2026-05-31 | 2026-09-23 15:02:44 | 959 |
| `webhook_ingest_requests` | 3,041 | 5472 kB | 2026-09-01 | 2026-09-23 15:02:46 | 3,043 |
| `payment_chip_matches` | 2,725 | 840 kB | 2026-08-10 | 2026-09-23 14:39:52 | 1,902 |
| `crypto_payments` | 1,158 | 1232 kB | 2026-06-07 | 2026-09-23 14:44:17 | 463 |
| `group_payment_method_bindings` | 943 | 328 kB | 2026-06-04 | 2026-09-23 09:34:03 | 228 |
| `stripe_customers` | 772 | 320 kB | 2026-05-27 | 2026-09-23 00:18:34 | 111 |
| `venmo_payer_bindings` | 552 | 232 kB | 2026-05-31 | 2026-09-23 06:54:18 | 130 |
| `payment_method_bind_attempts` | 527 | 384 kB | 2026-06-04 | 2026-09-23 14:47:24 | 293 |
| `zelle_payer_bindings` | 444 | 200 kB | 2026-06-05 | 2026-09-22 22:33:34 | 76 |
| `crypto_wallet_bindings` | 295 | 176 kB | 2026-06-08 | 2026-09-23 14:25:26 | 143 |
| `cashapp_payments` | 137 | 192 kB | 2026-06-08 | 2026-09-23 15:01:59 | 108 |
| `paypal_payments` | 114 | 192 kB | 2026-06-11 | 2026-09-23 09:32:06 | 32 |
| `cashapp_payer_bindings` | 53 | 96 kB | 2026-06-09 | 2026-09-23 03:31:13 | 37 |
| `manual_deposit_requests` | 45 | 168 kB | 2026-08-30 | 2026-09-23 07:00:10 | 45 |
| `paypal_payer_bindings` | 19 | 96 kB | 2026-06-14 | 2026-09-23 09:34:03 | 3 |

### Deposit flow & funnel

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `deposit_funnel_events` | 94,904 | 45 MB | 2026-07-09 | 2026-09-23 15:02:46 | 33,776 |
| `bot_flow_sessions` | 13,291 | 3584 kB | 2026-07-10 | 2026-09-23 15:00:51 | 4,866 |
| `deposit_incomplete_watches` | 7 | 80 kB | 2026-09-10 | 2026-09-21 23:51:59 | 6 |

### Escalation & support monitoring

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `escalation_decision_log` | 30,923 | 18 MB | 2026-09-02 | 2026-09-23 15:02:34 | 30,923 |
| `group_chat_tickets` | 15,493 | 12 MB | 2026-07-18 | 2026-09-23 07:31:13 | 6,328 |
| `escalation_events` | 11,324 | 6336 kB | 2026-08-14 | 2026-09-23 15:01:47 | 8,519 |
| `group_chat_daily_activity` | 8,902 | 1560 kB | 2026-07-16 | 2026-09-23 14:59:53 | 3,838 |
| `group_chat_daily_transcripts` | 8,752 | 18 MB | 2026-07-18 | 2026-09-23 07:00:34 | 3,820 |
| `escalation_episodes` | 6,325 | 5248 kB | 2026-08-14 | 2026-09-23 15:01:47 | 4,545 |
| `support_group_idle_episode_state` | 817 | 200 kB | 2026-08-12 | 2026-09-23 15:02:34 | 707 |
| `issue_reports` | 97 | 168 kB | 2026-06-23 | 2026-09-22 02:31:03 | 28 |
| `issue_report_attachments` | 78 | 7368 kB | 2026-06-28 | 2026-09-17 18:42:31 | 18 |
| `watched_group_escalation_state` | 9 | 72 kB | 2026-08-13 | 2026-09-23 07:16:37 | 8 |
| `issue_report_drafts` | 4 | 96 kB | 2026-06-23 | 2026-06-28 21:14:52 | 0 |

### Cashouts

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `cashier_cashout_jobs` | 1,115 | 352 kB | 2026-05-23 | 2026-09-23 13:17:29 | 293 |
| `staff_cashout_money_sends` | 85 | 56 kB | 2026-09-14 | 2026-09-23 13:20:33 | 85 |
| `staff_cashout_payments` | 71 | 48 kB | 2026-09-14 | 2026-09-23 13:17:29 | 71 |
| `staff_cashout_records` | 69 | 152 kB | 2026-09-14 | 2026-09-23 13:17:29 | 69 |
| `staff_cashout_notify_recipients` | 2 | 32 kB | 2026-09-13 | 2026-09-13 21:48:36 | 2 |
| `staff_cashout_slack_reminder_control` | 1 | 24 kB | 2026-09-16 | 2026-09-16 14:46:55 | 1 |

### Bonuses & rakeback

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `bonus_records` | 1,015 | 440 kB | 2026-05-08 | 2026-09-23 11:36:25 | 247 |
| `early_rakeback_lines` | 616 | 232 kB | 2026-07-03 | 2026-09-23 14:42:09 | 242 |
| `early_rakeback_snapshots` | 332 | 168 kB | 2026-07-03 | 2026-09-23 05:00:00 | 120 |
| `bonus_drafts` | 224 | 128 kB | 2026-07-04 | 2026-09-23 11:35:56 | 112 |
| `early_rakeback_claims` | 79 | 184 kB | 2026-09-17 | 2026-09-23 14:42:09 | 79 |

### Accounting & audit

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `trade_record_lines` | 12,861 | 2280 kB | 2026-06-21 | 2026-09-23 04:48:56 | 7,163 |
| `expenses` | 1,528 | 368 kB | 2026-08-31 | 2026-09-17 02:15:51 | 1,528 |
| `trade_record_uploads` | 212 | 152 kB | 2026-06-28 | 2026-09-23 10:09:16 | 132 |
| `audit_reconcile_runs` | 158 | 2128 kB | 2026-07-05 | 2026-09-23 10:09:27 | 99 |

### One-off ops / recovery

| Table | Rows | Size | First row | Last write (UTC) | Last 30d |
|---|---:|---:|---|---|---:|
| `inactive_group_outreach_rows` | 1,878 | 1120 kB | 2026-06-28 | 2026-06-28 19:02:35 | 0 |
| `migrated_group_recovery` | 1,615 | 1080 kB | 2026-06-11 | 2026-06-11 02:38:57 | 0 |
| `inactive_group_outreach_control` | 1 | 64 kB | 2026-06-28 | 2026-06-28 05:25:26 | 0 |
| `migration_recovery_control` | 1 | 64 kB | 2026-06-25 | 2026-06-25 06:44:29 | 0 |

---

## Reproducing

```bash
export PGURL=$(heroku config:get DATABASE_URL -a gg-support-bot-2025)
export PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=30000'
heroku pg:info -a gg-support-bot-2025
psql "$PGURL" -X   # run SELECTs only; mind the 20-connection cap
```
