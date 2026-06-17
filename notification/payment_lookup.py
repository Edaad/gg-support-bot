"""Look up payment rows from notification message ids."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from db.connection import get_db
from db.models import (
    CashAppPayment,
    CryptoPayment,
    PayPalPayment,
    VenmoPayment,
    ZellePayment,
)

METHOD_ORDER = ("crypto", "paypal", "cashapp", "zelle", "venmo")

_MODELS = {
    "crypto": CryptoPayment,
    "paypal": PayPalPayment,
    "cashapp": CashAppPayment,
    "zelle": ZellePayment,
    "venmo": VenmoPayment,
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
    with get_db() as session:
        for slug in METHOD_ORDER:
            payment = (
                session.query(_MODELS[slug])
                .filter_by(
                    notification_chat_id=int(notification_chat_id),
                    notification_message_id=int(notification_message_id),
                )
                .one_or_none()
            )
            if payment is not None:
                payment_id = int(payment.id)
                session.expunge(payment)
                return PaymentRef(
                    method_slug=slug,
                    payment_id=payment_id,
                    payment=payment,
                )
    return None
