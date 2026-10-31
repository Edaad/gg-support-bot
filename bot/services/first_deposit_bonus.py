"""First-deposit bonus announced when a payment is bound or a group title changes.

The announcement is a message in the support group. Chips are not credited.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from bot.services.club import get_first_deposit_settings
from bot.services.player_details import gg_player_id_from_title
from db.connection import get_db
from db.models import (
    CashAppPayment,
    CryptoPayment,
    Group,
    PayPalPayment,
    StripeCheckoutSession,
    VenmoPayment,
    ZellePayment,
)
from notification.chat_id import (
    telegram_chat_id_variants,
    telegram_chat_ids_match,
)

logger = logging.getLogger(__name__)

_BIDI = dict.fromkeys(
    map(
        ord,
        "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff",
    )
)

_RAILS = (VenmoPayment, ZellePayment, CashAppPayment, PayPalPayment, CryptoPayment)


@dataclass(frozen=True)
class PaymentView:
    at: datetime
    amount: Decimal


@dataclass(frozen=True)
class GroupView:
    chat_id: int
    title: str
    claimed: bool
    link: str | None


@dataclass(frozen=True)
class BonusDecision:
    announcement: str | None = None
    mark_chat_id: int | None = None
    dm_text: str | None = None
    dm_admin_id: int | None = None
    chat_key: str | None = None
    player_id: str | None = None


def clean_title(title: str | None) -> str:
    return (title or "").translate(_BIDI).strip()


def player_id_from_title(title: str | None) -> str | None:
    return gg_player_id_from_title(clean_title(title))


def bonus_amount(amount: Decimal, pct: int, cap: Decimal | None) -> Decimal:
    bonus = (amount * Decimal(pct) / Decimal(100)).quantize(Decimal("0.01"))
    if cap is not None and bonus > cap:
        bonus = cap.quantize(Decimal("0.01"))
    return bonus


def format_bonus_message(*, pct: int, bonus: Decimal, amount: Decimal) -> str:
    return (
        f"Congratulations on your first, you will get a "
        f"{pct}% first deposit bonus of ${bonus:,.2f} added to "
        f"your deposit of ${amount:,.2f} on us!"
    )


def format_duplicate_dm(player_id: str, groups: list[GroupView]) -> str:
    lines = [
        f"Two support groups for {player_id}. Run /delete confirm in one of them.",
        "",
    ]
    for group in sorted(groups, key=lambda g: (g.title, g.chat_id)):
        lines.append(group.title)
        if group.link:
            lines.append(group.link)
        lines.append("")
    return "\n".join(lines).strip()


def chat_key_for(chat_ids: list[int]) -> str:
    return ",".join(str(cid) for cid in sorted(int(c) for c in chat_ids))


def payment_belongs(
    *,
    title: str | None,
    chat_id: int,
    player_id: str,
    target_chat_id: int,
) -> bool:
    """True when this bound payment belongs to ``player_id`` for this check."""
    found = player_id_from_title(title)
    if found == player_id:
        return True
    return found is None and telegram_chat_ids_match(int(chat_id), int(target_chat_id))


def decide(
    *,
    player_id: str | None,
    target: GroupView | None,
    groups: list[GroupView],
    payments: list[PaymentView],
    bonus_enabled: bool,
    pct: int,
    cap: Decimal | None,
    dm_admin_id: int | None,
) -> BonusDecision:
    if not player_id or target is None:
        return BonusDecision()

    dm_text = None
    key = None
    if len(groups) > 1 and bonus_enabled and dm_admin_id:
        dm_text = format_duplicate_dm(player_id, groups)
        key = chat_key_for([g.chat_id for g in groups])

    announcement = None
    mark_chat_id = None
    if (
        len(groups) == 1
        and not target.claimed
        and bonus_enabled
        and pct > 0
        and payments
    ):
        earliest = min(payments, key=lambda p: _aware(p.at))
        amount = bonus_amount(earliest.amount, pct, cap)
        if amount > 0:
            announcement = format_bonus_message(
                pct=pct, bonus=amount, amount=earliest.amount
            )
            mark_chat_id = target.chat_id

    return BonusDecision(
        announcement=announcement,
        mark_chat_id=mark_chat_id,
        dm_text=dm_text,
        dm_admin_id=dm_admin_id if dm_text else None,
        chat_key=key,
        player_id=player_id if dm_text else None,
    )


def schedule_bound_payment(*, club_id: int, telegram_chat_id: int) -> None:
    """Run the bonus check without blocking the bind that just committed."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "first_deposit_bonus: no running loop chat_id=%s", telegram_chat_id
        )
        return
    task = loop.create_task(
        consider_bound_payment(
            club_id=int(club_id), telegram_chat_id=int(telegram_chat_id)
        )
    )
    task.add_done_callback(_log_task)


def _log_task(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("first_deposit_bonus: check failed")


async def consider_bound_payment(*, club_id: int, telegram_chat_id: int) -> None:
    decision = _load_decision(
        club_id=int(club_id), telegram_chat_id=int(telegram_chat_id)
    )
    if decision is None:
        return
    if (
        decision.dm_text
        and decision.dm_admin_id
        and decision.chat_key
        and decision.player_id
    ):
        if _reserve_duplicate_notice(club_id, decision.player_id, decision.chat_key):
            sent = await _send(decision.dm_admin_id, decision.dm_text)
            if not sent:
                _release_duplicate_notice(
                    club_id, decision.player_id, decision.chat_key
                )
    if decision.announcement and decision.mark_chat_id is not None:
        sent = await _send(decision.mark_chat_id, decision.announcement)
        if sent:
            _mark_claimed(decision.mark_chat_id)


async def consider_title_change(*, telegram_chat_id: int) -> None:
    club_id = _club_id_for_chat(int(telegram_chat_id))
    if club_id is None:
        return
    await consider_bound_payment(
        club_id=club_id, telegram_chat_id=int(telegram_chat_id)
    )


def _load_decision(*, club_id: int, telegram_chat_id: int) -> BonusDecision | None:
    from club_gc_settings import get_club_gc_config_by_link_club_id

    with get_db() as session:
        target_row = _group_for_chat(session, telegram_chat_id)
        if target_row is None or target_row.club_id != int(club_id):
            return None
        player_id = player_id_from_title(target_row.name)
        if not player_id:
            return None
        groups = _groups_for_player(session, club_id, player_id, target_row)
        payments = _payments(
            session,
            club_id=club_id,
            player_id=player_id,
            target_chat_id=int(target_row.chat_id),
            group_chat_ids=[g.chat_id for g in groups],
        )
        settings = get_first_deposit_settings(int(club_id))
        cfg = get_club_gc_config_by_link_club_id(int(club_id))
        admin_id = (
            int(cfg.command_admin_user_id)
            if cfg and cfg.command_admin_user_id
            else None
        )
        target = next(g for g in groups if g.chat_id == int(target_row.chat_id))
        return decide(
            player_id=player_id,
            target=target,
            groups=groups,
            payments=payments,
            bonus_enabled=bool(settings["bonus_enabled"]),
            pct=int(settings["bonus_pct"] or 0),
            cap=settings.get("bonus_cap"),
            dm_admin_id=admin_id,
        )


def _group_for_chat(session, chat_id: int) -> Group | None:
    ids = list(telegram_chat_id_variants(int(chat_id)))
    return session.query(Group).filter(Group.chat_id.in_(ids)).one_or_none()


def _club_id_for_chat(chat_id: int) -> int | None:
    with get_db() as session:
        row = _group_for_chat(session, chat_id)
        return int(row.club_id) if row is not None else None


def _groups_for_player(
    session, club_id: int, player_id: str, target: Group
) -> list[GroupView]:
    rows = (
        session.query(Group)
        .filter(Group.club_id == int(club_id), Group.name.ilike(f"%{player_id}%"))
        .all()
    )
    found: dict[int, GroupView] = {}
    for row in rows:
        if player_id_from_title(row.name) != player_id:
            continue
        found[int(row.chat_id)] = _group_view(row)
    if (
        int(target.chat_id) not in found
        and player_id_from_title(target.name) == player_id
    ):
        found[int(target.chat_id)] = _group_view(target)
    return list(found.values())


def _group_view(row: Group) -> GroupView:
    from bot.services.support_group_chats import fetch_invite_link_for_chat

    title = clean_title(row.name) or str(row.chat_id)
    link = fetch_invite_link_for_chat(int(row.chat_id), group_title=row.name)
    return GroupView(
        chat_id=int(row.chat_id),
        title=title,
        claimed=bool(row.first_deposit_claimed),
        link=link,
    )


def _dollars(amount_cents: int) -> Decimal:
    return Decimal(int(amount_cents)) / Decimal(100)


def _bound_at(row) -> datetime:
    return _aware(getattr(row, "bound_at", None) or row.created_at)


def _payments(
    session,
    *,
    club_id: int,
    player_id: str,
    target_chat_id: int,
    group_chat_ids: list[int],
) -> list[PaymentView]:
    views: list[PaymentView] = []
    target_ids = list(telegram_chat_id_variants(int(target_chat_id)))
    for model in _RAILS:
        rows = (
            session.query(model)
            .filter(
                model.club_id == int(club_id),
                model.is_test.is_(False),
                model.telegram_chat_id.isnot(None),
                or_(
                    model.bound_group_title_at_bind.ilike(f"%{player_id}%"),
                    model.telegram_chat_id.in_(target_ids),
                ),
            )
            .all()
        )
        for row in rows:
            if not payment_belongs(
                title=row.bound_group_title_at_bind,
                chat_id=int(row.telegram_chat_id),
                player_id=player_id,
                target_chat_id=target_chat_id,
            ):
                continue
            views.append(
                PaymentView(at=_bound_at(row), amount=_dollars(row.amount_cents))
            )

    stripe_ids: set[int] = set()
    for cid in group_chat_ids:
        stripe_ids.update(telegram_chat_id_variants(int(cid)))
    if not stripe_ids:
        return views
    stripe_rows = (
        session.query(StripeCheckoutSession)
        .filter(
            StripeCheckoutSession.club_id == int(club_id),
            StripeCheckoutSession.status == "complete",
            StripeCheckoutSession.telegram_chat_id.in_(list(stripe_ids)),
        )
        .all()
    )
    for row in stripe_rows:
        views.append(
            PaymentView(
                at=_aware(row.completed_at or row.created_at),
                amount=_dollars(row.amount_cents),
            )
        )
    return views


def _reserve_duplicate_notice(club_id: int, player_id: str, chat_key: str) -> bool:
    from sqlalchemy import text

    try:
        with get_db() as session:
            session.execute(
                text(
                    """
                    INSERT INTO first_deposit_duplicate_notices
                        (club_id, gg_player_id, chat_key)
                    VALUES (:club_id, :player_id, :chat_key)
                    """
                ),
                {
                    "club_id": int(club_id),
                    "player_id": player_id,
                    "chat_key": chat_key,
                },
            )
        return True
    except IntegrityError:
        return False


def _release_duplicate_notice(club_id: int, player_id: str, chat_key: str) -> None:
    from sqlalchemy import text

    with get_db() as session:
        session.execute(
            text(
                """
                DELETE FROM first_deposit_duplicate_notices
                WHERE club_id = :club_id
                  AND gg_player_id = :player_id
                  AND chat_key = :chat_key
                """
            ),
            {
                "club_id": int(club_id),
                "player_id": player_id,
                "chat_key": chat_key,
            },
        )


def _mark_claimed(chat_id: int) -> None:
    ids = list(telegram_chat_id_variants(int(chat_id)))
    with get_db() as session:
        session.query(Group).filter(Group.chat_id.in_(ids)).update(
            {Group.first_deposit_claimed: True},
            synchronize_session=False,
        )


async def _send(chat_id: int, text: str) -> bool:
    from bot.services.payment_group_notify import support_bot_tokens_to_try
    from telegram import Bot

    tokens = support_bot_tokens_to_try(is_test=False)
    if not tokens:
        logger.warning("first_deposit_bonus: no bot token chat_id=%s", chat_id)
        return False
    last_error: Exception | None = None
    for token in tokens:
        try:
            await Bot(token=token).send_message(chat_id=int(chat_id), text=text)
            return True
        except Exception as exc:
            last_error = exc
            logger.warning(
                "first_deposit_bonus: send failed chat_id=%s",
                chat_id,
                exc_info=True,
            )
    logger.warning(
        "first_deposit_bonus: all tokens failed chat_id=%s err=%s",
        chat_id,
        last_error,
    )
    return False


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
