"""Tests for group_activity_handler escalation decision logging."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from bot.handlers import group_activity as handler
from bot.services import escalation_notification as esc
from bot.services import escalation_observability as obs
from bot.services import support_group_idle_episode as idle_ep


def _update(*, chat_id=-100, user_id=7, is_bot=False, text="Need 500", message_id=99):
    user = SimpleNamespace(id=user_id, is_bot=is_bot, username=None)
    chat = SimpleNamespace(id=chat_id, type="supergroup", title="GTO / test")
    message = SimpleNamespace(
        message_id=message_id,
        text=text,
        caption=None,
        date=None,
        from_user=user,
        photo=None,
        video=None,
        document=None,
        animation=None,
        voice=None,
        video_note=None,
        audio=None,
        sticker=None,
        chat=chat,
    )
    return SimpleNamespace(
        effective_message=message,
        effective_chat=chat,
        effective_user=user,
        message=message,
    )


class GroupActivityDecisionLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_escalation_off_records_skip(self):
        update = _update()
        context = SimpleNamespace(bot=MagicMock(), job_queue=None, chat_data={})
        recorded = []

        def _capture(**kwargs):
            recorded.append(kwargs)
            return 1

        with (
            patch.object(handler, "get_club_for_chat", return_value=4),
            patch.object(handler.pk, "popup_keyboard_eligible", return_value=True),
            patch.object(handler.esc, "escalation_notification_eligible", return_value=False),
            patch.object(handler.ga, "is_support_sender", return_value=False),
            patch.object(handler.pk, "is_flow_command_text", return_value=False),
            patch.object(handler.ga, "record_human_message"),
            patch.object(handler.pk, "upsert_player_telegram_user_id"),
            patch.object(handler.pk, "remember_player_message"),
            patch.object(handler, "deposit_flow_active", return_value=False),
            patch.object(handler, "cashout_flow_active", return_value=False),
            patch.object(handler, "transfer_flow_active", return_value=False),
            patch.object(handler.pk, "silent_strip_if_installed", new_callable=AsyncMock),
            patch.object(handler.pk, "payment_window_gate_pending", return_value=False),
            patch.object(handler.pk, "schedule_popup_keyboard_idle"),
            patch.object(handler, "record_escalation_decision", side_effect=_capture),
            patch.object(handler.idle_ep, "consume_expected_flow_input", return_value=False),
        ):
            await handler.group_activity_handler(update, context)

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["decision"], obs.DECISION_SKIPPED)
        self.assertEqual(recorded[0]["reason"], obs.REASON_ESC_OFF)

    async def test_staff_no_episode(self):
        update = _update(user_id=7516419496, text="hi")
        context = SimpleNamespace(bot=MagicMock(), job_queue=None, chat_data={})
        recorded = []

        with (
            patch.object(handler, "get_club_for_chat", return_value=4),
            patch.object(handler.pk, "popup_keyboard_eligible", return_value=False),
            patch.object(handler.esc, "escalation_notification_eligible", return_value=True),
            patch.object(handler.ga, "is_support_sender", return_value=True),
            patch.object(handler.pk, "is_flow_command_text", return_value=False),
            patch.object(handler.ga, "record_human_message"),
            patch.object(handler.idle_ep, "episode_is_open", return_value=False),
            patch.object(handler, "record_escalation_decision", side_effect=lambda **k: recorded.append(k) or 1),
        ):
            await handler.group_activity_handler(update, context)

        self.assertEqual(recorded[0]["reason"], obs.REASON_STAFF_NO_EPISODE)

    async def test_expected_flow_skip(self):
        update = _update(text="500")
        context = SimpleNamespace(bot=MagicMock(), job_queue=None, chat_data={})
        recorded = []

        with (
            patch.object(handler, "get_club_for_chat", return_value=4),
            patch.object(handler.pk, "popup_keyboard_eligible", return_value=False),
            patch.object(handler.esc, "escalation_notification_eligible", return_value=True),
            patch.object(handler.ga, "is_support_sender", return_value=False),
            patch.object(handler.pk, "is_flow_command_text", return_value=False),
            patch.object(handler.ga, "record_human_message"),
            patch.object(handler.pk, "upsert_player_telegram_user_id"),
            patch.object(handler.pk, "remember_player_message"),
            patch.object(handler, "deposit_flow_active", return_value=True),
            patch.object(handler, "cashout_flow_active", return_value=False),
            patch.object(handler, "transfer_flow_active", return_value=False),
            patch.object(handler.idle_ep, "consume_expected_flow_input", return_value=True),
            patch.object(handler.pk, "cancel_popup_keyboard_idle"),
            patch.object(handler, "record_escalation_decision", side_effect=lambda **k: recorded.append(k) or 1),
        ):
            await handler.group_activity_handler(update, context)

        self.assertEqual(recorded[0]["reason"], obs.REASON_EXPECTED_FLOW)
        self.assertEqual(recorded[0]["decision"], obs.DECISION_SKIPPED)

    async def test_player_idle_opened(self):
        update = _update(text="Need 500")
        context = SimpleNamespace(bot=MagicMock(), job_queue=None, chat_data={})
        recorded = []
        eid = uuid4()
        result = idle_ep.ReachOutResult(
            outcome="opened", episode_id=eid, escalation_event_id=42
        )

        with (
            patch.object(handler, "get_club_for_chat", return_value=4),
            patch.object(handler.pk, "popup_keyboard_eligible", return_value=False),
            patch.object(handler.esc, "escalation_notification_eligible", return_value=True),
            patch.object(handler.ga, "is_support_sender", return_value=False),
            patch.object(handler.pk, "is_flow_command_text", return_value=False),
            patch.object(handler.ga, "record_human_message"),
            patch.object(handler.pk, "upsert_player_telegram_user_id"),
            patch.object(handler.pk, "remember_player_message"),
            patch.object(handler, "deposit_flow_active", return_value=False),
            patch.object(handler, "cashout_flow_active", return_value=False),
            patch.object(handler, "transfer_flow_active", return_value=False),
            patch.object(handler.idle_ep, "consume_expected_flow_input", return_value=False),
            patch.object(
                handler.esc,
                "handle_deposit_sent_player_followup",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(
                handler.esc,
                "handle_deposit_player_message",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(handler.esc, "is_valid_deposit_flow_answer", return_value=False),
            patch.object(
                handler.idle_ep,
                "on_player_reach_out",
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.object(handler, "record_escalation_decision", side_effect=lambda **k: recorded.append(k) or 1),
        ):
            await handler.group_activity_handler(update, context)

        self.assertEqual(recorded[0]["decision"], obs.DECISION_FIRED)
        self.assertEqual(recorded[0]["reason"], obs.REASON_PLAYER_IDLE_OPENED)
        self.assertEqual(recorded[0]["episode_id"], eid)
        self.assertEqual(recorded[0]["escalation_event_id"], 42)

    async def test_player_idle_fed(self):
        update = _update(text="?")
        context = SimpleNamespace(bot=MagicMock(), job_queue=None, chat_data={})
        recorded = []
        eid = uuid4()
        result = idle_ep.ReachOutResult(outcome="fed", episode_id=eid)

        with (
            patch.object(handler, "get_club_for_chat", return_value=4),
            patch.object(handler.pk, "popup_keyboard_eligible", return_value=False),
            patch.object(handler.esc, "escalation_notification_eligible", return_value=True),
            patch.object(handler.ga, "is_support_sender", return_value=False),
            patch.object(handler.pk, "is_flow_command_text", return_value=False),
            patch.object(handler.ga, "record_human_message"),
            patch.object(handler.pk, "upsert_player_telegram_user_id"),
            patch.object(handler.pk, "remember_player_message"),
            patch.object(handler, "deposit_flow_active", return_value=False),
            patch.object(handler, "cashout_flow_active", return_value=False),
            patch.object(handler, "transfer_flow_active", return_value=False),
            patch.object(handler.idle_ep, "consume_expected_flow_input", return_value=False),
            patch.object(
                handler.esc,
                "handle_deposit_sent_player_followup",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(
                handler.esc,
                "handle_deposit_player_message",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(handler.esc, "is_valid_deposit_flow_answer", return_value=False),
            patch.object(
                handler.idle_ep,
                "on_player_reach_out",
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.object(handler, "record_escalation_decision", side_effect=lambda **k: recorded.append(k) or 1),
        ):
            await handler.group_activity_handler(update, context)

        self.assertEqual(recorded[0]["reason"], obs.REASON_PLAYER_IDLE_FED)

    async def test_deposit_sent_followup_consumed(self):
        update = _update(text="where is my payment")
        context = SimpleNamespace(bot=MagicMock(), job_queue=None, chat_data={})
        recorded = []
        eid = uuid4()
        result = idle_ep.ReachOutResult(outcome="opened", episode_id=eid)

        with (
            patch.object(handler, "get_club_for_chat", return_value=4),
            patch.object(handler.pk, "popup_keyboard_eligible", return_value=False),
            patch.object(handler.esc, "escalation_notification_eligible", return_value=True),
            patch.object(handler.ga, "is_support_sender", return_value=False),
            patch.object(handler.pk, "is_flow_command_text", return_value=False),
            patch.object(handler.ga, "record_human_message"),
            patch.object(handler.pk, "upsert_player_telegram_user_id"),
            patch.object(handler.pk, "remember_player_message"),
            patch.object(handler, "deposit_flow_active", return_value=False),
            patch.object(handler, "cashout_flow_active", return_value=False),
            patch.object(handler, "transfer_flow_active", return_value=False),
            patch.object(handler.idle_ep, "consume_expected_flow_input", return_value=False),
            patch.object(
                handler.esc,
                "handle_deposit_sent_player_followup",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                handler.idle_ep,
                "feed_or_open_episode",
                new_callable=AsyncMock,
                return_value=result,
            ) as feed,
            patch.object(handler, "record_escalation_decision", side_effect=lambda **k: recorded.append(k) or 1),
        ):
            await handler.group_activity_handler(update, context)

        feed.assert_awaited_once()
        self.assertEqual(recorded[0]["decision"], obs.DECISION_FIRED)
        self.assertEqual(recorded[0]["reason"], esc.REASON_DEPOSIT_SENT_FOLLOWUP)


if __name__ == "__main__":
    unittest.main()
