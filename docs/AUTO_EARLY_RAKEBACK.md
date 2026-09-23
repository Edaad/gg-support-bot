# Automated early feeback (`/earlyrb`)

Turns `/earlyrb` from "an agent will get back to you" into an end-to-end flow across
three systems:

| System | Role |
|---|---|
| gg-support bot (this repo) | Drives the conversation, gates on club settings, persists the claim |
| ClubGG RPA bot | `POST /rake` reads this week's fee, `POST /deposit` adds the chips |
| Elevate (aon-beta) | `bot/quote` says what is owed, `bot/record` writes it to the ledger |

All player-facing copy says **fee** and **feeback**, never rake or rakeback.

## The flow

1. Player sends `/earlyrb` in their support group.
2. Re-check throttle (there is no daily limit — see below).
3. If the club has two unions, *"Which club would you like to claim your early feeback in?"*
   Single-union clubs skip straight through.
4. *"Finding your total fee for this week..."* — `POST /rake` on the RPA bot, Monday to
   today in US Eastern. The same call reports `has_upline`, which decides eligibility
   before any of the numbers matter.
5. *"Calculating your remaining feeback for this week..."* — `GET bot/quote` on Elevate with
   the **filtered** rake and PnL.
6. *"You've already claimed $Y.YY of feeback this week."* (omitted when Elevate
   reports zero or no total), then *"Your total remaining feeback for this week is:
   $X.XX — Would you like to claim?"* with **Claim** / **Cancel**, plus *"Early
   feeback counts as a deposit and will reset the cashout timer"*. If nothing is
   left, the player sees the claimed total in *"You've already claimed all of your
   feeback for this week ($Y.YY)."*
7. On Claim: *"Adding early feeback..."*, then `POST bot/record` on Elevate, then
   `POST /deposit` on the RPA bot (`label` **Early Feeback**, so the VM
   notification reads *Early Feeback success*). That status message is replaced with
   *"$X.XX feeback added to your account!"*

Fee lookup and chip-add run as ConversationHandler **non-blocking** callbacks
(`block=False`). The bot's update loop stays free for `/deposit`, `/cashout`, and
method copy in other groups while the screen robot works. `/cancel` during that
wait aborts the prompt; once chips are actually being added, cancel is refused.

## Where it stops instead

| Situation | Player sees | Staff see |
|---|---|---|
| RPA job not `success`, or no filtered fee | An Admin will be with you shortly. | `earlyrb_auto_failed` |
| `has_upline` is true | Unfortunately, members under an agency … contact your agent … | — |
| `has_upline` missing from the response | An Admin will be with you shortly. | `earlyrb_auto_failed` |
| Overall and filtered figures identical and non-zero | An Admin will be with you shortly. | `earlyrb_auto_failed` with the date range |
| Filtered fee is zero or negative | You don't have any fee recorded for this week yet. | — |
| Elevate says `nothing_remaining` | You've already claimed all of your feeback for this week. | — |
| Elevate says below minimum | Sorry! Your remaining feeback ($X.XX) … below the $Y.YY minimum … | — |
| Remaining over the club's max | An Admin will be with you shortly. | `earlyrb_auto_over_max` (+ head admins) |
| Quote or record errors | An Admin will be with you shortly. | `earlyrb_auto_failed` |
| Recorded, chips failed, Elevate rolled back | An Admin will be with you shortly. | `earlyrb_auto_failed` |
| Recorded, chips failed, rollback also failed | An Admin will be with you shortly. | `earlyrb_chips_not_added` (+ head admins) |

## Decisions worth knowing

**Record first, then add chips, then roll back if chips fail.** Elevate exposes
`DELETE bot/record` for bot-written rows, identified by the same `idempotency_key`.
If the ClubGG chip-add fails the bot deletes the record before telling the player
anything succeeded, so a later `/earlyrb` can quote freshly. If the delete itself
fails, Slack `earlyrb_chips_not_added` (+ head admins) fires — that row has
everything needed to add the chips by hand, and a re-record would double-pay.

**Members under an agency are turned away, not escalated.** `/rake` reports `has_upline`
(true for anyone below an agent or super agent, always false for a super agent itself);
their feeback is their agent's to pay, so the bot says so and stops. The check runs ahead
of the fee and date-filter checks, so an agency member never gets "no fee this week" or an
admin escalation when the real answer is "ineligible". The `role` field is read into the
log line only — `has_upline` alone decides.

An RPA build that does not return `has_upline` at all leaves it `None`, which escalates
rather than defaulting either way: reading a missing field as "no upline" would pay agency
members, and reading it as "has upline" would tell honest players something false. Requires
the `no_upline_tag` template calibrated on each VM's profile.

**The minimum comes only from Elevate.** `quote.minimumThreshold` / `belowMinimum` is the
single gate, so there is no minimum field on the dashboard. Set each club's
`earlyRakebackThreshold` on Elevate. Player copy always shows cents (`$19.50`), even
when Elevate's `displayDecimalPlaces` is 0 — otherwise a $19.50 remainder rounds to
`$20` and looks equal to a `$20` minimum.

**There is no daily limit.** A player may claim as often as they have feeback remaining.
Elevate is the real guard: once the week's feeback is taken, the next quote comes back
`nothing_remaining`, so repeat claims are bounded by the ledger rather than by a cooldown of
ours. What *is* limited is the fee lookup — each one writes an `earlyrb_check` activity and
the next within `EARLYRB_RECHECK_THROTTLE_SECONDS` (default 300) is refused, because every
lookup drives the single-threaded screen robot. That throttle spaces out checks; it never
costs a player a claim.

**A stuck claim counts as a deposit.** Once chips land — or chips fail *and* the
Elevate rollback also fails, so the money is still on the ledger — the bot writes a
`deposit` activity and invalidates pending one-time bypasses, which resets the 24h
cashout timer. A rolled-back chip-add does not. The Claim prompt still says a
successful claim will reset the timer.

**Dry run stops before Elevate.** When `GG_DEPOSIT_API_DRY_RUN=true` the Claim press skips
the record entirely and posts a dry-run Slack summary. Otherwise a rollout test would write
real ledger entries while no chips move.

**Warnings never block.** Elevate's `warnings` and `memberType` are logged and carried to
Slack but anything it calls eligible is auto-claimed.

## The equality heuristic has a known false positive

If overall and filtered rake *and* PnL all match and at least one is non-zero, the date
filter probably never applied and the flow escalates. A player whose entire ClubGG history is
this week legitimately matches, and on a Monday the window is a single day. The RPA already
OCR-verifies the date pill and returns `fail` rather than committing a wrong range, so this is
belt-and-braces; the Slack alert carries `data.range` so an admin can clear it in seconds.

## Club identifiers

One chain, no new config: `resolve_clubgg_club_name(club_name, union)` gives the ClubGG club,
and `api/club_slug.CLUB_LABEL_TO_SLUG` maps that label to the Elevate slug.

| Club + union | ClubGG | Elevate |
|---|---|---|
| Round Table + RT | Round Table | `round-table` |
| Round Table + AT | Aces Table | `aces-table` |
| Creator Club + CC | Creator Club | `creator-club` |
| Creator Club + AT | Aces Table | `aces-table` |
| ClubGTO | ClubGTO | `clubgto` |

`gg_player_id` comes from the group title, same as `/add` and `/cash`.

## Configuration

Dashboard → club → General:

- **Automated early feeback** — the toggle. Off means `/earlyrb` behaves exactly as before.
- **Maximum auto-claim early feeback ($)** — blank for no limit.
- **Escalate after auto early feeback** — verification ping. On, a successful auto-claim
  also Slack-alerts staff with the amount so they can confirm it. Off (default) = chips
  land with no success ping. Failures still always alert. A player question in the group
  during or after the flow still Slack-escalates as `player_idle`; this toggle does not
  change that.

Environment (worker dyno):

| Var | Default | Notes |
|---|---|---|
| `GG_DEPOSIT_API_BASE_URL` / `_TOKEN` | — | Already required for auto chip-adding |
| `GG_DEPOSIT_API_RAKE_POLL_TIMEOUT_SEC` | 420 | `/rake` queues behind live deposits |
| `AON_BETA_BASE_URL` | — | Includes the `/api` prefix |
| `AON_BETA_INTERNAL_API_KEY` | — | Must equal Elevate's `INTERNAL_API_KEY` |
| `AON_BETA_TIMEOUT_SEC` | 30 | |
| `EARLYRB_RECHECK_THROTTLE_SECONDS` | 300 | Min gap between fee lookups per group |

If the toggle is on but either API is unconfigured, the bot logs it and quietly falls back to
the canned request.

## Manual setup before switching a club on

1. **Calibrate the Members / rake-check card on every ClubGG VM** — templates
   `members_option`, `members_anchor`, `member_search_box`, `member_detail_anchor`,
   `custom_btn`, `date_picker_anchor`, `date_prev_month`/`date_next_month`,
   `date_confirm_btn`, `no_upline_tag`; regions `member_search_field`,
   `member_result_row_id`, `member_detail_id`, `member_role`, `member_rake`, `member_pnl`,
   `date_month_header`, `date_range_start`/`date_range_end`, `calendar_grid`. `/rake` fails
   without these and they deliberately do **not** appear in `/health`'s `missing_regions`,
   so nothing warns you. `no_upline_tag` decides eligibility: miscalibrated, it reads every
   member as having an upline and turns them all away.
2. Deploy a ClubGG deposit-bot build exposing `POST /rake` and prove it with a curl for one
   known player. Check the response carries `data.has_upline` — an older build without it
   sends every request to an admin.
3. Deploy `bot/quote` + `bot/record` on aon-beta; confirm its `INTERNAL_API_KEY` matches
   `AON_BETA_INTERNAL_API_KEY`.
4. Confirm Elevate has club slugs `round-table`, `aces-table`, `creator-club`, `clubgto`.
5. Set each club's `earlyRakebackThreshold` on Elevate (the only minimum), and confirm
   `earlyRakebackResetMode` lines up with a Monday-EST week — ClubGG's `/rake` window is fixed
   at Monday to today, US Eastern.
6. Set `AON_BETA_BASE_URL` and `AON_BETA_INTERNAL_API_KEY` on the **worker** dyno; today they
   only need to exist for the web dyno's audit sync.
7. Run the migrations:
   ```bash
alembic upgrade head   # runs automatically in the Heroku release phase
   ```
8. Enable the toggle and set the max for **one** club, keep `GG_DEPOSIT_API_DRY_RUN=true`, and
   test one known group end to end before turning dry run off. Tick **Escalate after auto
   early feeback** for the verification period so staff get a Slack ping after each
   successful auto-add.

## Reconciliation

Every Claim press writes an `early_rakeback_claims` row: the fee figures and date range, the
quoted and recorded amounts, the Elevate record/entry ids, the RPA request id, and a status of
`quoted`, `recorded`, `chips_added`, `rolled_back`, `chips_failed` or `escalated`. When Slack
reports `earlyrb_chips_not_added`, rollback failed and that row has everything needed to add
the chips by hand — a retry of the same `idempotency_key` would replay the original write
instead of paying again.

```sql
SELECT created_at, gg_player_id, clubgg_club, recorded_amount, status, detail
FROM early_rakeback_claims
WHERE status IN ('recorded', 'chips_failed', 'rolled_back', 'escalated')
ORDER BY created_at DESC;
```
