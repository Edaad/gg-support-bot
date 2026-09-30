"""Previous payout tags for a support group, from cashouts that were actually sent."""

from __future__ import annotations

from sqlalchemy import desc

from bot.services.cashout_handle_validation import validate_cashout_handle
from db.connection import get_db
from db.models import StaffCashoutMoneySend, StaffCashoutPayment, StaffCashoutRecord


def distinct_valid_tags(details_newest_first: list[str], slug: str) -> list[str]:
    """Normalized destinations, newest first. Blanks, invalid, and repeats drop out."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in details_newest_first:
        normalized = validate_cashout_handle(slug, raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def list_previous_cashout_tags(
    *,
    club_id: int,
    chat_id: int,
    method_id: int,
    sub_option_id: int | None,
    slug: str,
) -> list[str]:
    """Distinct tags already sent to this group for this method.

    A tag counts only when a money-sent row exists for the method. For crypto,
    the send and the payment must be the same coin. The tag itself is the
    payment line's payout details, newest send first.
    """
    with get_db() as session:
        query = (
            session.query(StaffCashoutPayment.payout_details)
            .join(
                StaffCashoutRecord,
                StaffCashoutRecord.id == StaffCashoutPayment.cashout_record_id,
            )
            .join(
                StaffCashoutMoneySend,
                StaffCashoutMoneySend.cashout_record_id == StaffCashoutRecord.id,
            )
            .filter(
                StaffCashoutRecord.club_id == int(club_id),
                StaffCashoutRecord.chat_id == int(chat_id),
                StaffCashoutMoneySend.payment_method_id == int(method_id),
                StaffCashoutPayment.payment_method_id == int(method_id),
                StaffCashoutPayment.payout_details.isnot(None),
                StaffCashoutPayment.payout_details != "",
            )
        )
        if sub_option_id is not None:
            sub_id = int(sub_option_id)
            query = query.filter(
                StaffCashoutMoneySend.payment_sub_option_id == sub_id,
                StaffCashoutPayment.payment_sub_option_id == sub_id,
            )
        rows = query.order_by(
            desc(StaffCashoutMoneySend.created_at),
            desc(StaffCashoutMoneySend.id),
        ).all()
    details = [row[0] for row in rows if row[0]]
    return distinct_valid_tags(details, slug)
