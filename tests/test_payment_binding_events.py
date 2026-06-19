"""Tests for persisted payment binding event tracking."""

import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.payment_binding_events import (
    EVENT_GROUP_BINDING_UPDATED,
    EVENT_NOTIFICATION_EDIT_FAILED,
    EVENT_NOTIFICATION_EDIT_OK,
    EVENT_NOTIFICATION_EDIT_SKIPPED,
    EVENT_NOTIFICATION_SENT,
    EVENT_PAYMENT_AUTO_BOUND,
    EVENT_PAYMENT_BOUND,
    BindingEventRecord,
    record_binding_event_in_session,
    sync_payment_notification_edit,
    track_ingest_notification,
)
from db.models import PaymentBindingEvent


class TestPaymentBindingEvents(IsolatedAsyncioTestCase):
    def test_record_binding_event_in_session(self):
        session = MagicMock()
        fake_row = MagicMock()
        fake_row.id = 42

        def _add(row):
            row.id = 42

        session.add.side_effect = _add
        session.flush.side_effect = None

        event_id = record_binding_event_in_session(
            session,
            BindingEventRecord(
                event_type=EVENT_PAYMENT_BOUND,
                payment_method_slug="zelle",
                payment_id=81,
                telegram_chat_id=-1001,
                club_id=4,
                bound_group_title="GTO / 1-2 / Player",
                bound_via="manual_notification",
                auto_bound=False,
                actor_telegram_user_id=123,
                notification_chat_id=-527,
                notification_message_id=7253,
            ),
        )
        self.assertEqual(event_id, 42)
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        self.assertIsInstance(added, PaymentBindingEvent)
        self.assertEqual(added.event_type, EVENT_PAYMENT_BOUND)
        self.assertEqual(added.payment_id, 81)

    @patch("bot.services.payment_binding_events.record_binding_event")
    def test_track_ingest_notification_auto_bound(self, mock_record):
        track_ingest_notification(
            payment_method_slug="zelle",
            payment_id=10,
            notification_chat_id=-1,
            notification_message_id=99,
            telegram_chat_id=-1002,
            club_id=2,
            bound_group_title="RT / 1-2 / A",
            auto_bound=True,
            bound_via="special_amount",
            bind_attempt_id=5,
        )
        self.assertEqual(mock_record.call_count, 2)
        first = mock_record.call_args_list[0][0][0]
        second = mock_record.call_args_list[1][0][0]
        self.assertEqual(first.event_type, EVENT_PAYMENT_AUTO_BOUND)
        self.assertEqual(second.event_type, EVENT_NOTIFICATION_SENT)

    @patch("bot.services.payment_binding_events.record_binding_event")
    def test_track_ingest_notification_unbound(self, mock_record):
        track_ingest_notification(
            payment_method_slug="zelle",
            payment_id=11,
            notification_chat_id=-1,
            notification_message_id=100,
            telegram_chat_id=None,
            club_id=None,
            bound_group_title=None,
            auto_bound=False,
        )
        mock_record.assert_called_once()
        self.assertEqual(mock_record.call_args[0][0].event_type, EVENT_NOTIFICATION_SENT)

    @patch("bot.services.payment_binding_events.record_binding_event")
    @patch(
        "bot.services.venmo_payments.edit_telegram_notification",
        new_callable=AsyncMock,
    )
    async def test_sync_payment_notification_edit_ok(self, mock_edit, mock_record):
        mock_edit.return_value = None
        ok = await sync_payment_notification_edit(
            payment_method_slug="zelle",
            payment_id=81,
            notification_chat_id=-527,
            notification_message_id=7253,
            text="bound text",
            bound_via="manual_notification",
        )
        self.assertTrue(ok)
        mock_edit.assert_awaited_once()
        recorded = mock_record.call_args[0][0]
        self.assertEqual(recorded.event_type, EVENT_NOTIFICATION_EDIT_OK)

    @patch("bot.services.payment_binding_events.record_binding_event")
    async def test_sync_payment_notification_edit_skipped(self, mock_record):
        ok = await sync_payment_notification_edit(
            payment_method_slug="zelle",
            payment_id=81,
            notification_chat_id=None,
            notification_message_id=None,
            text="bound text",
        )
        self.assertFalse(ok)
        recorded = mock_record.call_args[0][0]
        self.assertEqual(recorded.event_type, EVENT_NOTIFICATION_EDIT_SKIPPED)

    @patch("bot.services.payment_binding_events.record_binding_event")
    @patch(
        "bot.services.venmo_payments.edit_telegram_notification",
        new_callable=AsyncMock,
    )
    async def test_sync_payment_notification_edit_failed(self, mock_edit, mock_record):
        mock_edit.side_effect = RuntimeError("message to edit not found")
        with self.assertRaises(RuntimeError):
            await sync_payment_notification_edit(
                payment_method_slug="zelle",
                payment_id=81,
                notification_chat_id=-527,
                notification_message_id=7253,
                text="bound text",
            )
        recorded = mock_record.call_args[0][0]
        self.assertEqual(recorded.event_type, EVENT_NOTIFICATION_EDIT_FAILED)
        self.assertIn("message to edit not found", recorded.error_message or "")


class TestPaymentBindingEventConstants(unittest.TestCase):
    def test_event_constants(self):
        self.assertEqual(EVENT_GROUP_BINDING_UPDATED, "group_binding_updated")


if __name__ == "__main__":
    unittest.main()
