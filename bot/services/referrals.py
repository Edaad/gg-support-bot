"""Referral deep links: codes, first-click attribution, hop copy, bind/retitle acks."""

from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from club_gc_settings import get_club_gc_config_by_link_club_id
from db.connection import get_db
from db.models import (
    CashAppPayment,
    CryptoPayment,
    PayPalPayment,
    ReferralAttribution,
    ReferralLink,
    VenmoPayment,
    ZellePayment,
)
from bot.services.support_group_chats import (
    fetch_support_group_chat_by_club_player,
    fetch_support_group_chat_by_telegram_chat_id,
)

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_CREDITED = "credited"
STATUS_CLOSED_DUPLICATE = "closed_duplicate"

CODE_PREFIX = "ref_"
CODE_BODY_LEN = 12
_CODE_ALPHABET = string.ascii_letters + string.digits

UNTITLED_GROUP_ERROR = "This group needs a player id in the title."

REFERRAL_LINK_MESSAGE = (
    "Referral Program 🔥\n\n"
    "Invite friends with the link below and earn rewards!\n"
    "{url}\n\n"
    "For each referral, GET a $30-chip FREEPLAY bonus for each person you refer\n\n"
    "Rules:\n\n"
    "• You must have played in the club to qualify.\n"
    "• Referred players must be new, unique players.\n"
    "• Referred players must deposit at least $100 for the bonus to apply."
)


@dataclass(frozen=True)
class GroupMessage:
    chat_id: int
    text: str


class ReferralBindMessages(list):
    """Join/retitle acks. ``just_credited`` is the pending → credited transition."""

    def __init__(self, iterable=(), *, just_credited: bool = False):
        super().__init__(iterable)
        self.just_credited = just_credited


DEPOSIT_OBLIGATION_CENTS = 10_000

WAITING_HEADER = "Still waiting on a $100 deposit"
EARNED_HEADER = "$30 bonus earned"
EMPTY_SECTION = "Nobody here yet."
NO_REFERRALS = "You haven't referred any players yet."

_BOUND_PAYMENT_MODELS = (
    VenmoPayment,
    CashAppPayment,
    PayPalPayment,
    ZellePayment,
    CryptoPayment,
)

DepositAction = Literal["noop", "suppress", "notify", "slack_only"]


@dataclass(frozen=True)
class DepositSnapshot:
    attribution_id: int
    club_id: int
    referrer_chat_id: int
    referred_player_id: str
    suppressed: bool
    telegram_sent: bool
    slack_sent: bool
    slack_skipped: bool
    total_cents: int


@dataclass(frozen=True)
class StartResult:
    kind: Literal["hop", "existing", "unknown", "noop"]
    text: str
    club_id: int | None = None


def generate_referral_code() -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_BODY_LEN))
    return f"{CODE_PREFIX}{body}"


def is_referral_start_payload(arg: str | None) -> bool:
    if not arg:
        return False
    s = arg.strip()
    if not s.startswith(CODE_PREFIX):
        return False
    body = s[len(CODE_PREFIX) :]
    return len(body) == CODE_BODY_LEN and all(c in _CODE_ALPHABET for c in body)


def build_referral_url(*, bot_username: str, code: str) -> str:
    uname = (bot_username or "").strip().lstrip("@")
    return f"https://t.me/{uname}?start={code}"


def format_referral_link_message(url: str) -> str:
    return REFERRAL_LINK_MESSAGE.format(url=url)


def bound_deposit_cents_by_chat(session, chat_ids: list[int]) -> dict[int, int]:
    """Non-test bound Venmo, Cash App, PayPal, Zelle, and crypto cents per chat."""
    ids = [int(chat_id) for chat_id in chat_ids]
    totals = {chat_id: 0 for chat_id in ids}
    if not ids:
        return totals
    for model in _BOUND_PAYMENT_MODELS:
        rows = (
            session.query(model.telegram_chat_id, func.sum(model.amount_cents))
            .filter(
                model.telegram_chat_id.in_(ids),
                model.is_test.is_(False),
            )
            .group_by(model.telegram_chat_id)
            .all()
        )
        for chat_id, amount in rows:
            if chat_id is None:
                continue
            totals[int(chat_id)] = totals.get(int(chat_id), 0) + int(amount or 0)
    return totals


def bound_deposit_cents(session, chat_id: int) -> int:
    return bound_deposit_cents_by_chat(session, [int(chat_id)]).get(int(chat_id), 0)


def _credited_rows(session, *, club_id: int, referrer_chat_id: int):
    return (
        session.query(ReferralAttribution)
        .join(
            ReferralLink,
            ReferralAttribution.referral_link_id == ReferralLink.id,
        )
        .filter(
            ReferralLink.club_id == int(club_id),
            ReferralLink.referrer_chat_id == int(referrer_chat_id),
            ReferralAttribution.status == STATUS_CREDITED,
            ReferralAttribution.referred_gg_player_id.isnot(None),
        )
        .order_by(
            ReferralAttribution.credited_at.desc(),
            ReferralAttribution.id.desc(),
        )
        .all()
    )


def bonus_earned(row, cents_by_chat: dict[int, int]) -> bool:
    """True once the $100 obligation is met, including rows closed without a message."""
    if (
        getattr(row, "deposit_notify_suppressed", False)
        or getattr(row, "deposit_met_at", None) is not None
    ):
        return True
    chat_id = getattr(row, "referred_chat_id", None)
    if chat_id is None:
        return False
    return cents_by_chat.get(int(chat_id), 0) >= DEPOSIT_OBLIGATION_CENTS


def split_referral_bonus_ids(
    rows, cents_by_chat: dict[int, int]
) -> tuple[list[str], list[str]]:
    waiting: list[str] = []
    earned: list[str] = []
    for row in rows:
        player_id = (row.referred_gg_player_id or "").strip()
        if not player_id:
            continue
        if bonus_earned(row, cents_by_chat):
            earned.append(player_id)
        else:
            waiting.append(player_id)
    return waiting, earned


def get_referral_bonus_sections(
    *, club_id: int, referrer_chat_id: int
) -> tuple[list[str], list[str]]:
    """Credited player ids split into waiting and earned, newest first."""
    with get_db() as session:
        rows = _credited_rows(
            session, club_id=int(club_id), referrer_chat_id=int(referrer_chat_id)
        )
        chat_ids = [
            int(row.referred_chat_id)
            for row in rows
            if row.referred_chat_id is not None
        ]
        cents = bound_deposit_cents_by_chat(session, chat_ids)
        return split_referral_bonus_ids(rows, cents)


def _section_lines(header: str, player_ids: list[str]) -> list[str]:
    lines = [header, ""]
    if player_ids:
        lines.extend(f"• {player_id}" for player_id in player_ids)
    else:
        lines.append(EMPTY_SECTION)
    return lines


def _pack_lines(lines: list[str], max_chars: int) -> list[str]:
    messages: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            messages.append(current)
        current = line
    if current:
        messages.append(current)
    return messages


def format_my_referrals_messages(
    waiting_ids: list[str],
    earned_ids: list[str],
    *,
    max_chars: int = 4096,
) -> list[str]:
    """Render waiting and earned referral bonuses within Telegram's length limit."""
    if not waiting_ids and not earned_ids:
        return [NO_REFERRALS]
    lines = _section_lines(WAITING_HEADER, waiting_ids)
    lines.append("")
    lines.extend(_section_lines(EARNED_HEADER, earned_ids))
    return _pack_lines(lines, max_chars)


def format_referral_deposit_complete_message(player_id: str) -> str:
    return (
        f"🎉 Your referral {player_id} has completed their deposit obligations! "
        "30 credits will be added to your account!"
    )


def deposit_notify_action(
    *,
    suppressed: bool,
    telegram_sent: bool,
    slack_sent: bool,
    slack_skipped: bool,
    total_after: int,
    total_before: int,
    already_over_counts: bool,
) -> DepositAction:
    """Decide whether this check notifies, suppresses, retries Slack, or does nothing."""
    if suppressed or (telegram_sent and (slack_sent or slack_skipped)):
        return "noop"
    if telegram_sent:
        return "slack_only" if total_after >= DEPOSIT_OBLIGATION_CENTS else "noop"
    if total_after < DEPOSIT_OBLIGATION_CENTS:
        return "noop"
    if already_over_counts or total_before < DEPOSIT_OBLIGATION_CENTS:
        return "notify"
    return "suppress"


def attribution_should_suppress(row, total_cents: int) -> bool:
    """Existing credited referral already at $100 that has not been told."""
    if getattr(row, "status", None) != STATUS_CREDITED:
        return False
    if getattr(row, "deposit_notify_suppressed", False):
        return False
    if getattr(row, "deposit_telegram_sent_at", None) is not None:
        return False
    if getattr(row, "referred_chat_id", None) is None:
        return False
    if not (getattr(row, "referred_gg_player_id", None) or "").strip():
        return False
    return int(total_cents) >= DEPOSIT_OBLIGATION_CENTS


def support_account_username(club_id: int) -> str | None:
    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg is None:
        return None
    raw = str(cfg.referral_support_account or "").strip()
    if not raw:
        return None
    return raw if raw.startswith("@") else f"@{raw}"


def hop_message(club_id: int) -> str:
    username = support_account_username(club_id) or "@support"
    return f"Message {username} to get your support group."


def existing_player_message(invite_link: str | None) -> str:
    base = "You already have a support group. Message us there."
    link = (invite_link or "").strip()
    if link:
        return f"{base}\n\n{link}"
    return base


def ensure_referral_link(
    *,
    club_id: int,
    referrer_chat_id: int,
    referrer_gg_player_id: str,
) -> ReferralLink | None:
    """Insert or fetch the stable link for this support group. Returns detached row."""
    cid = int(club_id)
    chat_id = int(referrer_chat_id)
    player_id = (referrer_gg_player_id or "").strip()
    if not player_id:
        return None

    with get_db() as session:
        existing = (
            session.query(ReferralLink)
            .filter(
                ReferralLink.club_id == cid,
                ReferralLink.referrer_chat_id == chat_id,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.referrer_gg_player_id != player_id:
                existing.referrer_gg_player_id = player_id
                existing.updated_at = datetime.now(timezone.utc)
                session.flush()
            session.expunge(existing)
            return existing

    for _ in range(5):
        code = generate_referral_code()
        try:
            with get_db() as session:
                existing = (
                    session.query(ReferralLink)
                    .filter(
                        ReferralLink.club_id == cid,
                        ReferralLink.referrer_chat_id == chat_id,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    if existing.referrer_gg_player_id != player_id:
                        existing.referrer_gg_player_id = player_id
                        existing.updated_at = datetime.now(timezone.utc)
                        session.flush()
                    session.expunge(existing)
                    return existing
                row = ReferralLink(
                    code=code,
                    club_id=cid,
                    referrer_gg_player_id=player_id,
                    referrer_chat_id=chat_id,
                )
                session.add(row)
                session.flush()
                session.expunge(row)
                return row
        except IntegrityError:
            # Code collision or concurrent insert for same chat — retry.
            continue

    logger.error("referral ensure_link failed club_id=%s chat_id=%s", cid, chat_id)
    return None


def get_link_by_code(code: str) -> ReferralLink | None:
    with get_db() as session:
        row = (
            session.query(ReferralLink)
            .filter(ReferralLink.code == code.strip())
            .one_or_none()
        )
        if row is not None:
            session.expunge(row)
        return row


def _club_key_for_club_id(club_id: int) -> str | None:
    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    return cfg.club_key if cfg else None


def handle_start_payload(
    *,
    clicker_telegram_user_id: int,
    code: str,
) -> StartResult:
    """Process /start ref_CODE. First click wins; existing players get invite only."""
    link = get_link_by_code(code)
    if link is None:
        return StartResult(kind="unknown", text="That referral link is not valid.")

    club_key = _club_key_for_club_id(link.club_id)
    if club_key:
        existing = fetch_support_group_chat_by_club_player(
            club_key, int(clicker_telegram_user_id)
        )
        if existing is not None:
            return StartResult(
                kind="existing",
                text=existing_player_message(
                    existing.invite_link
                    if isinstance(existing.invite_link, str)
                    else None
                ),
                club_id=int(link.club_id),
            )

    try:
        with get_db() as session:
            already = (
                session.query(ReferralAttribution)
                .filter(
                    ReferralAttribution.club_id == int(link.club_id),
                    ReferralAttribution.clicker_telegram_user_id
                    == int(clicker_telegram_user_id),
                )
                .one_or_none()
            )
            if already is None:
                session.add(
                    ReferralAttribution(
                        referral_link_id=int(link.id),
                        club_id=int(link.club_id),
                        clicker_telegram_user_id=int(clicker_telegram_user_id),
                        status=STATUS_PENDING,
                    )
                )
                session.flush()
    except IntegrityError:
        # First click already recorded (race or second code).
        pass

    return StartResult(
        kind="hop",
        text=hop_message(link.club_id),
        club_id=int(link.club_id),
    )


def pending_hop_for_user(clicker_telegram_user_id: int) -> StartResult | None:
    """If the user has a pending attribution and still no group, return hop copy."""
    uid = int(clicker_telegram_user_id)
    with get_db() as session:
        rows = (
            session.query(ReferralAttribution)
            .filter(
                ReferralAttribution.clicker_telegram_user_id == uid,
                ReferralAttribution.status == STATUS_PENDING,
            )
            .order_by(ReferralAttribution.created_at.desc())
            .all()
        )
        pending = list(rows)
        for row in pending:
            session.expunge(row)

    for attr in pending:
        club_key = _club_key_for_club_id(attr.club_id)
        if club_key:
            existing = fetch_support_group_chat_by_club_player(club_key, uid)
            if existing is not None:
                continue
        return StartResult(
            kind="hop",
            text=hop_message(attr.club_id),
            club_id=int(attr.club_id),
        )
    return None


def _first_credit_acks(
    *,
    referrer_chat_id: int,
    referred_chat_id: int,
    referred_gg_player_id: str,
    referrer_gg_player_id: str,
) -> list[GroupMessage]:
    return [
        GroupMessage(
            chat_id=int(referrer_chat_id),
            text=f"Your referral {referred_gg_player_id} joined the club.",
        ),
        GroupMessage(
            chat_id=int(referred_chat_id),
            text=f"Referred by {referrer_gg_player_id}.",
        ),
    ]


def on_player_id_bound(
    *,
    chat_id: int,
    club_id: int | None,
    gg_player_id: str | None,
    previous_gg_player_id: str | None,
    conflict: bool = False,
) -> ReferralBindMessages:
    """Hook after title bind success or same-club conflict. Returns messages to send."""
    if club_id is None:
        return ReferralBindMessages()
    cid = int(club_id)
    chat = int(chat_id)
    messages = ReferralBindMessages()

    sgc = fetch_support_group_chat_by_telegram_chat_id(chat)
    player_tg_id = (
        int(sgc.player_telegram_user_id)
        if sgc is not None and sgc.player_telegram_user_id is not None
        else None
    )

    if conflict:
        if player_tg_id is None:
            return ReferralBindMessages()
        with get_db() as session:
            attr = (
                session.query(ReferralAttribution)
                .filter(
                    ReferralAttribution.club_id == cid,
                    ReferralAttribution.clicker_telegram_user_id == player_tg_id,
                    ReferralAttribution.status == STATUS_PENDING,
                )
                .one_or_none()
            )
            if attr is not None:
                attr.status = STATUS_CLOSED_DUPLICATE
                attr.updated_at = datetime.now(timezone.utc)
        return ReferralBindMessages()

    new_id = (gg_player_id or "").strip()
    if not new_id:
        return ReferralBindMessages()
    old_id = (previous_gg_player_id or "").strip() or None
    if old_id == new_id:
        return ReferralBindMessages()

    now = datetime.now(timezone.utc)
    just_credited = False

    with get_db() as session:
        # Referrer group retitle: update link player id and ping referred groups.
        link = (
            session.query(ReferralLink)
            .filter(
                ReferralLink.club_id == cid,
                ReferralLink.referrer_chat_id == chat,
            )
            .one_or_none()
        )
        if link is not None and link.referrer_gg_player_id != new_id:
            prev_referrer = link.referrer_gg_player_id
            link.referrer_gg_player_id = new_id
            link.updated_at = now
            credited = (
                session.query(ReferralAttribution)
                .filter(
                    ReferralAttribution.referral_link_id == link.id,
                    ReferralAttribution.status == STATUS_CREDITED,
                    ReferralAttribution.referred_chat_id.isnot(None),
                )
                .all()
            )
            for attr in credited:
                if attr.referred_chat_id is None:
                    continue
                messages.append(
                    GroupMessage(
                        chat_id=int(attr.referred_chat_id),
                        text=(f"Referred by was {prev_referrer} and now is {new_id}."),
                    )
                )

        # Referred group: credit pending or update credited id.
        if player_tg_id is not None:
            attr = (
                session.query(ReferralAttribution)
                .filter(
                    ReferralAttribution.club_id == cid,
                    ReferralAttribution.clicker_telegram_user_id == player_tg_id,
                )
                .one_or_none()
            )
            if attr is not None and attr.status == STATUS_PENDING:
                link_row = session.query(ReferralLink).get(attr.referral_link_id)
                attr.referred_gg_player_id = new_id
                attr.referred_chat_id = chat
                attr.status = STATUS_CREDITED
                attr.credited_at = now
                attr.acked_at = now
                attr.updated_at = now
                just_credited = True
                if link_row is not None:
                    messages.extend(
                        _first_credit_acks(
                            referrer_chat_id=int(link_row.referrer_chat_id),
                            referred_chat_id=chat,
                            referred_gg_player_id=new_id,
                            referrer_gg_player_id=link_row.referrer_gg_player_id,
                        )
                    )
            elif (
                attr is not None
                and attr.status == STATUS_CREDITED
                and attr.referred_gg_player_id
                and attr.referred_gg_player_id != new_id
            ):
                prev_referred = attr.referred_gg_player_id
                attr.referred_gg_player_id = new_id
                attr.referred_chat_id = chat
                attr.updated_at = now
                link_row = session.query(ReferralLink).get(attr.referral_link_id)
                if link_row is not None:
                    messages.append(
                        GroupMessage(
                            chat_id=int(link_row.referrer_chat_id),
                            text=(
                                f"Your referred player's id was {prev_referred} "
                                f"and now is {new_id}."
                            ),
                        )
                    )

    messages.just_credited = just_credited
    return messages


def _fetch_deposit_snapshot(chat_id: int) -> DepositSnapshot | None:
    with get_db() as session:
        attr = (
            session.query(ReferralAttribution)
            .filter(
                ReferralAttribution.referred_chat_id == int(chat_id),
                ReferralAttribution.status == STATUS_CREDITED,
            )
            .order_by(ReferralAttribution.id.asc())
            .first()
        )
        if attr is None:
            return None
        link = session.query(ReferralLink).get(attr.referral_link_id)
        if link is None:
            return None
        player_id = (attr.referred_gg_player_id or "").strip()
        total = bound_deposit_cents(session, int(chat_id))
        return DepositSnapshot(
            attribution_id=int(attr.id),
            club_id=int(attr.club_id),
            referrer_chat_id=int(link.referrer_chat_id),
            referred_player_id=player_id,
            suppressed=bool(attr.deposit_notify_suppressed),
            telegram_sent=attr.deposit_telegram_sent_at is not None,
            slack_sent=attr.deposit_slack_sent_at is not None,
            slack_skipped=attr.deposit_slack_skipped_at is not None,
            total_cents=total,
        )


def _save_deposit_flags(attribution_id: int, **flags) -> None:
    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(ReferralAttribution, int(attribution_id))
        if row is None:
            return
        if flags.get("met") and row.deposit_met_at is None:
            row.deposit_met_at = now
        if flags.get("suppressed"):
            row.deposit_notify_suppressed = True
            if row.deposit_met_at is None:
                row.deposit_met_at = now
        if flags.get("telegram_sent") and row.deposit_telegram_sent_at is None:
            row.deposit_telegram_sent_at = now
        if flags.get("slack_sent") and row.deposit_slack_sent_at is None:
            row.deposit_slack_sent_at = now
        if flags.get("slack_skipped") and row.deposit_slack_skipped_at is None:
            row.deposit_slack_skipped_at = now
        row.updated_at = now


async def _deliver_referrer_message(bot, chat_id: int, text: str) -> bool:
    sender = bot
    if sender is None:
        from telegram import Bot

        from bot.services.payment_group_notify import resolve_support_bot_token

        token = resolve_support_bot_token()
        if not token:
            logger.warning("referral deposit: no support bot token")
            return False
        sender = Bot(token=token)
    try:
        await sender.send_message(chat_id=int(chat_id), text=text)
    except Exception:
        logger.warning(
            "referral deposit telegram failed chat_id=%s", chat_id, exc_info=True
        )
        return False
    return True


async def _post_referral_deposit_slack(snap: DepositSnapshot) -> bool:
    from bot.services.escalation_notification import (
        REASON_REFERRAL_DEPOSIT_COMPLETE,
        format_referral_deposit_slack_text,
        notify_escalation_slack,
    )

    text = format_referral_deposit_slack_text(
        club_id=snap.club_id,
        chat_id=snap.referrer_chat_id,
        title=None,
        referred_player_id=snap.referred_player_id,
    )
    try:
        ok, _event_id = await notify_escalation_slack(
            REASON_REFERRAL_DEPOSIT_COMPLETE,
            club_id=snap.club_id,
            chat_id=snap.referrer_chat_id,
            slack_text=text,
        )
    except Exception:
        logger.warning(
            "referral deposit slack failed attribution_id=%s",
            snap.attribution_id,
            exc_info=True,
        )
        return False
    if ok:
        _save_deposit_flags(snap.attribution_id, slack_sent=True)
    return bool(ok)


async def _maybe_notify_referral_deposit(
    chat_id: int,
    *,
    already_over_counts: bool,
    bound_amount_cents: int,
    bound_is_test: bool,
    bot,
) -> None:
    snap = _fetch_deposit_snapshot(int(chat_id))
    if snap is None or not snap.referred_player_id:
        return
    contrib = 0 if bound_is_test else max(int(bound_amount_cents), 0)
    total_before = max(0, snap.total_cents - contrib)
    action = deposit_notify_action(
        suppressed=snap.suppressed,
        telegram_sent=snap.telegram_sent,
        slack_sent=snap.slack_sent,
        slack_skipped=snap.slack_skipped,
        total_after=snap.total_cents,
        total_before=total_before,
        already_over_counts=already_over_counts,
    )
    if action == "noop":
        return
    if action == "suppress":
        _save_deposit_flags(snap.attribution_id, suppressed=True)
        return
    _save_deposit_flags(snap.attribution_id, met=True)
    if action == "notify":
        text = format_referral_deposit_complete_message(snap.referred_player_id)
        sent = await _deliver_referrer_message(bot, snap.referrer_chat_id, text)
        if not sent:
            return
        _save_deposit_flags(snap.attribution_id, telegram_sent=True)
    from bot.services.escalation_notification import escalation_notification_enabled

    if not escalation_notification_enabled(snap.club_id):
        _save_deposit_flags(snap.attribution_id, slack_skipped=True)
        return
    await _post_referral_deposit_slack(snap)


async def maybe_notify_referral_deposit(
    chat_id: int,
    *,
    already_over_counts: bool,
    bound_amount_cents: int = 0,
    bound_is_test: bool = False,
    bot=None,
) -> None:
    """Tell the referrer once when this referred group reaches $100 in bound deposits."""
    try:
        await _maybe_notify_referral_deposit(
            int(chat_id),
            already_over_counts=already_over_counts,
            bound_amount_cents=int(bound_amount_cents),
            bound_is_test=bool(bound_is_test),
            bot=bot,
        )
    except Exception:
        logger.exception("referral deposit notify failed chat_id=%s", chat_id)
