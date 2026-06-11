"""Remap Telegram group chat ids in Postgres (basic → supergroup migration)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from db.connection import get_db
from db.models import Group

logger = logging.getLogger(__name__)


def is_legacy_basic_chat_id(chat_id: int) -> bool:
    """True for Bot API basic-group ids (negative, not ``-100…``)."""
    cid = int(chat_id)
    if cid >= 0:
        return False
    return not str(cid).startswith("-100")


def _variant_in_clause(variants: list[int]) -> tuple[str, dict[str, int]]:
    if not variants:
        raise ValueError("variants must not be empty")
    keys = [f"v{i}" for i in range(len(variants))]
    clause = ", ".join(f":{k}" for k in keys)
    params = {k: int(v) for k, v in zip(keys, variants)}
    return clause, params


def remap_chat_id_in_db(old_id: int, new_id: int) -> dict[str, int]:
    """Replace ``old_id`` with ``new_id`` across chat-keyed tables."""
    old_id = int(old_id)
    new_id = int(new_id)
    if old_id == new_id:
        return {}

    in_clause, in_params = _variant_in_clause([old_id])
    counts: dict[str, int] = {}

    with get_db() as session:
        def _run(label: str, sql: str, extra: dict[str, Any] | None = None) -> None:
            params: dict[str, Any] = {"new_id": new_id, "old_id": old_id, **in_params}
            if extra:
                params.update(extra)
            result = session.execute(text(sql), params)
            n = int(result.rowcount or 0)
            if n:
                counts[label] = counts.get(label, 0) + n

        old_group = session.execute(
            text(f"SELECT chat_id FROM groups WHERE chat_id IN ({in_clause}) LIMIT 1"),
            in_params,
        ).first()
        new_group = session.execute(
            text("SELECT chat_id FROM groups WHERE chat_id = :new_id LIMIT 1"),
            {"new_id": new_id},
        ).first()

        if old_group and new_group:
            _run(
                "groups.delete_legacy",
                f"DELETE FROM groups WHERE chat_id IN ({in_clause})",
            )
        elif old_group:
            _run(
                "groups",
                f"UPDATE groups SET chat_id = :new_id WHERE chat_id IN ({in_clause})",
            )

        _run(
            "player_details",
            f"""
            UPDATE player_details
            SET chat_ids = (
                SELECT COALESCE(array_agg(DISTINCT val), '{{}}'::bigint[])
                FROM (
                    SELECT CASE
                        WHEN x IN ({in_clause}) THEN :new_id
                        ELSE x
                    END AS val
                    FROM unnest(chat_ids) AS x
                ) sub
            )
            WHERE chat_ids @> ARRAY[:old_id]::bigint[]
            """,
        )

        def _remap_scalar_chat_col(table: str, col: str, *, unique: bool = False) -> None:
            label = f"{table}.{col}"
            old_row = session.execute(
                text(f"SELECT 1 FROM {table} WHERE {col} = :old_id LIMIT 1"),
                {"old_id": old_id},
            ).first()
            if not old_row:
                return
            if unique:
                new_row = session.execute(
                    text(f"SELECT 1 FROM {table} WHERE {col} = :new_id LIMIT 1"),
                    {"new_id": new_id},
                ).first()
                if new_row:
                    _run(f"{label}.delete_legacy", f"DELETE FROM {table} WHERE {col} = :old_id")
                    return
            _run(label, f"UPDATE {table} SET {col} = :new_id WHERE {col} = :old_id")

        for table, columns in (
            ("player_activities", ("chat_id",)),
            ("cooldown_bypasses", ("chat_id",)),
            ("broadcast_group_members", ("chat_id",)),
            ("cashier_cashout_jobs", ("chat_id",)),
            ("support_group_chats", ("telegram_chat_id",)),
            ("stripe_checkout_sessions", ("telegram_chat_id",)),
            ("venmo_payments", ("telegram_chat_id", "notification_chat_id")),
            ("venmo_payer_bindings", ("telegram_chat_id",)),
            ("cashapp_payments", ("telegram_chat_id", "notification_chat_id")),
            ("cashapp_payer_bindings", ("telegram_chat_id",)),
            ("paypal_payments", ("telegram_chat_id", "notification_chat_id")),
            ("paypal_payer_bindings", ("telegram_chat_id",)),
            ("zelle_payments", ("telegram_chat_id", "notification_chat_id")),
            ("zelle_payer_bindings", ("telegram_chat_id",)),
            ("crypto_payments", ("telegram_chat_id", "notification_chat_id")),
            ("crypto_wallet_bindings", ("telegram_chat_id",)),
            ("payment_method_bind_attempts", ("telegram_chat_id",)),
            ("group_payment_method_bindings", ("telegram_chat_id",)),
        ):
            for col in columns:
                _remap_scalar_chat_col(table, col)

        _remap_scalar_chat_col("stripe_customers", "telegram_chat_id", unique=True)

        session.commit()

    return counts


def find_legacy_group_chat_id(
    *,
    new_chat_id: int,
    title: str | None,
    club_id: int | None,
) -> int | None:
    """Legacy ``groups.chat_id`` for the same club/title before supergroup migration."""
    name = (title or "").strip()
    if not name or club_id is None:
        return None

    with get_db() as session:
        rows = (
            session.query(Group.chat_id)
            .filter(Group.club_id == int(club_id), Group.name == name)
            .all()
        )
    for (raw_cid,) in rows:
        cid = int(raw_cid)
        if cid != int(new_chat_id) and is_legacy_basic_chat_id(cid):
            return cid
    return None


def try_silent_supergroup_remap(
    old_id: int,
    new_id: int,
    *,
    chat_title: str | None = None,
) -> dict[str, int]:
    """Remap Postgres ids after basic → supergroup migration; no Telegram messages."""
    from bot.services.club import update_group_name

    counts = remap_chat_id_in_db(old_id, new_id)
    if chat_title:
        update_group_name(int(new_id), chat_title)
    logger.info(
        "Silent supergroup remap %s -> %s (%s rows)",
        old_id,
        new_id,
        sum(counts.values()),
    )
    return counts
