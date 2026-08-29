"""Tests for union manual-deposit instruction expiry."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.union_deposit_messages import UNION_INSTRUCTION_EXPIRED_TEXT
from bot.services.union_instruction_expiry import (
    cancel_union_instruction_expiry,
    expire_union_ack_now,
    expire_union_instruction_now,
    list_pending_union_ack_expiries,
    list_pending_union_instruction_expiries,
    restore_union_deposit_expiries,
    restore_union_instruction_expiries,
    schedule_union_ack_expiry,
    schedule_union_instruction_expiry,
    sweep_overdue_union_deposit_expiries,
)


class UnionInstructionExpiryTests(unittest.TestCase):
    def test_expired_text_matches_copy(self):
        self.assertIn(
            "The provided payment method has expired.",
            UNION_INSTRUCTION_EXPIRED_TEXT,
        )
        self.assertIn(
            "Please do not send a payment to this tag if you have not already.",
            UNION_INSTRUCTION_EXPIRED_TEXT,
        )
        self.assertIn("/deposit", UNION_INSTRUCTION_EXPIRED_TEXT)
        self.assertNotIn("•", UNION_INSTRUCTION_EXPIRED_TEXT)

    def test_create_request_atomic_sets_expiry_for_union(self):
        from bot.services.manual_deposit_requests import create_request_atomic
        from decimal import Decimal

        method = SimpleNamespace(
            id=3,
            name="Zelle",
            slug="zelle-union",
            union_type="zelle",
            method_tag="pay@zelle",
            tracks_manual_requests=True,
            is_active=True,
            deposit_limit=Decimal("1000"),
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = (
            method
        )
        captured: dict = {}

        def _add(row):
            captured["row"] = row
            row.id = 42

        session.add.side_effect = _add
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch(
            "bot.services.manual_deposit_requests.get_db", return_value=cm
        ), patch(
            "bot.services.manual_deposit_requests.capacity_allows",
            return_value=True,
        ):
            row = create_request_atomic(
                club_id=1,
                method_id=3,
                amount=Decimal("100"),
                telegram_chat_id=-100,
                group_title="GTO / 2222-2222 / jz",
                instruction_message_ids=[501, 502],
            )

        self.assertEqual(row.instruction_telegram_message_ids, [501, 502])
        self.assertIsNotNone(row.instruction_expires_at)

    def test_create_request_atomic_skips_expiry_without_message_ids(self):
        from bot.services.manual_deposit_requests import create_request_atomic
        from decimal import Decimal

        method = SimpleNamespace(
            id=3,
            name="Zelle",
            slug="zelle-union",
            union_type="zelle",
            method_tag="pay@zelle",
            tracks_manual_requests=True,
            is_active=True,
            deposit_limit=Decimal("1000"),
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = (
            method
        )

        def _add(row):
            row.id = 43

        session.add.side_effect = _add
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch(
            "bot.services.manual_deposit_requests.get_db", return_value=cm
        ), patch(
            "bot.services.manual_deposit_requests.capacity_allows",
            return_value=True,
        ):
            row = create_request_atomic(
                club_id=1,
                method_id=3,
                amount=Decimal("100"),
                telegram_chat_id=-100,
            )

        self.assertIsNone(row.instruction_expires_at)

    def test_list_pending_expiries(self):
        expires = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        session = MagicMock()
        session.query.return_value.filter.return_value.filter.return_value.filter.return_value.all.return_value = [
            (7, expires),
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.union_instruction_expiry.get_db", return_value=cm):
            pending = list_pending_union_instruction_expiries()

        self.assertEqual(pending, [(7, expires)])

    def test_schedule_and_cancel_job(self):
        jq = MagicMock()
        jq.get_jobs_by_name.return_value = [MagicMock()]
        context = SimpleNamespace(job_queue=jq)
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)

        schedule_union_instruction_expiry(context, 9, expires_at=expires)
        jq.run_once.assert_called_once()
        self.assertEqual(jq.run_once.call_args.kwargs["name"], "union_instruction_expire_9")

        session = MagicMock()
        session.get.return_value = SimpleNamespace(instruction_expires_at=expires)
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.union_instruction_expiry.get_db", return_value=cm):
            cancel_union_instruction_expiry(9, job_queue=jq)
        self.assertGreaterEqual(
            jq.get_jobs_by_name.return_value[0].schedule_removal.call_count,
            1,
        )

    def test_restore_reschedules_pending(self):
        jq = MagicMock()
        jq.get_jobs_by_name.return_value = []
        expires = datetime.now(timezone.utc) + timedelta(minutes=5)

        with patch(
            "bot.services.union_instruction_expiry.list_pending_union_instruction_expiries",
            return_value=[(11, expires)],
        ), patch(
            "bot.services.union_instruction_expiry._resolve_job_queue",
            return_value=jq,
        ):
            restore_union_instruction_expiries(jq)

        jq.run_once.assert_called_once()

    async def _run_expire(self):
        bot = AsyncMock()
        now = datetime.now(timezone.utc)
        row = SimpleNamespace(
            id=5,
            telegram_chat_id=-100,
            instruction_telegram_message_ids=[9001, 9002],
            instruction_expires_at=now - timedelta(minutes=1),
            instruction_expired_at=None,
            trade_record_checked=False,
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = (
            row
        )
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.union_instruction_expiry.get_db", return_value=cm):
            applied = await expire_union_instruction_now(bot, 5)

        self.assertTrue(applied)
        self.assertIsNotNone(row.instruction_expired_at)
        self.assertIsNone(row.instruction_expires_at)
        self.assertEqual(bot.edit_message_text.await_count, 2)
        bot.edit_message_text.assert_any_await(
            chat_id=-100,
            message_id=9001,
            text=UNION_INSTRUCTION_EXPIRED_TEXT,
        )

    def test_expire_edits_all_instruction_messages(self):
        import asyncio

        asyncio.run(self._run_expire())

    async def _run_expire_skips_checked(self):
        bot = AsyncMock()
        row = SimpleNamespace(
            id=6,
            telegram_chat_id=-100,
            instruction_telegram_message_ids=[9001],
            instruction_expires_at=datetime.now(timezone.utc),
            instruction_expired_at=None,
            trade_record_checked=True,
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = (
            row
        )
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.union_instruction_expiry.get_db", return_value=cm):
            applied = await expire_union_instruction_now(bot, 6)

        self.assertFalse(applied)
        bot.edit_message_text.assert_not_awaited()
        self.assertIsNone(row.instruction_expires_at)

    def test_expire_skips_when_trade_record_checked(self):
        import asyncio

        asyncio.run(self._run_expire_skips_checked())

    def test_sweep_overdue_instruction_expiry(self):
        import asyncio

        asyncio.run(self._run_sweep_overdue_instruction_expiry())

    async def _run_sweep_overdue_instruction_expiry(self):
        bot = AsyncMock()
        expire_mock = AsyncMock(return_value=True)

        with patch(
            "bot.services.union_instruction_expiry._list_overdue_union_ack_request_ids",
            return_value=[],
        ), patch(
            "bot.services.union_instruction_expiry._list_overdue_union_instruction_request_ids",
            return_value=[12],
        ), patch(
            "bot.services.union_instruction_expiry.expire_union_instruction_now",
            expire_mock,
        ):
            await sweep_overdue_union_deposit_expiries(bot)

        expire_mock.assert_awaited_once_with(bot, 12)


if __name__ == "__main__":
    unittest.main()
