"""Versioned prompt templates for group-chat ticket segmentation + classification."""

from __future__ import annotations

PROMPT_VERSION = "2.3.0"

TICKET_CATEGORIES: tuple[str, ...] = (
    "auto_deposit",
    "manual_deposit",
    "unfinished_deposit",
    "cashout",
    "unfinished_cashout",
    "early_rakeback",
    "rakeback",
    "bonus",
    "other",
)

CATEGORIES_LIST = "\n".join(f"- `{c}`" for c in TICKET_CATEGORIES)

SEGMENTATION_SYSTEM = """\
You are an expert at analyzing customer-support chat logs for an online gaming / poker platform.

You will receive the full message history of a single group chat between a customer and support \
agents for a specific date. A single chat can contain **multiple distinct support tickets** — for \
example the customer may request a deposit in the morning, ask about rakeback in the afternoon, \
and request a cashout at night.

Your job is to segment the message stream into individual tickets.

### Roles in the chat
- **Customer**: the player (non-admin, non-bot participant)
- **Admin accounts**: shared club support Telegram accounts (human AMs on shift)
- **Bots**: the support bot, translation bots, and any other bot accounts

### Rules for identifying ticket boundaries

- A **new ticket starts** when:
  • The customer expresses a new intent (deposit, cashout, rakeback, early rakeback, bonus, etc.)
  • There is a significant idle gap (roughly 4+ hours of silence) followed by a new message from the customer
  • The customer explicitly raises a new, unrelated topic while a previous topic is resolved or idle

- A **ticket ends** when:
  • The request is resolved (e.g. "Added $X", "Sent", "Done", customer confirms receipt)
  • The conversation goes idle with no follow-up on that topic
  • A new ticket starts on a different topic

- Tickets may **overlap** if a customer raises a second issue before the first is resolved. In that \
case, assign messages to the ticket they belong to (a message can belong to only one ticket).

- If the entire chat is one continuous support interaction on a single topic, output exactly one ticket.

- Ignore purely administrative messages (e.g. "bot joined", system notifications, \
`MessageActionChatEditTitle`, pin actions) unless they contain useful context.

### Early rakeback must be its own ticket

- Early rakeback is a **separate intent** from deposit, cashout, bonus, and standard rakeback.
- Start a new early-rakeback ticket when the customer asks to load early RB / rakeback early \
(e.g. `/earlyrb`, "load rb", "early rb", "can I get my rb", "add my early rakeback"), even if a \
deposit or other ticket is still open.
- Messages that only belong to early rakeback — balance checks for early RB, 24-hour early-RB \
cooldown replies, "Added N in early rb", denials under the $50 minimum — go on the early-rakeback \
ticket, **not** the deposit/bonus ticket.
- Do **not** fold "Added N in early rb" into a surrounding deposit ticket.
- If early rakeback and deposit interleave, split message_ids across two tickets (overlap in time \
is fine; each message belongs to exactly one ticket).

### Output format

Return a JSON object with a `tickets` array. Each ticket has:
- `ticket_index`: integer starting at 0
- `start_msg_id`: the message `id` of the first message in this ticket
- `end_msg_id`: the message `id` of the last message in this ticket
- `message_ids`: array of all message `id` values that belong to this ticket (source of truth)
- `brief_summary`: one-sentence summary of what the ticket is about
"""

SEGMENTATION_USER_TEMPLATE = """\
Here are the messages for chat "{chat_name}". Segment them into tickets.

Messages (JSON):
{messages_json}

Return ONLY a JSON object with a `tickets` array as described. No extra text.
"""

CLASSIFICATION_SYSTEM = """\
You are an expert at classifying customer-support tickets for an online gaming / poker platform.

You will receive the messages of a single support ticket (already segmented). Classify it and \
extract structured timing data.

### Categories (pick exactly one)
{categories_list}

Category rules:
- `auto_deposit`: deposit intent **with** a deposit fulfillment line posted by a **bot** (see below)
- `manual_deposit`: deposit intent **with** a deposit fulfillment line posted by an **admin account** \
(see below)
- `unfinished_deposit`: deposit intent started (`/deposit`, amount, method, payment link, "I sent") \
but **no** deposit fulfillment line — includes bot auto-cancel, customer ghosted, method issues, \
KYC hold with no chips added
- `cashout`: cashout attempt that **progressed past** the policy gate (amount / rails / working / \
sent). Do **not** use this for 24h-wait or outside-hours blocks alone
- `unfinished_cashout`: cashout blocked by **24-hour wait** or **outside cashout hours** before the \
payout flow really starts (bot or admin policy block)
- `early_rakeback`: **all** early rakeback ask / fulfill / deny (load early RB, `/earlyrb`, \
under-minimum denials, cooldown denials, "Added N in early rb", including early-rb exceptions \
granted during other threads)
- `rakeback`: standard rakeback (not early) — % tier questions, rakeback level bumps, weekly RB \
policy — **not** early loads
- `bonus`: bonus / freeplay request or fulfillment that is **not** early rakeback
- `other`: everything else

### Deposit fulfillment (auto vs manual vs unfinished)

Decide using the **deposit fulfillment line** only — the message that actually credits the deposit \
chips, typically like `Added 35 chips, best of luck…` or `Added 1000!`.

- Count as fulfillment: short confirmations that chips for the **deposit** were added now.
- Do **not** count as fulfillment:
  • Bot instruction / promo templates (`chips will be added`, `Once sent…`, first-deposit bonus \
blurbs, checkout links)
  • Admin mid-flow chatter (`Adding!`, `Working on it`, `will add when received`) without the \
actual credit line
- If there is **no** deposit fulfillment line → `unfinished_deposit`.
- If the **bot** posts the deposit fulfillment line → `auto_deposit`, even if an admin said \
`Adding!` or helped earlier.
- If an **admin account** posts the deposit fulfillment line and the bot does not → `manual_deposit`.
- If both bot and admin post fulfillment-style lines for the same deposit amount, prefer the \
line that credits the deposit chips; a later admin `added N bonus chips` after a bot \
`Added N chips` still leaves the ticket as `auto_deposit`.

### Cashout vs unfinished_cashout

- `unfinished_cashout`: `/cashout` or a cashout ask hits "wait X hours since last deposit/cashout" \
or "outside active instant cashout hours (8 AM–11 PM EST)" and the payout flow never really starts.
- `cashout`: the attempt progressed (amount collected, rails collected, working, sent) even if it \
later stalls.
- For `unfinished_cashout`, set `resolution` to the timestamp of the blocking message and mention \
the block reason in `summary`.
- `admin_first_response` stays null when no admin replied.

### Roles
- **Customer**: the player
- **Admin accounts** (human AM accounts): {admin_names}
- **Bots**: {bot_names}
- Treat message `is_bot: true` as a bot even if the username is not listed.
- `admin_first_response` must be the first reply from an **admin account**, never a bot.

### What to extract

Return a JSON object with:
- `category`: one of the categories above
- `events`: object with these optional timestamp fields (ISO-8601 or null):
  - `customer_first_message`: timestamp of the first customer message
  - `admin_first_response`: timestamp of the first admin-account response (not bot)
  - `resolution`: timestamp when the issue was resolved **or** when a bot/admin clearly closed \
the attempt (including unfinished_cashout policy blocks); otherwise null
  - `escalation`: timestamp when escalated (or null)
- `summary`: one-sentence summary. Include useful narrative details that are not separate \
fields (payment rail if mentioned, which account handled it, blockers, whether it looked \
resolved/escalated, cashout block reason, etc.)
"""

CLASSIFICATION_USER_TEMPLATE = """\
Classify this ticket from chat "{chat_name}".

Messages (JSON):
{messages_json}

Return ONLY the JSON classification object. No extra text.
"""

SEGMENTATION_TOOL_NAME = "record_segmentation"
CLASSIFICATION_TOOL_NAME = "record_classification"

SEGMENTATION_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tickets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticket_index": {"type": "integer"},
                    "start_msg_id": {"type": "integer"},
                    "end_msg_id": {"type": "integer"},
                    "message_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "brief_summary": {"type": "string"},
                },
                "required": [
                    "ticket_index",
                    "start_msg_id",
                    "end_msg_id",
                    "message_ids",
                    "brief_summary",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tickets"],
    "additionalProperties": False,
}

CLASSIFICATION_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": list(TICKET_CATEGORIES),
        },
        "events": {
            "type": "object",
            "properties": {
                "customer_first_message": {"type": ["string", "null"]},
                "admin_first_response": {"type": ["string", "null"]},
                "resolution": {"type": ["string", "null"]},
                "escalation": {"type": ["string", "null"]},
            },
            "required": [
                "customer_first_message",
                "admin_first_response",
                "resolution",
                "escalation",
            ],
            "additionalProperties": False,
        },
        "summary": {"type": "string"},
    },
    "required": ["category", "events", "summary"],
    "additionalProperties": False,
}


def build_classification_system(
    *,
    admin_names: list[str],
    bot_names: list[str],
) -> str:
    admins = ", ".join(admin_names) if admin_names else "(none configured)"
    bots = ", ".join(bot_names) if bot_names else "(none configured; rely on is_bot)"
    return CLASSIFICATION_SYSTEM.format(
        categories_list=CATEGORIES_LIST,
        admin_names=admins,
        bot_names=bots,
    )
