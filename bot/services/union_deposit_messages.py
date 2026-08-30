"""Player-facing copy for union deposit ack + instruction messages."""

from __future__ import annotations

import html
from decimal import Decimal

from bot.services.union_deposit_instruction import build_union_deposit_instruction

UNION_SPECIAL_INSTRUCTIONS_HEADER = "Special instructions"
_BULLET = "•"
UNION_INSTRUCTION_RECURRING_LINE = (
    "This is NOT a recurring payment method and should not be sent again."
)
UNION_ACK_EXPIRED_TEXT = (
    "These instructions expired.\n\nUse /deposit to get new instructions."
)


def _instruction_warning_bullets(*, html: bool, bold_not: bool = False) -> list[str]:
    if html and bold_not:
        first = (
            f"{_BULLET} This is <b>NOT</b> a recurring payment method "
            "and should not be sent again."
        )
    else:
        first = (
            f"{_BULLET} This is NOT a recurring payment method "
            "and should not be sent again."
        )
    return [
        first,
        f"{_BULLET} Please initiate a new deposit before sending again.",
        (
            f"{_BULLET} Once the payment is sent, please send a screen recording "
            "within 10 minutes."
        ),
    ]


UNION_INSTRUCTION_EXPIRED_TEXT = (
    "The provided payment method has expired.\n\n"
    "Please do not send a payment to this tag if you have not already.\n\n"
    "Use /deposit to get a valid method."
)
UNION_ACK_BUTTON_LABEL = "I HAVE READ THE INSTRUCTIONS ABOVE"
UNION_ACK_CALLBACK_PREFIX = "depum"


def build_union_special_instructions_text(*, html: bool = True) -> str:
    lines = [f"<b>{UNION_SPECIAL_INSTRUCTIONS_HEADER}</b>" if html else UNION_SPECIAL_INSTRUCTIONS_HEADER, ""]
    lines.extend(_instruction_warning_bullets(html=html, bold_not=html))
    return "\n".join(lines)


def build_union_instruction_with_footer(
    method,
    *,
    used_sum: Decimal,
    html_mode: bool = False,
) -> str | None:
    """Min/max instruction text for the group chat after ack."""
    return build_union_deposit_instruction(
        method,
        used_sum=used_sum,
        html_mode=html_mode,
    )


def union_ack_callback_data(request_id: int) -> str:
    return f"{UNION_ACK_CALLBACK_PREFIX}:{int(request_id)}"
