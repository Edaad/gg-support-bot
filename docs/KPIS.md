# Club KPIs

The dashboard **KPIs** tab (`/kpis`) shows five daily/weekly numbers (tickets,
response time, automation, deposits, new players) for one period beside the
period before it, a trend, and a club-by-club table. It is computed live from
existing tables. There is no snapshot table and no migration.

- Code: `api/kpis.py` (counting rules), `api/routes/kpis.py` (`GET /api/kpis`),
  `dashboard/src/pages/Kpis.tsx`, MCP tool `get_kpis`.
- Access: admins (all clubs) and the GTO login (locked to ClubGTO). Account
  managers get 403.
- Tests: `python -m unittest tests.test_kpis tests.test_mcp_kpis -v`.

## Periods

All dates are America/New_York days. SQL filters on UTC bounds, and each row is
bucketed into its ET day in Python, so DST weeks keep the right edges.

| Granularity | Period | Previous | Trend |
|---|---|---|---|
| `day` | one day (default: yesterday) | the day before | 14 days |
| `week` | Mon–Sun (default: last complete week) | the week before | 8 weeks |
| `month` | calendar month (default: last complete month) | the month before | 6 months |
| `custom` | `from`–`to`, up to 366 days | same length, right before | daily (≤31 days), weekly (≤26 weeks) or monthly |

While a week or month is still running, the previous period is cut to the same
number of elapsed days (`previous_truncated`), so this week so far is compared
with the same days of last week.

```
GET /api/kpis?granularity=week&date=2026-10-05&club_id=4
GET /api/kpis?granularity=custom&from=2026-10-01&to=2026-10-13
```

`date` can be any day inside the period. Rates are fractions (0.45 = 45%).

## Counting rules

**Excluded chats** are left out of every KPI:
- test and staging titles (`… / TEST`, `@jz034`, `1111-1111` / `2222-2222` /
  `8888-8888`, `rt/`)
- titles containing a staff handle from `GC_USERS_TO_INVITE` (e.g.
  @RoundTableSupport2)
- `support_group_chats.is_internal`

| KPI | Rule |
|---|---|
| **Deposits** | Bound rows only (`telegram_chat_id` set, `is_test` false), each counted once. Rails and dates: Venmo / Zelle / Cash App / PayPal by `created_at`; crypto by ISO `paid_at`, else `created_at`; Stripe `status='complete'` by `completed_at`, else `created_at`; Pool Pay = `manual_deposit_requests` on a `union_method` method with `trade_record_checked`, by `created_at`. Club = the bound group's `groups.club_id`, else the row's `club_id`. Per-club numbers add up to the total. |
| **New players** | `groups.added_at` in the period. *Deposited* = the group has at least one deposit (same rule) at any time up to now, so recent periods catch up over the following days. *First deposits* = groups whose earliest-ever deposit falls in the period. |
| **Response time** | `group_chat_tickets` by `activity_date`. First response = `compute_ticket_frt(events)`: player's first message to the first human admin reply. The median and *under 2 min* (share of first replies within 2 minutes, ≤ 120 s) use human-answered tickets only. Tickets with no human reply are reported separately. |
| **Tickets by category** | Same rows, counted by `category`. |
| **Automation success** | `clubgg_rpa_events` by `started_at` for `payment_auto_deposit`, `early_rakeback`, `auto_cashout` and `transfer`. Success = completed ÷ (completed + failed + stuck). *Stuck* means still `started` after 15 minutes. `stale` rows are shown but not counted. `staff_*` sources are left out. Club = the row's group, else the row's `club_id`. On the dashboard a row is Healthy at 90%+ (auto-cashout 80%+); below that it's flagged Watch. |

Aces Table has no club row. Its traffic counts under Round Table (or under
Creator Club for CC's own Aces deposits).

## Freshness

Tickets are written nightly (around 03:00 ET) for the previous day.
`freshness.tickets_complete_through` is the latest `activity_date` present, and
`period.tickets_pending` marks a period that runs past it. The UI flags those
tiles as "filling in". Everything else is live. While the period includes today,
the page refreshes itself every 5 minutes.
