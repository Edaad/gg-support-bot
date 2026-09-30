"""Build the response audit (``response_events``) for one America/New_York day.

Deterministic, Postgres-only (no Telegram / MTProto, no Claude). Reads the
nightly transcripts (day D plus the D+1 00:00–03:00 ET tail), escalation events
and the escalation decision log, then upserts that day's rows per chat.
Re-running replaces the day's rows; events that still exist keep their ids, so
judge verdicts survive.

Usage:
  python scripts/run_response_audit.py --activity-date 2026-09-29 --chat-id -100123
  python scripts/run_response_audit.py --activity-date 2026-09-29           # full day
  python scripts/run_response_audit.py --activity-date 2026-09-29 --force   # rebuild a day that already ran

  heroku run -a YOUR_APP -- python scripts/run_response_audit.py --activity-date 2026-09-29
"""

from __future__ import annotations

import argparse
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
logger = logging.getLogger("run_response_audit")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--activity-date",
        required=True,
        help="America/New_York calendar day YYYY-MM-DD",
    )
    p.add_argument("--chat-id", type=int, default=None, help="Single chat only")
    p.add_argument(
        "--force",
        action="store_true",
        help="Rebuild a day that already has a builder run recorded",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        activity_date = date.fromisoformat(str(args.activity_date).strip()[:10])
    except ValueError:
        logger.error("Invalid --activity-date %r", args.activity_date)
        return 2

    from bot.services.response_audit import run_response_audit

    summary = run_response_audit(
        activity_date, chat_id=args.chat_id, force=bool(args.force)
    )
    if summary.skipped_existing:
        logger.info(
            "%s already built; pass --force to rebuild", activity_date.isoformat()
        )
        return 0
    logger.info(
        "done date=%s chats=%s excluded=%s events=%s candidates=%s",
        activity_date.isoformat(),
        summary.chats_scanned,
        summary.chats_excluded,
        summary.events,
        summary.candidates,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
