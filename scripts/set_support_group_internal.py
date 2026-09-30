"""Flag a support group as internal / test so the response audit skips it.

Usage:
  python scripts/set_support_group_internal.py --chat-id -100123          # mark internal
  python scripts/set_support_group_internal.py --chat-id -100123 --off    # clear the flag
  python scripts/set_support_group_internal.py --list                     # show flagged chats
"""

from __future__ import annotations

import argparse
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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chat-id", type=int, default=None)
    p.add_argument("--off", action="store_true", help="Clear is_internal")
    p.add_argument("--list", action="store_true", help="List internal chats")
    args = p.parse_args()

    from db.connection import get_db
    from db.models import SupportGroupChat

    with get_db() as session:
        if args.list:
            for row in (
                session.query(SupportGroupChat)
                .filter(SupportGroupChat.is_internal.is_(True))
                .order_by(SupportGroupChat.telegram_chat_id)
            ):
                print(f"{row.telegram_chat_id}\t{row.telegram_chat_title}")
            return 0
        if args.chat_id is None:
            p.error("--chat-id is required unless --list")
        rows = (
            session.query(SupportGroupChat)
            .filter(SupportGroupChat.telegram_chat_id == int(args.chat_id))
            .all()
        )
        if not rows:
            print(
                f"No support_group_chats row for chat {args.chat_id}", file=sys.stderr
            )
            return 1
        for row in rows:
            row.is_internal = not args.off
        print(
            f"chat {args.chat_id}: is_internal={'false' if args.off else 'true'} "
            f"({len(rows)} row(s))"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
