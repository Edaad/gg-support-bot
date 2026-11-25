"""Postgres state for QuickNode hints and verified transfers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from db.connection import get_db
from db.models import CryptoPayment, QuicknodeTransfer, QuicknodeTxHint

HINT_PENDING = "pending"
HINT_DONE = "done"
HINT_HELD = "held"
HINT_REJECTED = "rejected"

TRANSFER_SHADOW = "shadow"
TRANSFER_POSTED = "posted"
TRANSFER_HELD = "held"
TRANSFER_REJECTED = "rejected"
TRANSFER_NOTIFY_FAILED = "notify_failed"


def utc(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every stored time is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _insert_for(session):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def record_hints(chain: str, tx_hashes: list[str], now: datetime | None = None) -> int:
    """Insert-if-missing on (chain, tx_hash). Returns how many rows were new."""
    if not tx_hashes:
        return 0
    current = now or datetime.now(timezone.utc)
    with get_db() as session:
        insert = _insert_for(session)
        stmt = (
            insert(QuicknodeTxHint)
            .values(
                [
                    {
                        "chain": chain,
                        "tx_hash": tx_hash,
                        "state": HINT_PENDING,
                        "attempts": 0,
                        "first_seen_at": current,
                        "next_check_at": current,
                    }
                    for tx_hash in tx_hashes
                ]
            )
            .on_conflict_do_nothing(index_elements=["chain", "tx_hash"])
        )
        result = session.execute(stmt)
        return int(result.rowcount or 0)


@dataclass(frozen=True)
class ClaimedHint:
    id: int
    chain: str
    tx_hash: str
    attempts: int
    first_seen_at: datetime
    alerted_at: datetime | None


def claim_due_hints(
    now: datetime, *, limit: int, lease: timedelta
) -> list[ClaimedHint]:
    """Lease up to ``limit`` due pending hints; another worker skips them for ``lease``."""
    with get_db() as session:
        rows = (
            session.query(QuicknodeTxHint)
            .filter(
                QuicknodeTxHint.state == HINT_PENDING,
                QuicknodeTxHint.next_check_at <= now,
                or_(
                    QuicknodeTxHint.claimed_at.is_(None),
                    QuicknodeTxHint.claimed_at < now - lease,
                ),
            )
            .order_by(QuicknodeTxHint.next_check_at, QuicknodeTxHint.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .all()
        )
        claimed = []
        for row in rows:
            row.claimed_at = now
            claimed.append(
                ClaimedHint(
                    id=int(row.id),
                    chain=row.chain,
                    tx_hash=row.tx_hash,
                    attempts=int(row.attempts or 0),
                    first_seen_at=utc(row.first_seen_at),
                    alerted_at=utc(row.alerted_at),
                )
            )
        return claimed


def finish_hint(
    hint_id: int,
    *,
    state: str,
    reason: str | None,
    next_check_at: datetime | None = None,
    attempts: int | None = None,
    alerted_at: datetime | None = None,
) -> None:
    with get_db() as session:
        row = session.query(QuicknodeTxHint).filter_by(id=hint_id).one()
        row.state = state
        row.reason = reason
        row.claimed_at = None
        if next_check_at is not None:
            row.next_check_at = next_check_at
        if attempts is not None:
            row.attempts = attempts
        if alerted_at is not None:
            row.alerted_at = alerted_at


def transfer_exists(source_external_id: str) -> bool:
    with get_db() as session:
        return (
            session.query(QuicknodeTransfer.id)
            .filter_by(source_external_id=source_external_id)
            .first()
            is not None
        )


def insert_transfer(**fields) -> bool:
    """Insert-if-missing on source_external_id. False when it already existed."""
    with get_db() as session:
        insert = _insert_for(session)
        stmt = (
            insert(QuicknodeTransfer)
            .values(**fields)
            .on_conflict_do_nothing(index_elements=["source_external_id"])
        )
        return bool(session.execute(stmt).rowcount)


def crypto_payment_id_for(source_external_id: str) -> int | None:
    with get_db() as session:
        row = (
            session.query(CryptoPayment.id)
            .filter_by(source_external_id=source_external_id)
            .first()
        )
        return int(row[0]) if row else None
