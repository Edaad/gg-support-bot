"""Versioned prompt templates for group-chat ticket segmentation + classification."""

from __future__ import annotations

PROMPT_VERSION = "2.1.0"

TICKET_CATEGORIES: tuple[str, ...] = (
    "auto_deposit",
    "deposit",
    "cashout",
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

- Ignore purely administrative messages (e.g. "bot joined", system notifications) unless they contain useful context.

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
- `auto_deposit`: deposit flow where a **bot** posts the chips-added / completion message
- `deposit`: deposit flow where an **admin account** posts the added / completion message
- `cashout`: cashout / payout request
- `early_rakeback`: early rakeback request or fulfillment
- `rakeback`: standard rakeback (not early)
- `bonus`: bonus request or fulfillment
- `other`: everything else

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
  - `resolution`: timestamp when the issue was resolved (or null)
  - `escalation`: timestamp when escalated (or null)
- `summary`: one-sentence summary. Include useful narrative details that are not separate \
fields (payment rail if mentioned, which account handled it, blockers, whether it looked \
resolved/escalated, etc.)
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
