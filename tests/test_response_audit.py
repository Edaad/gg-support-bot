"""Response audit builder: synthetic transcripts through the clock rules."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time as dt_time, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from bot.services import response_audit as ra
from bot.services.agent_handoff_copy import ADMIN_SHORTLY_COPY, AGENT_SHORTLY_COPY
from bot.services.automated_staff_messages import (
    KIND_CASH_OWED_PIN,
    KIND_STAFF_ADD_CONFIRMATION,
)
from bot.services.escalation_notification import (
    DEPOSIT_SENT_ACK_COPY_CRYPTO,
    REASON_AUTO_CASHOUT_ESCALATION,
    REASON_DEPOSIT_SENT_TIMEOUT,
    REASON_PLAYER_IDLE,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 9, 29)
CHAT = -1001234567890
CLUB = 2
PLAYER = 111
STAFF = 222
CLUB_ACCOUNT = 333
UNKNOWN = 444
BOT = 999
BTC_TXID = "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"

EXACT, PREFIXES = ra.automated_template_texts()


def at(hour: int, minute: int, second: int = 0, *, next_day: bool = False) -> datetime:
    day = DAY + timedelta(days=1) if next_day else DAY
    local = datetime.combine(day, dt_time(hour, minute, second), tzinfo=ET)
    return local.astimezone(timezone.utc)


def msg(
    mid: int,
    when: datetime,
    sender: int | None,
    text: str = "",
    *,
    media: str | None = None,
    username: str | None = None,
) -> dict:
    return {
        "id": mid,
        "date": when.isoformat(),
        "sender_id": sender,
        "sender_name": None,
        "username": username,
        "is_bot": sender == BOT,
        "text": text,
        "reply_to_msg_id": None,
        "media_type": media,
        "media_filename": None,
        "edit_date": None,
        "is_service": False,
    }


def audit_input(messages, *, tail=None, **kw) -> ra.ChatAuditInput:
    base = dict(
        activity_date=DAY,
        chat_id=CHAT,
        club_id=CLUB,
        group_title="RT / 1234-5678 / Sam",
        messages=messages,
        tail_messages=tail or [],
        staff_ids=frozenset({STAFF, CLUB_ACCOUNT}),
        bot_usernames=frozenset({"ytranslatebot"}),
        player_id=PLAYER,
        template_exact=EXACT,
        template_prefixes=PREFIXES,
    )
    base.update(kw)
    return ra.ChatAuditInput(**base)


class ScenarioTests(unittest.TestCase):
    def test_gratitude_only_burst_starts_nothing(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), CLUB_ACCOUNT, "Added 100, good luck!!"),
                    msg(2, at(10, 1), PLAYER, "Bet bet thank you g looks"),
                    msg(3, at(10, 1, 20), PLAYER, "🙏"),
                ]
            )
        )
        self.assertEqual(events, [])

    def test_staff_add_after_bot_received_is_no_event(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "", media="MessageMediaPhoto"),
                    msg(
                        2,
                        at(10, 0, 30),
                        BOT,
                        "Payment of $100 received — credits will be loaded to "
                        "your account shortly!!",
                    ),
                    msg(3, at(10, 0, 33), CLUB_ACCOUNT, "Added 100 credits, gl!!"),
                ],
                automated_kinds={3: KIND_STAFF_ADD_CONFIRMATION},
            )
        )
        self.assertEqual(events, [])

    def test_screenshot_inside_live_deposit_is_no_event(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "/deposit"),
                    msg(2, at(10, 0, 1), BOT, "How much would you like to deposit?"),
                    msg(3, at(10, 0, 20), PLAYER, "$200"),
                    msg(4, at(10, 0, 22), BOT, "Send $200 to @rt-pay then tap below."),
                    msg(5, at(10, 3), PLAYER, "", media="MessageMediaPhoto"),
                    msg(6, at(10, 3, 10), PLAYER, "sent", media="MessageMediaPhoto"),
                ]
            )
        )
        self.assertEqual(events, [])

    def test_zero_owed_from_staff_account_does_not_stop_clock(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "where is my cashout??"),
                    msg(2, at(10, 2), CLUB_ACCOUNT, "$0 owed"),
                    msg(3, at(10, 9), STAFF, "Sending now, sorry for the wait"),
                ]
            )
        )
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.start_kind, ra.START_PLAYER_QUESTION)
        self.assertEqual(ev.clock_stop_msg_id, 3)
        self.assertEqual(ev.responder_telegram_user_id, STAFF)
        self.assertEqual(ev.response_seconds, 540)
        self.assertTrue(ev.is_candidate)
        self.assertIn(2, [m["id"] for m in ev.excerpt])

    def test_recorded_automated_post_does_not_stop_clock(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "can I cash out 300"),
                    msg(2, at(10, 1), CLUB_ACCOUNT, "$300 owed"),
                    msg(3, at(10, 2), CLUB_ACCOUNT, "on it"),
                ],
                automated_kinds={2: KIND_CASH_OWED_PIN},
            )
        )
        self.assertEqual(events[0].clock_stop_msg_id, 3)
        self.assertEqual(events[0].response_seconds, 120)
        self.assertFalse(events[0].is_candidate)
        self.assertIsNone(events[0].excerpt)

    def test_staff_run_deposit_for_player_is_no_player_question(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), STAFF, "/deposit"),
                    msg(2, at(10, 0, 1), BOT, "How much would you like to deposit?"),
                    msg(3, at(10, 0, 30), PLAYER, "150"),
                    msg(4, at(10, 0, 40), BOT, "Pick a method below."),
                    msg(5, at(10, 0, 55), PLAYER, "venmo please"),
                    msg(6, at(10, 5), PLAYER, "", media="MessageMediaPhoto"),
                ],
                # Escalation decision log: expected wizard input.
                flow_answer_msg_ids=frozenset({5}),
            )
        )
        self.assertEqual(events, [])

    def test_btc_txid_starts_crypto_clock(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), BOT, DEPOSIT_SENT_ACK_COPY_CRYPTO),
                    msg(2, at(10, 2), PLAYER, BTC_TXID),
                    msg(3, at(10, 3), STAFF, "Added 500"),
                ]
            )
        )
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.start_kind, ra.START_CRYPTO_TXID)
        self.assertEqual(ev.clock_start_msg_id, 2)
        self.assertEqual(ev.response_seconds, 60)

    def test_bot_handoff_line_starts_clock_and_attaches_slack_event(self):
        esc = ra.SlackEscalation(
            id=77,
            reason=REASON_AUTO_CASHOUT_ESCALATION,
            created_at=at(10, 1, 2),
        )
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "/cashout"),
                    msg(2, at(10, 1), BOT, AGENT_SHORTLY_COPY),
                    msg(3, at(10, 10), STAFF, "hey what's up"),
                ],
                escalations=[esc],
            )
        )
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.start_kind, ra.START_BOT_HANDOFF)
        self.assertEqual(ev.clock_start_msg_id, 2)
        self.assertEqual(ev.escalation_event_id, 77)
        self.assertEqual(ev.response_seconds, 540)

    def test_admin_shortly_line_is_a_handoff(self):
        events = ra.build_chat_events(
            audit_input([msg(1, at(10, 0), BOT, ADMIN_SHORTLY_COPY)])
        )
        self.assertEqual([e.start_kind for e in events], [ra.START_BOT_HANDOFF])
        self.assertIsNone(events[0].response_seconds)

    def test_clock_crossing_midnight_stops_inside_tail(self):
        events = ra.build_chat_events(
            audit_input(
                [msg(1, at(23, 58), PLAYER, "hello can someone help me cash out")],
                tail=[msg(2, at(0, 7, next_day=True), STAFF, "hey! on it")],
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].clock_stop_msg_id, 2)
        self.assertEqual(events[0].response_seconds, 540)

    def test_tail_player_question_belongs_to_next_day(self):
        events = ra.build_chat_events(
            audit_input(
                [],
                tail=[msg(1, at(0, 30, next_day=True), PLAYER, "anyone around?")],
            )
        )
        self.assertEqual(events, [])

    def test_unknown_sender_is_flagged(self):
        events = ra.build_chat_events(
            audit_input([msg(1, at(11, 0), UNKNOWN, "hello? anyone there")])
        )
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].pre_labels["unknown_sender"])
        self.assertIsNone(events[0].response_seconds)
        self.assertTrue(events[0].is_candidate)
        self.assertEqual(events[0].excerpt[0]["role"], ra.ROLE_UNKNOWN)


class RuleDetailTests(unittest.TestCase):
    def test_new_burst_while_clock_open_does_not_open_second(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "is venmo working today"),
                    msg(2, at(10, 5), PLAYER, "hello??"),
                    msg(3, at(10, 7), STAFF, "yes it is"),
                ]
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].clock_start_msg_id, 1)

    def test_burst_clock_is_first_message(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "", media="MessageMediaPhoto"),
                    msg(2, at(10, 0, 20), PLAYER, "why is this not loaded yet"),
                    msg(3, at(10, 1), STAFF, "checking"),
                ]
            )
        )
        self.assertEqual(events[0].clock_start_msg_id, 1)
        self.assertEqual(events[0].trigger_msg_ids, [1, 2])

    def test_slack_escalation_within_60s_attaches_to_player_question(self):
        esc = ra.SlackEscalation(
            id=5, reason=REASON_DEPOSIT_SENT_TIMEOUT, created_at=at(10, 0, 40)
        )
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "did you get my payment"),
                    msg(2, at(10, 4), STAFF, "yes adding now"),
                ],
                escalations=[esc],
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start_kind, ra.START_PLAYER_QUESTION)
        self.assertEqual(events[0].escalation_event_id, 5)

    def test_slack_escalation_alone_starts_clock_at_created_at(self):
        esc = ra.SlackEscalation(
            id=6, reason=REASON_DEPOSIT_SENT_TIMEOUT, created_at=at(14, 0, 0)
        )
        events = ra.build_chat_events(
            audit_input(
                [msg(1, at(14, 2), STAFF, "got it, adding")],
                escalations=[esc],
            )
        )
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.start_kind, ra.START_SLACK_ESCALATION)
        self.assertIsNone(ev.clock_start_msg_id)
        self.assertEqual(ev.escalation_event_id, 6)
        self.assertEqual(ev.response_seconds, 120)
        self.assertEqual(
            ev.pre_labels["escalation_reason"], REASON_DEPOSIT_SENT_TIMEOUT
        )

    def test_non_start_reason_is_ignored(self):
        esc = ra.SlackEscalation(
            id=7, reason=REASON_PLAYER_IDLE, created_at=at(14, 0, 0)
        )
        self.assertEqual(ra.build_chat_events(audit_input([], escalations=[esc])), [])

    def test_bot_messages_never_stop_clock_and_commands_do(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "i need to withdraw"),
                    msg(2, at(10, 0, 5), BOT, "Translation: i need to withdraw"),
                    msg(3, at(10, 2), STAFF, "/cashout"),
                ]
            )
        )
        self.assertEqual(events[0].clock_stop_msg_id, 3)
        self.assertTrue(events[0].pre_labels["staff_drove_wizard"])

    def test_translation_bot_by_username_is_bot(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(10, 0), PLAYER, "necesito ayuda"),
                    msg(2, at(10, 0, 3), 5555, "I need help", username="YTranslateBot"),
                ]
            )
        )
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0].pre_labels["unknown_sender"])
        self.assertIsNone(events[0].clock_stop_at)

    def test_carry_over_from_previous_day_blocks_new_burst(self):
        events = ra.build_chat_events(
            audit_input(
                [
                    msg(1, at(0, 2), PLAYER, "still waiting on that cashout"),
                    msg(2, at(0, 5), STAFF, "sent!"),
                ],
                carry_open_until=at(0, 5),
            )
        )
        self.assertEqual(events, [])

    def test_excerpt_window_and_cap(self):
        messages = [
            # A separate earlier burst (ends 09:56:30, >60 s before the question).
            msg(i, at(9, 40) + timedelta(seconds=10 * i), PLAYER, "", media="Photo")
            for i in range(1, 100)
        ]
        messages.append(msg(500, at(10, 0), PLAYER, "hello why no answer"))
        messages.append(msg(501, at(10, 20), STAFF, "sorry!"))
        messages.append(msg(502, at(11, 0), STAFF, "late message outside window"))
        events = ra.build_chat_events(audit_input(messages))
        ev = [e for e in events if e.clock_start_msg_id == 500][0]
        ids = [m["id"] for m in ev.excerpt]
        self.assertLessEqual(len(ids), ra.EXCERPT_MAX_MESSAGES)
        self.assertIn(500, ids)
        self.assertIn(501, ids)
        self.assertNotIn(502, ids)
        self.assertEqual(ids, sorted(ids, key=lambda i: (i >= 500, i)))


class TextRuleTests(unittest.TestCase):
    def test_gratitude(self):
        for text in (
            "thanks",
            "Bet bet thank you g looks",
            "gl",
            "okay got it",
            "sounds good",
            "thank you so much bro appreciate it",
            "👍🙏",
        ):
            self.assertTrue(ra.is_gratitude_or_closer(text), text)
        for text in (
            "thanks but where is my cashout",
            "ok when will it load?",
            "can I deposit 50",
        ):
            self.assertFalse(ra.is_gratitude_or_closer(text), text)

    def test_wizard_answers(self):
        for text in (
            "$50",
            "50$",
            "1,000",
            "100.00",
            "@john-doe",
            "$johndoe",
            "jd@example.com",
            "sent",
            "done",
            "just sent it",
            BTC_TXID,
        ):
            self.assertTrue(ra.is_wizard_answer_text(text), text)
        self.assertFalse(ra.is_wizard_answer_text("Is venmo available?"))

    def test_hash_like(self):
        self.assertTrue(ra.is_hash_like("0x" + "a" * 64))
        self.assertTrue(ra.is_hash_like(f"https://mempool.space/tx/{BTC_TXID}"))
        self.assertFalse(ra.is_hash_like("hello"))

    def test_test_titles(self):
        self.assertTrue(ra.is_test_title("RT / 8888-8888 / Test"))
        self.assertTrue(ra.is_test_title("CC / 2222-2222 / QA"))
        self.assertTrue(ra.is_test_title("Rt/"))
        self.assertFalse(ra.is_test_title("RT / 1234-5678 / Sam"))

    def test_automated_templates(self):
        for text in (
            "$0 owed",
            "0 owed",
            "Your cashout will be processed ASAP!",
            "Group created. Invite link: https://t.me/+abc",
        ):
            self.assertTrue(ra.matches_automated_template(text, EXACT, PREFIXES), text)
        self.assertFalse(ra.matches_automated_template("sending now", EXACT, PREFIXES))


class EscalationChatRefTests(unittest.TestCase):
    def test_supergroup_link(self):
        from bot.services.escalation_notification import (
            format_chat_ref_line,
            with_chat_ref,
        )

        line = format_chat_ref_line(-1003995457474)
        self.assertEqual(line, "Chat: `-1003995457474` https://t.me/c/3995457474")
        self.assertEqual(format_chat_ref_line(-4001), "Chat: `-4001`")
        self.assertIsNone(format_chat_ref_line(0))
        once = with_chat_ref("*Head*", -1003995457474)
        self.assertEqual(with_chat_ref(once, -1003995457474), once)


class ManualActionEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_action_notification_logs_escalation_event(self):
        from bot.services.payment_auto_deposit import CREATOR_STAFF_FOOTER_MANUAL
        from notification import payment_notification_delivery as delivery

        text = f"Venmo $50\n\n{CREATOR_STAFF_FOOTER_MANUAL}"
        with (
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=AsyncMock(return_value=(-1, 10)),
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_escalation",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.services.escalation_observability.record_escalation_event"
            ) as record,
        ):
            await delivery.deliver_payment_notification(
                text,
                bind_chat_ids=[-1],
                support_chat_id=CHAT,
                support_club_id=CLUB,
                support_group_title="RT / 1 / Sam",
            )
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs["reason"], "payment_manual_action")
        self.assertEqual(kwargs["telegram_chat_id"], CHAT)
        self.assertTrue(kwargs["slack_ok"])

    async def test_auto_notification_logs_nothing(self):
        from bot.services.payment_auto_deposit import CREATOR_STAFF_FOOTER_AUTO
        from notification import payment_notification_delivery as delivery

        with (
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=AsyncMock(return_value=(-1, 10)),
            ),
            patch(
                "bot.services.escalation_observability.record_escalation_event"
            ) as record,
        ):
            await delivery.deliver_payment_notification(
                f"Venmo $50\n\n{CREATOR_STAFF_FOOTER_AUTO}",
                bind_chat_ids=[-1],
                support_chat_id=CHAT,
            )
        record.assert_not_called()


class RecordSentTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_sent_handles_albums_and_ignores_missing_ids(self):
        from types import SimpleNamespace

        from bot.services import automated_staff_messages as asm

        with patch.object(asm, "record_automated_staff_messages") as rec:
            await asm.record_sent(
                [
                    SimpleNamespace(chat_id=CHAT, id=1),
                    SimpleNamespace(chat_id=CHAT, id=2),
                ],
                kind=asm.KIND_CASH_ASAP,
            )
            await asm.record_sent(None, kind=asm.KIND_CASH_ASAP)
        rec.assert_called_once_with([(CHAT, 1), (CHAT, 2)], asm.KIND_CASH_ASAP)


if __name__ == "__main__":
    unittest.main()
