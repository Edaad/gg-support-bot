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
from notification.chat_id import telegram_chat_id_variants

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
    payment_is_test: bool
    telegram_chat_id: int | None


def _payment_ref_from_row(slug: str, payment: object) -> PaymentRef:
    return PaymentRef(
        method_slug=slug,
        payment_id=int(payment.id),
        payment_is_test=bool(getattr(payment, "is_test", False)),
        telegram_chat_id=getattr(payment, "telegram_chat_id", None),
    )


def find_payment_by_notification(
    notification_chat_id: int,
    notification_message_id: int,
) -> Optional[PaymentRef]:
    chat_ids = telegram_chat_id_variants(int(notification_chat_id))
    msg_id = int(notification_message_id)
    with get_db() as session:
        for slug in METHOD_ORDER:
            payment = (
                session.query(_MODELS[slug])
                .filter(
                    _MODELS[slug].notification_chat_id.in_(chat_ids),
                    _MODELS[slug].notification_message_id == msg_id,
                )
                .one_or_none()
            )
            if payment is not None:
                return _payment_ref_from_row(slug, payment)
    return None
