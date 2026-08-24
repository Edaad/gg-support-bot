"""Tests for payment bind reply routing (including Add another member ForceReply)."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from notification.handlers.bind import payment_bind_reply_handler
from notification.payment_lookup import PaymentRef
from notification.handlers.bind_callbacks import (
    BIND_ADD_MEMBER_PENDING_KEY,
    get_add_member_pending,
    payment_bind_add_member_reply_handler,
    set_add_member_pending,
)

NOTIF_CHAT_ID = -5273879167
_CLUB_ENV = {"PAYMENT_NOTIFICATION_CHAT_ID_GTO": str(NOTIF_CHAT_ID)}


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
    context = SimpleNamespace(
        chat_data={},
        user_data={},
        application=SimpleNamespace(bot_data={}),
    )
    set_add_member_pending(
        context,
        chat_id=NOTIF_CHAT_ID,
        method_slug="crypto",
        payment_id=116,
        notification_message_id=9408,
        actor_user_id=prompt_actor,
    )
    return context


class ForceReplyAddMemberRoutingTestCase(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, _CLUB_ENV, clear=False)
    @patch(
        "notification.handlers.bind.payment_bind_add_member_reply_handler",
        new_callable=AsyncMock,
    )
    async def test_pending_add_member_delegates_before_notification_check(
        self,
        mock_add_member: AsyncMock,
    ) -> None:
        context = _context_with_pending()

        await payment_bind_reply_handler(_reply_update(), context)

        mock_add_member.assert_awaited_once()

    @patch.dict(os.environ, _CLUB_ENV, clear=False)
    @patch(
        "notification.handlers.bind.payment_bind_add_member_reply_handler",
        new_callable=AsyncMock,
    )
    @patch("notification.handlers.bind.find_payment_by_notification", return_value=None)
    async def test_non_pending_reply_to_force_prompt_is_ignored(
        self,
        _mock_find: MagicMock,
        mock_add_member: AsyncMock,
    ) -> None:
        context = MagicMock()
        context.chat_data = {}
        context.user_data = {}
        context.application = MagicMock()
        context.application.bot_data = {}

        await payment_bind_reply_handler(_reply_update(), context)

        mock_add_member.assert_not_awaited()


class SharedChatAddMemberTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_different_staff_member_can_complete_add_member(self) -> None:
        with patch.dict(os.environ, _CLUB_ENV, clear=False):
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
        self.assertNotIn(BIND_ADD_MEMBER_PENDING_KEY, context.application.bot_data.get(BIND_ADD_MEMBER_PENDING_KEY, {}))


class ChatIdVariantPendingTestCase(unittest.TestCase):
    @patch.dict(
        os.environ,
        {"PAYMENT_NOTIFICATION_CHAT_ID_GTO": "-1005273879167"},
        clear=False,
    )
    def test_pending_survives_supergroup_chat_id_variant(self) -> None:
        context = SimpleNamespace(
            chat_data={},
            user_data={},
            application=SimpleNamespace(bot_data={}),
        )
        set_add_member_pending(
            context,
            chat_id=-1005273879167,
            method_slug="crypto",
            payment_id=116,
            notification_message_id=9408,
            actor_user_id=8318575265,
        )
        pending = get_add_member_pending(context, chat_id=-5273879167)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["payment_id"], 116)


class UnboundImmediateBindTestCase(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, _CLUB_ENV, clear=False)
    @patch("notification.handlers.bind.find_payment_by_notification")
    @patch("notification.handlers.bind.resolve_bound_group")
    @patch("notification.handlers.bind.bind_scope_mismatch_error", return_value=None)
    @patch("notification.handlers.bind.get_db")
    @patch("notification.handlers.bind.candidates_for_payment", return_value=[])
    @patch("notification.handlers.bind.format_payment_row", return_value="row")
    @patch(
        "notification.handlers.bind.bind_venmo_payment_from_reply",
        new_callable=AsyncMock,
    )
    @patch(
        "notification.handlers.bind.bind_zelle_payment_from_reply",
        new_callable=AsyncMock,
    )
    async def test_unbound_zelle_reply_uses_canonical_chat(
        self,
        mock_zelle_bind: AsyncMock,
        mock_venmo_bind: AsyncMock,
        _mock_row: MagicMock,
        _mock_candidates: MagicMock,
        mock_get_db: MagicMock,
        _mock_scope: MagicMock,
        mock_resolve: MagicMock,
        mock_find: MagicMock,
    ) -> None:
        bound = SimpleNamespace(
            telegram_chat_id=-1001111111111,
            group_title="AT / 6794-0359 / Pratul",
            club_id=2,
        )
        mock_find.return_value = PaymentRef(
            method_slug="zelle",
            payment_id=42,
            payment_is_test=False,
            telegram_chat_id=None,
        )
        mock_resolve.return_value = SimpleNamespace(
            ok=True, bound_group=bound, error=None
        )
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = (
            SimpleNamespace(alert_scope=None, bound_group_title_at_bind=None)
        )
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False
        mock_zelle_bind.return_value = SimpleNamespace(
            ok=True, bound_group=bound, error=None
        )

        update = _reply_update(
            text="AT / 6794-0359 / Pratul",
            reply_text="🔔 Zelle Payment Notification",
        )
        update.message.reply_text = AsyncMock()
        context = SimpleNamespace(
            chat_data={},
            user_data={},
            application=SimpleNamespace(bot_data={}),
        )

        await payment_bind_reply_handler(update, context)

        mock_zelle_bind.assert_awaited_once()
        kwargs = mock_zelle_bind.await_args.kwargs
        self.assertEqual(kwargs["notification_chat_id"], NOTIF_CHAT_ID)
        self.assertEqual(kwargs["group_title_input"], "AT / 6794-0359 / Pratul")
        mock_venmo_bind.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()
        self.assertIn("Bound to AT / 6794-0359 / Pratul", update.message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
