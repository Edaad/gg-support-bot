#!/usr/bin/env python
"""Create an issue report ticket (DB + Slack) without a frontend.

Usage:
    python scripts/create_issue_report.py \\
        --title "Deposit button broken" \\
        --description "Player taps deposit, nothing happens" \\
        --tags cashout,deposit \\
        --screenshot ./screenshot.png

Requires DATABASE_URL. Slack posts when SLACK_OPS_* env is configured.
"""

from __future__ import annotations

import argparse
import asyncio
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

from bot.services.issue_reports import IssueReportFileInput, create_issue_report
from db.connection import get_db


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an issue report ticket")
    parser.add_argument("--title", required=True, help="Issue title")
    parser.add_argument("--description", required=True, help="Issue description")
    parser.add_argument(
        "--tags",
        default="",
        help="Comma-separated tags (bot_issue, cashout, deposit, rakeback)",
    )
    parser.add_argument("--reporter-name", default="", help="Reporter display name")
    parser.add_argument(
        "--screenshot",
        action="append",
        default=[],
        metavar="PATH",
        help="Screenshot path (repeatable, max 5)",
    )
    return parser.parse_args()


def _guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")


def _load_files(paths: list[str]) -> list[IssueReportFileInput]:
    files: list[IssueReportFileInput] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise SystemExit(f"Screenshot not found: {path}")
        files.append(
            IssueReportFileInput(
                filename=path.name,
                content_type=_guess_content_type(path),
                content=path.read_bytes(),
            )
        )
    return files


async def _run() -> int:
    args = _parse_args()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    files = _load_files(args.screenshot)

    with get_db() as db:
        report = await create_issue_report(
            db,
            title=args.title,
            description=args.description,
            tags=tags,
            reporter_name=args.reporter_name or None,
            reporter_source="cli",
            files=files,
        )

    print(f"Created issue report #{report.id}")
    print(f"  title: {report.title}")
    print(f"  tags: {', '.join(report.tags) if report.tags else '(none)'}")
    print(f"  attachments: {len(report.attachments)}")
    if report.slack_message_ts:
        print(f"  slack_message_ts: {report.slack_message_ts}")
    else:
        print("  slack: not posted (check SLACK_OPS_* env and logs)")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
