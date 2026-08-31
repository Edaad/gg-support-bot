# Chip transfers between unions (`/transfer`)

Lets a player in a club with two unions move chips between them. The bot claims
from the source union and adds to the destination union, both through the ClubGG
deposit API. Off by default per club.

## Turning it on

Dashboard → Club Detail → **Chip transfers between unions**
(`clubs.enable_transfer`). It also needs, in the same club:

- the deposit API configured (`GG_DEPOSIT_API_BASE_URL` + `GG_DEPOSIT_API_TOKEN`)
- **Auto claim on /cash** (`clubs.auto_claim_enabled`) — the claim leg uses it

Clubs with a single union (ClubGTO and friends) ignore the flag: `/transfer` is a
silent no-op there.

## The four moves

The club's union pair drives everything, so only these exist:

- Round Table: `RT -> AT` and `AT -> RT`
- Creator Club: `CC -> AT` and `AT -> CC`

The player picks the **destination**; the source is the other union in the pair. A
Creator Club player can never target the Round Table union, and vice versa.

## Flow

1. Player (or an admin on their behalf) sends `/transfer` in the club group.
2. The bot checks the flag, that the club has two unions, that the deposit API and
   auto-claim are on, and that the group title carries a readable GG player id.
   Any of those failing escalates **before** anything moves.
3. The bot asks which club to transfer to, with the two union buttons.
4. The bot asks for the amount.
5. `Claiming N chips from <source>...this will just take a minute!`
6. On a successful claim: `Adding N chips to <destination>...`
7. On a successful add: `Successfully transferred N chips from <source> to <destination>!`

Anything off-script posts `An agent will be with you shortly.` once, Slack-escalates
with reason `transfer_escalation`, and ends the conversation. A bare `/transfer` is
never treated as off-script, and staff/admin messages mid-flow are ignored rather
than escalated.

## Who can run it

Chips always come from the **group's** player — the GG player id is read from the
group title, never from whoever is typing. So it does not matter who answers the
prompts.

An admin can therefore run a transfer end to end in a player's chat: send
`/transfer`, pick the destination, type the amount. The player can also answer an
admin-started flow, which is the normal case when the admin is just kicking it off.
Other admins in the group are ignored, so a stray number from someone else in the
conversation cannot fire a transfer.

This needs **Allow admin commands** (`clubs.allow_admin_commands`) on for the club,
the same gate `/deposit` and `/cashout` use. With it off, `/transfer` is a silent
no-op for global admins. Club staff are not global admins, so they run the flow as
an ordinary participant and can always complete it.

## Why the claim runs first

There is no balance API. The bot cannot check what a player holds, and a claim
does not report how much it actually took. Claiming first makes the common mistake
safe: asking to move more chips than you have fails the claim leg, so nothing has
been added and nothing is lost — just an escalation.

## Failure matrix

Two independent RPA operations with no transaction around them:

- **Claim fails** (`fail`, `error`, …) — the add never runs. Nothing moved.
- **Claim `uncertain`** — the add never runs, because the claim may have gone
  through. Escalates as "may have claimed, verify on ClubGG, do not re-claim".
- **Claim ok, add fails or `uncertain`** — the dangerous one. Chips have left the
  source and are not in the destination. Escalates naming the amount, both clubs,
  and that the player is owed that amount in the destination. **Never**
  auto-reversed: the add may silently have landed, so adding back could leave the
  player with chips in both clubs.
- **Both ok** — success message, and the Aces ack is recorded if the destination
  was Aces Table (see below).

## Interactions

- **No `player_activities` row is written.** A transfer is neither a deposit nor a
  cashout, so it does not reset the 24h cashout cooldown, does not affect the
  low-deposit cashout hold, and does not count toward
  `clubs.aces_option_min_deposits`.
- **A successful transfer into Aces records `groups.aces_join_ack_at`** for Creator
  Club groups. Without it a player could hold chips in Aces that automated
  `/cashout` would never offer to pay out. The group title is not changed.
- **Mutually exclusive with `/deposit` and `/cashout`** in the same group; starting
  one while another is open tells the player to `/cancel` first.
- Conversation times out after 10 minutes, same as the cashout flow.

## Accepted risk: transfers into Aces for non-members

A transfer into Aces Table is allowed even when the group has no Aces history (no
join ack, no `AT` token in the title). A player who is not an Aces member will
**always** fail the add leg, which means the chips are claimed from the source and
owed in the destination until an agent fixes it. This was a deliberate product
choice over refusing up front. The Slack alert carries everything needed to
resolve it by hand: amount, both clubs, and the do-not-re-claim instruction.

If this generates real support load, the cheap fix already exists —
`has_aces_deposit_history(chat_id)` in
[`bot/services/round_table_unions.py`](../bot/services/round_table_unions.py)
would gate it before any chips move.

## Migration

```bash
heroku run -a YOUR_APP -- python migrate_enable_transfer.py
```

> Run this before or with the deploy. The model declares `clubs.enable_transfer`,
> so until the column exists every query against `clubs` fails.

## Single-group test

Per the repo's Telegram testing rule, prove one group before enabling more:

1. Enable the flag on **one** club (start with Round Table).
2. In one group, `/transfer` a small amount `RT -> AT`. Confirm all three messages,
   and verify both legs on ClubGG.
3. Transfer it back `AT -> RT`.
4. Send a random message mid-flow and confirm exactly one
   "An agent will be with you shortly." plus one Slack alert.
5. Only then enable Creator Club, and check that a `CC AT` group can move both ways.

## Code map

- [`bot/handlers/transfer.py`](../bot/handlers/transfer.py) — conversation, copy, escalation
- [`bot/services/chip_transfer.py`](../bot/services/chip_transfer.py) — plan resolution and the two legs
- [`bot/services/clubgg_deposit_api.py`](../bot/services/clubgg_deposit_api.py) — `run_auto_claim`, `run_auto_chip_add` (the latter takes `union_shorthand` to pin the destination)
- Tests: [`tests/test_chip_transfer.py`](../tests/test_chip_transfer.py), [`tests/test_transfer_handler.py`](../tests/test_transfer_handler.py)
