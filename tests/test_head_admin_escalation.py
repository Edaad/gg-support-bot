"""Tests for head-admin escalation fan-out (RPA failures)."""

from __future__ import annotations

import logging
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import escalation_notification as esc
from bot.services import head_admin_escalation as ha
from bot.services.slack_ops_notify import notify_slack_head_admin_escalation


class HeadAdminAllowlistTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_rpa_reason_is_noop(self) -> None:
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
        ) as notify:
            ok = await ha.maybe_notify_head_admin_escalation(
                "Cash out initiated.",
                reason=esc.REASON_CASHOUT_STARTED,
            )
        self.assertFalse(ok)
        notify.assert_not_awaited()

    async def test_rpa_deposit_calls_slack(self) -> None:
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
            return_value=True,
        ) as notify:
            ok = await ha.maybe_notify_head_admin_escalation(
                "RPA deposit failed — add chips manually.\nClub: X",
                reason=esc.REASON_RPA_DEPOSIT_FAILED,
            )
        self.assertTrue(ok)
        notify.assert_awaited_once_with(
            "RPA deposit failed — add chips manually.\nClub: X",
            source=esc.REASON_RPA_DEPOSIT_FAILED,
        )

    async def test_rpa_cashout_calls_slack(self) -> None:
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
            return_value=True,
        ) as notify:
            ok = await ha.maybe_notify_head_admin_escalation(
                "RPA cashout failed — claim chips manually.\nClub: X",
                reason=esc.REASON_RPA_CASHOUT_FAILED,
            )
        self.assertTrue(ok)
        notify.assert_awaited_once()

    async def test_rpa_deposit_uncertain_calls_slack(self) -> None:
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
            return_value=True,
        ) as notify:
            ok = await ha.maybe_notify_head_admin_escalation(
                "Deposit UNCERTAIN — verify on ClubGG (do not retry).\n"
                "Club: ClubGTO\n"
                "OCR mismatch on trade record amount: saw '', expected one of ['1']",
                reason=esc.REASON_RPA_DEPOSIT_UNCERTAIN,
            )
        self.assertTrue(ok)
        notify.assert_awaited_once()

    async def test_rpa_cashout_uncertain_calls_slack(self) -> None:
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
            return_value=True,
        ) as notify:
            ok = await ha.maybe_notify_head_admin_escalation(
                "Cashout UNCERTAIN — verify on ClubGG (do not re-claim).\nClub: X",
                reason=esc.REASON_RPA_CASHOUT_UNCERTAIN,
            )
        self.assertTrue(ok)
        notify.assert_awaited_once()


    async def test_union_deposit_first_calls_head_admin(self) -> None:
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
            return_value=True,
        ) as notify:
            ok = await ha.maybe_notify_head_admin_escalation(
                "First-time union deposit — verify with union.",
                reason=esc.REASON_UNION_DEPOSIT_FIRST,
            )
        self.assertTrue(ok)
        notify.assert_awaited_once()


class NotifyEscalationSlackFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_rpa_fans_out_identical_text(self) -> None:
        with patch.object(esc, "_club_display_name", return_value="Round Table"):
            expected = esc.format_escalation_slack_text(
                esc.REASON_RPA_DEPOSIT_FAILED,
                club_id=1,
                chat_id=99,
                title="CC / 1 / Nick",
            )
            with patch(
                "bot.services.slack_ops_notify.notify_slack_escalation",
                new_callable=AsyncMock,
                return_value=True,
            ) as normal:
                with patch(
                    "bot.services.head_admin_escalation.maybe_notify_head_admin_escalation",
                    new_callable=AsyncMock,
                    return_value=True,
                ) as head:
                    ok = await esc.notify_escalation_slack(
                        esc.REASON_RPA_DEPOSIT_FAILED,
                        club_id=1,
                        chat_id=99,
                        title="CC / 1 / Nick",
                    )
        self.assertTrue(ok)
        normal.assert_awaited_once_with(
            expected, source=esc.REASON_RPA_DEPOSIT_FAILED
        )
        head.assert_awaited_once_with(
            expected, reason=esc.REASON_RPA_DEPOSIT_FAILED
        )

    async def test_non_rpa_still_calls_maybe_notify_which_noops(self) -> None:
        with patch.object(esc, "_club_display_name", return_value="Round Table"):
            with patch(
                "bot.services.slack_ops_notify.notify_slack_escalation",
                new_callable=AsyncMock,
                return_value=True,
            ) as normal:
                with patch(
                    "bot.services.head_admin_escalation.maybe_notify_head_admin_escalation",
                    new_callable=AsyncMock,
                    return_value=False,
                ) as head:
                    ok = await esc.notify_escalation_slack(
                        esc.REASON_CASHOUT_STARTED,
                        club_id=1,
                        chat_id=99,
                        title="G",
                    )
        self.assertTrue(ok)
        normal.assert_awaited_once()
        head.assert_awaited_once()
        self.assertEqual(
            head.await_args.kwargs["reason"], esc.REASON_CASHOUT_STARTED
        )

    async def test_head_admin_runs_even_if_normal_fails(self) -> None:
        with patch.object(esc, "_club_display_name", return_value="Round Table"):
            with patch(
                "bot.services.slack_ops_notify.notify_slack_escalation",
                new_callable=AsyncMock,
                return_value=False,
            ):
                with patch(
                    "bot.services.head_admin_escalation.maybe_notify_head_admin_escalation",
                    new_callable=AsyncMock,
                    return_value=True,
                ) as head:
                    ok = await esc.notify_escalation_slack(
                        esc.REASON_RPA_CASHOUT_FAILED,
                        club_id=1,
                        chat_id=99,
                        title="G",
                    )
        self.assertFalse(ok)
        head.assert_awaited_once()


class RpaGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_rpa_deposit_skipped_when_club_toggle_off(self) -> None:
        with patch.object(esc, "escalation_notification_enabled", return_value=False):
            with patch.object(
                esc, "notify_escalation_slack", new_callable=AsyncMock
            ) as notify:
                await esc.notify_rpa_deposit_failed(
                    club_id=1, chat_id=99, title="G"
                )
        notify.assert_not_awaited()

    async def test_rpa_cashout_skipped_when_club_toggle_off(self) -> None:
        with patch.object(esc, "escalation_notification_enabled", return_value=False):
            with patch.object(
                esc, "notify_escalation_slack", new_callable=AsyncMock
            ) as notify:
                await esc.notify_rpa_cashout_failed(
                    club_id=1, chat_id=99, title="G"
                )
        notify.assert_not_awaited()

    async def test_rpa_deposit_notifies_when_enabled(self) -> None:
        with patch.object(esc, "escalation_notification_enabled", return_value=True):
            with patch.object(
                esc, "notify_escalation_slack", new_callable=AsyncMock
            ) as notify:
                await esc.notify_rpa_deposit_failed(
                    club_id=1, chat_id=99, title="G"
                )
        notify.assert_awaited_once()
        self.assertEqual(
            notify.await_args.args[0], esc.REASON_RPA_DEPOSIT_FAILED
        )

    async def test_rpa_deposit_uncertain_skipped_when_club_toggle_off(self) -> None:
        with patch.object(esc, "escalation_notification_enabled", return_value=False):
            with patch.object(
                esc, "notify_escalation_slack", new_callable=AsyncMock
            ) as notify:
                await esc.notify_rpa_deposit_uncertain(
                    club_id=1,
                    chat_id=99,
                    title="G",
                    detail="OCR mismatch",
                )
        notify.assert_not_awaited()

    async def test_rpa_deposit_uncertain_includes_detail(self) -> None:
        with patch.object(esc, "escalation_notification_enabled", return_value=True):
            with patch.object(
                esc, "notify_escalation_slack", new_callable=AsyncMock
            ) as notify:
                await esc.notify_rpa_deposit_uncertain(
                    club_id=1,
                    chat_id=99,
                    title="GTO / 1 / Nick",
                    detail="OCR mismatch on trade record amount: saw '', expected one of ['1']",
                )
        notify.assert_awaited_once()
        self.assertEqual(
            notify.await_args.args[0], esc.REASON_RPA_DEPOSIT_UNCERTAIN
        )
        self.assertEqual(
            notify.await_args.kwargs["message_text"],
            "OCR mismatch on trade record amount: saw '', expected one of ['1']",
        )

    async def test_uncertain_format_includes_detail_body(self) -> None:
        with patch.object(esc, "_club_display_name", return_value="ClubGTO"):
            text = esc.format_escalation_slack_text(
                esc.REASON_RPA_DEPOSIT_UNCERTAIN,
                club_id=1,
                chat_id=99,
                title="GTO / 1 / Nick",
                message_text=(
                    "OCR mismatch on trade record amount: saw '', expected one of ['1']"
                ),
            )
        self.assertIn("Deposit UNCERTAIN", text)
        self.assertIn("Club: ClubGTO", text)
        self.assertIn("OCR mismatch on trade record amount", text)


class NotifySlackHeadAdminEscalationTests(unittest.IsolatedAsyncioTestCase):
    async def test_warns_when_channel_missing(self) -> None:
        with patch.dict(
            os.environ,
            {"SLACK_ESCALATION_BOT_TOKEN": "xoxb-test"},
            clear=True,
        ):
            with self.assertLogs(
                "bot.services.slack_ops_notify", level=logging.WARNING
            ) as cm:
                ok = await notify_slack_head_admin_escalation(
                    "alert", source="rpa_deposit_failed"
                )
        self.assertFalse(ok)
        self.assertTrue(
            any("slack_head_admin_escalation: skipped" in m for m in cm.output)
        )

    @patch.dict(
        os.environ,
        {
            "SLACK_ESCALATION_BOT_TOKEN": "xoxb-test",
            "SLACK_HEAD_ADMIN_ESCALATION_CHANNEL_ID": "C_HEAD",
        },
        clear=True,
    )
    @patch("bot.services.slack_ops_notify.httpx.AsyncClient")
    async def test_posts_to_head_admin_channel(
        self, mock_client_cls: MagicMock
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": True, "ts": "1.2"}
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        ok = await notify_slack_head_admin_escalation(
            "RPA deposit failed", source="rpa_deposit_failed"
        )

        self.assertTrue(ok)
        payload = mock_client.post.await_args.kwargs["json"]
        self.assertEqual(payload["channel"], "C_HEAD")
        self.assertEqual(payload["text"], "RPA deposit failed")
        headers = mock_client.post.await_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer xoxb-test")


if __name__ == "__main__":
    unittest.main()
