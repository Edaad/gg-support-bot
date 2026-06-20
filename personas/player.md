# Persona: Player

**Use:** UX (Telegram bot + support groups) · Product/requirements (features, flows, copy)

---

## Snapshot

A club member who moves money in and out of private poker clubs (ClubGG) and gets help through a **dedicated Telegram support group**. They rarely think in terms of "the bot" or "the system" — they think in terms of **their account manager** and **whether their money landed**.

**Typical setup:** One club, one support group, one AM.

**Round Table exception:** Some players are in **both Round Table (TMT union)** and **Aces Table (Massiv union)** — separate clubs, separate groups, separate AMs. Same pattern applies elsewhere (e.g. Creator / TMT, clubgto / Massiv).

---

## Goals (priority order)

| # | Goal | What they're trying to do |
|---|------|---------------------------|
| 1 | **Deposit** | Add chips via `/deposit` or by telling their AM amount + method |
| 2 | **Cash out** | Withdraw via `/cashout` or AM-assisted request |
| 3 | **General questions** | Rake, games, rules, account issues |
| 4 | **Escalation** | Stuck deposit/cashout — money moved but nothing updated |

---

## How they enter the system

1. **First contact:** DMs the club admin account (e.g. `@RoundTableSupport2`).
2. **Happy path:** MTProto enabled → auto-added to personal support group.
3. **Broken path:** Auto-add fails → AM manually creates group (`/gc`).
4. **After onboarding:** AM is their **only** human point of contact.

### Onboarding is complete when all three are true

- [ ] Personal support group exists and they've joined
- [ ] First successful deposit
- [ ] AM verified identity (ClubGG username, payment details)

Until all three, they're still "new" — need more hand-holding and AM involvement.

---

## How they work day to day

**Channel:** Personal support group only (not bot DM, not random club chats).

**Two parallel ways to get things done:**

| Channel | Used for |
|---------|----------|
| **AM (human)** | Questions, reassurance, walking through flows, fixing problems |
| **Bot commands** | Self-serve deposit/cashout/referral |

**Commands they use (group only):**

- `/deposit` — amount + payment method
- `/cashout` — amount + payout method
- `/referral` — how to refer someone
- `/list` — all available commands
- `/cancel` — abort an in-progress command

---

## Two modes (same persona)

### New player (low comfort)

- Doesn't know commands; AM walks them through `/deposit` step by step
- Messages AM instead of finishing bot prompts
- Needs explicit confirmation at every step
- Most hurt by **bot confusion** and **slow AM replies**

### Veteran (power user)

- Runs `/deposit` and `/cashout` with minimal AM involvement
- Uses `/list` as reference; knows `/cancel`
- Only pings AM when something breaks or limits block them
- Most hurt by **stuck money** and **silence while waiting**

Design and copy should work for **both in the same group thread** — not two separate products.

---

## What "success" feels like

1. **AM confirms in the group** — "done", "sent", screenshot → immediate relief
2. **Real proof** — chips show in ClubGG or cash hits their account → they move on

Bot completion messages help veterans; new players still want a human nod.

---

## Top frustrations

1. **Stuck money** — sent payment / requested cashout, nothing updated
2. **Bot confusion** — wrong step, don't know what to type, `/cancel` unclear
3. **Slow AM** — waiting in group with no update while anxious about money

---

## UX / product implications

| Area | Player need |
|------|-------------|
| **Onboarding** | Reliable auto-add; clear fallback when MTProto fails; AM can `/gc` without player confusion |
| **Commands** | Obvious next step in `/deposit`/`/cashout`; `/cancel` always visible mid-flow; `/list` discoverable |
| **Group scope** | Commands only work in *their* group — error copy should say "use your support group", not generic bot errors |
| **Multi-club (RT+AT)** | Player must know **which group** to use per club/union; avoid cross-club command confusion |
| **Failure states** | Money issues need status + ETA language; don't leave them in a silent bot state |
| **AM handoff** | Bot and AM share one thread — bot failures should make it easy to @ AM without restarting |

---

## Anti-patterns (design against these)

- Assuming players DM the bot directly for `/deposit`
- Long bot flows with no human-visible progress in the group
- Success = bot message only, no AM acknowledgment for new players
- One-size copy for veterans and first-timers
- Treating RT and AT as one support relationship when they're separate unions/clubs
