# Automated cashouts

Turns the player-facing `/cashout` and `/withdraw` flow into a fully automated
cashout for clubs that opt in. The bot claims the chips, collects a validated
payout handle from the player, and records the cashout to the hub using the exact
same GGCashier/Zapier completion path that staff use today. Anything that falls
outside the strict procedure posts a single "an agent will be with you shortly"
message and Slack-escalates, then the bot bows out so a human can finish.

## Gating

Automated cashout runs only when **all** of these are true:

- The club dashboard toggle **Automated cashouts** (`clubs.enable_auto_cashout`)
  is on (Club Detail → General).
- The ClubGG deposit/claim API is configured on the worker
  (`GG_DEPOSIT_API_BASE_URL` + `GG_DEPOSIT_API_TOKEN`).
- **Auto claim on /cash** (`clubs.auto_claim_enabled`) is on — the claim engine.

Mode precedence for `/cashout`: **cashout simple-mode wins → else automated
cashout → else the normal method + canned-instructions flow.** Non-auto clubs and
every other flow are unchanged.

## Flow

1. Player runs `/cashout` (or `/withdraw`) and enters an amount. Existing
   eligibility (24-hour cooldown + business hours) still gates entry.
2. The bot verifies at least one **active** cashout method with an automated handle
   format is available for the amount (below-min / no-method ends the flow with the
   usual message).
3. For a club with unions the bot asks which one, mirroring `/deposit`: Round Table
   offers RT / AT, Creator Club offers CC / AT. Single-union clubs (GTO, …) skip
   this step. Creator Club is **not** join-gated here (that gate is deposit-only);
   picking Aces Table with no chips there just fails the claim and escalates.
4. The bot shows the eligible methods (single choice). Crypto asks for the asset
   sub-option.
5. The bot asks for the player's payout handle and validates it for the method. The
   handle is **extracted from anywhere in the message**, so `Venmo: @test` or a
   message with several handles is fine (the first valid one wins):

   | Method  | Accepted |
   |---------|----------|
   | Venmo   | `@username` or a `venmo.com` link (bare or with `https://`) |
   | Cash App| `$cashtag` or a `cash.app` link |
   | Zelle   | US phone number or email |
   | Crypto  | wallet-address-looking token (case preserved) |
   | PayPal  | email or a `paypal.me` link |

   A plain email is **not** accepted as a Venmo handle — that escalates rather than
   recording `@gmail.com` as the payout destination.
6. **Only once everything above succeeds**, the bot posts "Claiming chips…" and runs
   the ClubGG auto-claim for the chosen club/union. On failure or an UNCERTAIN
   result it escalates and stops. Claiming last means the player is never waiting on
   ClubGG before being asked for their method and handle.
7. On a clean claim, the cashout is recorded via `complete_cashout_job` → Zapier
   (Glide RT Hub) + `staff_cashout_records` audit + the group "$X owed" pin + the
   cooldown activity. The player gets a short confirmation.

Because the bot only allows cashouts 24h after the last deposit/cashout and the
trade record is enforced by the bot, both attestations are auto-marked on the job.

## Escalation (agent will be with you shortly)

The bot posts "An agent will be with you shortly." exactly once and Slack-escalates
(reason `auto_cashout_escalation`) whenever:

- The chip claim fails or is UNCERTAIN.
- The player's reply is not a valid handle for the chosen method.
- Any off-script message arrives from the target customer (free text, media, etc.).
- Recording the cashout to the hub fails.

Only the **target customer's** messages are evaluated. Global admins and club staff
can message freely to take over quietly — their messages are ignored, not escalated.

If chips were already claimed when the flow escalates, the Slack alert says
`Chips already claimed: $X — cashout NOT recorded. Finish the payout manually; DO
NOT re-claim.` The claim is never auto-reversed.

## Rollout

See the deploy + single-group test steps in
[`docs/HEROKU.md`](HEROKU.md#automated-cashouts-on-cashout-fully-automated). Enable
the toggle for one club, keep `GG_DEPOSIT_API_DRY_RUN=true`, and verify one group
end-to-end before enabling widely.
