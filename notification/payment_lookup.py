"""Look up payment rows from notification message ids."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from bot.services.cashapp_payments import find_cashapp_payment_by_notification_message
from bot.services.crypto_payments import find_crypto_payment_by_notification_message
from bot.services.paypal_payments import find_paypal_payment_by_notification_message
from bot.services.venmo_payments import find_payment_by_notification_message
from bot.services.zelle_payments import find_zelle_payment_by_notification_message

METHOD_ORDER = ("crypto", "paypal", "cashapp", "zelle", "venmo")

_FINDERS: dict[str, Callable[[int, int], object | None]] = {
    "crypto": find_crypto_payment_by_notification_message,
    "paypal": find_paypal_payment_by_notification_message,
    "cashapp": find_cashapp_payment_by_notification_message,
    "zelle": find_zelle_payment_by_notification_message,
    "venmo": find_payment_by_notification_message,
}


@dataclass(frozen=True)
class PaymentRef:
    method_slug: str
    payment_id: int
    payment: object


def find_payment_by_notification(
    notification_chat_id: int,
    notification_message_id: int,
) -> Optional[PaymentRef]:
    for slug in METHOD_ORDER:
        finder = _FINDERS[slug]
        payment = finder(int(notification_chat_id), int(notification_message_id))
        if payment is not None:
            return PaymentRef(
                method_slug=slug,
                payment_id=int(payment.id),
                payment=payment,
            )
    return None
