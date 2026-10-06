#!/usr/bin/env python
"""Mark credited referrals already at $100 as handled, without notifying.

Dry-run by default. --apply writes deposit_met_at and deposit_notify_suppressed.
--chat-id limits the scan to one referred support group.

    python scripts/suppress_referral_deposit_bonus.py
    python scripts/suppress_referral_deposit_bonus.py --chat-id -100123
    python scripts/suppress_referral_deposit_bonus.py --chat-id -100123 --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bot.services.referrals import (  # noqa: E402
    STATUS_CREDITED,
    attribution_should_suppress,
    bound_deposit_cents,
)
from db.connection import get_db  # noqa: E402
from db.models import ReferralAttribution  # noqa: E402


def suppression_candidates(session, chat_id: int | None) -> list[tuple]:
    query = session.query(ReferralAttribution).filter(
        ReferralAttribution.status == STATUS_CREDITED,
        ReferralAttribution.deposit_notify_suppressed.is_(False),
        ReferralAttribution.deposit_telegram_sent_at.is_(None),
        ReferralAttribution.referred_chat_id.isnot(None),
    )
    if chat_id is not None:
        query = query.filter(ReferralAttribution.referred_chat_id == int(chat_id))
    matches = []
    for row in query.order_by(ReferralAttribution.id.asc()).all():
        total = bound_deposit_cents(session, int(row.referred_chat_id))
        if attribution_should_suppress(row, total):
            matches.append((row, total))
    return matches


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the suppress flags. Without this, only print.",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="Only the referred support group with this Telegram chat id.",
    )
    args = parser.parse_args()
    with get_db() as session:
        matches = suppression_candidates(session, args.chat_id)
        if not matches:
            print("No credited referrals already at $100 need suppressing.")
            return
        now = datetime.now(timezone.utc)
        for row, total in matches:
            verb = "suppress" if args.apply else "would suppress"
            print(
                f"{verb} id={row.id} chat_id={row.referred_chat_id} "
                f"player={row.referred_gg_player_id} cents={total}"
            )
            if args.apply:
                row.deposit_notify_suppressed = True
                if row.deposit_met_at is None:
                    row.deposit_met_at = now
                row.updated_at = now
        if not args.apply:
            print(f"{len(matches)} row(s). Re-run with --apply to write.")


if __name__ == "__main__":
    main()
