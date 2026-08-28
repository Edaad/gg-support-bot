"""Tests for union deposit ack step and message copy."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.manual_deposit_requests import (
    UnionAckValidationError,
    complete_union_ack,
    get_union_ack_pending,
    set_union_ack_pending,
)
from bot.services.union_deposit_messages import (
    UNION_ACK_EXPIRED_TEXT,
    UNION_INSTRUCTION_RECURRING_LINE,
    build_union_instruction_with_footer,
    build_union_special_instructions_text,
    union_ack_callback_data,
)
from bot.services.union_instruction_expiry import (
    expire_union_ack_now,
    list_pending_union_ack_expiries,
    restore_union_deposit_expiries,
    schedule_union_ack_expiry,
)


class UnionDepositMessagesTests(unittest.TestCase):
    def test_special_instructions_copy(self):
        text = build_union_special_instructions_text()
        self.assertIn("Special instructions;", text)
        self.assertIn("screen recording within 10 minutes", text)

    def test_instruction_includes_recurring_footer(self):
        method = SimpleNamespace(
            union_type="zelle",
            method_tag="$zelle-tag",
            payment_account_name=None,
            deposit_limit=Decimal("1000"),
            min_amount=Decimal("50"),
            max_amount=None,
        )
        text = build_union_instruction_with_footer(method, used_sum=Decimal("100"))
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn("Zelle Tag: $zelle-tag", text)
        self.assertTrue(text.endswith(UNION_INSTRUCTION_RECURRING_LINE))

    def test_ack_callback_data(self):
        self.assertEqual(union_ack_callback_data(42), "depum:42")


class UnionAckPendingTests(unittest.TestCase):
    def _pending_row(self, **overrides):
        now = datetime.now(timezone.utc)
        base = dict(
            id=1,
            telegram_chat_id=-100,
            initiated_by_telegram_user_id=555,
            acknowledged_at=None,
            ack_expired_at=None,
            trade_record_checked=False,
            ack_expires_at=now + timedelta(minutes=10),
            ack_telegram_message_id=900,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_get_union_ack_pending_ok(self):
        row = self._pending_row()
        session = MagicMock()
        session.get.return_value = row
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.manual_deposit_requests.get_db", return_value=cm):
            loaded = get_union_ack_pending(
                1,
                telegram_chat_id=-100,
                initiated_by_telegram_user_id=555,
            )
        self.assertEqual(loaded.id, 1)

    def test_get_union_ack_pending_wrong_user(self):
        row = self._pending_row()
        session = MagicMock()
        session.get.return_value = row
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.manual_deposit_requests.get_db", return_value=cm):
            with self.assertRaises(UnionAckValidationError):
                get_union_ack_pending(
                    1,
                    telegram_chat_id=-100,
                    initiated_by_telegram_user_id=999,
                )

    def test_set_and_complete_union_ack(self):
        ack_expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        instruction_expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        pending_row = SimpleNamespace(
            ack_telegram_message_id=None,
            ack_expires_at=None,
            acknowledged_at=None,
            instruction_telegram_message_ids=None,
            instruction_expires_at=None,
        )
        complete_row = SimpleNamespace(
            acknowledged_at=None,
            ack_expires_at=ack_expires,
            instruction_telegram_message_ids=None,
            instruction_expires_at=None,
        )
        session = MagicMock()
        session.get.side_effect = [pending_row, complete_row]
        session.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = (
            complete_row
        )
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.manual_deposit_requests.get_db", return_value=cm):
            set_union_ack_pending(1, ack_message_id=901, expires_at=ack_expires)
            row = complete_union_ack(
                1,
                instruction_message_ids=[902],
                instruction_expires_at=instruction_expires,
            )

        self.assertEqual(pending_row.ack_telegram_message_id, 901)
        self.assertEqual(pending_row.ack_expires_at, ack_expires)
        self.assertIsNotNone(row.acknowledged_at)
        self.assertIsNone(row.ack_expires_at)
        self.assertEqual(row.instruction_telegram_message_ids, [902])
        self.assertEqual(row.instruction_expires_at, instruction_expires)


class UnionAckExpiryTests(unittest.IsolatedAsyncioTestCase):
    def test_list_pending_ack_expiries(self):
        expires = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        session = MagicMock()
        session.query.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.all.return_value = [
            (3, expires),
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.union_instruction_expiry.get_db", return_value=cm):
            pending = list_pending_union_ack_expiries()

        self.assertEqual(pending, [(3, expires)])

    def test_schedule_ack_expiry(self):
        jq = MagicMock()
        jq.get_jobs_by_name.return_value = []
        context = SimpleNamespace(job_queue=jq)
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)

        schedule_union_ack_expiry(context, 4, expires_at=expires)
        jq.run_once.assert_called_once()
        self.assertEqual(jq.run_once.call_args.kwargs["name"], "union_ack_expire_4")

    async def test_expire_ack_edits_message(self):
        bot = AsyncMock()
        now = datetime.now(timezone.utc)
        row = SimpleNamespace(
            id=8,
            telegram_chat_id=-100,
            ack_telegram_message_id=7001,
            ack_expires_at=now - timedelta(minutes=1),
            ack_expired_at=None,
            acknowledged_at=None,
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
            applied = await expire_union_ack_now(bot, 8)

        self.assertTrue(applied)
        self.assertIsNotNone(row.ack_expired_at)
        self.assertIsNone(row.ack_expires_at)
        bot.edit_message_text.assert_awaited_once_with(
            chat_id=-100,
            message_id=7001,
            text=UNION_ACK_EXPIRED_TEXT,
        )

    def test_restore_union_deposit_expiries_schedules_both(self):
        jq = MagicMock()
        jq.get_jobs_by_name.return_value = []
        ack_expires = datetime.now(timezone.utc) + timedelta(minutes=3)
        instr_expires = datetime.now(timezone.utc) + timedelta(minutes=8)

        with patch(
            "bot.services.union_instruction_expiry.list_pending_union_ack_expiries",
            return_value=[(1, ack_expires)],
        ), patch(
            "bot.services.union_instruction_expiry.list_pending_union_instruction_expiries",
            return_value=[(2, instr_expires)],
        ), patch(
            "bot.services.union_instruction_expiry._resolve_job_queue",
            return_value=jq,
        ):
            restore_union_deposit_expiries(jq)

        self.assertEqual(jq.run_once.call_count, 2)


if __name__ == "__main__":
    unittest.main()
