"""Tests for payment bind reply routing (including Add another member ForceReply)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from notification.handlers.bind import payment_bind_reply_handler
from notification.handlers.bind_callbacks import BIND_ADD_MEMBER_PENDING_KEY

NOTIF_CHAT_ID = -5273879167


def _reply_update(
    *,
    text: str = "GTO / 3342-5648 / Abadani",
    reply_message_id: int = 9409,
    reply_text: str = "Send the group title for the member to add.",
) -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_to_message = MagicMock()
    update.message.reply_to_message.message_id = reply_message_id
    update.message.reply_to_message.text = reply_text
    update.message.reply_to_message.caption = None
    update.effective_chat = MagicMock()
    update.effective_chat.id = NOTIF_CHAT_ID
    update.effective_user = MagicMock()
    update.effective_user.id = 7516419496
    return update


class ForceReplyAddMemberRoutingTestCase(unittest.IsolatedAsyncioTestCase):
    @patch(
        "notification.handlers.bind.payment_bind_add_member_reply_handler",
        new_callable=AsyncMock,
    )
    @patch("notification.handlers.bind.notification_chat_id", return_value=NOTIF_CHAT_ID)
    async def test_pending_add_member_delegates_before_notification_check(
        self,
        _mock_chat: MagicMock,
        mock_add_member: AsyncMock,
    ) -> None:
        context = MagicMock()
        context.user_data = {
            BIND_ADD_MEMBER_PENDING_KEY: {
                "method_slug": "crypto",
                "payment_id": 116,
                "notification_message_id": 9408,
            }
        }

        await payment_bind_reply_handler(_reply_update(), context)

        mock_add_member.assert_awaited_once()
        call_update, call_context = mock_add_member.await_args.args
        self.assertEqual(call_update.message.text, "GTO / 3342-5648 / Abadani")
        self.assertIs(call_context, context)

    @patch(
        "notification.handlers.bind.payment_bind_add_member_reply_handler",
        new_callable=AsyncMock,
    )
    @patch("notification.handlers.bind.find_payment_by_notification", return_value=None)
    @patch("notification.handlers.bind.notification_chat_id", return_value=NOTIF_CHAT_ID)
    async def test_non_pending_reply_to_force_prompt_is_ignored(
        self,
        _mock_chat: MagicMock,
        _mock_find: MagicMock,
        mock_add_member: AsyncMock,
    ) -> None:
        context = MagicMock()
        context.user_data = {}

        await payment_bind_reply_handler(_reply_update(), context)

        mock_add_member.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
