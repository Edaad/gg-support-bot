"""Tests for payment bind reply routing (including Add another member ForceReply)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from notification.handlers.bind import payment_bind_reply_handler
from notification.handlers.bind_callbacks import (
    BIND_ADD_MEMBER_PENDING_KEY,
    payment_bind_add_member_reply_handler,
    set_add_member_pending,
)

NOTIF_CHAT_ID = -5273879167


def _reply_update(
    *,
    text: str = "GTO / 3342-5648 / Abadani",
    user_id: int = 7516419496,
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
    update.effective_user.id = user_id
    return update


def _plain_text_update(
    *,
    text: str = "GTO / 3342-5648 / Abadani",
    user_id: int = 6713100304,
) -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_to_message = None
    update.effective_chat = MagicMock()
    update.effective_chat.id = NOTIF_CHAT_ID
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def _context_with_pending(*, prompt_actor: int = 8318575265) -> SimpleNamespace:
    context = SimpleNamespace(chat_data={}, user_data={})
    set_add_member_pending(
        context,
        method_slug="crypto",
        payment_id=116,
        notification_message_id=9408,
        actor_user_id=prompt_actor,
    )
    return context


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
        context = _context_with_pending()

        await payment_bind_reply_handler(_reply_update(), context)

        mock_add_member.assert_awaited_once()

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
        context.chat_data = {}
        context.user_data = {}

        await payment_bind_reply_handler(_reply_update(), context)

        mock_add_member.assert_not_awaited()


class SharedChatAddMemberTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_different_staff_member_can_complete_add_member(self) -> None:
        context = _context_with_pending(prompt_actor=8318575265)
        bound = SimpleNamespace(
            telegram_chat_id=-1001111111111,
            group_title="GTO / 3342-5648 / Abadani",
            club_id=4,
        )
        update = _plain_text_update(user_id=6713100304)
        update.message.reply_text = AsyncMock()

        with (
            patch(
                "notification.handlers.bind_callbacks.notification_chat_id",
                return_value=NOTIF_CHAT_ID,
            ),
            patch(
                "notification.handlers.bind_callbacks.load_payment",
                return_value=SimpleNamespace(is_test=False, alert_scope="clubgto"),
            ),
            patch(
                "notification.handlers.bind_callbacks.resolve_bound_group",
                return_value=SimpleNamespace(ok=True, bound_group=bound, error=None),
            ),
            patch(
                "notification.handlers.bind_callbacks.crypto_scope_error",
                return_value=None,
            ),
            patch(
                "notification.handlers.bind_callbacks.bind_scope_mismatch_error",
                return_value=None,
            ),
        ):
            await payment_bind_add_member_reply_handler(update, context)

        update.message.reply_text.assert_awaited_once()
        self.assertIn("Confirm add", update.message.reply_text.await_args.args[0])
        self.assertNotIn(BIND_ADD_MEMBER_PENDING_KEY, context.chat_data)


if __name__ == "__main__":
    unittest.main()
