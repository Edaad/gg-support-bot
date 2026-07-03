# Club audit timezone config

## Goal

Per-club audit day boundaries so trade records, payments, early RB, and reconcile bucket events into the correct local day.

## Problem

Today everything uses one Eastern window. Reality:

| Slug | Trade record timezone |
|---|---|
| `round-table`, `creator-club` | EST (`America/New_York`, DST-aware) |
| `clubgto`, `aces-table` | Fixed `UTC-5` (no DST) |

`aces-table` and `round-table` share Postgres `club_id` but differ in policy → **key by gg-computer slug**, not `club_id` alone.

## Policy enum

```
AMERICA_NEW_YORK   # round-table, creator-club
FIXED_UTC_MINUS_5  # clubgto, aces-table
```

## Module

`api/club_audit_timezone.py`

| Function | Purpose |
|---|---|
| `audit_timezone_for_slug(slug)` | Lookup policy |
| `audit_day_bounds_utc(slug, date)` | Local calendar day → UTC range |
| `audit_day_window_utc(slug, date)` | Day + 1hr grace (export) |
| `parse_row_datetime(raw, date, policy)` | Trade record row → aware UTC |
| `occurred_at_in_audit_day(ts, slug, date)` | Filter helper |

Move timezone math out of `audit_export.py`; all audit features consume this module.

## Schema

`trade_record_uploads` — add:

- `club_slug` — from file metadata
- `audit_timezone_policy` — snapshot at ingest

Backfill existing rows from metadata where possible.

**Follow-up:** unique constraint may need `(club_slug, audit_date)` if RT/AT same-day uploads collide on shared `club_id`.

## Rollout

1. `club_audit_timezone.py` + unit tests (no behavior change)
2. Migration + persist `club_slug` / `audit_timezone_policy` on upload
3. `trade_record_parser` — club-aware row timestamps → UTC in `trade_record_lines`
4. `audit_export` — per-club window when filtering payments
5. Early RB ingest + reconcile (future) — same `audit_day_window_utc(slug, date)`

## Upload API response

Include `club_slug`, `audit_timezone_policy`, `audit_timezone_label` (e.g. `ET`, `UTC-5`).

## Optional validation

Parse Period suffix `(UTC-5:00)`; warn if it conflicts with slug policy (don't block v1).

## Tests

- EST vs EDT bounds for `AMERICA_NEW_YORK`
- Fixed `UTC-5` — no DST shift
- Same summer date: `round-table` vs `clubgto` windows differ by 1hr
- Row at 11:30pm local stays in that club's audit day
- Unknown slug → clear error

## Out of scope

- DB-editable timezone (admin UI)
- `club-elevate` until onboarded
- aon-side changes (filter early RB in gg-support-bot)

## Depends on

`api/club_slug.py`, `api/trade_record_parser.py`, `api/audit_export.py`, `api/routes/audit.py`
