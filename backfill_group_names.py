"""One-time script: backfill group names for all linked groups using the Telegram Bot API."""
import asyncio
import os

from telegram import Bot

from db.connection import init_engine, get_db
from db.models import Group

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN env var is required")


async def backfill():
    engine = init_engine()
    bot = Bot(token=BOT_TOKEN)

    with get_db() as session:
        groups = session.query(Group).all()
        print(f"Found {len(groups)} groups to backfill")

        updated = 0
        for g in groups:
            try:
                chat = await bot.get_chat(g.chat_id)
                if chat.title and chat.title != g.name:
                    g.name = chat.title
                    updated += 1
                    print(f"  {g.chat_id} -> {chat.title}")
            except Exception as e:
                print(f"  {g.chat_id} -> SKIP ({e})")

        session.commit()
        print(f"Done. Updated {updated} group name(s).")

    await bot.shutdown()


asyncio.run(backfill())
