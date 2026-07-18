"""Manually run group-chat ticket analysis for a given America/New_York day.

Re-analyzes complete transcripts and replaces (upserts) ticket rows per chat on
success. Chats run in parallel by default. Does not touch Telegram / MTProto —
only Postgres + Claude.

Usage:
  # One chat first (recommended)
  python scripts/run_group_chat_analysis.py --activity-date 2026-07-17 --chat-id -100123

  # Full day (all chats in parallel by default; upsert tickets)
  python scripts/run_group_chat_analysis.py --activity-date 2026-07-17

  # Cap parallelism if Anthropic rate-limits
  python scripts/run_group_chat_analysis.py --activity-date 2026-07-17 --concurrency 10

  # Only incomplete / failed analysis (skip already-complete)
  python scripts/run_group_chat_analysis.py --activity-date 2026-07-17 --no-force

  heroku run -a YOUR_APP -- python scripts/run_group_chat_analysis.py --activity-date 2026-07-17 --chat-id -100123
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_group_chat_analysis")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--activity-date",
        required=True,
        help="America/New_York calendar day YYYY-MM-DD",
    )
    p.add_argument("--chat-id", type=int, default=None, help="Single chat only")
    p.add_argument("--club-id", type=int, default=None, help="Single club only")
    p.add_argument(
        "--budget-seconds",
        type=float,
        default=30 * 60,
        help="Retry budget (default 1800)",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Max parallel chats (0=unlimited; default from GROUP_CHAT_ANALYSIS_CONCURRENCY or 0)",
    )
    p.add_argument(
        "--force",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Re-analyze already-complete chats and replace tickets (default: true)",
    )
    return p.parse_args()


async def _main() -> int:
    args = _parse_args()
    try:
        activity_date = date.fromisoformat(str(args.activity_date).strip()[:10])
    except ValueError:
        logger.error("Invalid --activity-date %r", args.activity_date)
        return 2

    from bot.services.group_chat_analysis import (
        analyze_with_retries,
        list_analysis_targets,
    )

    targets = list_analysis_targets(
        activity_date,
        chat_id=args.chat_id,
        club_id=args.club_id,
        force=bool(args.force),
    )
    logger.info(
        "targets=%s activity_date=%s force=%s chat_id=%s club_id=%s concurrency=%s",
        len(targets),
        activity_date.isoformat(),
        args.force,
        args.chat_id,
        args.club_id,
        args.concurrency,
    )
    if not targets:
        logger.info("Nothing to analyze.")
        return 0

    summary = await analyze_with_retries(
        activity_date,
        chat_id=args.chat_id,
        club_id=args.club_id,
        budget_seconds=float(args.budget_seconds),
        force=bool(args.force),
        concurrency=args.concurrency,
    )
    logger.info(
        "done complete=%s failed=%s timed_out=%s",
        summary.complete,
        summary.failed,
        summary.timed_out,
    )
    for r in summary.results:
        if r.status != "complete":
            logger.warning(
                "chat_id=%s status=%s tickets=%s error=%s",
                r.chat_id,
                r.status,
                r.ticket_count,
                r.error,
            )
    return 0 if summary.failed == 0 and summary.timed_out == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
