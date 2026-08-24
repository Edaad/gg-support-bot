"""Persist and resolve fan-out payment notification Telegram posts."""

from __future__ import annotations

from typing import Sequence

from db.connection import get_db
from db.models import PaymentNotificationPost
from notification.chat_id import telegram_chat_id_variants


def record_payment_notification_posts(
    *,
    payment_method_slug: str,
    payment_id: int,
    posts: Sequence[tuple[int, int]],
) -> None:
    """Store every successful bind-chat notification post for a payment."""
    slug = (payment_method_slug or "").strip().lower()
    pid = int(payment_id)
    if not slug or not posts:
        return

    with get_db() as session:
        for chat_id, message_id in posts:
            cid = int(chat_id)
            mid = int(message_id)
            exists = (
                session.query(PaymentNotificationPost.id)
                .filter_by(notification_chat_id=cid, notification_message_id=mid)
                .one_or_none()
            )
            if exists is not None:
                continue
            session.add(
                PaymentNotificationPost(
                    payment_method_slug=slug,
                    payment_id=pid,
                    notification_chat_id=cid,
                    notification_message_id=mid,
                )
            )


def list_payment_notification_posts(
    *,
    payment_method_slug: str,
    payment_id: int,
) -> list[tuple[int, int]]:
    slug = (payment_method_slug or "").strip().lower()
    with get_db() as session:
        rows = (
            session.query(
                PaymentNotificationPost.notification_chat_id,
                PaymentNotificationPost.notification_message_id,
            )
            .filter_by(payment_method_slug=slug, payment_id=int(payment_id))
            .order_by(PaymentNotificationPost.id.asc())
            .all()
        )
    return [(int(chat_id), int(message_id)) for chat_id, message_id in rows]


def find_payment_notification_post(
    notification_chat_id: int,
    notification_message_id: int,
) -> tuple[str, int] | None:
    """Return (method_slug, payment_id) for a staff notification message."""
    chat_ids = telegram_chat_id_variants(int(notification_chat_id))
    msg_id = int(notification_message_id)
    with get_db() as session:
        row = (
            session.query(
                PaymentNotificationPost.payment_method_slug,
                PaymentNotificationPost.payment_id,
            )
            .filter(
                PaymentNotificationPost.notification_chat_id.in_(chat_ids),
                PaymentNotificationPost.notification_message_id == msg_id,
            )
            .order_by(PaymentNotificationPost.id.asc())
            .first()
        )
    if row is None:
        return None
    return str(row[0]), int(row[1])
