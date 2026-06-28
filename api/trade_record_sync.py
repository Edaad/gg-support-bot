"""Sync trade-record identities to Postgres and gg-computer."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.trade_record_parser import ParsedIdentity
from bot.services.gg_computer import bulk_upsert_player_details, gg_computer_base_url

logger = logging.getLogger(__name__)


@dataclass
class IdentitySyncReport:
    identities_extracted: int = 0
    postgres_inserted: int = 0
    postgres_updated: int = 0
    gg_computer_upserted: int = 0
    gg_computer_modified: int = 0
    gg_computer_skipped: int = 0
    gg_computer_error: str | None = None
    skipped_identities: list[str] = field(default_factory=list)


def sync_identities(
    session: Session,
    *,
    club_id: int,
    club_slug: str,
    identities: list[ParsedIdentity],
) -> IdentitySyncReport:
    report = IdentitySyncReport(identities_extracted=len(identities))
    slug = club_slug.strip().lower()

    items = [
        {"gg_id": ident.gg_player_id, "nickname": ident.nickname}
        for ident in identities
    ]

    for ident in identities:
        gg_id = ident.gg_player_id
        nickname = ident.nickname.strip()
        existing = session.execute(
            text(
                """
                SELECT id, gg_nickname, chat_ids
                FROM player_details
                WHERE club_id = :club_id AND gg_player_id = :gg_id
                """
            ),
            {"club_id": int(club_id), "gg_id": gg_id},
        ).first()

        if existing:
            session.execute(
                text(
                    """
                    UPDATE player_details
                    SET gg_nickname = :nickname
                    WHERE club_id = :club_id AND gg_player_id = :gg_id
                    """
                ),
                {
                    "club_id": int(club_id),
                    "gg_id": gg_id,
                    "nickname": nickname,
                },
            )
            report.postgres_updated += 1
        else:
            session.execute(
                text(
                    """
                    INSERT INTO player_details (chat_ids, gg_player_id, gg_nickname, club_id)
                    VALUES ('{}'::bigint[], :gg_id, :nickname, :club_id)
                    """
                ),
                {
                    "club_id": int(club_id),
                    "gg_id": gg_id,
                    "nickname": nickname,
                },
            )
            report.postgres_inserted += 1

    if not gg_computer_base_url():
        report.gg_computer_error = "gg_computer_not_configured"
        return report

    try:
        gc_result = bulk_upsert_player_details(slug, items)
        report.gg_computer_upserted = int(gc_result.get("upserted", 0))
        report.gg_computer_modified = int(gc_result.get("modified", 0))
        report.gg_computer_skipped = int(gc_result.get("skipped", 0))
    except Exception as exc:
        logger.warning("gg-computer bulk upsert failed: %s", exc)
        report.gg_computer_error = str(exc)

    return report
