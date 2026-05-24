"""One-time script: backfill group names for all linked groups using the Telegram Bot API."""
import asyncio
import os

from telegram import Bot

from db.connection import init_engine, get_db
from db.models import Group, SupportGroupChat

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN env var is required")

_GROUP_NAME_MAX = 255
_SUPPORT_GROUP_TITLE_MAX = 5000


async def backfill():
    engine = init_engine()
    bot = Bot(token=BOT_TOKEN)

    with get_db() as session:
        groups = session.query(Group).all()
        print(f"Found {len(groups)} groups to backfill")

        updated_groups = 0
        updated_sgc = 0
        for g in groups:
            try:
                chat = await bot.get_chat(g.chat_id)
                if not chat.title:
                    continue
                title = chat.title.strip()
                group_name = title[:_GROUP_NAME_MAX]
                support_title = title[:_SUPPORT_GROUP_TITLE_MAX]

                if group_name != g.name:
                    g.name = group_name
                    updated_groups += 1
                    print(f"  groups {g.chat_id} -> {group_name}")

                sgc_rows = (
                    session.query(SupportGroupChat)
                    .filter(SupportGroupChat.telegram_chat_id == int(g.chat_id))
                    .all()
                )
                for row in sgc_rows:
                    if row.telegram_chat_title != support_title:
                        row.telegram_chat_title = support_title
                        updated_sgc += 1
                        print(
                            f"  support_group_chats row {row.id} "
                            f"chat_id={g.chat_id} -> {support_title[:80]}"
                        )
            except Exception as e:
                print(f"  {g.chat_id} -> SKIP ({e})")

        session.commit()
        print(
            f"Done. Updated {updated_groups} group name(s), "
            f"{updated_sgc} support_group_chats title(s)."
        )

    await bot.shutdown()


asyncio.run(backfill())
