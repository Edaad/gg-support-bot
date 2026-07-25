"""Tests for multi-payer (distinct first/last name) staff warnings."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.payment_multi_payer_warning import (
    evaluate_multi_payer_warning,
    format_multi_payer_warning_text,
    maybe_warn_multi_payer,
    normalize_payer_name,
)


class TestNormalizePayerName(unittest.TestCase):
    def test_collapses_case_and_whitespace(self) -> None:
        self.assertEqual(
            normalize_payer_name("  John   SMITH "),
            "john smith",
        )


class TestFormatMultiPayerWarningText(unittest.TestCase):
    def test_plain_copy(self) -> None:
        text = format_multi_payer_warning_text(
            payment_method_slug="zelle",
            group_title="CC / 3912-4783 / gillyyy21",
            payer_names=["ANNA HOLLENSTEINER", "DYLAN CESTARO", "ZOE CRUZ"],
            html=False,
        )
        self.assertIn("DO NOT ADD CHIPS", text)
        self.assertIn("more than 2 different payers on Zelle", text)
        self.assertIn("using 3 different accounts to pay", text)
        self.assertIn("Group: CC / 3912-4783 / gillyyy21", text)
        self.assertIn("• ANNA HOLLENSTEINER", text)
        self.assertNotIn("<b>", text)

    def test_html_escapes(self) -> None:
        text = format_multi_payer_warning_text(
            payment_method_slug="venmo",
            group_title="A <B> & C",
            payer_names=["Tom & Jerry"],
            html=True,
        )
        self.assertIn("DO NOT ADD CHIPS", text)
        self.assertIn("Venmo", text)
        self.assertIn("A &lt;B&gt; &amp; C", text)
        self.assertIn("Tom &amp; Jerry", text)


class TestEvaluateMultiPayerWarning(unittest.TestCase):
    def _patch_prior(self, prior: dict[str, str]):
        return patch(
            "bot.services.payment_multi_payer_warning._distinct_payer_names",
            return_value=prior,
        )

    def test_fires_when_third_new_name(self) -> None:
        prior = {
            "anna hollensteiner": "ANNA HOLLENSTEINER",
            "dylan cestaro": "DYLAN CESTARO",
        }
        with patch("bot.services.payment_multi_payer_warning.get_db") as get_db:
            get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            get_db.return_value.__exit__ = MagicMock(return_value=False)
            with self._patch_prior(prior):
                names = evaluate_multi_payer_warning(
                    payment_method_slug="zelle",
                    payment_id=99,
                    telegram_chat_id=-1001,
                    payer_name="ZOE CRUZ",
                )
        self.assertEqual(
            names,
            ["ANNA HOLLENSTEINER", "DYLAN CESTARO", "ZOE CRUZ"],
        )

    def test_no_fire_when_name_already_known(self) -> None:
        prior = {
            "anna hollensteiner": "ANNA HOLLENSTEINER",
            "dylan cestaro": "DYLAN CESTARO",
            "zoe cruz": "ZOE CRUZ",
        }
        with patch("bot.services.payment_multi_payer_warning.get_db") as get_db:
            get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            get_db.return_value.__exit__ = MagicMock(return_value=False)
            with self._patch_prior(prior):
                names = evaluate_multi_payer_warning(
                    payment_method_slug="zelle",
                    payment_id=99,
                    telegram_chat_id=-1001,
                    payer_name="zoe  cruz",
                )
        self.assertIsNone(names)

    def test_no_fire_at_two_names(self) -> None:
        prior = {"anna hollensteiner": "ANNA HOLLENSTEINER"}
        with patch("bot.services.payment_multi_payer_warning.get_db") as get_db:
            get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            get_db.return_value.__exit__ = MagicMock(return_value=False)
            with self._patch_prior(prior):
                names = evaluate_multi_payer_warning(
                    payment_method_slug="venmo",
                    payment_id=99,
                    telegram_chat_id=-1001,
                    payer_name="DYLAN CESTARO",
                )
        self.assertIsNone(names)


class TestMaybeWarnMultiPayer(unittest.IsolatedAsyncioTestCase):
    async def test_sends_telegram_reply_and_slack(self) -> None:
        names = ["A One", "B Two", "C Three"]
        with (
            patch(
                "bot.services.payment_multi_payer_warning.evaluate_multi_payer_warning",
                return_value=names,
            ),
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new_callable=AsyncMock,
                return_value=(-100999, 42),
            ) as send_tg,
            patch(
                "bot.services.slack_ops_notify.notify_slack_ops",
                new_callable=AsyncMock,
                return_value=True,
            ) as send_slack,
        ):
            ok = await maybe_warn_multi_payer(
                payment_method_slug="zelle",
                payment_id=7,
                telegram_chat_id=-1001,
                payer_name="C Three",
                group_title="CC / 1 / player",
                notification_message_id=123,
                is_test=False,
            )
        self.assertTrue(ok)
        send_tg.assert_awaited_once()
        kwargs = send_tg.await_args.kwargs
        self.assertEqual(kwargs.get("reply_to_message_id"), 123)
        self.assertIn("DO NOT ADD CHIPS", send_tg.await_args.args[0])
        send_slack.assert_awaited_once()
        self.assertEqual(send_slack.await_args.kwargs.get("source"), "multi_payer_warning")
        self.assertIn("3 different accounts", send_slack.await_args.args[0])

    async def test_skips_test_payments(self) -> None:
        with patch(
            "bot.services.payment_multi_payer_warning.evaluate_multi_payer_warning"
        ) as evaluate:
            ok = await maybe_warn_multi_payer(
                payment_method_slug="zelle",
                payment_id=7,
                telegram_chat_id=-1001,
                payer_name="C Three",
                group_title="CC / 1 / player",
                is_test=True,
            )
        self.assertFalse(ok)
        evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
