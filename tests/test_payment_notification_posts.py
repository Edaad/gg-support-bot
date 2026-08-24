"""Tests for fan-out payment notification post tracking."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from notification.payment_lookup import find_payment_by_notification
from notification.payment_notification_posts import (
    find_payment_notification_post,
    list_payment_notification_posts,
    record_payment_notification_posts,
)

GTO_CHAT = -1001111111111
RT_CHAT = -1002222222222
CC_CHAT = -1003333333333333


class RecordAndFindPostsTestCase(unittest.TestCase):
    def test_record_and_find_secondary_fanout_copy(self) -> None:
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = None
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch("notification.payment_notification_posts.get_db", return_value=ctx):
            record_payment_notification_posts(
                payment_method_slug="crypto",
                payment_id=99,
                posts=[(GTO_CHAT, 10), (CC_CHAT, 20)],
            )

        self.assertEqual(session.add.call_count, 2)

        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            "crypto",
            99,
        )

        with patch("notification.payment_notification_posts.get_db", return_value=ctx):
            found = find_payment_notification_post(CC_CHAT, 20)

        self.assertEqual(found, ("crypto", 99))


class FindPaymentByNotificationPostsTestCase(unittest.TestCase):
    def test_lookup_uses_posts_before_legacy_columns(self) -> None:
        payment = MagicMock()
        payment.id = 42
        payment.is_test = False
        payment.telegram_chat_id = None

        session = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch(
                "notification.payment_lookup.find_payment_notification_post",
                return_value=("crypto", 42),
            ),
            patch("notification.payment_lookup.get_db", return_value=ctx),
        ):
            session.query.return_value.filter_by.return_value.one_or_none.return_value = payment
            ref = find_payment_by_notification(CC_CHAT, 77)

        self.assertIsNotNone(ref)
        assert ref is not None
        self.assertEqual(ref.method_slug, "crypto")
        self.assertEqual(ref.payment_id, 42)


class SyncEditsAllPostsTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_sync_payment_notification_edit_targets_every_post(self) -> None:
        from bot.services.payment_binding_events import sync_payment_notification_edit

        with (
            patch(
                "notification.payment_notification_posts.list_payment_notification_posts",
                return_value=[(GTO_CHAT, 1), (CC_CHAT, 2)],
            ),
            patch(
                "bot.services.venmo_payments.edit_telegram_notification",
                new_callable=AsyncMock,
            ) as edit_mock,
            patch(
                "bot.services.payment_binding_events.record_binding_event",
            ) as record_mock,
        ):
            ok = await sync_payment_notification_edit(
                payment_method_slug="crypto",
                payment_id=5,
                notification_chat_id=GTO_CHAT,
                notification_message_id=1,
                text="bound",
            )

        self.assertTrue(ok)
        self.assertEqual(edit_mock.await_count, 2)
        edit_mock.assert_any_await(GTO_CHAT, 1, "bound")
        edit_mock.assert_any_await(CC_CHAT, 2, "bound")
        edit_ok_events = [
            call.args[0].event_type
            for call in record_mock.call_args_list
            if call.args[0].event_type == "notification_edit_ok"
        ]
        self.assertEqual(len(edit_ok_events), 2)


class ListPostsTestCase(unittest.TestCase):
    def test_list_payment_notification_posts(self) -> None:
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            (GTO_CHAT, 1),
            (CC_CHAT, 2),
        ]
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch("notification.payment_notification_posts.get_db", return_value=ctx):
            posts = list_payment_notification_posts(
                payment_method_slug="venmo",
                payment_id=3,
            )

        self.assertEqual(posts, [(GTO_CHAT, 1), (CC_CHAT, 2)])


if __name__ == "__main__":
    unittest.main()
