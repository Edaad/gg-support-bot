"""Tests for payment notification club-bucket routing."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from notification.handlers.bind import _staff_notification_chat_key
from notification.handlers.bind_callbacks import _canonical_notification_chat_id
from notification.payment_notification_delivery import (
    deliver_payment_notification,
    payment_notification_html_to_slack,
)
from notification.payment_notification_routing import (
    BUCKET_CREATOR_CLUB,
    BUCKET_GTO,
    BUCKET_RT_AT,
    club_binding_notification_chat_ids,
    ingest_notification_titles,
    notification_bucket_for_title,
    notification_destination_bucket,
    resolve_ingest_notification_chat_id,
    resolve_ingest_notification_chat_ids,
    resolve_notification_chat_id,
    resolve_notification_chat_ids,
)


MAIN_CHAT = -1000000000001
GTO_CHAT = -1000000000002
RT_CHAT = -1000000000003
CREATOR_CHAT = -1000000000004

_CHAT_ENV = {
    "PAYMENT_NOTIFICATION_CHAT_ID": str(MAIN_CHAT),
    "PAYMENT_NOTIFICATION_CHAT_ID_GTO": str(GTO_CHAT),
    "PAYMENT_NOTIFICATION_CHAT_ID_RT_AT": str(RT_CHAT),
    "PAYMENT_NOTIFICATION_CHAT_ID_CREATOR_CLUB": str(CREATOR_CHAT),
}


class NotificationBucketForTitleTestCase(unittest.TestCase):
    def test_gto_standard_title(self):
        self.assertEqual(
            notification_bucket_for_title("GTO / 8190-5287 / ThePirate343"),
            BUCKET_GTO,
        )

    def test_gto_megagroup_without_player_id(self):
        self.assertEqual(notification_bucket_for_title("GTO / / Player"), BUCKET_GTO)

    def test_rt_at_combined_prefix(self):
        self.assertEqual(
            notification_bucket_for_title("RT AT / 6485-8168 / Angus"),
            BUCKET_RT_AT,
        )

    def test_cc_title(self):
        self.assertEqual(
            notification_bucket_for_title("CC / 1-2 / p"),
            BUCKET_CREATOR_CLUB,
        )

    def test_at_title(self):
        self.assertEqual(notification_bucket_for_title("AT / 1-2 / p"), BUCKET_RT_AT)

    def test_cc_at_title_is_mixed(self):
        self.assertIsNone(notification_bucket_for_title("CC AT / 1-2 / p"))

    def test_junk_has_no_bucket(self):
        self.assertIsNone(notification_bucket_for_title("Random group"))
        self.assertIsNone(notification_bucket_for_title(""))
        self.assertIsNone(notification_bucket_for_title(None))

    def test_mixed_tokens_on_one_title(self):
        self.assertIsNone(notification_bucket_for_title("GTO RT / 1-2 / x"))


class NotificationDestinationBucketTestCase(unittest.TestCase):
    def test_empty_is_main(self):
        self.assertIsNone(notification_destination_bucket([]))

    def test_all_gto(self):
        self.assertEqual(
            notification_destination_bucket(
                ["GTO / 1-2 / A", "GTO / / B"],
            ),
            BUCKET_GTO,
        )

    def test_all_rt_at(self):
        self.assertEqual(
            notification_destination_bucket(["RT / 1-2 / A", "AT / 3-4 / B"]),
            BUCKET_RT_AT,
        )

    def test_all_creator(self):
        self.assertEqual(
            notification_destination_bucket(["CC / 1-2 / A", "CC / 3-4 / B"]),
            BUCKET_CREATOR_CLUB,
        )

    def test_mixed_buckets_is_main(self):
        self.assertIsNone(
            notification_destination_bucket(
                ["RT / 1-2 / A", "GTO / 3-4 / B"],
            )
        )


class ResolveNotificationChatIdTestCase(unittest.TestCase):
    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_gto_titles_use_gto_chat(self):
        self.assertEqual(resolve_notification_chat_ids(["GTO / 1-2 / A"]), [GTO_CHAT])

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_cc_titles_use_creator_chat(self):
        self.assertEqual(resolve_notification_chat_ids(["CC / 1-2 / A"]), [CREATOR_CHAT])

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_rt_titles_use_rt_chat(self):
        self.assertEqual(resolve_notification_chat_ids(["RT / 1-2 / A"]), [RT_CHAT])

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_mixed_broadcasts_to_all_club_chats(self):
        self.assertEqual(
            resolve_notification_chat_ids(["GTO / 1-2 / A", "RT / 3-4 / B"]),
            [GTO_CHAT, RT_CHAT, CREATOR_CHAT],
        )

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_unknown_broadcasts_to_all_club_chats(self):
        self.assertEqual(resolve_notification_chat_ids([]), [GTO_CHAT, RT_CHAT, CREATOR_CHAT])
        self.assertEqual(resolve_notification_chat_ids(["nope"]), [GTO_CHAT, RT_CHAT, CREATOR_CHAT])

    @patch.dict(
        os.environ,
        {
            "PAYMENT_NOTIFICATION_CHAT_ID": str(MAIN_CHAT),
            "PAYMENT_NOTIFICATION_CHAT_ID_GTO": "",
            "PAYMENT_NOTIFICATION_CHAT_ID_RT_AT": str(RT_CHAT),
            "PAYMENT_NOTIFICATION_CHAT_ID_CREATOR_CLUB": str(CREATOR_CHAT),
        },
        clear=False,
    )
    def test_missing_gto_env_broadcasts(self):
        self.assertEqual(
            resolve_notification_chat_id(["GTO / 1-2 / A"]),
            RT_CHAT,
        )

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_legacy_single_chat_helper(self):
        self.assertEqual(resolve_notification_chat_id(["GTO / 1-2 / A"]), GTO_CHAT)


class ResolveIngestNotificationChatIdTestCase(unittest.TestCase):
    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_gto_only_candidates(self):
        cands = [
            SimpleNamespace(group_title="GTO / 1-2 / A"),
            SimpleNamespace(group_title="GTO / / B"),
        ]
        self.assertEqual(
            resolve_ingest_notification_chat_ids(
                auto_bound=False,
                ambiguous_candidates=cands,
            ),
            [GTO_CHAT],
        )

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_mixed_candidates_broadcast(self):
        cands = [
            SimpleNamespace(group_title="GTO / 1-2 / A"),
            SimpleNamespace(group_title="RT / 3-4 / B"),
        ]
        self.assertEqual(
            resolve_ingest_notification_chat_ids(
                auto_bound=False,
                ambiguous_candidates=cands,
            ),
            [GTO_CHAT, RT_CHAT, CREATOR_CHAT],
        )

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_no_candidates_broadcast(self):
        self.assertEqual(
            resolve_ingest_notification_chat_ids(auto_bound=False, ambiguous_candidates=[]),
            [GTO_CHAT, RT_CHAT, CREATOR_CHAT],
        )

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_auto_bound_uses_bound_title(self):
        self.assertEqual(
            resolve_ingest_notification_chat_ids(
                group_title="CC / 1-2 / p",
                auto_bound=True,
                ambiguous_candidates=[SimpleNamespace(group_title="GTO / 9-9 / x")],
            ),
            [CREATOR_CHAT],
        )

    def test_ingest_titles_ignore_candidates_when_auto_bound(self):
        titles = ingest_notification_titles(
            group_title="GTO / 1-2 / A",
            auto_bound=True,
            ambiguous_candidates=[SimpleNamespace(group_title="RT / 3-4 / B")],
        )
        self.assertEqual(titles, ["GTO / 1-2 / A"])

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    def test_club_binding_chat_ids(self):
        self.assertEqual(
            club_binding_notification_chat_ids(),
            [GTO_CHAT, RT_CHAT, CREATOR_CHAT],
        )


class StaffChatAllowlistTestCase(unittest.TestCase):
    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    @patch("notification.handlers.bind.notification_chat_id", return_value=MAIN_CHAT)
    def test_bind_allows_club_chats_and_rejects_other(self, _main):
        self.assertEqual(_staff_notification_chat_key(GTO_CHAT), GTO_CHAT)
        self.assertEqual(_staff_notification_chat_key(CREATOR_CHAT), CREATOR_CHAT)
        self.assertEqual(_staff_notification_chat_key(MAIN_CHAT), MAIN_CHAT)
        self.assertIsNone(_staff_notification_chat_key(-1099))

    @patch.dict(os.environ, _CHAT_ENV, clear=False)
    @patch(
        "notification.handlers.bind_callbacks.notification_chat_id",
        return_value=MAIN_CHAT,
    )
    def test_callback_canonical_allows_club_chats(self, _main):
        self.assertEqual(_canonical_notification_chat_id(RT_CHAT), RT_CHAT)
        self.assertEqual(_canonical_notification_chat_id(GTO_CHAT), GTO_CHAT)
        self.assertEqual(_canonical_notification_chat_id(CREATOR_CHAT), CREATOR_CHAT)
        self.assertIsNone(_canonical_notification_chat_id(-1099))


class SendTelegramNotificationFallbackTestCase(unittest.IsolatedAsyncioTestCase):
    @patch.dict(
        os.environ,
        {
            "TELEGRAM_NOTIFICATION_BOT_TOKEN": "tok",
            "PAYMENT_NOTIFICATION_CHAT_ID": str(MAIN_CHAT),
        },
        clear=False,
    )
    @patch("bot.services.venmo_payments._telegram_api", new_callable=AsyncMock)
    async def test_uses_explicit_club_chat(self, mock_api):
        mock_api.return_value = {
            "ok": True,
            "result": {"message_id": 9, "chat": {"id": GTO_CHAT}},
        }
        from bot.services.venmo_payments import send_telegram_notification

        chat, mid = await send_telegram_notification("hi", chat_id=GTO_CHAT)
        self.assertEqual((chat, mid), (GTO_CHAT, 9))
        self.assertEqual(mock_api.await_args.args[1]["chat_id"], GTO_CHAT)

    @patch.dict(
        os.environ,
        {
            "TELEGRAM_NOTIFICATION_BOT_TOKEN": "tok",
            "PAYMENT_NOTIFICATION_CHAT_ID": str(MAIN_CHAT),
        },
        clear=False,
    )
    @patch("bot.services.venmo_payments._telegram_api", new_callable=AsyncMock)
    async def test_club_send_failure_retries_main(self, mock_api):
        mock_api.side_effect = [
            RuntimeError("forbidden"),
            {"ok": True, "result": {"message_id": 10, "chat": {"id": MAIN_CHAT}}},
        ]
        from bot.services.venmo_payments import send_telegram_notification

        chat, mid = await send_telegram_notification("hi", chat_id=GTO_CHAT)
        self.assertEqual((chat, mid), (MAIN_CHAT, 10))
        self.assertEqual(mock_api.await_count, 2)
        self.assertEqual(mock_api.await_args_list[0].args[1]["chat_id"], GTO_CHAT)
        self.assertEqual(mock_api.await_args_list[1].args[1]["chat_id"], MAIN_CHAT)


class PaymentNotificationHtmlToSlackTestCase(unittest.TestCase):
    def test_converts_bold_and_links(self):
        html_text = (
            '🔔 Venmo Payment Notification\n'
            'Group Chat: <a href="https://t.me/c/1/2">RT / 1-2 / p</a>\n'
            'Amount: <b>$50.00</b>'
        )
        slack = payment_notification_html_to_slack(html_text)
        self.assertIn("<https://t.me/c/1/2|RT / 1-2 / p>", slack)
        self.assertIn("*$50.00*", slack)
        self.assertNotIn("<b>", slack)


class DeliverPaymentNotificationTestCase(unittest.IsolatedAsyncioTestCase):
    @patch.dict(
        os.environ,
        {
            "TELEGRAM_NOTIFICATION_BOT_TOKEN": "tok",
            "PAYMENT_NOTIFICATION_CHAT_ID": str(MAIN_CHAT),
        },
        clear=False,
    )
    @patch(
        "bot.services.slack_ops_notify.notify_slack_escalation",
        new_callable=AsyncMock,
    )
    @patch("bot.services.venmo_payments._telegram_api", new_callable=AsyncMock)
    async def test_delivers_to_bind_chats_and_slack(self, mock_api, mock_slack):
        mock_api.side_effect = [
            {"ok": True, "result": {"message_id": 1, "chat": {"id": GTO_CHAT}}},
            {"ok": True, "result": {"message_id": 2, "chat": {"id": RT_CHAT}}},
        ]
        mock_slack.return_value = True
        chat, mid = await deliver_payment_notification(
            "🔔 Venmo Payment Notification\nAmount: <b>$1.00</b>",
            bind_chat_ids=[GTO_CHAT, RT_CHAT],
        )
        self.assertEqual((chat, mid), (GTO_CHAT, 1))
        self.assertEqual(mock_api.await_count, 2)
        mock_slack.assert_awaited_once()
        self.assertIn("*$1.00*", mock_slack.await_args.args[0])
        self.assertEqual(mock_slack.await_args.kwargs["source"], "payment_notification")


if __name__ == "__main__":
    unittest.main()
