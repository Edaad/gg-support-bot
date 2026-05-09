"""Optional CLI to authorize a club Telethon session (SMS + optional 2FA).

Does not log or store OTP codes or cloud passwords. Requires TG_API_ID, TG_API_HASH,
DATABASE_URL for session snapshot when GC_MTPROTO_DB_SESSIONS is enabled.

Usage:
  DATABASE_URL=... TG_API_ID=... TG_API_HASH=... python scripts/mtproto_login_cli.py --club-key round_table
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass


async def _run(club_key: str) -> None:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import (
        authenticate_mtproto_code,
        authenticate_mtproto_password,
        is_client_authorized,
        send_code_for_phone,
    )
    from bot.services.mtproto_session_db import snapshot_disk_session_to_database
    from telethon.errors import SessionPasswordNeededError

    cfg = CLUB_GC_CONFIG.get(club_key)
    if not cfg:
        print(f"Unknown club_key: {club_key!r}", file=sys.stderr)
        sys.exit(1)

    if await is_client_authorized(cfg):
        print("Session already authorized.")
        await snapshot_disk_session_to_database(cfg)
        return

    phone = cfg.mtproto_phone_number
    if not phone or not phone.strip():
        phone = input("Phone (+country…): ").strip()
    if not phone:
        print("Phone required.", file=sys.stderr)
        sys.exit(1)

    phone_code_hash = await send_code_for_phone(cfg, phone)
    code = input("Login code: ").strip()
    try:
        await authenticate_mtproto_code(
            cfg, phone=phone, code=code, phone_code_hash=phone_code_hash
        )
    except SessionPasswordNeededError:
        pw = getpass.getpass("2FA cloud password: ")
        await authenticate_mtproto_password(cfg, password=pw)

    if not await is_client_authorized(cfg):
        print("Authorization failed.", file=sys.stderr)
        sys.exit(1)

    if await snapshot_disk_session_to_database(cfg):
        print("Session saved to database (StringSession).")
    else:
        print("Session on disk only; enable GC_MTPROTO_DB_SESSIONS or sync from dashboard.")


def main() -> None:
    p = argparse.ArgumentParser(description="Telethon MTProto login for /gc clubs")
    p.add_argument(
        "--club-key",
        required=True,
        choices=("round_table", "creator_club", "clubgto"),
    )
    args = p.parse_args()
    asyncio.run(_run(args.club_key))


if __name__ == "__main__":
    main()
