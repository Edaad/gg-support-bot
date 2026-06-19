"""Unit tests for notification bot /report command."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import ForceReply
from telegram.ext import ConversationHandler

from notification.handlers.report import (
    REPORT_REASON,
    format_report_ticket,
    report_cancel,
    report_entry,
    report_reason,
)

NOTIF_CHAT_ID = -1009999999999


def _make_update(
    *,
    chat_id: int = NOTIF_CHAT_ID,
    user_id: int = 111,
    username: str = "staff1",
    text: str = "/report",
    reply_text: str = "🔔 Payment Notification\n\nGroup Chat: RT / test",
    reply_message_id: int = 42,
    has_reply: bool = True,
) -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.message_id = 99
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username

    if has_reply:
        reply = MagicMock()
        reply.message_id = reply_message_id
        reply.text = reply_text
        reply.caption = None
        update.message.reply_to_message = reply
    else:
        update.message.reply_to_message = None

    return update


def _make_context() -> MagicMock:
    context = MagicMock()
    context.user_data = {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


class TestFormatReportTicket(unittest.TestCase):
    def test_includes_notification_and_reason(self) -> None:
        text = format_report_ticket(
            reporter_username="staff1",
            reporter_user_id=111,
            notification_chat_id=NOTIF_CHAT_ID,
            notification_message_id=42,
            notification_text="🔔 Payment Notification",
            reason="Wrong amount shown",
        )
        self.assertIn("Notification bug report", text)
        self.assertIn("@staff1", text)
        self.assertIn("message_id=42", text)
        self.assertIn("🔔 Payment Notification", text)
        self.assertIn("Wrong amount shown", text)


class TestReportEntry(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {"PAYMENT_NOTIFICATION_CHAT_ID": str(NOTIF_CHAT_ID)})
    async def test_requires_reply(self) -> None:
        update = _make_update(has_reply=False)
        context = _make_context()

        state = await report_entry(update, context)

        self.assertEqual(state, ConversationHandler.END)
        update.message.reply_text.assert_awaited_once()
        self.assertIn("Reply to the notification", update.message.reply_text.await_args.args[0])

    @patch.dict(os.environ, {"PAYMENT_NOTIFICATION_CHAT_ID": str(NOTIF_CHAT_ID)})
    async def test_starts_conversation_when_reply_present(self) -> None:
        update = _make_update()
        context = _make_context()

        state = await report_entry(update, context)

        self.assertEqual(state, REPORT_REASON)
        self.assertEqual(context.user_data["report_notification_message_id"], 42)
        self.assertIn("Payment Notification", context.user_data["report_notification_text"])
        update.message.reply_text.assert_awaited_once_with(
            "Reply to this message with what was wrong.",
            reply_markup=ForceReply(selective=True),
        )

    @patch.dict(os.environ, {"PAYMENT_NOTIFICATION_CHAT_ID": "-1000000000001"})
    async def test_ignores_wrong_chat(self) -> None:
        update = _make_update(chat_id=NOTIF_CHAT_ID)
        context = _make_context()

        state = await report_entry(update, context)

        self.assertEqual(state, ConversationHandler.END)
        update.message.reply_text.assert_not_awaited()


class TestReportReason(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {"PAYMENT_NOTIFICATION_CHAT_ID": str(NOTIF_CHAT_ID)})
    @patch(
        "bot.services.slack_ops_notify.notify_slack_ops",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_happy_path_posts_to_slack(self, mock_slack: AsyncMock) -> None:
        update = _make_update(text="Wrong group title")
        context = _make_context()
        context.user_data.update(
            {
                "report_notification_message_id": 42,
                "report_notification_text": "🔔 Payment Notification",
                "report_notification_chat_id": NOTIF_CHAT_ID,
            }
        )

        state = await report_reason(update, context)

        self.assertEqual(state, ConversationHandler.END)
        context.bot.send_message.assert_not_awaited()
        update.message.reply_text.assert_awaited_once_with("Report submitted. Thanks!")
        self.assertNotIn("report_notification_message_id", context.user_data)
        mock_slack.assert_awaited_once()
        self.assertEqual(mock_slack.await_args.kwargs["source"], "notification_report")
        self.assertIn("Wrong group title", mock_slack.await_args.args[0])

    @patch.dict(os.environ, {"PAYMENT_NOTIFICATION_CHAT_ID": str(NOTIF_CHAT_ID)})
    @patch(
        "bot.services.slack_ops_notify.notify_slack_ops",
        new_callable=AsyncMock,
        return_value=False,
    )
    async def test_slack_failure_informs_reporter(self, mock_slack: AsyncMock) -> None:
        update = _make_update(text="Bad memo")
        context = _make_context()
        context.user_data.update(
            {
                "report_notification_message_id": 42,
                "report_notification_text": "🔔 Payment Notification",
                "report_notification_chat_id": NOTIF_CHAT_ID,
            }
        )

        state = await report_reason(update, context)

        self.assertEqual(state, ConversationHandler.END)
        update.message.reply_text.assert_awaited_once()
        self.assertIn("Slack", update.message.reply_text.await_args.args[0])
        mock_slack.assert_awaited_once()

    @patch.dict(os.environ, {"PAYMENT_NOTIFICATION_CHAT_ID": str(NOTIF_CHAT_ID)})
    async def test_empty_reason_reprompts(self) -> None:
        update = _make_update(text="   ")
        context = _make_context()
        context.user_data["report_notification_message_id"] = 42
        context.user_data["report_notification_chat_id"] = NOTIF_CHAT_ID

        state = await report_reason(update, context)

        self.assertEqual(state, REPORT_REASON)
        update.message.reply_text.assert_awaited_once_with(
            "Please describe what was wrong."
        )


class TestReportCancel(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_clears_state(self) -> None:
        update = _make_update()
        context = _make_context()
        context.user_data["report_notification_message_id"] = 42

        state = await report_cancel(update, context)

        self.assertEqual(state, ConversationHandler.END)
        self.assertNotIn("report_notification_message_id", context.user_data)
        update.message.reply_text.assert_awaited_once_with("Cancelled.")


if __name__ == "__main__":
    unittest.main()
