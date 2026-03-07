"""
One-time migration from old schema (user_commands / group_club) to new
structured tables (clubs, payment_methods, payment_sub_options, groups, custom_commands).

Usage:
    DATABASE_URL=... python -m db.migrate
"""

import os
import sys

import psycopg2
from urllib.parse import urlparse

from db.connection import init_engine, get_db
from db.models import Base, Club, PaymentMethod, Group, CustomCommand

DEPOSIT_CMD_MAP = {
    "botvenmo": ("Venmo", "venmo"),
    "botzelle": ("Zelle", "zelle"),
    "botstripe": ("Stripe (Apple Pay / Debit Card)", "stripe"),
    "botcashapp": ("Cashapp", "cashapp"),
    "botcrypto": ("Crypto", "crypto"),
}

CASHOUT_CMD_MAP = {
    "botcashoutzelle": ("Zelle", "zelle"),
    "botcashoutcrypto": ("Crypto", "crypto"),
    "botcashoutcashapp": ("Cashapp", "cashapp"),
    "botcashoutvenmo": ("Venmo", "venmo"),
}

SPECIAL_CMDS = {"botwelcome", "list"} | set(DEPOSIT_CMD_MAP) | set(CASHOUT_CMD_MAP)


def _legacy_connection():
    url = os.getenv("DATABASE_URL", "")
    if not url:
        sys.exit("DATABASE_URL not set")
    parsed = urlparse(url)
    return psycopg2.connect(
        database=parsed.path[1:],
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port,
    )


def _read_legacy_data():
    conn = _legacy_connection()
    users = {}
    groups = {}
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, command_name, command_type, content, file_id, caption "
                "FROM user_commands"
            )
            for uid, cmd_name, cmd_type, content, file_id, caption in cur.fetchall():
                users.setdefault(int(uid), {})[cmd_name] = {
                    "type": cmd_type or "text",
                    "content": content,
                    "file_id": file_id,
                    "caption": caption,
                }
            cur.execute("SELECT chat_id, club_user_id FROM group_club")
            for chat_id, club_uid in cur.fetchall():
                groups[int(chat_id)] = int(club_uid)
    conn.close()
    return users, groups


def _make_payment_method(cmd_name, cmd_data, direction, name, slug, sort_order):
    rtype = cmd_data.get("type", "text")
    return PaymentMethod(
        direction=direction,
        name=name,
        slug=slug,
        response_type=rtype,
        response_text=cmd_data.get("content") if rtype == "text" else None,
        response_file_id=cmd_data.get("file_id") if rtype == "photo" else None,
        response_caption=cmd_data.get("caption") if rtype == "photo" else None,
        has_sub_options=(slug == "crypto"),
        is_active=True,
        sort_order=sort_order,
    )


def migrate():
    engine = init_engine()
    Base.metadata.create_all(engine)

    users, legacy_groups = _read_legacy_data()
    club_user_ids = set(legacy_groups.values()) | set(users.keys())

    with get_db() as session:
        existing = {c.telegram_user_id for c in session.query(Club).all()}
        if existing:
            print(f"Skipping {len(existing)} clubs already migrated")

        for uid in sorted(club_user_ids):
            if uid in existing:
                continue
            cmds = users.get(uid, {})

            welcome_data = cmds.get("botwelcome", {})
            list_data = cmds.get("list", {})

            club = Club(
                name=f"Club {uid}",
                telegram_user_id=uid,
                welcome_type=welcome_data.get("type", "text"),
                welcome_text=welcome_data.get("content"),
                welcome_file_id=welcome_data.get("file_id"),
                welcome_caption=welcome_data.get("caption"),
                list_type=list_data.get("type", "text"),
                list_text=list_data.get("content"),
                list_file_id=list_data.get("file_id"),
                list_caption=list_data.get("caption"),
                is_active=True,
            )
            session.add(club)
            session.flush()

            sort = 0
            for cmd_key, (display_name, slug) in DEPOSIT_CMD_MAP.items():
                if cmd_key in cmds:
                    pm = _make_payment_method(
                        cmd_key, cmds[cmd_key], "deposit", display_name, slug, sort
                    )
                    pm.club_id = club.id
                    session.add(pm)
                    sort += 1

            sort = 0
            for cmd_key, (display_name, slug) in CASHOUT_CMD_MAP.items():
                if cmd_key in cmds:
                    pm = _make_payment_method(
                        cmd_key, cmds[cmd_key], "cashout", display_name, slug, sort
                    )
                    pm.club_id = club.id
                    session.add(pm)
                    sort += 1

            for cmd_name, cmd_data in cmds.items():
                if cmd_name in SPECIAL_CMDS:
                    continue
                rtype = cmd_data.get("type", "text")
                cc = CustomCommand(
                    club_id=club.id,
                    command_name=cmd_name,
                    response_type=rtype,
                    response_text=cmd_data.get("content") if rtype == "text" else None,
                    response_file_id=cmd_data.get("file_id") if rtype == "photo" else None,
                    response_caption=cmd_data.get("caption") if rtype == "photo" else None,
                    is_active=True,
                )
                session.add(cc)

            print(f"  Migrated club {uid} (id={club.id})")

        uid_to_club = {
            c.telegram_user_id: c.id for c in session.query(Club).all()
        }
        for chat_id, club_uid in legacy_groups.items():
            club_id = uid_to_club.get(club_uid)
            if club_id is None:
                continue
            exists = session.query(Group).filter_by(chat_id=chat_id).first()
            if not exists:
                session.add(Group(chat_id=chat_id, club_id=club_id))

        print("Migration complete")


if __name__ == "__main__":
    migrate()
