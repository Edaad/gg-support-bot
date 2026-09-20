"""Referral deep links: codes, first-click attribution, hop copy, bind/retitle acks."""

from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.exc import IntegrityError

from club_gc_settings import get_club_gc_config_by_link_club_id
from db.connection import get_db
from db.models import ReferralAttribution, ReferralLink
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
    "This is your unique referral link.\n\n"
    "Share it with your friends. If they join the club, you'll get exclusive rewards!!\n\n"
    "{url}"
)


@dataclass(frozen=True)
class GroupMessage:
    chat_id: int
    text: str


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


def get_credited_referral_player_ids(
    *, club_id: int, referrer_chat_id: int
) -> list[str]:
    """Return current credited player ids for a referrer group, newest first."""
    with get_db() as session:
        rows = (
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
        return [
            row.referred_gg_player_id.strip()
            for row in rows
            if row.referred_gg_player_id and row.referred_gg_player_id.strip()
        ]


def format_my_referrals_messages(
    player_ids: list[str], *, max_chars: int = 4096
) -> list[str]:
    """Render all credited referrals without exceeding Telegram message limits."""
    if not player_ids:
        return ["You haven't referred any players yet."]

    header = f"You have referred {len(player_ids)} players:"
    messages: list[str] = []
    current = header
    for player_id in player_ids:
        line = f"• {player_id}"
        candidate = (
            f"{current}\n\n{line}" if current == header else f"{current}\n{line}"
        )
        if len(candidate) <= max_chars:
            current = candidate
            continue
        messages.append(current)
        current = line
    messages.append(current)
    return messages


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
) -> list[GroupMessage]:
    """Hook after title bind success or same-club conflict. Returns messages to send."""
    if club_id is None:
        return []
    cid = int(club_id)
    chat = int(chat_id)
    messages: list[GroupMessage] = []

    sgc = fetch_support_group_chat_by_telegram_chat_id(chat)
    player_tg_id = (
        int(sgc.player_telegram_user_id)
        if sgc is not None and sgc.player_telegram_user_id is not None
        else None
    )

    if conflict:
        if player_tg_id is None:
            return []
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
        return []

    new_id = (gg_player_id or "").strip()
    if not new_id:
        return []
    old_id = (previous_gg_player_id or "").strip() or None
    if old_id == new_id:
        return []

    now = datetime.now(timezone.utc)

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

    return messages
