"""Test: CC MTProto account creates a megagroup and invites RT support.

Verifies the cross-account /bind workaround when RT cannot CreateChannel
(ChannelsTooMuchError) but can join a group created elsewhere.

Usage (production one-off — disable worker MTProto first; see docs/HEROKU.md):
  heroku config:set GC_MTPROTO_ENABLED=false -a gg-support-bot-2025
  heroku restart worker -a gg-support-bot-2025
  heroku run -a gg-support-bot-2025 -- python scripts/test_cross_account_group_invite.py --apply
  heroku config:unset GC_MTPROTO_ENABLED -a gg-support-bot-2025
  heroku restart worker -a gg-support-bot-2025

Local:
  python scripts/test_cross_account_group_invite.py
  python scripts/test_cross_account_group_invite.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass


async def _run(
    *,
    creator_club_key: str,
    invite_marker: str,
    title: str,
    apply: bool,
) -> int:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import (
        _invite_one,
        _with_single_flood_retry,
        export_invite_link_for_peer,
        is_client_authorized,
        make_client,
    )
    from telethon.tl.functions.channels import CreateChannelRequest
    from telethon.utils import get_peer_id

    cfg = CLUB_GC_CONFIG.get(creator_club_key)
    if cfg is None:
        print(f"Unknown creator club_key: {creator_club_key!r}", file=sys.stderr)
        return 1

    if not await is_client_authorized(cfg):
        print(
            f"Creator session not authorized for {creator_club_key!r}. "
            "Complete Dashboard Telegram login first.",
            file=sys.stderr,
        )
        return 1

    print(f"Creator club: {cfg.club_display_name} ({creator_club_key})")
    print(f"Megagroup title: {title!r}")
    print(f"Invite target: {invite_marker!r}")
    print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")

    if not apply:
        print("\nDry-run only. Re-run with --apply to create the group and invite RT support.")
        return 0

    client = make_client(cfg)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("Session not authorized after connect.", file=sys.stderr)
            return 1

        me = await client.get_me()
        me_label = (
            f"@{me.username}" if getattr(me, "username", None) else f"id={getattr(me, 'id', '?')}"
        )
        print(f"Connected as {me_label}")

        mega = await _with_single_flood_retry(
            "CreateChannelRequest",
            lambda: client(
                CreateChannelRequest(
                    title=title,
                    about="Cross-account GC bind test",
                    megagroup=True,
                    broadcast=False,
                )
            ),
        )
        chan = mega.chats[0] if getattr(mega, "chats", None) else None
        if not chan:
            print("CreateChannel returned no chat.", file=sys.stderr)
            return 1

        channel_ent = await client.get_entity(chan)
        chat_id = int(get_peer_id(channel_ent))
        chat_title = getattr(channel_ent, "title", None) or title
        print(f"Created megagroup chat_id={chat_id} title={chat_title!r}")

        ok, err = await _invite_one(client, channel_ent, invite_marker)
        if ok:
            print(f"Invited {invite_marker}: ok")
        else:
            print(f"Invited {invite_marker}: FAILED ({err or 'unknown'})")

        link = await export_invite_link_for_peer(client, channel_ent)
        print(f"Invite link: {link or '(export failed)'}")
        print(
            "\nNext: from RT account DM with a test player, send "
            f"/bind {link or '<invite-link>'}"
        )
        return 0 if ok else 2
    finally:
        await client.disconnect()


def main() -> None:
    p = argparse.ArgumentParser(
        description="CC creates megagroup and invites RT support (bind workaround test)"
    )
    p.add_argument(
        "--creator-club-key",
        default="creator_club",
        choices=("round_table", "creator_club", "clubgto"),
        help="MTProto session that creates the group (default: creator_club)",
    )
    p.add_argument(
        "--invite",
        default="@roundtablesupport2",
        help="RT support to add (@username or numeric id; default: @roundtablesupport2)",
    )
    p.add_argument(
        "--title",
        default="CC test / / RT bind theory",
        help="Megagroup title",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Create group and invite (default: dry-run)",
    )
    args = p.parse_args()
    raise SystemExit(
        asyncio.run(
            _run(
                creator_club_key=args.creator_club_key,
                invite_marker=args.invite,
                title=args.title,
                apply=args.apply,
            )
        )
    )


if __name__ == "__main__":
    main()
