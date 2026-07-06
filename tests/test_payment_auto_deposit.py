"""Tests for Creator Club payment auto-deposit orchestration."""

from __future__ import annotations

import unittest
from contextlib import ExitStack
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


def _enter_creator_club_eligible(stack: ExitStack) -> None:
    stack.enter_context(
        patch.object(
            pad,
            "get_club_by_id",
            return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
        )
    )
    stack.enter_context(
        patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=True)
    )
    stack.enter_context(
        patch.object(pad, "has_recent_deposit_command_in_chat", return_value=True)
    )


class RequestIdPaymentTestCase(unittest.TestCase):
    def test_payment_request_id(self) -> None:
        self.assertEqual(request_id_for_payment("venmo", 42), "payment-venmo-42")

    def test_payment_request_id_with_bonus_part(self) -> None:
        self.assertEqual(
            request_id_with_part("payment-venmo-42", part="bonus"),
            "payment-venmo-42-bonus",
        )


class AutoDepositIneligibleReasonTestCase(unittest.TestCase):
    def test_eligible_returns_none(self) -> None:
        with ExitStack() as stack:
            _enter_creator_club_eligible(stack)
            self.assertIsNone(
                pad.auto_deposit_ineligible_reason(
                    club_id=CLUB_ID_CREATOR,
                    telegram_chat_id=CHAT_ID,
                    auto_bound=True,
                    group_title="CC / 1234-5678 / Jacob",
                )
            )

    def test_toggle_off(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=False),
        ):
            self.assertEqual(
                pad.auto_deposit_ineligible_reason(
                    club_id=CLUB_ID_CREATOR,
                    telegram_chat_id=CHAT_ID,
                    auto_bound=True,
                ),
                "auto_deposit_on_payment_disabled",
            )

    def test_no_recent_deposit_command(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=True),
            patch.object(pad, "has_recent_deposit_command_in_chat", return_value=False),
        ):
            self.assertEqual(
                pad.auto_deposit_ineligible_reason(
                    club_id=CLUB_ID_CREATOR,
                    telegram_chat_id=CHAT_ID,
                    auto_bound=True,
                    group_title="CC / 1234-5678 / Jacob",
                ),
                "no_recent_deposit_command",
            )

    def test_no_player_id_in_title(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=True),
            patch.object(pad, "has_recent_deposit_command_in_chat", return_value=True),
        ):
            self.assertEqual(
                pad.auto_deposit_ineligible_reason(
                    club_id=CLUB_ID_CREATOR,
                    telegram_chat_id=CHAT_ID,
                    auto_bound=True,
                    group_title="CC / / @wywyrobro",
                ),
                "no_player_id_in_title",
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

    async def test_logs_skip_reason_for_creator_club(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=False),
            patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock) as mock_run,
            patch.object(pad.logger, "info") as mock_log,
        ):
            await pad.maybe_auto_deposit_from_payment(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=True,
                payment_method_slug="zelle",
                payment_id=1115,
            )
        mock_run.assert_not_awaited()
        mock_log.assert_any_call(
            "payment_auto_deposit: skipped method=%s payment_id=%s "
            "chat_id=%s club_id=%s auto_bound=%s reason=%s",
            "zelle",
            1115,
            CHAT_ID,
            CLUB_ID_CREATOR,
            True,
            "auto_deposit_on_payment_disabled",
        )

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

    async def test_skips_when_auto_deposit_on_payment_disabled(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=False),
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

    async def test_skips_when_no_player_id_in_title(self) -> None:
        with (
            patch.object(
                pad,
                "get_club_by_id",
                return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
            ),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=True),
            patch.object(pad, "has_recent_deposit_command_in_chat", return_value=True),
            patch.object(pad, "run_auto_chip_add", new_callable=AsyncMock) as mock_run,
            patch.object(pad.logger, "info") as mock_log,
        ):
            await pad.maybe_auto_deposit_from_payment(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                amount_cents=2000,
                auto_bound=True,
                payment_method_slug="stripe",
                payment_id=77,
                group_title="CC / / @wywyrobro",
            )
        mock_run.assert_not_awaited()
        mock_log.assert_any_call(
            "payment_auto_deposit: skipped method=%s payment_id=%s "
            "chat_id=%s club_id=%s auto_bound=%s reason=%s",
            "stripe",
            77,
            CHAT_ID,
            CLUB_ID_CREATOR,
            True,
            "no_player_id_in_title",
        )

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
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=True),
            patch.object(pad, "has_recent_deposit_command_in_chat", return_value=True),
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
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=True),
            patch.object(pad, "has_recent_deposit_command_in_chat", return_value=True),
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


class CreatorStaffFooterTestCase(unittest.TestCase):
    def _creator_club_patch(self):
        return patch.object(
            pad,
            "get_club_by_id",
            return_value=SimpleNamespace(name=pad.CREATOR_CLUB_NAME),
        )

    def test_manual_footer_when_no_player_id_in_title(self) -> None:
        with ExitStack() as stack:
            _enter_creator_club_eligible(stack)
            footer = pad.format_creator_club_staff_footer(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                auto_bound=True,
                group_title="CC / / @wywyrobro",
            )
        self.assertEqual(footer, pad.CREATOR_STAFF_FOOTER_MANUAL)

    def test_eligible_footer(self) -> None:
        with ExitStack() as stack:
            _enter_creator_club_eligible(stack)
            footer = pad.format_creator_club_staff_footer(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                auto_bound=True,
                group_title="CC / 1234-5678 / Jacob",
            )
        self.assertEqual(footer, pad.CREATOR_STAFF_FOOTER_AUTO)

    def test_no_recent_deposit_footer(self) -> None:
        with (
            self._creator_club_patch(),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=True),
            patch.object(pad, "has_recent_deposit_command_in_chat", return_value=False),
        ):
            footer = pad.format_creator_club_staff_footer(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                auto_bound=True,
                group_title="CC / 1234-5678 / Jacob",
            )
        self.assertEqual(footer, pad.CREATOR_STAFF_FOOTER_NO_RECENT_DEPOSIT)

    def test_manual_footer_when_not_auto_bound(self) -> None:
        with self._creator_club_patch():
            footer = pad.format_creator_club_staff_footer(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                auto_bound=False,
            )
        self.assertEqual(footer, pad.CREATOR_STAFF_FOOTER_MANUAL)

    def test_manual_footer_when_goods_and_services(self) -> None:
        with (
            self._creator_club_patch(),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=True),
        ):
            footer = pad.format_creator_club_staff_footer(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                auto_bound=True,
                goods_or_services=True,
            )
        self.assertEqual(footer, pad.CREATOR_STAFF_FOOTER_MANUAL)

    def test_manual_footer_when_auto_deposit_disabled(self) -> None:
        with (
            self._creator_club_patch(),
            patch.object(pad, "get_auto_deposit_on_payment_enabled", return_value=False),
        ):
            footer = pad.format_creator_club_staff_footer(
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                auto_bound=True,
                group_title="CC / 1234-5678 / Jacob",
            )
        self.assertEqual(footer, pad.CREATOR_STAFF_FOOTER_MANUAL)

    def test_no_footer_for_round_table(self) -> None:
        with patch.object(
            pad,
            "get_club_by_id",
            return_value=SimpleNamespace(name="Round Table"),
        ):
            footer = pad.format_creator_club_staff_footer(
                club_id=CLUB_ID_RT,
                telegram_chat_id=CHAT_ID,
                auto_bound=True,
            )
        self.assertIsNone(footer)

    def test_append_preserves_body(self) -> None:
        body = "🔔 Venmo Payment Notification\n\nAmount: $50"
        with ExitStack() as stack:
            _enter_creator_club_eligible(stack)
            out = pad.append_creator_club_staff_footer(
                body,
                club_id=CLUB_ID_CREATOR,
                telegram_chat_id=CHAT_ID,
                auto_bound=True,
                group_title="CC / 1234-5678 / Jacob",
            )
        self.assertTrue(out.startswith(body))
        self.assertIn(pad.CREATOR_STAFF_FOOTER_AUTO, out)

    def test_append_unchanged_for_non_creator(self) -> None:
        body = "🔔 Venmo Payment Notification"
        with patch.object(
            pad,
            "get_club_by_id",
            return_value=SimpleNamespace(name="ClubGTO"),
        ):
            out = pad.append_creator_club_staff_footer(
                body,
                club_id=2,
                telegram_chat_id=CHAT_ID,
                auto_bound=True,
            )
        self.assertEqual(out, body)


if __name__ == "__main__":
    unittest.main()
