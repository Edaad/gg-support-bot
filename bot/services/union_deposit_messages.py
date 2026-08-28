"""Player-facing copy for union deposit ack + instruction messages."""

from __future__ import annotations

from decimal import Decimal

from bot.services.union_deposit_instruction import build_union_deposit_instruction

UNION_SPECIAL_INSTRUCTIONS_HEADER = "Special instructions;"
UNION_SPECIAL_INSTRUCTIONS_BODY = (
    "This is NOT a recurring payment method and should not be sent again.",
    "Please initiate a new deposit before sending again",
    "Once the payment is sent, please send a screen recording within 10 minutes",
)
UNION_INSTRUCTION_RECURRING_LINE = (
    "This is NOT a recurring payment method and should not be sent again."
)
UNION_ACK_EXPIRED_TEXT = (
    "These instructions expired.\n\nUse /deposit to get new instructions."
)
UNION_INSTRUCTION_EXPIRED_TEXT = (
    "This is NOT a recurring payment method and should not be sent again.\n\n"
    "Please initiate a new deposit before sending again\n\n"
    "Once the payment is sent, please send a screen recording within 10 minutes"
)
UNION_ACK_BUTTON_LABEL = "I HAVE READ THE INSTRUCTIONS ABOVE"
UNION_ACK_CALLBACK_PREFIX = "depum"


def build_union_special_instructions_text() -> str:
    lines = [UNION_SPECIAL_INSTRUCTIONS_HEADER, ""]
    lines.extend(UNION_SPECIAL_INSTRUCTIONS_BODY)
    return "\n".join(lines)


def build_union_instruction_with_footer(
    method,
    *,
    used_sum: Decimal,
) -> str | None:
    """Min/max instruction plus recurring-payment footer line."""
    core = build_union_deposit_instruction(method, used_sum=used_sum)
    if not core:
        return None
    return f"{core}\n\n{UNION_INSTRUCTION_RECURRING_LINE}"


def union_ack_callback_data(request_id: int) -> str:
    return f"{UNION_ACK_CALLBACK_PREFIX}:{int(request_id)}"
