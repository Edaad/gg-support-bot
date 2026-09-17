import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.commands import command_router
from bot.handlers.stripe import STRIPE_COMMAND_DISABLED_MESSAGE, stripe_handler


def _private_update(cmd: str):
    chat = SimpleNamespace(id=12345, type="private", title=None)
    message = MagicMock()
    message.text = cmd
    message.reply_text = AsyncMock()
    update = SimpleNamespace(
        message=message,
        effective_chat=chat,
        effective_user=SimpleNamespace(id=999),
    )
    return update, message


def _group_update(cmd: str, *, chat_id=-100123, club_id=2):
    chat = SimpleNamespace(id=chat_id, type="supergroup", title="RT / Test Player")
    message = MagicMock()
    message.text = cmd
    message.reply_text = AsyncMock()
    update = SimpleNamespace(
        message=message,
        effective_chat=chat,
        effective_user=SimpleNamespace(id=111),
    )
    return update, message


class DisabledStripeCommandTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_stripe_dm_does_not_create_checkout(self):
        update, message = _private_update("/stripe")
        context = SimpleNamespace()

        await stripe_handler(update, context)

        message.reply_text.assert_awaited_once_with(STRIPE_COMMAND_DISABLED_MESSAGE)

    async def test_command_router_dm_stripe_does_not_serve_custom_command(self):
        update, message = _private_update("/stripe")
        context = SimpleNamespace()

        with patch("bot.handlers.commands.get_club_id_for_telegram_user", return_value=2), patch(
            "bot.handlers.commands.get_custom_command",
            return_value={"response_type": "text", "response_text": "static stripe link"},
        ) as get_cmd:
            await command_router(update, context)

        message.reply_text.assert_not_called()
        get_cmd.assert_not_called()

    async def test_stripe_group_does_not_create_checkout(self):
        update, message = _group_update("/stripe")
        context = SimpleNamespace()

        await stripe_handler(update, context)

        message.reply_text.assert_awaited_once_with(STRIPE_COMMAND_DISABLED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
