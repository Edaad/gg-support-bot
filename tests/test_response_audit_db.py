"""run_response_audit end to end on in-memory SQLite (load → rules → upsert)."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, time as dt_time, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from bot.services import response_audit as ra
from db.models import (
    AutomatedStaffMessage,
    Club,
    EscalationDecisionLog,
    GroupChatDailyTranscript,
    ResponseAuditRun,
    ResponseAuditVerdict,
    ResponseEvent,
    SupportGroupChat,
)
from tests.support.sqlite_db import get_db_for, make_sqlite_session_factory

ET = ZoneInfo("America/New_York")
DAY = date(2026, 9, 29)
CHAT = -1001234567890
INTERNAL_CHAT = -1001111111111
TEST_CHAT = -1002222222222
OPS_CHAT = -1003333333333
PLAYER = 111
STAFF = 222
CLUB_ACCOUNT = 333

TABLES = [
    "clubs",
    "club_linked_accounts",
    "groups",
    "support_group_chats",
    "group_chat_daily_activity",
    "group_chat_daily_transcripts",
    "escalation_episodes",
    "escalation_events",
    "escalation_decision_log",
    "automated_staff_messages",
    "staff_handle_ids",
    "response_events",
    "response_audit_verdicts",
    "response_audit_runs",
]


def at(hour: int, minute: int) -> datetime:
    return datetime.combine(DAY, dt_time(hour, minute), tzinfo=ET).astimezone(
        timezone.utc
    )


def msg(mid, when, sender, text, *, is_bot=False):
    return {
        "id": mid,
        "date": when.isoformat(),
        "sender_id": sender,
        "sender_name": None,
        "username": None,
        "is_bot": is_bot,
        "text": text,
        "reply_to_msg_id": None,
        "media_type": None,
        "media_filename": None,
        "edit_date": None,
        "is_service": False,
    }


class RunResponseAuditTests(unittest.TestCase):
    def setUp(self):
        self.factory = make_sqlite_session_factory(TABLES)
        get_db = get_db_for(self.factory)
        self._patches = [
            patch("db.connection.get_db", get_db),
            patch("bot.services.automated_staff_messages.get_db", get_db),
            patch("bot.services.response_audit_staff.get_db", get_db),
            patch.dict(os.environ, {"RESPONSE_AUDIT_EXTRA_STAFF_IDS": str(STAFF)}),
        ]
        for p in self._patches:
            p.start()
        self._seed()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _transcript(self, session, chat_id, messages):
        session.add(
            GroupChatDailyTranscript(
                activity_date=DAY,
                chat_id=chat_id,
                club_id=1,
                status="complete",
                message_count=len(messages),
                messages=messages,
                tail_messages=[],
                attempt_count=1,
            )
        )

    def _seed(self):
        question = [
            msg(1, at(10, 0), PLAYER, "where is my cashout??"),
            msg(2, at(10, 2), CLUB_ACCOUNT, "$250 owed"),
            msg(3, at(10, 3), PLAYER, "150"),
            msg(4, at(10, 9), STAFF, "sending now"),
        ]
        with self.factory() as s:
            s.add(Club(id=1, name="Round Table", telegram_user_id=CLUB_ACCOUNT))
            s.flush()
            for chat, title, internal in (
                (CHAT, "RT / 1234-5678 / Sam", False),
                (INTERNAL_CHAT, "RT / 1000-0001 / Staff Room", True),
                (TEST_CHAT, "RT / 8888-8888 / QA", False),
            ):
                s.add(
                    SupportGroupChat(
                        club_key="round_table",
                        club_display_name="Round Table",
                        player_telegram_user_id=PLAYER if chat == CHAT else None,
                        telegram_chat_id=chat,
                        telegram_chat_title=title,
                        is_internal=internal,
                    )
                )
                self._transcript(s, chat, question)
            # Club-linked but no support row and not a GC title → skipped.
            self._transcript(s, OPS_CHAT, question)
            s.add(
                AutomatedStaffMessage(chat_id=CHAT, message_id=2, kind="cash_owed_pin")
            )
            s.add(
                EscalationDecisionLog(
                    decision="skipped",
                    reason="expected_flow",
                    telegram_chat_id=CHAT,
                    telegram_message_id=3,
                    created_at=at(10, 3),
                )
            )
            s.commit()

    def test_builds_events_skips_internal_test_and_non_support_chats(self):
        summary = ra.run_response_audit(DAY)
        self.assertEqual(summary.chats_scanned, 1)
        self.assertEqual(summary.chats_excluded, 3)
        self.assertEqual(summary.events, 1)
        self.assertEqual(summary.candidates, 1)
        with self.factory() as s:
            rows = s.query(ResponseEvent).all()
            self.assertEqual(len(rows), 1)
            ev = rows[0]
            self.assertEqual(ev.chat_id, CHAT)
            self.assertEqual(ev.start_kind, "player_question")
            self.assertEqual(ev.clock_stop_msg_id, 4)
            self.assertEqual(ev.response_seconds, 540)
            self.assertTrue(ev.is_candidate)
            self.assertEqual(ev.group_title, "RT / 1234-5678 / Sam")
            self.assertEqual(ev.rule_version, ra.RULE_VERSION)
            self.assertEqual([m["id"] for m in ev.excerpt], [1, 2, 3, 4])
            self.assertIsNotNone(s.get(ResponseAuditRun, DAY))

    def test_rerun_is_idempotent_and_keeps_verdicts(self):
        ra.run_response_audit(DAY)
        with self.factory() as s:
            ev_id = s.query(ResponseEvent.id).scalar()
            s.add(ResponseAuditVerdict(response_event_id=ev_id, verdict="BREACH"))
            s.commit()

        skipped = ra.run_response_audit(DAY)
        self.assertTrue(skipped.skipped_existing)

        again = ra.run_response_audit(DAY, force=True)
        self.assertEqual(again.events, 1)
        with self.factory() as s:
            rows = s.query(ResponseEvent).all()
            self.assertEqual([r.id for r in rows], [ev_id])
            self.assertEqual(s.query(ResponseAuditVerdict).count(), 1)

    def test_single_chat_run(self):
        summary = ra.run_response_audit(DAY, chat_id=CHAT)
        self.assertEqual(summary.events, 1)
        with self.factory() as s:
            # Per-chat runs do not write the day's run marker.
            self.assertIsNone(s.get(ResponseAuditRun, DAY))


if __name__ == "__main__":
    unittest.main()
