import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.commands import command_router
from bot.handlers.group_checkout_commands import GROUP_CHECKOUT_DM_MESSAGE
from bot.handlers.stripe import stripe_handler


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


class GroupCheckoutDmTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_stripe_dm_rejects_with_group_message(self):
        update, message = _private_update("/stripe")
        context = SimpleNamespace()

        await stripe_handler(update, context)

        message.reply_text.assert_awaited_once_with(GROUP_CHECKOUT_DM_MESSAGE)

    async def test_command_router_dm_stripe_does_not_serve_custom_command(self):
        update, message = _private_update("/stripe")
        context = SimpleNamespace()

        with patch("bot.handlers.commands.get_club_id_for_telegram_user", return_value=2), patch(
            "bot.handlers.commands.get_custom_command",
            return_value={"response_type": "text", "response_text": "static stripe link"},
        ) as get_cmd:
            await command_router(update, context)

        message.reply_text.assert_awaited_once_with(GROUP_CHECKOUT_DM_MESSAGE)
        get_cmd.assert_not_called()

    async def test_stripe_group_creates_checkout_session(self):
        update, message = _group_update("/stripe")
        context = SimpleNamespace()
        result = SimpleNamespace(
            checkout_url="https://checkout.stripe.com/group-session",
            session_id="cs_test",
            customer_id="cus_test",
            expires_at=datetime.now(timezone.utc),
        )

        with (
            patch("bot.handlers.stripe.get_club_for_chat", return_value=2),
            patch("bot.handlers.group_checkout_commands.stripe_configured", return_value=True),
            patch(
                "bot.handlers.group_checkout_commands.create_stripe_checkout_session",
                return_value=result,
            ) as create_session,
            patch("bot.handlers.group_checkout_commands.update_group_name"),
        ):
            await stripe_handler(update, context)

        create_session.assert_called_once()
        self.assertEqual(create_session.call_args.kwargs["club_id"], 2)
        self.assertIsNone(create_session.call_args.kwargs.get("checkout_min_usd"))
        self.assertEqual(message.reply_text.await_count, 2)
        pay_call = message.reply_text.await_args_list[1]
        self.assertIn("checkout.stripe.com/group-session", pay_call.args[0])


if __name__ == "__main__":
    unittest.main()
