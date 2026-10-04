"""Staff-unanswered issue-report Slack posts are deleted after 10 minutes."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import escalation_notification as esc


def _job_queue() -> MagicMock:
    jq = MagicMock()
    jq.get_jobs_by_name.return_value = []
    return jq


class StaffUnansweredSlackDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_post_schedules_delete(self) -> None:
        jq = _job_queue()
        with (
            patch(
                "bot.services.slack_ops_notify.post_issue_channel_plain",
                new=AsyncMock(return_value=(True, "C9", "111.222")),
            ),
            patch(
                "bot.services.escalation_observability.record_escalation_event",
                return_value=9,
            ),
            patch(
                "bot.services.escalation_observability.update_escalation_event_slack_ok"
            ),
            patch(
                "bot.services.escalation_observability.mark_escalation_event_slack_delete"
            ) as mark,
            patch(
                "bot.services.escalation_observability.live_history_episode_id",
                return_value=None,
            ),
            patch.object(
                esc, "staff_unanswered_slack_delete_seconds", return_value=600
            ),
        ):
            ok, event_id = await esc.notify_staff_unanswered_issue_channel(
                club_id=None,
                chat_id=-1001,
                title="Player GC",
                message_text="still waiting",
                job_queue=jq,
            )

        self.assertTrue(ok)
        self.assertEqual(event_id, 9)
        mark.assert_called_once()
        self.assertEqual(mark.call_args.kwargs["channel_id"], "C9")
        self.assertEqual(mark.call_args.kwargs["message_ts"], "111.222")
        jq.run_once.assert_called_once()
        self.assertEqual(jq.run_once.call_args.kwargs["name"], "esc_slack_delete_9")
        self.assertAlmostEqual(jq.run_once.call_args.kwargs["when"], 600, delta=2)

    async def test_webhook_post_is_not_scheduled(self) -> None:
        jq = _job_queue()
        with (
            patch(
                "bot.services.slack_ops_notify.post_issue_channel_plain",
                new=AsyncMock(return_value=(True, None, None)),
            ),
            patch(
                "bot.services.escalation_observability.record_escalation_event",
                return_value=4,
            ),
            patch(
                "bot.services.escalation_observability.update_escalation_event_slack_ok"
            ),
            patch(
                "bot.services.escalation_observability.mark_escalation_event_slack_delete"
            ) as mark,
            patch(
                "bot.services.escalation_observability.live_history_episode_id",
                return_value=None,
            ),
        ):
            ok, event_id = await esc.notify_staff_unanswered_issue_channel(
                club_id=None,
                chat_id=-1001,
                title="Player GC",
                message_text="still waiting",
                job_queue=jq,
            )

        self.assertTrue(ok)
        self.assertEqual(event_id, 4)
        mark.assert_not_called()
        jq.run_once.assert_not_called()

    async def test_callback_clears_pending_after_delete(self) -> None:
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        ctx = SimpleNamespace(job=SimpleNamespace(data={"event_id": 9}))
        with (
            patch(
                "bot.services.escalation_observability.get_pending_slack_delete",
                return_value=("C9", "111.222", due),
            ),
            patch(
                "bot.services.slack_ops_notify.delete_issue_channel_message",
                new=AsyncMock(return_value=True),
            ) as delete,
            patch(
                "bot.services.escalation_observability.clear_escalation_event_slack_delete"
            ) as clear,
        ):
            await esc._staff_unanswered_slack_delete_callback(ctx)

        delete.assert_awaited_once_with("C9", "111.222")
        clear.assert_called_once_with(9)

    async def test_callback_keeps_pending_when_delete_fails(self) -> None:
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        ctx = SimpleNamespace(job=SimpleNamespace(data={"event_id": 9}))
        with (
            patch(
                "bot.services.escalation_observability.get_pending_slack_delete",
                return_value=("C9", "111.222", due),
            ),
            patch(
                "bot.services.slack_ops_notify.delete_issue_channel_message",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "bot.services.escalation_observability.clear_escalation_event_slack_delete"
            ) as clear,
        ):
            await esc._staff_unanswered_slack_delete_callback(ctx)

        clear.assert_not_called()

    async def test_callback_skips_when_not_yet_due(self) -> None:
        later = datetime.now(timezone.utc) + timedelta(minutes=5)
        ctx = SimpleNamespace(job=SimpleNamespace(data={"event_id": 9}))
        with (
            patch(
                "bot.services.escalation_observability.get_pending_slack_delete",
                return_value=("C9", "111.222", later),
            ),
            patch(
                "bot.services.slack_ops_notify.delete_issue_channel_message",
                new=AsyncMock(),
            ) as delete,
        ):
            await esc._staff_unanswered_slack_delete_callback(ctx)

        delete.assert_not_awaited()

    def test_restore_reschedules_pending_deletes(self) -> None:
        jq = _job_queue()
        future = datetime.now(timezone.utc) + timedelta(seconds=120)
        past = datetime.now(timezone.utc) - timedelta(seconds=30)
        with patch(
            "bot.services.escalation_observability.list_pending_slack_deletes",
            return_value=[(1, future), (2, past)],
        ):
            esc.restore_staff_unanswered_slack_deletes(jq)

        self.assertEqual(jq.run_once.call_count, 2)
        waits = {
            call.kwargs["data"]["event_id"]: call.kwargs["when"]
            for call in jq.run_once.call_args_list
        }
        self.assertAlmostEqual(waits[1], 120, delta=2)
        self.assertEqual(waits[2], 0.1)
