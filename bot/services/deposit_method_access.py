"""Per-support-group deposit method blacklist / whitelist + public visibility."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Literal, Optional, Sequence

from config import ADMIN_USER_IDS
from db.connection import get_db
from db.models import Club, ClubLinkedAccount, ClubPaymentMethod, GroupDepositMethodAccess
from bot.services.club import get_group_title_for_chat, is_club_staff

logger = logging.getLogger(__name__)

AccessType = Literal["blacklist", "whitelist"]
AccessAction = Literal["blacklist", "whitelist", "remove"]


@dataclass(frozen=True)
class AccessEntry:
    id: int
    telegram_chat_id: int
    club_id: int
    club_payment_method_id: int
    access_type: AccessType
    method_name: str
    method_slug: str
    group_title: Optional[str]


def can_manage_deposit_access(telegram_user_id: int, club_id: int) -> bool:
    if telegram_user_id in ADMIN_USER_IDS:
        return True
    return is_club_staff(telegram_user_id, club_id)


def can_use_deposit_access_commands(telegram_user_id: int) -> bool:
    if telegram_user_id in ADMIN_USER_IDS:
        return True
    with get_db() as session:
        if (
            session.query(Club.id)
            .filter(
                Club.is_active.is_(True),
                Club.telegram_user_id == telegram_user_id,
            )
            .first()
        ):
            return True
        return (
            session.query(ClubLinkedAccount.id)
            .join(Club)
            .filter(
                ClubLinkedAccount.telegram_user_id == telegram_user_id,
                Club.is_active.is_(True),
            )
            .first()
            is not None
        )


def _staff_club_ids(session, telegram_user_id: int) -> list[int]:
    owned = [
        int(r[0])
        for r in session.query(Club.id)
        .filter(
            Club.is_active.is_(True),
            Club.telegram_user_id == telegram_user_id,
        )
        .all()
    ]
    linked = [
        int(r[0])
        for r in session.query(ClubLinkedAccount.club_id)
        .join(Club)
        .filter(
            ClubLinkedAccount.telegram_user_id == telegram_user_id,
            Club.is_active.is_(True),
        )
        .all()
    ]
    return sorted(set(owned + linked))


def _access_map_for_chat(session, chat_id: int) -> dict[int, AccessType]:
    rows = (
        session.query(
            GroupDepositMethodAccess.club_payment_method_id,
            GroupDepositMethodAccess.access_type,
        )
        .filter_by(telegram_chat_id=int(chat_id))
        .all()
    )
    out: dict[int, AccessType] = {}
    for method_id, access_type in rows:
        if access_type in ("blacklist", "whitelist"):
            out[int(method_id)] = access_type  # type: ignore[assignment]
    return out


def method_visible_for_chat(
    *,
    is_public: bool,
    access_type: Optional[AccessType],
) -> bool:
    """Visibility matrix: public − blacklist; private + whitelist only; blacklist wins."""
    if access_type == "blacklist":
        return False
    if is_public:
        return True
    return access_type == "whitelist"


def filter_deposit_methods_for_chat(
    chat_id: int, methods: Sequence[dict]
) -> List[dict]:
    if not methods:
        return []
    method_ids = [int(m["id"]) for m in methods if m.get("id") is not None]
    try:
        with get_db() as session:
            access = _access_map_for_chat(session, chat_id)
            # Prefer is_public on dict; fall back to DB for older callers.
            missing_ids = [
                mid
                for mid, m in zip(method_ids, methods)
                if "is_public" not in m
            ]
            public_by_id: dict[int, bool] = {}
            if missing_ids:
                rows = (
                    session.query(ClubPaymentMethod.id, ClubPaymentMethod.is_public)
                    .filter(ClubPaymentMethod.id.in_(missing_ids))
                    .all()
                )
                public_by_id = {int(r[0]): bool(r[1]) for r in rows}
    except Exception:
        # Migration not applied yet, or table missing — do not block deposits.
        logger.exception(
            "filter_deposit_methods_for_chat failed chat_id=%s; showing all methods",
            chat_id,
        )
        return list(methods)

    result: List[dict] = []
    for m in methods:
        mid = int(m["id"])
        is_public = bool(m["is_public"]) if "is_public" in m else public_by_id.get(mid, True)
        if method_visible_for_chat(
            is_public=is_public,
            access_type=access.get(mid),
        ):
            result.append(m)
    return result


def is_deposit_method_allowed_for_chat(chat_id: int, method_id: int) -> bool:
    try:
        with get_db() as session:
            method = session.query(ClubPaymentMethod).get(int(method_id))
            if not method or method.direction != "deposit" or not method.is_active:
                return False
            access = _access_map_for_chat(session, chat_id)
            return method_visible_for_chat(
                is_public=bool(getattr(method, "is_public", True)),
                access_type=access.get(int(method_id)),
            )
    except Exception:
        logger.exception(
            "is_deposit_method_allowed_for_chat failed chat_id=%s method_id=%s; allowing",
            chat_id,
            method_id,
        )
        return True


def methods_for_action(
    club_id: int, chat_id: int, action: AccessAction
) -> List[dict]:
    """Active deposit methods eligible for the given /depositaccess action."""
    with get_db() as session:
        methods = (
            session.query(ClubPaymentMethod)
            .filter_by(club_id=int(club_id), direction="deposit", is_active=True)
            .order_by(ClubPaymentMethod.sort_order, ClubPaymentMethod.id)
            .all()
        )
        access = _access_map_for_chat(session, chat_id)
        result: List[dict] = []
        for m in methods:
            mid = int(m.id)
            current = access.get(mid)
            is_public = bool(getattr(m, "is_public", True))
            if action == "blacklist":
                if is_public and current != "blacklist":
                    result.append({"id": mid, "name": m.name, "slug": m.slug})
            elif action == "whitelist":
                if (not is_public) and current != "whitelist":
                    result.append({"id": mid, "name": m.name, "slug": m.slug})
            elif action == "remove":
                if current is not None:
                    result.append(
                        {
                            "id": mid,
                            "name": m.name,
                            "slug": m.slug,
                            "access_type": current,
                        }
                    )
        return result


def upsert_access(
    *,
    telegram_chat_id: int,
    club_id: int,
    club_payment_method_id: int,
    access_type: AccessType,
    created_by_telegram_user_id: Optional[int] = None,
) -> AccessEntry:
    with get_db() as session:
        method = session.query(ClubPaymentMethod).get(int(club_payment_method_id))
        if not method or int(method.club_id) != int(club_id):
            raise ValueError("Payment method not found for this club.")
        if method.direction != "deposit":
            raise ValueError("Only deposit methods can be blacklisted or whitelisted.")

        row = (
            session.query(GroupDepositMethodAccess)
            .filter_by(
                telegram_chat_id=int(telegram_chat_id),
                club_payment_method_id=int(club_payment_method_id),
            )
            .first()
        )
        if row:
            row.access_type = access_type
            row.club_id = int(club_id)
            if created_by_telegram_user_id is not None:
                row.created_by_telegram_user_id = int(created_by_telegram_user_id)
        else:
            row = GroupDepositMethodAccess(
                telegram_chat_id=int(telegram_chat_id),
                club_id=int(club_id),
                club_payment_method_id=int(club_payment_method_id),
                access_type=access_type,
                created_by_telegram_user_id=(
                    int(created_by_telegram_user_id)
                    if created_by_telegram_user_id is not None
                    else None
                ),
            )
            session.add(row)
        session.flush()
        entry_id = int(row.id)
        method_name = method.name
        method_slug = method.slug

    title, _ = get_group_title_for_chat(int(telegram_chat_id))
    return AccessEntry(
        id=entry_id,
        telegram_chat_id=int(telegram_chat_id),
        club_id=int(club_id),
        club_payment_method_id=int(club_payment_method_id),
        access_type=access_type,
        method_name=method_name,
        method_slug=method_slug,
        group_title=title,
    )


def delete_access(
    *,
    telegram_chat_id: int,
    club_payment_method_id: int,
) -> Optional[AccessEntry]:
    with get_db() as session:
        row = (
            session.query(GroupDepositMethodAccess)
            .filter_by(
                telegram_chat_id=int(telegram_chat_id),
                club_payment_method_id=int(club_payment_method_id),
            )
            .first()
        )
        if not row:
            return None
        method = session.query(ClubPaymentMethod).get(int(club_payment_method_id))
        entry = AccessEntry(
            id=int(row.id),
            telegram_chat_id=int(row.telegram_chat_id),
            club_id=int(row.club_id),
            club_payment_method_id=int(row.club_payment_method_id),
            access_type=row.access_type,  # type: ignore[arg-type]
            method_name=method.name if method else "?",
            method_slug=method.slug if method else "?",
            group_title=None,
        )
        session.delete(row)
        session.flush()

    title, _ = get_group_title_for_chat(int(telegram_chat_id))
    return AccessEntry(
        id=entry.id,
        telegram_chat_id=entry.telegram_chat_id,
        club_id=entry.club_id,
        club_payment_method_id=entry.club_payment_method_id,
        access_type=entry.access_type,
        method_name=entry.method_name,
        method_slug=entry.method_slug,
        group_title=title,
    )


def list_access_entries(actor_user_id: int) -> List[AccessEntry]:
    with get_db() as session:
        q = session.query(GroupDepositMethodAccess, ClubPaymentMethod).join(
            ClubPaymentMethod,
            ClubPaymentMethod.id == GroupDepositMethodAccess.club_payment_method_id,
        )
        if actor_user_id not in ADMIN_USER_IDS:
            club_ids = _staff_club_ids(session, actor_user_id)
            if not club_ids:
                return []
            q = q.filter(GroupDepositMethodAccess.club_id.in_(club_ids))
        rows = q.order_by(
            GroupDepositMethodAccess.club_id,
            GroupDepositMethodAccess.telegram_chat_id,
            ClubPaymentMethod.sort_order,
            ClubPaymentMethod.id,
        ).all()
        raw = [
            (
                int(access.id),
                int(access.telegram_chat_id),
                int(access.club_id),
                int(access.club_payment_method_id),
                access.access_type,
                method.name,
                method.slug,
            )
            for access, method in rows
        ]

    result: List[AccessEntry] = []
    for (
        entry_id,
        chat_id,
        club_id,
        method_id,
        access_type,
        method_name,
        method_slug,
    ) in raw:
        title, _ = get_group_title_for_chat(chat_id)
        result.append(
            AccessEntry(
                id=entry_id,
                telegram_chat_id=chat_id,
                club_id=club_id,
                club_payment_method_id=method_id,
                access_type=access_type,  # type: ignore[arg-type]
                method_name=method_name,
                method_slug=method_slug,
                group_title=title,
            )
        )
    return result

def format_access_list(entries: Iterable[AccessEntry]) -> str:
    rows = list(entries)
    if not rows:
        return "No deposit method blacklist or whitelist entries."
    lines = ["Deposit method access:"]
    for e in rows:
        title = e.group_title or f"chat {e.telegram_chat_id}"
        lines.append(
            f"• {title}\n  {e.access_type} — {e.method_name} ({e.method_slug})"
        )
    return "\n".join(lines)
