"""Tests for bot_flow_sessions lifecycle and deposit session resolution."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bot.services.deposit_funnel_events import STEP_DEPOSIT_STARTED
from bot.services.flow_sessions import (
    END_REASON_CANCELLED,
    END_REASON_CHIPS_CREDITED,
    END_REASON_SUPERSEDED,
    FlowSessionInfo,
    ResolvedDepositSession,
    abandon_flow_session,
    complete_flow_session,
    get_active_session,
    resolve_deposit_session_id,
    start_flow_session,
)
from db.models import BotFlowSession, DepositFunnelEvent, PaymentMethodBindAttempt


class FlowSessionsLifecycleTest(unittest.TestCase):
    @patch("bot.services.flow_sessions.get_db")
    def test_start_supersedes_existing_active_session(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        existing = BotFlowSession(
            session_uuid="old-uuid",
            telegram_chat_id=-1001,
            flow_type="deposit",
            status="active",
            club_id=1,
            started_at=datetime.now(timezone.utc),
        )
        session.query.return_value.filter_by.return_value.one_or_none.return_value = (
            existing
        )

        new_uuid = start_flow_session(
            telegram_chat_id=-1001,
            flow_type="deposit",
            club_id=1,
            telegram_user_id=42,
        )

        self.assertNotEqual(new_uuid, "old-uuid")
        self.assertEqual(existing.status, "abandoned")
        self.assertEqual(existing.end_reason, END_REASON_SUPERSEDED)
        session.add.assert_called_once()

    @patch("bot.services.flow_sessions.get_db")
    def test_complete_marks_session_completed(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        row = BotFlowSession(
            session_uuid="sess-1",
            telegram_chat_id=-1001,
            flow_type="deposit",
            status="active",
            club_id=1,
            started_at=datetime.now(timezone.utc),
        )
        session.query.return_value.filter_by.return_value.one_or_none.return_value = row

        complete_flow_session("sess-1", end_reason=END_REASON_CHIPS_CREDITED)

        self.assertEqual(row.status, "completed")
        self.assertEqual(row.end_reason, END_REASON_CHIPS_CREDITED)
        self.assertIsNotNone(row.ended_at)

    @patch("bot.services.flow_sessions.get_db")
    def test_abandon_marks_session_abandoned(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        row = BotFlowSession(
            session_uuid="sess-2",
            telegram_chat_id=-1002,
            flow_type="deposit",
            status="active",
            club_id=1,
            started_at=datetime.now(timezone.utc),
        )
        session.query.return_value.filter_by.return_value.one_or_none.return_value = row

        abandon_flow_session("sess-2", end_reason=END_REASON_CANCELLED)

        self.assertEqual(row.status, "abandoned")
        self.assertEqual(row.end_reason, END_REASON_CANCELLED)

    @patch("bot.services.flow_sessions.get_db")
    def test_get_active_session_returns_info(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        started = datetime(2026, 1, 1, tzinfo=timezone.utc)
        row = BotFlowSession(
            session_uuid="sess-3",
            telegram_chat_id=-1003,
            flow_type="deposit",
            status="active",
            club_id=2,
            telegram_user_id=9,
            started_at=started,
        )
        session.query.return_value.filter_by.return_value.one_or_none.return_value = row

        info = get_active_session(-1003)

        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.session_uuid, "sess-3")
        self.assertEqual(info.flow_type, "deposit")


class ResolveDepositSessionIdTest(unittest.TestCase):
    @patch("bot.services.flow_sessions.get_db")
    def test_resolve_from_bind_attempt(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        attempt = PaymentMethodBindAttempt(
            id=5,
            telegram_chat_id=-1001,
            club_id=1,
            payment_method_slug="venmo",
            method_id=1,
            variant_id=1,
            deposit_session_id="bind-sess",
            bind_kind="special_amount",
            status="pending",
            expires_at=datetime.now(timezone.utc),
        )
        started = DepositFunnelEvent(
            deposit_session_id="bind-sess",
            step=STEP_DEPOSIT_STARTED,
            telegram_chat_id=-1001,
            club_id=1,
            telegram_user_id=3,
            is_first_deposit=True,
            requires_method_setup=True,
        )

        def query_side(model):
            q = MagicMock()
            if model is PaymentMethodBindAttempt:
                q.filter_by.return_value.one_or_none.return_value = attempt
            elif model is DepositFunnelEvent:
                q.filter_by.return_value.one_or_none.return_value = started
            return q

        session.query.side_effect = query_side

        resolved = resolve_deposit_session_id(bind_attempt_id=5)

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.deposit_session_id, "bind-sess")
        self.assertTrue(resolved.is_first_deposit)

    @patch("bot.services.flow_sessions.get_active_session")
    @patch("bot.services.flow_sessions.get_db")
    def test_resolve_falls_back_to_active_session(
        self, mock_get_db, mock_active
    ):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter_by.return_value.one_or_none.return_value = None
        mock_active.return_value = FlowSessionInfo(
            session_uuid="active-sess",
            telegram_chat_id=-1004,
            flow_type="deposit",
            status="active",
            club_id=1,
            telegram_user_id=4,
            started_at=datetime.now(timezone.utc),
        )
        started = DepositFunnelEvent(
            deposit_session_id="active-sess",
            step=STEP_DEPOSIT_STARTED,
            telegram_chat_id=-1004,
            club_id=1,
            is_first_deposit=False,
            requires_method_setup=False,
        )
        session.query.side_effect = lambda model: (
            MagicMock(
                filter_by=MagicMock(
                    return_value=MagicMock(one_or_none=MagicMock(return_value=started))
                )
            )
            if model is DepositFunnelEvent
            else MagicMock(
                filter_by=MagicMock(
                    return_value=MagicMock(one_or_none=MagicMock(return_value=None))
                )
            )
        )

        resolved = resolve_deposit_session_id(telegram_chat_id=-1004)

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.deposit_session_id, "active-sess")


if __name__ == "__main__":
    unittest.main()
