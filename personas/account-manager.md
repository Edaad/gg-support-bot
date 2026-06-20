# Persona: Account Manager (AM)

**Use:** UX (Telegram bot + support groups) · Product/requirements (features, flows, escalation)

---

## Snapshot

A shift-based support operator who handles player money, onboarding, and questions through **shared club Telegram accounts**. Players see one consistent human account per club; they do not know AMs rotate on shifts.

**Not their job:** Debug bot/system failures — they escalate via Slack to head admin, engineer, or rakeback admin.

**Typical setup:** No assigned player "book." Whoever is on shift monitors club admin accounts and responds to any inbound player DM or support-group thread across **all clubs** (Round Table, Aces Table, Creator Club, ClubGTO).

---

## Goals (priority order)

| # | Goal | What they're trying to do |
|---|------|---------------------------|
| 1 | **Deposits & cashouts** | Verify payment in RT Hub, load chips, send payout, confirm in-group |
| 2 | **Onboarding** | Intake via admin-account DM → support group → verify identity → first deposit |
| 3 | **Player questions** | Rake, games, rules, account issues |
| 4 | **Escalation** | Slack → head admin / engineer / rakeback admin when blocked |

**Explicitly out of scope:** Fixing bot/MTProto/payment-binding bugs (engineer territory). Running `/gc` when auto-add fails is **onboarding ops**, not engineering.

---

## Tools & surfaces

| Surface | Role |
|---------|------|
| **Telegram** | Primary — club admin account DMs, player support groups, bot/MTProto commands |
| **RT Hub** (Glide) | Deposits, cashouts, bonuses, early rakeback (Elevate system) |
| **Slack** | Internal comms — post "on" at shift start, escalate by topic |
| **Sling** | Shift schedules — who is working when |
| **Dashboard** (gg-support-bot) | **Not used** |

AMs do **not** live in the product dashboard. RT Hub is their ops ledger; Telegram is their workspace.

---

## How shifts work

1. AM posts **"on"** in the Slack channel when starting a shift.
2. Shift roster lives in **Sling**.
3. During the shift, AM uses **shared club Telegram accounts** (e.g. `@RoundTableSupport2`) — any player DM or group message is fair game.
4. Handoff to the next shift: read **group history** + **RT Hub**; no formal ticket queue. Slack carries escalations and open internal threads.

**Player-facing illusion:** One AM account + bot in each support group, plus backup AM accounts and translation bots. The human name behind the account changes; the account identity does not.

---

## Support group composition

Each player support group typically contains:

- **Primary AM account** — what the player treats as "their AM"
- **Support bot** — `/deposit`, `/cashout`, `/referral`, `/list`, `/cancel`
- **Backup AM accounts** — coverage when primary is rate-limited or unavailable
- **Translation bots** — e.g. `@YTranslateBot` for non-English players

All AMs are **same level** — full access to MTProto and bot commands. No senior/junior hierarchy.

---

## Telegram toolkit (regular use)

| Action | Where / how |
|--------|-------------|
| Confirm deposits/cashouts | Reply in player's support group |
| New player intake | Respond on club admin account DM |
| Create group when auto-add fails | `/gc` (MTProto) |
| Staff-initiated cashout | `/cash <amount>` → GGCashier wizard |
| Staff/admin commands | `/set`, `/unbindmethod`, payment bind replies, etc. |

Commands and MTProto are available to every AM — not gated by rank.

---

## Escalation routing

**First move:** Post in **Slack**.

| Topic | Route to |
|-------|----------|
| Policy, limits, disputes, bonus/comp decisions | **Head admin** |
| Bot broken, MTProto/auto-add failed, payment notifications | **Engineer** |
| Elevate rakeback exceptions beyond RT Hub | **Rakeback admin** |

AMs run commands and workflows; they do not root-cause system failures.

---

## What success feels like (shift)

1. **Fast first response** — player never feels ignored, even if payout takes time
2. **Closed threads** — open deposit/cashout requests resolved with in-group confirmation
3. **RT Hub accuracy** — every Telegram transaction reflected correctly in Glide
4. **Minimal escalations** — only when truly blocked

---

## Top frustrations

1. **Player anxiety** — repeated "where's my money?" before RT Hub verification completes
2. **Volume spikes** — too many simultaneous deposit/cashout threads for one shift
3. **Bot/MTProto failures** — auto-add fails, stuck commands; blocked until engineer
4. **Tool friction** — context split across Telegram, RT Hub, and Slack

---

## UX / product implications

| Area | AM need |
|------|---------|
| **Shift pool** | Any AM can pick up any thread; group history must be readable without private AM notes |
| **Consistent voice** | Multiple humans, one account — bot copy and AM replies should feel unified |
| **First response** | Bot should acknowledge receipt while AM checks RT Hub; reduces duplicate pings |
| **RT Hub ↔ Telegram** | Clear mapping from bot deposit/cashout flows to RT Hub records AMs can find fast |
| **Onboarding** | Reliable auto-add; when it fails, `/gc` path must be smooth without engineering |
| **Escalation** | Issue reports / Slack templates that carry club, player, chat id, and what was already tried |
| **Translation** | Non-English flows shouldn't force AM to paste into external tools mid-thread |
| **No dashboard** | Don't assume AMs will use gg-support-bot admin UI — ops live in RT Hub + Telegram |

---

## Relationship to Player persona

| Player believes | AM reality |
|---------------|------------|
| "My AM" in the support group | Shared account; whoever is on shift |
| AM confirms → money is done | AM confirms when RT Hub/process says so; proof is ClubGG/bank |
| One club, one group | Shift pool covers RT, AT, CC, GTO — AM must know union/club context |

Design player-facing flows so **any on-shift AM** can continue the thread without asking the player to repeat themselves.

---

## Anti-patterns (design against these)

- Assuming a dedicated AM–player assignment or "book"
- Building AM workflows only in the gg-support-bot dashboard
- Bot states that only make sense to the AM who started them (opaque to next shift)
- Forcing AMs to debug MTProto sessions or payment webhooks
- Escalation paths that skip Slack context (engineer gets "it's broken" with no chat id)
- Inconsistent tone across shifts on the same account
