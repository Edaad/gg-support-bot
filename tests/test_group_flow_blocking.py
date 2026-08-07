"""Mutual exclusion: active deposit/cashout blocks the other (and same) command."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from bot.handlers import cashout as co
from bot.handlers import deposit as dep
from bot.handlers import flow_cancel as fc


def _group_update(*, chat_id: int = -1001, text: str = "/deposit"):
    chat = SimpleNamespace(id=chat_id, type="supergroup", title="GTO / 1 / test")
    message = SimpleNamespace(
        text=text,
        reply_text=AsyncMock(),
        chat=chat,
        date=None,
    )
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=chat,
        effective_user=SimpleNamespace(id=42),
    )


class GroupFlowBlockingHelpersTestCase(unittest.TestCase):
    def test_format_block_message_mentions_cancel(self):
        msg = fc.format_group_flow_block_message(active="deposit")
        self.assertIn("/deposit", msg)
        self.assertIn("/cancel", msg)

    def test_deposit_blocking_includes_payment_wait(self):
        context = SimpleNamespace(chat_data={})
        with patch.object(fc, "deposit_payment_wait_active", return_value=True):
            self.assertTrue(fc.deposit_blocking_active(context, -1001))


class GroupFlowBlockingEntryTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_block_helper_deposit_wait_blocks_cashout(self):
        update = _group_update(text="/cashout")
        context = SimpleNamespace(chat_data={})

        with (
            patch.object(fc, "deposit_blocking_active", return_value=True),
            patch.object(fc, "cashout_blocking_active", return_value=False),
        ):
            blocked = await fc.block_if_group_money_flow_active(
                update, context, starting="cashout", chat_id=-1001
            )

        self.assertTrue(blocked)
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("/deposit", text)
        self.assertIn("/cancel", text)

    async def test_block_helper_cashout_blocks_deposit(self):
        update = _group_update(text="/deposit")
        context = SimpleNamespace(chat_data={})

        with (
            patch.object(fc, "deposit_blocking_active", return_value=False),
            patch.object(fc, "cashout_blocking_active", return_value=True),
        ):
            blocked = await fc.block_if_group_money_flow_active(
                update, context, starting="deposit", chat_id=-1001
            )

        self.assertTrue(blocked)
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("/cashout", text)

    async def test_deposit_blocked_while_cashout_active(self):
        update = _group_update(text="/deposit")
        context = SimpleNamespace(chat_data={}, bot_data={}, job_queue=None)

        with (
            patch.object(dep, "is_update_too_old", return_value=False),
            patch.object(
                dep,
                "block_if_group_money_flow_active",
                AsyncMock(return_value=True),
            ) as block,
        ):
            result = await dep.deposit_entry(update, context)

        self.assertEqual(result, ConversationHandler.END)
        block.assert_awaited_once()

    async def test_cashout_entry_calls_block_helper(self):
        update = _group_update(text="/cashout")
        context = SimpleNamespace(chat_data={}, bot_data={}, job_queue=None)

        with (
            patch.object(co, "is_update_too_old", return_value=False),
            patch.object(
                co,
                "block_if_group_money_flow_active",
                AsyncMock(return_value=True),
            ) as block,
        ):
            result = await co.cashout_entry(update, context)

        self.assertEqual(result, ConversationHandler.END)
        block.assert_awaited_once()

    async def test_cancel_clears_payment_wait_when_wizard_over(self):
        chat = SimpleNamespace(id=-1001, type="supergroup")
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            message=message,
            effective_chat=chat,
            effective_user=SimpleNamespace(id=1),
        )
        context = SimpleNamespace(chat_data={}, user_data={}, job_queue=None)

        with (
            patch.object(fc, "deposit_flow_active", return_value=False),
            patch.object(fc, "cashout_flow_active", return_value=False),
            patch.object(fc, "get_active_dm_flow", return_value=None),
            patch.object(fc, "clear_deposit_payment_wait", AsyncMock(return_value=True)) as clear,
        ):
            await fc.flow_cancel_handler(update, context)

        clear.assert_awaited_once()
        message.reply_text.assert_awaited()
        self.assertIn("Deposit cancelled", message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
