#!/usr/bin/env python
"""One-off: compute migration recovery Slack stats and post to ops channel.

Use detached mode on Heroku (MTProto scan can take many minutes):

    heroku run:detached -a YOUR_APP -- python scripts/post_migration_recovery_slack_summary.py
    heroku logs -a YOUR_APP --dyno run.NNNN --tail
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from bot.services.migration_recovery import (
    compute_recovery_slack_stats,
    format_recovery_slack_summary,
    record_slack_summary_post,
)
from bot.services.slack_ops_notify import notify_slack_ops

logger = logging.getLogger("post_migration_recovery_slack_summary")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _run() -> int:
    stats = await compute_recovery_slack_stats()
    logger.info("clubs with tier 1+2 rows: %s", len(stats))
    for entry in stats:
        logger.info(
            "%s total=%s left=%s done=%s/%s",
            entry.club_key,
            entry.total,
            entry.left,
            entry.done,
            entry.total,
        )
    if not stats:
        print("no tier 1+2 rows")
        return 0

    text = format_recovery_slack_summary(stats)
    ok = await notify_slack_ops(text, source="migration_recovery")
    if ok:
        record_slack_summary_post()
    print("posted:", ok)
    return 0 if ok else 1


def main() -> None:
    _configure_logging()
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
