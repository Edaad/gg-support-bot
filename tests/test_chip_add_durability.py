"""Durable chip-add: saved before the ClubGG call, dropped after the ttl."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from bot.services import clubgg_deposit_api as api
from bot.services import payment_auto_deposit as pad
from bot.services.chip_add_recovery import (
    payment_from_request_id,
    resume_due_chip_adds,
)
from bot.services.clubgg_rpa_events import (
    CHIP_ADD_CLAIMABLE,
    CHIP_ADD_SWEEP_GRACE,
    chip_add_expired,
    pending_chip_add_due,
)


def _now() -> datetime:
    return datetime(2026, 10, 24, 2, 16, tzinfo=timezone.utc)


class TestChipAddTtl(unittest.TestCase):
    def test_pending_is_due_after_the_grace_period(self) -> None:
        now = _now()
        self.assertTrue(
            pending_chip_add_due(
                started_at=now - timedelta(seconds=20),
                expires_at=now + timedelta(minutes=5),
                now=now,
            )
        )

    def test_pending_waits_out_the_grace_period(self) -> None:
        now = _now()
        self.assertFalse(
            pending_chip_add_due(
                started_at=now - timedelta(seconds=4),
                expires_at=now + timedelta(minutes=5),
                now=now,
                grace=CHIP_ADD_SWEEP_GRACE,
            )
        )

    def test_expired_request_is_not_sent(self) -> None:
        now = _now()
        expires = now - timedelta(seconds=1)
        self.assertTrue(chip_add_expired(expires, now))
        self.assertFalse(
            pending_chip_add_due(
                started_at=now - timedelta(minutes=11),
                expires_at=expires,
                now=now,
            )
        )


class TestStaffAddClaim(unittest.TestCase):
    def test_command_lock_can_be_claimed_for_the_send(self) -> None:
        self.assertIn("started", CHIP_ADD_CLAIMABLE)
        self.assertNotIn("sending", CHIP_ADD_CLAIMABLE)


class TestExpiredChipAddAlert(unittest.IsolatedAsyncioTestCase):
    async def test_expired_chip_add_includes_reason(self) -> None:
        expired = [
            {
                "request_id": "payment-zelle-1",
                "club_id": 4,
                "telegram_chat_id": -99,
                "group_title": "GTO / 1 / Nick",
            }
        ]
        with (
            patch(
                "bot.services.chip_add_recovery.expire_elapsed_chip_adds",
                return_value=expired,
            ),
            patch(
                "bot.services.chip_add_recovery.due_pending_chip_adds",
                return_value=[],
            ),
            patch(
                "bot.services.escalation_notification.notify_rpa_deposit_failed",
                new_callable=AsyncMock,
            ) as notify,
        ):
            resumed = await resume_due_chip_adds(_now())
        self.assertEqual(resumed, 0)
        notify.assert_awaited_once_with(
            club_id=4,
            chat_id=-99,
            title="GTO / 1 / Nick",
            detail="chip add expired before it could run",
        )


class TestPaymentRequestId(unittest.TestCase):
    def test_parses_zelle_payment(self) -> None:
        self.assertEqual(payment_from_request_id("payment-zelle-4723"), ("zelle", 4723))

    def test_ignores_staff_add(self) -> None:
        self.assertIsNone(payment_from_request_id("tg--5569119710-12"))


class TestScheduleSavesBeforeTheTask(unittest.IsolatedAsyncioTestCase):
    async def test_persist_runs_before_the_background_task(self) -> None:
        loop = Mock()
        order: list[str] = []

        def _capture_task(coro, **kwargs):
            coro.close()
            order.append("task")

        loop.create_task.side_effect = _capture_task
        with (
            patch.object(
                pad,
                "_persist_payment_chip_add",
                side_effect=lambda **kwargs: order.append("persist"),
            ),
            patch.object(pad.asyncio, "get_running_loop", return_value=loop),
            patch.object(pad, "record_payment_funnel_from_ingest"),
        ):
            pad.schedule_auto_deposit_from_payment(
                club_id=4,
                telegram_chat_id=-1,
                amount_cents=3000,
                auto_bound=True,
                payment_method_slug="zelle",
                payment_id=4723,
                group_title="GTO / 4175-7082 / Sam Malone",
            )
        self.assertEqual(order, ["persist", "task"])

    def test_ineligible_payment_is_not_saved(self) -> None:
        with (
            patch.object(
                pad,
                "auto_deposit_ineligible_reason",
                return_value="no_recent_deposit_command",
            ),
            patch("bot.services.clubgg_rpa_events.enqueue_pending_chip_add") as enqueue,
        ):
            pad._persist_payment_chip_add(
                club_id=4,
                telegram_chat_id=-1,
                amount_cents=3000,
                auto_bound=True,
                payment_method_slug="zelle",
                payment_id=4723,
                group_title="GTO / 4175-7082 / Sam Malone",
                goods_or_services=False,
                requires_refund=False,
            )
        enqueue.assert_not_called()


class TestClaimGate(unittest.IsolatedAsyncioTestCase):
    async def test_owned_request_is_not_sent_again(self) -> None:
        with (
            patch("bot.services.clubgg_rpa_events.enqueue_pending_chip_add") as enqueue,
            patch(
                "bot.services.clubgg_rpa_events.claim_pending_chip_add",
                return_value="owned",
            ),
            patch.object(
                api, "_run_auto_chip_add_body", new_callable=AsyncMock
            ) as body,
        ):
            ok, status = await api.run_auto_chip_add(
                club_id=4,
                chat_id=-1,
                amount=Decimal("30"),
                request_id="payment-zelle-4723",
                group_title="GTO / 4175-7082 / Sam Malone",
            )
        self.assertFalse(ok)
        self.assertEqual(status, "owned")
        enqueue.assert_called_once()
        body.assert_not_called()

    async def test_expired_request_is_not_sent(self) -> None:
        with (
            patch("bot.services.clubgg_rpa_events.enqueue_pending_chip_add"),
            patch(
                "bot.services.clubgg_rpa_events.claim_pending_chip_add",
                return_value="expired",
            ),
            patch.object(
                api, "_run_auto_chip_add_body", new_callable=AsyncMock
            ) as body,
        ):
            ok, status = await api.run_auto_chip_add(
                club_id=4,
                chat_id=-1,
                amount=Decimal("30"),
                request_id="payment-zelle-4723",
            )
        self.assertFalse(ok)
        self.assertEqual(status, "expired")
        body.assert_not_called()
