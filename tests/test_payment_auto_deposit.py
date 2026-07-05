"""Tests for Creator Club payment auto-deposit orchestration."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import payment_auto_deposit as pad
from bot.services.clubgg_deposit_api import (
    _claim_request,
    request_id_for_payment,
    request_id_with_part,
)

CHAT_ID = -1001234567890
CLUB_ID_CREATOR = 3
CLUB_ID_RT = 1


class RequestIdPaymentTestCase(unittest.TestCase):
    def test_payment_request_id(self) -> None:
        self.assertEqual(request_id_for_payment("venmo", 42), "payment-venmo-42")

    def test_payment_request_id_with_bonus_part(self) -> None:
        self.assertEqual(
            request_id_with_part("payment-venmo-42", part="bonus"),
            "payment-venmo-42-bonus",
        )


class MaybeAutoDepositGatingTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_skips_when_not_auto_bound(self) -> None:
        with patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock) as mock_run:
            await pad.maybe_auto_deposit_from_payment(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=False,
                payment_method_slug="venmo",
                payment_id=1,
            )
        mock_run.assert_not_awaited()

    async def test_skips_goods_and_services(self) -> None:
        with patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock) as mock_run:
            await pad.maybe_auto_deposit_from_payment(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=True,
                payment_method_slug="venmo",
                payment_id=1,
                goods_or_services=True,
            )
        mock_run.assert_not_awaited()

    async def test_skips_non_creator_club(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name="Round Table"),
            ),
            patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock) as mock_run,
        ):
            await pad.maybe_auto_deposit_from_payment(
                club_id=CLUB_ID_RT,
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=True,
                payment_method_slug="venmo",
                payment_id=1,
            )
        mock_run.assert_not_awaited()

    async def test_skips_when_auto_chip_adding_disabled(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_chip_adding_enabled", return_value=False),
            patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock) as mock_run,
        ):
            await pad.maybe_auto_deposit_from_payment(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=True,
                payment_method_slug="venmo",
                payment_id=1,
            )
        mock_run.assert_not_awaited()

    async def test_skips_when_ids_missing(self) -> None:
        with patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock) as mock_run:
            await pad.maybe_auto_deposit_from_payment(
                club_id=None,
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=True,
                payment_method_slug="venmo",
                payment_id=1,
            )
        mock_run.assert_not_awaited()


class MaybeAutoDepositSuccessTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_success_records_activity_and_sends_confirmation(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_chip_adding_enabled", return_value=True),
            patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock, return_value=True) as mock_run,
            patch.object(pad, "record_activity_for_chat") as mock_record,
            patch.object(pad, "invalidate_pending_one_time_bypasses") as mock_invalidate,
            patch.object(pad, "_send_add_confirmation", new_callable=AsyncMock) as mock_confirm,
        ):
            await pad.maybe_auto_deposit_from_payment(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=True,
                payment_method_slug="venmo",
                payment_id=99,
                group_title="CC / 1234-5678 / Jacob",
            )

        mock_run.assert_awaited_once()
        call_kwargs = mock_run.await_args.kwargs
        self.assertEqual(call_kwargs["club_id"], CLUB_ID_CREATOR)
        self.assertEqual(call_kwargs["chat_id"], CHAT_ID)
        self.assertEqual(call_kwargs["amount"], Decimal(50))
        self.assertEqual(call_kwargs["request_id"], "payment-venmo-99")
        mock_record.assert_called_once_with(CLUB_ID_CREATOR, CHAT_ID, "deposit")
        mock_invalidate.assert_called_once_with(CLUB_ID_CREATOR, CHAT_ID)
        mock_confirm.assert_awaited_once()

    async def test_failure_skips_activity_and_confirmation(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_chip_adding_enabled", return_value=True),
            patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock, return_value=False),
            patch.object(pad, "record_activity_for_chat") as mock_record,
            patch.object(pad, "_send_add_confirmation", new_callable=AsyncMock) as mock_confirm,
        ):
            await pad.maybe_auto_deposit_from_payment(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=True,
                payment_method_slug="zelle",
                payment_id=2,
                group_title="CC / 1234-5678 / Player",
            )

        mock_record.assert_not_called()
        mock_confirm.assert_not_awaited()


class SendAddConfirmationTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_mtproto_path_used_when_configured(self) -> None:
        cfg = MagicMock()
        with (
            patch.object(pad, "get_club_gc_config_by_link_club_id", return_value=cfg),
            patch.object(
                pad,
                "_send_add_confirmation_once",
                new_callable=AsyncMock,
            ) as mock_mtproto,
            patch.object(pad, "format_add_confirmation", return_value="Added 50 chips, good luck!!"),
        ):
            await pad._send_add_confirmation(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount=Decimal(50),
                group_title="CC / 1234-5678 / Jacob",
            )
        mock_mtproto.assert_awaited_once_with(cfg, CHAT_ID, "Added 50 chips, good luck!!")

    async def test_bot_fallback_when_mtproto_unavailable(self) -> None:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        with (
            patch.object(pad, "get_club_gc_config_by_link_club_id", return_value=None),
            patch.object(pad, "format_add_confirmation", return_value="Added 50 chips, good luck!!"),
            patch.object(pad, "support_bot_tokens_to_try", return_value=["token"]),
            patch.object(pad, "Bot", return_value=mock_bot),
        ):
            await pad._send_add_confirmation(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount=Decimal(50),
                group_title=None,
            )
        mock_bot.send_message.assert_awaited_once_with(
            chat_id=CHAT_ID,
            text="Added 50 chips, good luck!!",
        )


class PaymentRequestIdempotencyTestCase(unittest.TestCase):
    def test_claim_request_prevents_duplicate_payment_id(self) -> None:
        rid = request_id_for_payment("venmo", 12345)
        self.assertTrue(_claim_request(rid))
        self.assertFalse(_claim_request(rid))


class PlayerLabelFromTitleTestCase(unittest.TestCase):
    def test_extracts_tail_label(self) -> None:
        self.assertEqual(
            pad._player_label_from_title("CC / 1234-5678 / Jacob"),
            "Jacob",
        )

    def test_returns_none_without_tail(self) -> None:
        self.assertIsNone(pad._player_label_from_title("CC / 1234-5678"))


if __name__ == "__main__":
    unittest.main()
